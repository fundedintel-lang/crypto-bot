from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from .errors import MarketDataError
from .models import Candle
from .utils import RESOLUTION_SECONDS, UTC, to_decimal


@dataclass(frozen=True)
class EMAResult:
    value: Decimal
    candles_used: int
    last_closed_candle_time: datetime


def exponential_moving_average(values: list[Decimal], period: int) -> Decimal:
    """EMA seeded with the SMA of the first `period` values."""
    if period < 2:
        raise ValueError("EMA period must be at least 2")
    if len(values) < period:
        raise MarketDataError(
            f"need at least {period} values for EMA{period}, received {len(values)}"
        )
    alpha = Decimal(2) / Decimal(period + 1)
    ema = sum(values[:period], Decimal("0")) / Decimal(period)
    for value in values[period:]:
        ema = value * alpha + ema * (Decimal("1") - alpha)
    return ema


def calculate_closed_candle_ema(
    candles: list[Candle],
    *,
    period: int,
    resolution: str,
    now: datetime,
    max_age_seconds: int = 10_800,
) -> EMAResult:
    if resolution not in RESOLUTION_SECONDS:
        raise ValueError(f"unsupported resolution: {resolution}")
    interval = RESOLUTION_SECONDS[resolution]
    now_utc = now.astimezone(UTC)
    epoch_now = now_utc.timestamp()

    unique: dict[int, Candle] = {}
    for candle in candles:
        if candle.close <= 0:
            continue
        # `time` is candle start time. Only fully closed candles may be used.
        if candle.time + interval > epoch_now:
            continue
        unique[candle.time] = candle
    ordered = [unique[key] for key in sorted(unique)]
    if len(ordered) < period:
        raise MarketDataError(
            f"need at least {period} closed {resolution} candles, received {len(ordered)}"
        )
    last = ordered[-1]
    closed_at = last.time + interval
    age = epoch_now - closed_at
    if age > max_age_seconds:
        raise MarketDataError(
            f"latest closed candle is too old ({int(age)}s > {max_age_seconds}s)"
        )

    closes = [candle.close if isinstance(candle.close, Decimal) else to_decimal(candle.close) for candle in ordered]
    value = exponential_moving_average(closes, period)
    return EMAResult(
        value=value,
        candles_used=len(ordered),
        last_closed_candle_time=datetime.fromtimestamp(closed_at, UTC),
    )
