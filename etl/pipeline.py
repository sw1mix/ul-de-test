import json
import logging
from datetime import date

from etl.db import ROOT, connect, execute_script, read_date

log = logging.getLogger("etl")

DWH_SQL = ROOT / "sql" / "dwh.sql"
STATS_PATH = ROOT / "artifacts" / "etl_stats.json"

DELETE_WINDOW = """
DELETE FROM core.prices
WHERE loading_date BETWEEN %(start)s AND %(end)s
"""

DELETE_QUARANTINE = """
DELETE FROM core.dq_quarantine_prices
WHERE loading_date BETWEEN %(start)s AND %(end)s
"""

RELOAD_CITIES = """
TRUNCATE core.cities_dedup
"""

INSERT_CITIES = """
INSERT INTO core.cities_dedup (gc_id, city_id, name, full_name_local, latitude, longitude)
SELECT DISTINCT ON (gc_id)
    gc_id, id, name, full_name_local, latitude, longitude
FROM public.cities
ORDER BY gc_id, id
"""

INSERT_QUARANTINE = """
INSERT INTO core.dq_quarantine_prices (
    price_id, loading_date, reason_code, price, currency_code, distance,
    total_weight, total_volume, loading_city_gcid, unloading_city_gcid, nearest_rate_date
)
WITH src AS (
    SELECT
        p.*,
        EXISTS (SELECT 1 FROM public.cities c WHERE c.gc_id = p.loading_city_gcid) AS from_known,
        EXISTS (SELECT 1 FROM public.cities c WHERE c.gc_id = p.unloading_city_gcid) AS to_known,
        er.value AS fx_rate
    FROM public.prices p
    LEFT JOIN public.exchange_rates er
        ON p.currency_code <> '643'
       AND er.num_code = p.currency_code
       AND er.setting_date = p.nearest_rate_date
    WHERE p.loading_date BETWEEN %(start)s AND %(end)s
),
marked AS (
    SELECT
        src.*,
        CASE
            WHEN price <= 0 THEN 'PRICE_NON_POSITIVE'
            WHEN currency_code = '643' AND price <= 1000 THEN 'RUB_PRICE_LE_1000'
            WHEN distance <= 0 THEN 'DISTANCE_NON_POSITIVE'
            WHEN total_weight < 0 THEN 'WEIGHT_NEGATIVE'
            WHEN total_volume < 0 THEN 'VOLUME_NEGATIVE'
            WHEN NOT from_known OR NOT to_known THEN 'CITY_NOT_IN_DIM'
            WHEN currency_code <> '643' AND fx_rate IS NULL THEN 'FX_RATE_MISSING'
            WHEN currency_code NOT IN ('643', '840', '978') THEN 'CURRENCY_UNKNOWN'
            ELSE NULL
        END AS reason_code
    FROM src
)
SELECT
    id, loading_date, reason_code, price, currency_code, distance,
    total_weight, total_volume, loading_city_gcid, unloading_city_gcid, nearest_rate_date
FROM marked
WHERE reason_code IS NOT NULL
"""

INSERT_CORE = """
INSERT INTO core.prices (
    price_id, loading_date, end_loading_date, loading_city_gcid, unloading_city_gcid,
    distance, total_weight, total_volume, weight_category, volume_category, body_type,
    price, currency_code, price_rub, fx_rate, nearest_rate_date, complex, price_type,
    is_fact, organization_id, outlier_mark
)
WITH src AS (
    SELECT
        p.*,
        CASE WHEN p.currency_code = '643' THEN 1.0 ELSE er.value END AS fx_rate
    FROM public.prices p
    LEFT JOIN public.exchange_rates er
        ON p.currency_code <> '643'
       AND er.num_code = p.currency_code
       AND er.setting_date = p.nearest_rate_date
    WHERE p.loading_date BETWEEN %(start)s AND %(end)s
      AND NOT EXISTS (
          SELECT 1 FROM core.dq_quarantine_prices q WHERE q.price_id = p.id
      )
)
SELECT
    id,
    loading_date,
    end_loading_date,
    loading_city_gcid,
    unloading_city_gcid,
    distance,
    total_weight,
    total_volume,
    core.weight_category(total_weight, weight_category),
    core.volume_category(total_volume, volume_category),
    body_type,
    price,
    currency_code,
    price * fx_rate,
    fx_rate,
    nearest_rate_date,
    complex,
    price_type,
    is_fact,
    organization_id,
    outlier_mark
FROM src
WHERE fx_rate IS NOT NULL
"""

