from datetime import date

from etl.db import split_sql
from etl.rules import (
    AS_OF,
    dq_reason,
    reliability,
    relative_change,
    split_name,
    volume_category,
    weight_category,
)
from ml.features import (
    BANNED_FEATURES,
    FEATURE_COLUMNS,
    month_cycle,
    place_parts,
)


def test_weight_boundaries():
    assert weight_category(30) == "G20T"
    assert weight_category(10) == "FTL"
    assert weight_category(9.96) == "FTL"
    assert weight_category(5) == "10T"
    assert weight_category(2.5) == "5T"
    assert weight_category(0.5) == "L5T"
    assert weight_category(0.44) == "OTH"
    assert weight_category(1, "FTL") == "FTL"


def test_volume_boundaries():
    assert volume_category(120) == "FTL"
    assert volume_category(0) == "OTH"
    assert volume_category(120.1) == "OTH"
    assert volume_category(-1) == "OTH"


def test_dq_priority_and_same_city_kept():
    valid = dict(
        price=84500,
        currency_code="643",
        distance=800,
        total_weight=20,
        total_volume=40,
        city_known=True,
        fx_rate_found=True,
    )
    assert dq_reason(**valid) is None
    assert dq_reason(**{**valid, "price": 0}) == "PRICE_NON_POSITIVE"
    assert dq_reason(**{**valid, "price": 1000}) == "RUB_PRICE_LE_1000"
    assert dq_reason(**{**valid, "price": 50, "currency_code": "840"}) is None
    assert dq_reason(**{**valid, "currency_code": "840", "fx_rate_found": False}) == "FX_RATE_MISSING"
    assert dq_reason(**{**valid, "distance": 0}) == "DISTANCE_NON_POSITIVE"
    assert dq_reason(**{**valid, "total_weight": -0.1}) == "WEIGHT_NEGATIVE"
    assert dq_reason(**{**valid, "city_known": False}) == "CITY_NOT_IN_DIM"


def test_reliability_uses_month_end_not_the_next_available_month():
    september = date(2026, 9, 1)
    august = date(2026, 8, 1)
    assert reliability(30, 5, AS_OF, september) == "HIGH"
    assert reliability(30, 4, AS_OF, september) == "MEDIUM"
    assert reliability(40, 8, AS_OF, august) == "MEDIUM"
    assert reliability(9, 8, AS_OF, september) == "LOW_DATA"


def test_relative_change_does_not_borrow_another_month():
    assert relative_change(110, 100) == 10
    assert relative_change(100, None) is None
    assert relative_change(100, 0) is None


def test_time_split_boundary_belongs_to_test():
    assert split_name(date(2026, 7, 25)) == "ignore"
    assert split_name(date(2026, 7, 26)) == "train"
    assert split_name(date(2026, 10, 4)) == "train"
    assert split_name(date(2026, 10, 5)) == "test"
    assert split_name(date(2026, 10, 26)) == "test"
    assert split_name(date(2026, 10, 27)) == "future"


def test_december_sits_next_to_january():
    december = month_cycle(12)
    january = month_cycle(1)
    june = month_cycle(6)
    near = (december[0] - january[0]) ** 2 + (december[1] - january[1]) ** 2
    far = (december[0] - june[0]) ** 2 + (december[1] - june[1]) ** 2
    assert near < far


def test_region_comes_from_the_part_after_the_comma():
    assert place_parts("Казань, Республика Татарстан") == ("Казань", "Республика Татарстан")
    assert place_parts("Москва") == ("Москва", "NA")


def test_model_features_do_not_include_the_target():
    assert set(FEATURE_COLUMNS).isdisjoint(BANNED_FEATURES)


def test_sql_splitter_keeps_function_body():
    script = """
CREATE FUNCTION core.demo()
RETURNS text
LANGUAGE sql
AS $$
  SELECT 'a;b';
$$;

SELECT 1;
"""
    parts = split_sql(script)
    assert len(parts) == 2
    assert "a;b" in parts[0]
    assert parts[1] == "SELECT 1;"
