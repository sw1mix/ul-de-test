from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

AS_OF = date(2026, 10, 26)
TRAIN_START = date(2026, 7, 26)
TEST_START = date(2026, 10, 5)


def round_tenth(weight: float) -> Decimal:
    return Decimal(str(weight)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def weight_category(weight: float, given: str | None = None) -> str:
    if given is not None and given.strip() != "":
        return given.strip()
    rounded = round_tenth(weight)
    if rounded >= 30:
        return "G20T"
    if rounded >= 10:
        return "FTL"
    if rounded >= 5:
        return "10T"
    if rounded >= Decimal("2.5"):
        return "5T"
    if rounded >= Decimal("0.5"):
        return "L5T"
    return "OTH"


def volume_category(volume: float, given: str | None = None) -> str:
    if given is not None and given.strip() != "":
        return given.strip()
    if volume > 0 and volume <= 120:
        return "FTL"
    return "OTH"


def dq_reason(
    *,
    price: float,
    currency_code: str,
    distance: float,
    total_weight: float,
    total_volume: float,
    city_known: bool,
    fx_rate_found: bool,
) -> str | None:
    if price <= 0:
        return "PRICE_NON_POSITIVE"
    if currency_code == "643" and price <= 1000:
        return "RUB_PRICE_LE_1000"
    if distance <= 0:
        return "DISTANCE_NON_POSITIVE"
    if total_weight < 0:
        return "WEIGHT_NEGATIVE"
    if total_volume < 0:
        return "VOLUME_NEGATIVE"
    if not city_known:
        return "CITY_NOT_IN_DIM"
    if currency_code != "643" and not fx_rate_found:
        return "FX_RATE_MISSING"
    if currency_code not in {"643", "840", "978"}:
        return "CURRENCY_UNKNOWN"
    return None


def month_end(day: date) -> date:
    if day.month == 12:
        first_next = date(day.year + 1, 1, 1)
    else:
        first_next = date(day.year, day.month + 1, 1)
    return first_next - timedelta(days=1)


def reliability(trips: int, carriers: int, as_of: date, price_month: date) -> str:
    reference = min(as_of, month_end(price_month))
    fresh = (as_of - reference).days <= 30
    if trips >= 30 and carriers >= 5 and fresh:
        return "HIGH"
    if trips >= 10:
        return "MEDIUM"
    return "LOW_DATA"


def relative_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / previous * 100


def split_name(loading_day: date, as_of: date = AS_OF) -> str:
    if loading_day > as_of:
        return "future"
    if loading_day >= TEST_START:
        return "test"
    if loading_day >= TRAIN_START:
        return "train"
    return "ignore"
