import math

MONTH_PERIOD = 12.0
DOW_PERIOD = 7.0
DOY_PERIOD = 365.25

FEATURE_COLUMNS = (
    "distance",
    "total_weight",
    "total_volume",
    "origin_lat",
    "origin_lon",
    "dest_lat",
    "dest_lon",
    "origin_city",
    "destination_city",
    "origin_region",
    "destination_region",
    "body_group",
    "body_type",
    "weight_category",
    "volume_category",
    "year",
    "month_sin",
    "month_cos",
    "dow_sin",
    "dow_cos",
    "doy_sin",
    "doy_cos",
)

CATEGORICAL_COLUMNS = (
    "origin_city",
    "destination_city",
    "origin_region",
    "destination_region",
    "body_group",
    "body_type",
    "weight_category",
    "volume_category",
)

NUMERIC_BASELINE_COLUMNS = (
    "distance",
    "total_weight",
    "total_volume",
    "origin_lat",
    "origin_lon",
    "dest_lat",
    "dest_lon",
    "year",
    "month_sin",
    "month_cos",
    "dow_sin",
    "dow_cos",
    "doy_sin",
    "doy_cos",
)

BANNED_FEATURES = frozenset(
    {
        "price",
        "price_rub",
        "target_price_rub",
        "price_with_vat",
        "outlier_mark",
        "price_group_id",
        "avg_price",
        "min_km_price",
        "max_km_price",
        "price_km",
        "fx_rate",
        "carrier_cost",
    }
)


def cyclical(value: float, period: float) -> tuple[float, float]:
    angle = 2.0 * math.pi * float(value) / period
    return math.sin(angle), math.cos(angle)


def month_cycle(month: int) -> tuple[float, float]:
    return cyclical(month % 12, MONTH_PERIOD)


def place_parts(full_name: str) -> tuple[str, str]:
    city, separator, region = full_name.partition(",")
    if not separator:
        return city.strip() or "NA", "NA"
    return city.strip() or "NA", region.strip() or "NA"