INSERT_GROUPS = """
INSERT INTO core.price_body_group (price_id, body_group, body_std_id)
SELECT DISTINCT
    p.price_id,
    core.body_group(s.body_type_id),
    core.body_std_id(s.body_type_id)
FROM core.prices p
JOIN (
    SELECT pbt.price_id, pbt.body_type_id
    FROM public.price_body_types pbt
    UNION ALL
    SELECT pr.price_id, pr.body_type
    FROM core.prices pr
    WHERE NOT EXISTS (
        SELECT 1 FROM public.price_body_types x WHERE x.price_id = pr.price_id
    )
) s ON s.price_id = p.price_id
WHERE p.loading_date BETWEEN %(start)s AND %(end)s
"""

CORE_STATS = """
SELECT count(*),
       coalesce(sum(price_id), 0)::bigint,
       coalesce(round(sum(price_rub)::numeric, 2), 0)
FROM core.prices
WHERE loading_date BETWEEN %(start)s AND %(end)s
"""

QUARANTINE_STATS = """
SELECT reason_code, count(*)
FROM core.dq_quarantine_prices
WHERE loading_date BETWEEN %(start)s AND %(end)s
GROUP BY reason_code
ORDER BY reason_code
"""

REBUILD_MART = """
TRUNCATE mart.route_card
"""

INSERT_MART = """
INSERT INTO mart.route_card (
    price_month, loading_city_gcid, unloading_city_gcid, loading_city, unloading_city,
    body_group, body_std_id, weight_category,
    p25_trip, median_trip, p75_trip, avg_trip,
    p25_km, median_km, p75_km,
    trips_count, carriers_count, cv_rate, mom_pct, yoy_pct, reliability_level, as_of
)
WITH params AS (
    SELECT %(as_of)s::date AS as_of
),
base AS (
    SELECT DISTINCT
        date_trunc('month', d.price_date)::date AS price_month,
        p.price_id,
        p.loading_city_gcid,
        p.unloading_city_gcid,
        g.body_group,
        g.body_std_id,
        p.weight_category,
        p.loading_date,
        p.price_rub,
        p.distance,
        p.organization_id,
        p.price_type
    FROM core.prices p
    JOIN core.price_body_group g ON g.price_id = p.price_id
    JOIN public.price_dates d ON d.price_id = p.price_id
    CROSS JOIN params
    WHERE p.is_fact
      AND NOT p.complex
      AND NOT p.outlier_mark
      AND p.price_rub > 0
      AND p.distance > 0
      AND d.price_date <= params.as_of
),
agg AS (
    SELECT
        price_month,
        loading_city_gcid,
        unloading_city_gcid,
        body_group,
        min(body_std_id) AS body_std_id,
        weight_category,
        percentile_cont(0.25) WITHIN GROUP (ORDER BY price_rub) AS p25_trip,
        percentile_cont(0.50) WITHIN GROUP (ORDER BY price_rub) AS median_trip,
        percentile_cont(0.75) WITHIN GROUP (ORDER BY price_rub) AS p75_trip,
        avg(price_rub) AS avg_trip,
        percentile_cont(0.25) WITHIN GROUP (ORDER BY price_rub / distance) AS p25_km,
        percentile_cont(0.50) WITHIN GROUP (ORDER BY price_rub / distance) AS median_km,
        percentile_cont(0.75) WITHIN GROUP (ORDER BY price_rub / distance) AS p75_km,
        count(DISTINCT price_id) AS trips_count,
        count(DISTINCT organization_id) FILTER (WHERE price_type IN (3, 9)) AS carriers_count,
        CASE
            WHEN count(DISTINCT price_id) < 2 OR avg(price_rub) = 0 THEN NULL
            ELSE stddev_samp(price_rub) / avg(price_rub)
        END AS cv_rate
    FROM base
    GROUP BY price_month, loading_city_gcid, unloading_city_gcid, body_group, weight_category
)
SELECT
    a.price_month,
    a.loading_city_gcid,
    a.unloading_city_gcid,
    cf.name,
    ct.name,
    a.body_group,
    a.body_std_id,
    a.weight_category,
    a.p25_trip,
    a.median_trip,
    a.p75_trip,
    a.avg_trip,
    a.p25_km,
    a.median_km,
    a.p75_km,
    a.trips_count,
    a.carriers_count,
    a.cv_rate,
    CASE
        WHEN prev.median_trip IS NULL OR prev.median_trip = 0 THEN NULL
        ELSE (a.median_trip - prev.median_trip) / prev.median_trip * 100
    END AS mom_pct,
    CASE
        WHEN yoy.median_trip IS NULL OR yoy.median_trip = 0 THEN NULL
        ELSE (a.median_trip - yoy.median_trip) / yoy.median_trip * 100
    END AS yoy_pct,
    CASE
        WHEN a.trips_count >= 30
         AND a.carriers_count >= 5
         AND (
            params.as_of - LEAST(
                params.as_of,
                (a.price_month + INTERVAL '1 month' - INTERVAL '1 day')::date
            )
         ) <= 30
        THEN 'HIGH'
        WHEN a.trips_count >= 10 THEN 'MEDIUM'
        ELSE 'LOW_DATA'
    END AS reliability_level,
    params.as_of
FROM agg a
JOIN core.cities_dedup cf ON cf.gc_id = a.loading_city_gcid
JOIN core.cities_dedup ct ON ct.gc_id = a.unloading_city_gcid
CROSS JOIN params
LEFT JOIN agg prev
    ON prev.price_month = (a.price_month - INTERVAL '1 month')::date
   AND prev.loading_city_gcid = a.loading_city_gcid
   AND prev.unloading_city_gcid = a.unloading_city_gcid
   AND prev.body_group = a.body_group
   AND prev.weight_category = a.weight_category
LEFT JOIN agg yoy
    ON yoy.price_month = (a.price_month - INTERVAL '1 year')::date
   AND yoy.loading_city_gcid = a.loading_city_gcid
   AND yoy.unloading_city_gcid = a.unloading_city_gcid
   AND yoy.body_group = a.body_group
   AND yoy.weight_category = a.weight_category
"""

