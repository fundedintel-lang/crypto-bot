from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

UTC = UTC
IST = timezone(timedelta(hours=5, minutes=30), "IST")
RESOLUTION_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "12h": 43200,
    "1d": 86400,
    "1w": 604800,
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_decimal(value: Any, *, default: Decimal | None = None) -> Decimal:
    """Convert API values to Decimal without going through binary floats."""
    if value is None or value == "":
        if default is not None:
            return default
        raise ValueError("missing decimal value")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, float):
        result = Decimal(str(value))
    else:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            if default is not None:
                return default
            raise ValueError(f"invalid decimal value: {value!r}") from exc
    if not result.is_finite():
        if default is not None:
            return default
        raise ValueError(f"non-finite decimal value: {value!r}")
    return result


def decimal_str(value: Decimal | str | int | float | None) -> str:
    if value is None:
        return ""
    dec = value if isinstance(value, Decimal) else to_decimal(value)
    text = format(dec, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _datetime_from_numeric(number: Decimal) -> datetime:
    """Infer seconds / milliseconds / microseconds for API numeric timestamps."""
    magnitude = abs(number)
    if magnitude > Decimal("100000000000000"):  # microseconds since epoch
        divisor = Decimal(1_000_000)
    elif magnitude > Decimal("100000000000"):  # milliseconds since epoch
        divisor = Decimal(1_000)
    else:
        divisor = Decimal(1)
    return datetime.fromtimestamp(float(number / divisor), UTC)


def parse_iso_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float, Decimal)):
        return _datetime_from_numeric(Decimal(str(value)))
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{10,19}", text):
        return _datetime_from_numeric(Decimal(text))
    # Delta returns up to nanosecond precision; truncate to Python's microseconds.
    text = re.sub(r"\.(\d{6})\d+", r".\1", text)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_timestamp(value: Any, *, unit: str = "us") -> datetime | None:
    """Parse Delta ticker timestamps (microseconds by default)."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip()
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        try:
            number = Decimal(text)
        except (InvalidOperation, ValueError):
            return None
        divisor = Decimal({"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[unit])
        return datetime.fromtimestamp(float(number / divisor), UTC)
    return parse_iso_datetime(text)


def parse_hhmm(value: str) -> time:
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", value)
    if not match:
        raise ValueError(f"time must be HH:MM, got {value!r}")
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"time out of range: {value!r}")
    return time(hour=hour, minute=minute)


def as_bool(value: Any, *, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


def quantize_to_tick(
    value: Decimal,
    tick_size: Decimal,
    *,
    direction: str = "nearest",
) -> Decimal:
    if tick_size <= 0:
        return value
    rounding = {
        "nearest": ROUND_HALF_EVEN,
        "up": ROUND_CEILING,
        "down": ROUND_FLOOR,
    }[direction]
    exponent = tick_size.normalize().as_tuple().exponent
    quantum = Decimal(1).scaleb(exponent)
    return value.quantize(quantum, rounding=rounding)


def clamp_to_band(
    value: Decimal,
    *,
    lower: Decimal | None,
    upper: Decimal | None,
    tick_size: Decimal,
    direction: str,
) -> Decimal:
    result = value
    if lower is not None and result < lower:
        result = lower + tick_size
    if upper is not None and result > upper:
        result = upper - tick_size
    if lower is not None and result < lower:
        result = quantize_to_tick(lower, tick_size, direction="up")
    if upper is not None and result > upper:
        result = quantize_to_tick(upper, tick_size, direction="down")
    return quantize_to_tick(result, tick_size, direction=direction)


def json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return decimal_str(value)
    if isinstance(value, datetime):
        return iso_utc(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def json_dumps(value: Any, *, sort_keys: bool = False) -> str:
    return json.dumps(value, default=json_default, separators=(",", ":"), sort_keys=sort_keys)


def json_loads(value: str | bytes | None, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    return json.loads(value)


def chunks(values: Iterable[Any], size: int) -> Iterable[list[Any]]:
    batch: list[Any] = []
    for value in values:
        batch.append(value)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil(pct * len(ordered)) - 1))
    return ordered[idx]


def redact(value: str | None, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return f"{value[:keep]}…{value[-2:]}"
