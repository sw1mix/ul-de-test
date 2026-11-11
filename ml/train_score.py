import json
import logging
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from etl.db import ROOT, connect, read_date
from etl.rules import TEST_START, TRAIN_START, split_name
from ml.features import (
    CATEGORICAL_COLUMNS,
    DOW_PERIOD,
    DOY_PERIOD,
    FEATURE_COLUMNS,
    MONTH_PERIOD,
    NUMERIC_BASELINE_COLUMNS,
)

log = logging.getLogger("ml")
METRICS_PATH = ROOT / "artifacts" / "metrics.json"
MODEL_NAME = "catboost_regressor"

FEATURE_SQL = """
SELECT
    p.price_id,
    p.loading_date,
    p.loading_city_gcid,
    p.unloading_city_gcid,
    p.distance,
    p.total_weight,
    p.total_volume,
    p.weight_category,
    p.volume_category,
    p.price_rub AS target_price_rub,
    p.is_fact,
    p.complex,
    p.outlier_mark,
    COALESCE(p.body_type::text, 'NA') AS body_type,
    CASE
        WHEN p.body_type IS NOT NULL THEN core.body_group(p.body_type)
        ELSE COALESCE(
            (
                SELECT g.body_group
                FROM core.price_body_group g
                WHERE g.price_id = p.price_id
                ORDER BY g.body_group
                LIMIT 1
            ),
            'Не указан'
        )
    END AS body_group,
    btrim(split_part(cf.full_name_local, ',', 1)) AS origin_city,
    COALESCE(NULLIF(btrim(split_part(cf.full_name_local, ',', 2)), ''), 'NA') AS origin_region,
    cf.latitude AS origin_lat,
    cf.longitude AS origin_lon,
    btrim(split_part(ct.full_name_local, ',', 1)) AS destination_city,
    COALESCE(NULLIF(btrim(split_part(ct.full_name_local, ',', 2)), ''), 'NA') AS destination_region,
    ct.latitude AS dest_lat,
    ct.longitude AS dest_lon
FROM core.prices p
JOIN core.cities_dedup cf ON cf.gc_id = p.loading_city_gcid
JOIN core.cities_dedup ct ON ct.gc_id = p.unloading_city_gcid
WHERE p.loading_date >= %(train_start)s
"""


def _cycle(values: np.ndarray, period: float) -> tuple[np.ndarray, np.ndarray]:
    angle = 2.0 * np.pi * values / period
    return np.sin(angle), np.cos(angle)


def add_calendar(frame: pd.DataFrame) -> pd.DataFrame:
    prepared = frame.copy()
    dates = pd.to_datetime(prepared["loading_date"])
    prepared["year"] = dates.dt.year.astype(int)
    month_sin, month_cos = _cycle((dates.dt.month.to_numpy() % 12).astype(float), MONTH_PERIOD)
    dow_sin, dow_cos = _cycle(dates.dt.dayofweek.to_numpy().astype(float), DOW_PERIOD)
    doy_sin, doy_cos = _cycle(dates.dt.dayofyear.to_numpy().astype(float), DOY_PERIOD)
    prepared["month_sin"] = month_sin
    prepared["month_cos"] = month_cos
    prepared["dow_sin"] = dow_sin
    prepared["dow_cos"] = dow_cos
    prepared["doy_sin"] = doy_sin
    prepared["doy_cos"] = doy_cos
    for column in CATEGORICAL_COLUMNS:
        prepared[column] = prepared[column].fillna("NA").astype(str)
    return prepared


def regression_metrics(actual: np.ndarray, predicted: np.ndarray) -> dict:
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    return {
        "r2": float(r2_score(actual, predicted)),
        "mape_pct": float(np.mean(np.abs((actual - predicted) / actual)) * 100),
        "rmse": float(np.sqrt(mean_squared_error(actual, predicted))),
        "mae": float(mean_absolute_error(actual, predicted)),
        "n": int(actual.shape[0]),
    }


def _population(frame: pd.DataFrame) -> pd.Series:
    return (
        frame["is_fact"].astype(bool)
        & ~frame["complex"].astype(bool)
        & ~frame["outlier_mark"].astype(bool)
        & (frame["target_price_rub"] > 0)
        & (frame["distance"] > 0)
    )


def _write_predictions(conn, rows: list[tuple]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM mart.ml_price_predictions WHERE model_name = %(model)s",
            {"model": MODEL_NAME},
        )
        with cur.copy(
            "COPY mart.ml_price_predictions ("
            "price_id, loading_date, loading_city_gcid, unloading_city_gcid, body_group, "
            "actual_price_rub, predicted_price_rub, abs_error_rub, ape_pct, model_name, scored_at"
            ") FROM STDIN"
        ) as copy:
            for row in rows:
                copy.write_row(row)