MART_STATS = """
SELECT count(*),
       coalesce(sum(trips_count), 0)::bigint,
       coalesce(sum(carriers_count), 0)::bigint,
       coalesce(sum(round(median_trip::numeric, 4)), 0)
FROM mart.route_card
"""


def _session(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SET work_mem = '256MB'")
        cur.execute("SET statement_timeout = 0")


def _window_stats(conn, start: date, end: date) -> tuple:
    with conn.cursor() as cur:
        cur.execute(CORE_STATS, {"start": start, "end": end})
        core = tuple(cur.fetchone())
        cur.execute(QUARANTINE_STATS, {"start": start, "end": end})
        reasons = tuple((row[0], row[1]) for row in cur.fetchall())
    return core, reasons


def reload_window(conn, start: date, end: date) -> tuple:
    params = {"start": start, "end": end}
    with conn.cursor() as cur:
        cur.execute(DELETE_WINDOW, params)
        cur.execute(DELETE_QUARANTINE, params)
        cur.execute(RELOAD_CITIES)
        cur.execute(INSERT_CITIES)
        cur.execute(INSERT_QUARANTINE, params)
        cur.execute(INSERT_CORE, params)
        cur.execute(INSERT_GROUPS, params)
    return _window_stats(conn, start, end)


def rebuild_mart(conn, as_of: date) -> tuple:
    with conn.cursor() as cur:
        cur.execute(REBUILD_MART)
        cur.execute(INSERT_MART, {"as_of": as_of})
        cur.execute(MART_STATS)
        return tuple(cur.fetchone())


def verify(conn, start: date, end: date, as_of: date) -> dict:
    checks = {
        "weight": (
            "SELECT core.weight_category(30, NULL), core.weight_category(10, NULL), "
            "core.weight_category(9.96, NULL), core.weight_category(0.44, NULL), "
            "core.weight_category(29.96, NULL)",
            ("G20T", "FTL", "FTL", "OTH", "G20T"),
        ),
        "volume": (
            "SELECT core.volume_category(120, NULL), core.volume_category(0, NULL), "
            "core.volume_category(120.1, NULL)",
            ("FTL", "OTH", "OTH"),
        ),
        "body": (
            "SELECT core.body_group(57), core.body_group(46), core.body_group(1), "
            "core.body_group(NULL), core.body_group(68)",
            ("Тент", "Рефрижератор", "Борт", "Не указан", "Прочие"),
        ),
    }
    with conn.cursor() as cur:
        for name, (query, expected) in checks.items():
            cur.execute(query)
            got = tuple(cur.fetchone())
            if got != expected:
                raise RuntimeError(f"{name} rules mismatch: {got} != {expected}")

        cur.execute(
            """
            SELECT full_name_local FROM core.cities_dedup WHERE gc_id = 1003
            """
        )
        kazan = cur.fetchone()[0]
        if kazan != "Казань, Республика Татарстан":
            raise RuntimeError(f"city dedup kept the wrong Kazan row: {kazan}")

        cur.execute(
            """
            SELECT
                (SELECT count(*) FROM public.prices
                 WHERE loading_date BETWEEN %(start)s AND %(end)s),
                (SELECT count(*) FROM core.prices
                 WHERE loading_date BETWEEN %(start)s AND %(end)s),
                (SELECT count(*) FROM core.dq_quarantine_prices
                 WHERE loading_date BETWEEN %(start)s AND %(end)s)
            """,
            {"start": start, "end": end},
        )
        source_n, core_n, quarantine_n = cur.fetchone()
        if source_n != core_n + quarantine_n:
            raise RuntimeError(
                f"source {source_n} != core {core_n} + quarantine {quarantine_n}"
            )

        cur.execute(
            """
            SELECT count(*) FROM core.prices p
            JOIN core.dq_quarantine_prices q ON q.price_id = p.price_id
            """
        )
        overlap = cur.fetchone()[0]
        if overlap:
            raise RuntimeError(f"price landed in core and quarantine: {overlap}")

        cur.execute(
            "SELECT count(*) FROM mart.route_card WHERE price_month > %(as_of)s",
            {"as_of": as_of},
        )
        future_months = cur.fetchone()[0]
        if future_months:
            raise RuntimeError(f"future months in route card: {future_months}")

        cur.execute(
            """
            SELECT reason_code, count(*)
            FROM core.dq_quarantine_prices
            GROUP BY reason_code
            ORDER BY reason_code
            """
        )
        reasons = {row[0]: row[1] for row in cur.fetchall()}
        cur.execute("SELECT count(*), coalesce(sum(trips_count), 0) FROM mart.route_card")
        mart_rows, mart_trips = cur.fetchone()

    if "PRICE_NON_POSITIVE" not in reasons or "FX_RATE_MISSING" not in reasons:
        raise RuntimeError(f"expected quarantine buckets missing: {reasons}")

    return {
        "source_rows": source_n,
        "core_rows": core_n,
        "quarantine_rows": quarantine_n,
        "quarantine_by_reason": reasons,
        "mart_rows": mart_rows,
        "mart_trips": mart_trips,
        "as_of": as_of.isoformat(),
        "window": [start.isoformat(), end.isoformat()],
    }


def run_etl() -> dict:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    start = read_date("START_DATE", "2023-01-01")
    end = read_date("END_DATE", "2027-12-31")
    as_of = read_date("AS_OF_DATE", "2026-10-26")
    log.info("etl window %s..%s as_of %s", start, end, as_of)

    with connect() as conn:
        _session(conn)
        execute_script(conn, DWH_SQL)
        conn.commit()

        first = reload_window(conn, start, end)
        conn.commit()
        second = reload_window(conn, start, end)
        conn.commit()
        if first != second:
            raise RuntimeError(f"core reload is not idempotent: {first} != {second}")
        core_count, core_sum_id, core_sum_rub = first[0]
        log.info(
            "core rows %s sum_price_rub %s quarantine %s",
            core_count,
            core_sum_rub,
            dict(first[1]),
        )

        mart_first = rebuild_mart(conn, as_of)
        conn.commit()
        mart_second = rebuild_mart(conn, as_of)
        conn.commit()
        if mart_first != mart_second:
            raise RuntimeError(f"mart rebuild is not idempotent: {mart_first} != {mart_second}")
        log.info("mart rows %s trips %s", mart_first[0], mart_first[1])

        stats = verify(conn, start, end, as_of)
        conn.commit()

    stats["core_price_rub_sum"] = float(core_sum_rub)
    stats["idempotent_rerun"] = True
    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATS_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("etl checks passed, stats written to %s", STATS_PATH)
    return stats


def main() -> None:
    run_etl()
