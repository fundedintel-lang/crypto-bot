#!/usr/bin/env python3
"""Reproducible, model-based backtest for the exact BTC options strategy.

Price path: real Delta India .DEXBTUSD 1-hour candles bundled in this folder.
Option premium: Black-Scholes mark-price model because Delta's public REST API
does not expose historical bid/ask order books for expired option contracts.

This is an estimate, NOT a record of actual Delta fills.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = Path(__file__).resolve().parent
CAPITAL_START = 1000.0
EMA_PERIOD = 21
TARGET_POINTS = 1500.0
ALLOCATION_PCT = 0.02
CONTRACT_VALUE = 0.001  # BTC per Delta BTC option contract
FEE_RATE = 0.0451       # 3.5% premium commission + 0.01% taker + 1% safety buffer
IV = 0.32               # observed Delta BTC option mark_iv, annualized
INCLUDE_ADD = True      # exact strategy: add at 01:00 IST only when losing
STRIKE_STEP = 200.0
ENTRY_HOUR_UTC = 13     # 18:30 IST
ADD_OFFSET_HOURS = 6.5  # 01:00 IST next day
EXIT_OFFSET_HOURS = 22.8333333333  # 17:20 IST next day (11:50 UTC)
EXPIRY_OFFSET_HOURS = 23.0         # 17:30 IST next day (12:00 UTC)


@dataclass
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float


@dataclass
class Trade:
    cycle: str
    direction: str
    entry_ist: str
    spot_entry: float
    ema21: float
    strike: int
    target: float
    entry_premium: float
    initial_quantity: int
    initial_cost: float
    add_premium: float | None
    add_quantity: int
    add_cost: float
    exit_reason: str
    exit_ist: str
    spot_exit: float
    exit_premium: float
    proceeds: float
    pnl: float
    pnl_pct_capital: float
    equity_before: float
    equity_after: float


def load_candles() -> list[Candle]:
    result: dict[int, Candle] = {}
    for name in ("btc_candles_prior_raw.txt", "btc_candles_raw.txt"):
        payload = json.loads((ROOT / name).read_text(encoding="utf-8"))
        for raw in payload["result"]:
            candle = Candle(
                time=int(raw["time"]),
                open=float(raw["open"]),
                high=float(raw["high"]),
                low=float(raw["low"]),
                close=float(raw["close"]),
            )
            result[candle.time] = candle
    return [result[key] for key in sorted(result)]


def ema(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"need {period} values, got {len(values)}")
    alpha = 2 / (period + 1)
    value = sum(values[:period]) / period
    for item in values[period:]:
        value = item * alpha + value * (1 - alpha)
    return value


def normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(spot: float, strike: float, years: float, direction: str, sigma: float | None = None) -> float:
    sigma = IV if sigma is None else sigma
    years = max(years, 1e-8)
    if spot <= 0 or strike <= 0:
        return 0.0
    sqrt_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma * years) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    if direction == "CE":
        theoretical = spot * normal_cdf(d1) - strike * normal_cdf(d2)
        intrinsic = max(0.0, spot - strike)
    else:
        theoretical = strike * normal_cdf(-d2) - spot * normal_cdf(-d1)
        intrinsic = max(0.0, strike - spot)
    return max(theoretical, intrinsic)


def years_between(later: datetime, earlier: datetime) -> float:
    return max((later - earlier).total_seconds(), 1.0) / (365.0 * 24.0 * 3600.0)


def fmt_dt(value: datetime) -> str:
    return value.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")


def round_strike(spot: float) -> int:
    return int(round(spot / STRIKE_STEP) * STRIKE_STEP)


def quantity_for(budget: float, premium: float, max_quantity: int | None = None) -> int:
    if premium <= 0:
        return 0
    unit_cost = premium * CONTRACT_VALUE * (1 + FEE_RATE)
    qty = int(budget // unit_cost)
    if max_quantity is not None:
        qty = min(qty, max_quantity)
    return max(qty, 0)


def run() -> dict[str, Any]:
    candles = load_candles()
    by_time = {c.time: c for c in candles}
    equity = CAPITAL_START
    trades: list[Trade] = []
    start_date = datetime(2026, 10, 19, tzinfo=UTC).date()
    end_date = datetime(2026, 10, 25, tzinfo=UTC).date()
    current = start_date
    while current <= end_date:
        entry_dt = datetime(current.year, current.month, current.day, ENTRY_HOUR_UTC, tzinfo=UTC)
        add_dt = entry_dt + timedelta(hours=ADD_OFFSET_HOURS)
        exit_dt = entry_dt + timedelta(hours=EXIT_OFFSET_HOURS)
        expiry_dt = entry_dt + timedelta(hours=EXPIRY_OFFSET_HOURS)

        prior_closes = [c.close for c in candles if c.time < int(entry_dt.timestamp())]
        if len(prior_closes) < EMA_PERIOD:
            raise ValueError(f"not enough history for {current}")
        ema21 = ema(prior_closes, EMA_PERIOD)
        entry_candle = by_time.get(int(entry_dt.timestamp()) - 3600)
        if entry_candle is None:
            raise ValueError(f"missing entry candle for {current}")
        spot_entry = entry_candle.close
        direction = "CE" if spot_entry > ema21 else "PE" if spot_entry < ema21 else "NONE"
        target = spot_entry + TARGET_POINTS if direction == "CE" else spot_entry - TARGET_POINTS
        strike = round_strike(spot_entry)
        t_entry = years_between(expiry_dt, entry_dt)
        entry_premium = bs_price(spot_entry, strike, t_entry, direction)

        capital = equity
        budget = capital * ALLOCATION_PCT
        qty = quantity_for(budget, entry_premium)
        initial_cost = qty * entry_premium * CONTRACT_VALUE * (1 + FEE_RATE)

        # Scan hourly path for target and 1 AM mark.
        path = [c for c in candles if int(entry_dt.timestamp()) <= c.time < int(exit_dt.timestamp())]
        target_candle: Candle | None = None
        for candle in path:
            hit = (
                (direction == "CE" and candle.high >= target)
                or (direction == "PE" and candle.low <= target)
            )
            if hit:
                target_candle = candle
                break

        add_premium: float | None = None
        add_qty = 0
        add_cost = 0.0
        add_allowed = INCLUDE_ADD and (
            target_candle is None
            or datetime.fromtimestamp(target_candle.time, UTC) >= add_dt
        )
        if add_allowed:
            add_bucket = int(add_dt.timestamp()) // 3600 * 3600
            add_candle = by_time.get(add_bucket)
            if add_candle:
                t_add = years_between(expiry_dt, add_dt)
                add_premium = bs_price(add_candle.close, strike, t_add, direction)
                if add_premium < entry_premium:
                    add_qty = quantity_for(budget, add_premium, max_quantity=qty)
                    add_cost = add_qty * add_premium * CONTRACT_VALUE * (1 + FEE_RATE)

        total_qty = qty + add_qty
        if target_candle is not None:
            exit_reason = "target_hit"
            exit_spot = target
            exit_dt_actual = datetime.fromtimestamp(target_candle.time + 1800, UTC)
            t_exit = years_between(expiry_dt, exit_dt_actual)
        else:
            exit_reason = "time_exit"
            exit_bucket = int(exit_dt.timestamp()) // 3600 * 3600
            exit_candle = by_time.get(exit_bucket, path[-1] if path else entry_candle)
            exit_spot = exit_candle.close
            exit_dt_actual = exit_dt
            t_exit = years_between(expiry_dt, exit_dt_actual)
        exit_premium = bs_price(exit_spot, strike, t_exit, direction)
        proceeds = total_qty * exit_premium * CONTRACT_VALUE * (1 - FEE_RATE)
        pnl = proceeds - initial_cost - add_cost
        equity += pnl

        trades.append(
            Trade(
                cycle=current.isoformat(),
                direction=direction,
                entry_ist=fmt_dt(entry_dt),
                spot_entry=round(spot_entry, 2),
                ema21=round(ema21, 2),
                strike=strike,
                target=round(target, 2),
                entry_premium=round(entry_premium, 4),
                initial_quantity=qty,
                initial_cost=round(initial_cost, 4),
                add_premium=(round(add_premium, 4) if add_premium is not None else None),
                add_quantity=add_qty,
                add_cost=round(add_cost, 4),
                exit_reason=exit_reason,
                exit_ist=fmt_dt(exit_dt_actual),
                spot_exit=round(exit_spot, 2),
                exit_premium=round(exit_premium, 4),
                proceeds=round(proceeds, 4),
                pnl=round(pnl, 4),
                pnl_pct_capital=round(pnl / capital * 100, 4),
                equity_before=round(capital, 4),
                equity_after=round(equity, 4),
            )
        )
        current += timedelta(days=1)

    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    net = sum(pnls)
    equity_curve = [CAPITAL_START] + [t.equity_after for t in trades]
    peak = equity_curve[0]
    max_dd = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        max_dd = max(max_dd, (peak - value) / peak * 100)
    profit_factor = gross_profit / gross_loss if gross_loss else float("inf")
    expectancy = net / len(trades) if trades else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    target_hits = sum(1 for t in trades if t.exit_reason == "target_hit")
    time_exits = sum(1 for t in trades if t.exit_reason == "time_exit")
    adds = sum(1 for t in trades if t.add_quantity > 0)
    ce = sum(1 for t in trades if t.direction == "CE")
    pe = sum(1 for t in trades if t.direction == "PE")
    orders = sum(1 + (1 if t.add_quantity > 0 else 0) + 1 for t in trades)

    summary = {
        "method": "Actual Delta BTC index candles + Black-Scholes option mark model",
        "data_source": "Delta India /v2/history/candles .DEXBTUSD 1h",
        "data_window_ist": "2026-10-17 17:30 IST to 2026-10-26 15:30 IST",
        "cycle_window_ist": "2026-10-19 18:30 IST to 2026-10-25 18:30 IST",
        "capital_start_usd": CAPITAL_START,
        "capital_end_usd": round(equity, 4),
        "net_pnl_usd": round(net, 4),
        "total_return_pct": round((equity / CAPITAL_START - 1) * 100, 4),
        "total_cycles": len(trades),
        "total_orders": orders,
        "initial_entries": len(trades),
        "additional_entries": adds,
        "exit_orders": len(trades),
        "ce_cycles": ce,
        "pe_cycles": pe,
        "target_hits": target_hits,
        "time_exits": time_exits,
        "winning_cycles": len(wins),
        "losing_cycles": len(losses),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0,
        "gross_profit_usd": round(gross_profit, 4),
        "gross_loss_usd": round(gross_loss, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else None,
        "expectancy_usd_per_cycle": round(expectancy, 4),
        "average_win_usd": round(avg_win, 4),
        "average_loss_usd": round(avg_loss, 4),
        "best_cycle_usd": round(max(pnls), 4) if pnls else 0,
        "worst_cycle_usd": round(min(pnls), 4) if pnls else 0,
        "max_drawdown_pct": round(max_dd, 4),
        "iv_assumption": IV,
        "fee_rate_assumption": FEE_RATE,
        "include_additional_entry": INCLUDE_ADD,
        "contract_value_btc": CONTRACT_VALUE,
        "strike_step_usd": STRIKE_STEP,
        "target_points": TARGET_POINTS,
        "ema_period": EMA_PERIOD,
        "stop_loss": False,
        "limitations": [
            "Historical Delta bid/ask order books for expired options are not public; premiums are modeled, not actual fills.",
            "Hourly high/low is used to detect the ±1500 target; a 1-second live bot could exit at a slightly different price.",
            "Constant 32% IV and nearest-200 ATM strike are approximations of Delta's live chain.",
            "Fees use a conservative 4.51% all-in rate.",
            "Seven cycles is a small sample and is not statistically reliable.",
        ],
    }
    return {"summary": summary, "trades": [asdict(t) for t in trades]}


def main() -> int:
    global CAPITAL_START, TARGET_POINTS, IV, FEE_RATE, INCLUDE_ADD
    parser = argparse.ArgumentParser(description="BTC options strategy model backtest")
    parser.add_argument("--capital", type=float, default=CAPITAL_START)
    parser.add_argument("--target", type=float, default=TARGET_POINTS)
    parser.add_argument("--iv", type=float, default=IV)
    parser.add_argument("--fee", type=float, default=FEE_RATE)
    parser.add_argument("--no-add", action="store_true", help="disable 01:00 loss-based add (analysis only)")
    args = parser.parse_args()
    CAPITAL_START = args.capital
    TARGET_POINTS = args.target
    IV = args.iv
    FEE_RATE = args.fee
    INCLUDE_ADD = not args.no_add

    report = run()
    (ROOT / "backtest_summary.json").write_text(
        json.dumps(report["summary"], indent=2), encoding="utf-8"
    )
    (ROOT / "backtest_trades.json").write_text(
        json.dumps(report["trades"], indent=2), encoding="utf-8"
    )
    with (ROOT / "backtest_trades.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(report["trades"][0].keys()),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(report["trades"])
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