def _verify_predictions(conn, as_of) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FROM mart.ml_price_predictions
            WHERE loading_date > %(as_of)s
              AND (
                actual_price_rub IS NOT NULL
                OR abs_error_rub IS NOT NULL
                OR ape_pct IS NOT NULL
              )
            """,
            {"as_of": as_of},
        )
        leaked = cur.fetchone()[0]
        if leaked:
            raise RuntimeError(f"future rows stored an actual price: {leaked}")
        cur.execute(
            """
            SELECT count(*) FROM mart.ml_price_predictions
            WHERE loading_date BETWEEN %(test_start)s AND %(as_of)s
              AND actual_price_rub IS NULL
            """,
            {"test_start": TEST_START, "as_of": as_of},
        )
        missing_actual = cur.fetchone()[0]
        if missing_actual:
            raise RuntimeError(f"test rows without actual price: {missing_actual}")


def run_ml() -> dict:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    as_of = read_date("AS_OF_DATE", "2026-10-26")
    seed = int(os.environ.get("MODEL_SEED", "42"))
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(FEATURE_SQL, {"train_start": TRAIN_START})
            columns = [desc.name for desc in cur.description]
            frame = pd.DataFrame(cur.fetchall(), columns=columns)
    if frame["price_id"].duplicated().any():
        raise RuntimeError("feature query returned duplicate price_id")

    frame["split"] = frame["loading_date"].map(lambda value: split_name(value, as_of))
    frame = add_calendar(frame)
    eligible = _population(frame)
    train = frame.loc[eligible & (frame["split"] == "train")].copy()
    test = frame.loc[eligible & (frame["split"] == "test")].copy()
    future = frame.loc[frame["split"] == "future"].copy()
    if train.empty or test.empty:
        raise RuntimeError(f"empty split: train {len(train)} test {len(test)}")
    log.info("rows train %s test %s future %s", len(train), len(test), len(future))

    ridge = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("model", Ridge(alpha=1.0)),
        ]
    )
    ridge.fit(train[list(NUMERIC_BASELINE_COLUMNS)], train["target_price_rub"])
    ridge_metrics = regression_metrics(
        test["target_price_rub"].to_numpy(),
        ridge.predict(test[list(NUMERIC_BASELINE_COLUMNS)]),
    )
    log.info("ridge baseline %s", {key: round(value, 4) for key, value in ridge_metrics.items()})

    train_pool = Pool(
        train[list(FEATURE_COLUMNS)],
        label=train["target_price_rub"],
        cat_features=list(CATEGORICAL_COLUMNS),
    )
    test_pool = Pool(test[list(FEATURE_COLUMNS)], cat_features=list(CATEGORICAL_COLUMNS))
    future_pool = Pool(future[list(FEATURE_COLUMNS)], cat_features=list(CATEGORICAL_COLUMNS))
    model = CatBoostRegressor(
        iterations=400,
        depth=6,
        learning_rate=0.08,
        loss_function="RMSE",
        random_seed=seed,
        thread_count=4,
        allow_writing_files=False,
        verbose=False,
    )
    model.fit(train_pool)
    test_pred = model.predict(test_pool)
    future_pred = model.predict(future_pool) if not future.empty else np.array([])
    catboost_metrics = regression_metrics(test["target_price_rub"].to_numpy(), test_pred)
    log.info(
        "catboost %s",
        {key: round(value, 4) for key, value in catboost_metrics.items()},
    )

    scored_at = datetime.now(timezone.utc)
    rows: list[tuple] = []
    actual = test["target_price_rub"].to_numpy(dtype=float)
    for index, price in enumerate(test.itertuples(index=False)):
        predicted = float(test_pred[index])
        error = abs(float(actual[index]) - predicted)
        rows.append(
            (
                int(price.price_id),
                price.loading_date,
                int(price.loading_city_gcid),
                int(price.unloading_city_gcid),
                price.body_group,
                float(actual[index]),
                predicted,
                error,
                error / float(actual[index]) * 100,
                MODEL_NAME,
                scored_at,
            )
        )
    for index, price in enumerate(future.itertuples(index=False)):
        rows.append(
            (
                int(price.price_id),
                price.loading_date,
                int(price.loading_city_gcid),
                int(price.unloading_city_gcid),
                price.body_group,
                None,
                float(future_pred[index]),
                None,
                None,
                MODEL_NAME,
                scored_at,
            )
        )

    with connect() as conn:
        _write_predictions(conn, rows)
        _verify_predictions(conn, as_of)
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM mart.ml_price_predictions")
            stored = cur.fetchone()[0]
        conn.commit()
    if stored != len(rows):
        raise RuntimeError(f"stored predictions {stored} != built {len(rows)}")

    report = {
        "model_name": MODEL_NAME,
        "random_seed": seed,
        "as_of": as_of.isoformat(),
        "train_window": f"{TRAIN_START.isoformat()} inclusive, 2026-10-05 exclusive",
        "test_window": f"{TEST_START.isoformat()} inclusive, {as_of.isoformat()} inclusive",
        "n_train": int(len(train)),
        "n_test": int(len(test)),
        "n_future": int(len(future)),
        "catboost": catboost_metrics,
        "ridge_baseline": ridge_metrics,
        "features": list(FEATURE_COLUMNS),
    }
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    METRICS_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("metrics written to %s", METRICS_PATH)
    return report


def main() -> None:
    run_ml()
