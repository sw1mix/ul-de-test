-- Аналитический слой: core (очищенные факты) и mart (витрины).
-- Повторный прогон безопасен: объекты создаются с IF NOT EXISTS / OR REPLACE.

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS mart;

CREATE OR REPLACE FUNCTION core.body_group(body_type_id integer)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT CASE
    WHEN body_type_id IS NULL THEN 'Не указан'
    WHEN body_type_id IN (56, 57, 58, 59, 60, 61) THEN 'Тент'
    WHEN body_type_id IN (4, 46, 47, 48) THEN 'Рефрижератор'
    WHEN body_type_id = 2 THEN 'Изотерм'
    WHEN body_type_id IN (1, 15) THEN 'Борт'
    ELSE 'Прочие'
  END
$$;

CREATE OR REPLACE FUNCTION core.body_std_id(body_type_id integer)
RETURNS integer
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT CASE
    WHEN body_type_id IS NULL THEN NULL
    WHEN body_type_id IN (56, 57, 58, 59, 60, 61) THEN 56
    WHEN body_type_id IN (4, 46, 47, 48) THEN 4
    WHEN body_type_id = 2 THEN 2
    WHEN body_type_id IN (1, 15) THEN 15
    ELSE body_type_id
  END
$$;

CREATE OR REPLACE FUNCTION core.weight_category(weight double precision, given text)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT COALESCE(
    NULLIF(btrim(given), ''),
    CASE
      WHEN round(weight::numeric, 1) >= 30 THEN 'G20T'
      WHEN round(weight::numeric, 1) >= 10 THEN 'FTL'
      WHEN round(weight::numeric, 1) >= 5 THEN '10T'
      WHEN round(weight::numeric, 1) >= 2.5 THEN '5T'
      WHEN round(weight::numeric, 1) >= 0.5 THEN 'L5T'
      ELSE 'OTH'
    END
  )
$$;

CREATE OR REPLACE FUNCTION core.volume_category(volume double precision, given text)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
  SELECT COALESCE(
    NULLIF(btrim(given), ''),
    CASE
      WHEN volume > 0 AND volume <= 120 THEN 'FTL'
      ELSE 'OTH'
    END
  )
$$;

CREATE TABLE IF NOT EXISTS core.cities_dedup (
    gc_id           integer PRIMARY KEY,
    city_id         bigint NOT NULL,
    name            text NOT NULL,
    full_name_local text NOT NULL,
    latitude        double precision,
    longitude       double precision
);

CREATE TABLE IF NOT EXISTS core.prices (
    price_id            bigint PRIMARY KEY,
    loading_date        date NOT NULL,
    end_loading_date    date,
    loading_city_gcid   integer NOT NULL,
    unloading_city_gcid integer NOT NULL,
    distance            double precision NOT NULL,
    total_weight        double precision NOT NULL,
    total_volume        double precision NOT NULL,
    weight_category     varchar(8) NOT NULL,
    volume_category     varchar(8) NOT NULL,
    body_type           integer,
    price               double precision NOT NULL,
    currency_code       varchar(3) NOT NULL,
    price_rub           double precision NOT NULL,
    fx_rate             double precision NOT NULL,
    nearest_rate_date   date,
    complex             boolean NOT NULL,
    price_type          smallint NOT NULL,
    is_fact             boolean NOT NULL,
    organization_id     integer,
    outlier_mark        boolean NOT NULL
);

CREATE INDEX IF NOT EXISTS core_prices_loading_date_idx
    ON core.prices (loading_date);

CREATE TABLE IF NOT EXISTS core.price_body_group (
    price_id    bigint NOT NULL REFERENCES core.prices (price_id) ON DELETE CASCADE,
    body_group  text NOT NULL,
    body_std_id integer,
    PRIMARY KEY (price_id, body_group)
);

CREATE TABLE IF NOT EXISTS core.dq_quarantine_prices (
    price_id            bigint PRIMARY KEY,
    loading_date        date NOT NULL,
    reason_code         text NOT NULL,
    price               double precision,
    currency_code       varchar(3),
    distance            double precision,
    total_weight        double precision,
    total_volume        double precision,
    loading_city_gcid   integer,
    unloading_city_gcid integer,
    nearest_rate_date   date,
    quarantined_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS dq_quarantine_reason_idx
    ON core.dq_quarantine_prices (reason_code);

CREATE TABLE IF NOT EXISTS mart.route_card (
    price_month         date NOT NULL,
    loading_city_gcid   integer NOT NULL,
    unloading_city_gcid integer NOT NULL,
    loading_city        text NOT NULL,
    unloading_city      text NOT NULL,
    body_group          text NOT NULL,
    body_std_id         integer,
    weight_category     varchar(8) NOT NULL,
    p25_trip            double precision,
    median_trip         double precision,
    p75_trip            double precision,
    avg_trip            double precision,
    p25_km              double precision,
    median_km           double precision,
    p75_km              double precision,
    trips_count         integer NOT NULL,
    carriers_count      integer NOT NULL,
    cv_rate             double precision,
    mom_pct             double precision,
    yoy_pct             double precision,
    reliability_level   text NOT NULL,
    as_of               date NOT NULL,
    PRIMARY KEY (
        price_month,
        loading_city_gcid,
        unloading_city_gcid,
        body_group,
        weight_category
    )
);

CREATE TABLE IF NOT EXISTS mart.ml_price_predictions (
    price_id            bigint NOT NULL,
    loading_date        date NOT NULL,
    loading_city_gcid   integer NOT NULL,
    unloading_city_gcid integer NOT NULL,
    body_group          text NOT NULL,
    actual_price_rub    double precision,
    predicted_price_rub double precision NOT NULL,
    abs_error_rub       double precision,
    ape_pct             double precision,
    model_name          text NOT NULL,
    scored_at           timestamptz NOT NULL,
    PRIMARY KEY (price_id, model_name)
);
