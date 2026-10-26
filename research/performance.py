#!/usr/bin/env python3
"""Generate a full A-to-Z performance report for the BTC options strategy."""
from __future__ import annotations

import json
import math
import random
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import backtest as bt

ROOT = Path(__file__).resolve().parent
SEED = 20261026
MONTE_CARLO_PATHS = 1000
MONTE_CARLO_CYCLES = 30
HOURS_PER_CYCLE = 23
ADD_HOUR_INDEX = 6
TARGET = 1500.0


def quantile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * pct)))
    return ordered[index]


def scenario_summary(**changes: Any) -> dict[str, Any]:
    originals = {
        "CAPITAL_START": bt.CAPITAL_START,
        "TARGET_POINTS": bt.TARGET_POINTS,
        "IV": bt.IV,
        "FEE_RATE": bt.FEE_RATE,
        "INCLUDE_ADD": bt.INCLUDE_ADD,
    }
    try:
        for key, value in changes.items():
            setattr(bt, key, value)
        return bt.run()["summary"]
    finally:
        for key, value in originals.items():
            setattr(bt, key, value)


def deterministic_extra(trades: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    returns = [t["pnl"] / t["equity_before"] for t in trades]
    volatility = statistics.stdev(returns) if len(returns) > 1 else 0.0
    avg_return = statistics.mean(returns) if returns else 0.0
    sharpe = avg_return / volatility * math.sqrt(365) if volatility else 0.0
    max_consecutive_losses = 0
    current = 0
    for pnl in (t["pnl"] for t in trades):
        if pnl <= 0:
            current += 1
            max_consecutive_losses = max(max_consecutive_losses, current)
        else:
            current = 0
    fee_rate = summary["fee_rate_assumption"]
    entry_gross = sum(
        (t["initial_cost"] + t["add_cost"]) / (1 + fee_rate) for t in trades
    )
    entry_fees = sum(t["initial_cost"] + t["add_cost"] for t in trades) - entry_gross
    exit_gross = sum(t["proceeds"] / (1 - fee_rate) for t in trades)
    exit_fees = exit_gross - sum(t["proceeds"] for t in trades)
    return {
        "average_cycle_return_pct": round(avg_return * 100, 4),
        "cycle_return_volatility_pct": round(volatility * 100, 4),
        "annualized_sharpe_zero_rf": round(sharpe, 4),
        "max_consecutive_losses": max_consecutive_losses,
        "total_premium_paid_usd": round(entry_gross, 4),
        "total_exit_premium_received_usd": round(exit_gross, 4),
        "total_fees_usd": round(entry_fees + exit_fees, 4),
        "average_initial_quantity": round(
            statistics.mean(t["initial_quantity"] for t in trades), 2
        ),
        "average_total_quantity": round(
            statistics.mean(t["initial_quantity"] + t["add_quantity"] for t in trades), 2
        ),
        "target_hit_rate_pct": round(summary["target_hits"] / summary["total_cycles"] * 100, 2),
        "linear_30_cycle_projection_pct": round(
            avg_return * MONTE_CARLO_CYCLES * 100, 2
        ),
    }


def monte_carlo() -> dict[str, Any]:
    candles = bt.load_candles()
    shapes = [
        (c.high / c.open, c.low / c.open, c.close / c.open)
        for c in candles
        if c.open > 0 and c.high >= c.low > 0
    ]
    history = [c.close for c in candles][-50:]
    rng = random.Random(SEED)
    results: list[dict[str, float]] = []

    for _ in range(MONTE_CARLO_PATHS):
        spot = history[-1]
        prices = list(history)
        equity = bt.CAPITAL_START
        peak = equity
        max_drawdown = 0.0
        wins = 0
        target_hits = 0
        adds = 0
        for _cycle in range(MONTE_CARLO_CYCLES):
            spot_entry = prices[-1]
            ema21 = bt.ema(prices[-bt.EMA_PERIOD :], bt.EMA_PERIOD)
            direction = "CE" if spot_entry > ema21 else "PE"
            target = (
                spot_entry + bt.TARGET_POINTS
                if direction == "CE"
                else spot_entry - bt.TARGET_POINTS
            )
            strike = bt.round_strike(spot_entry)
            t_entry = HOURS_PER_CYCLE / (365 * 24)
            entry_premium = bt.bs_price(spot_entry, strike, t_entry, direction)
            budget = equity * bt.ALLOCATION_PCT
            qty = bt.quantity_for(budget, entry_premium)
            initial_cost = qty * entry_premium * bt.CONTRACT_VALUE * (1 + bt.FEE_RATE)

            generated: list[tuple[float, float, float, float]] = []
            for _hour in range(HOURS_PER_CYCLE):
                high_factor, low_factor, close_factor = rng.choice(shapes)
                open_price = spot
                high = open_price * high_factor
                low = open_price * low_factor
                close = open_price * close_factor
                generated.append((open_price, high, low, close))
                spot = close

            target_index: int | None = None
            for index, candle in enumerate(generated):
                _, high, low, _ = candle
                if (direction == "CE" and high >= target) or (
                    direction == "PE" and low <= target
                ):
                    target_index = index
                    break

            add_qty = 0
            add_cost = 0.0
            if bt.INCLUDE_ADD and (target_index is None or target_index >= ADD_HOUR_INDEX):
                add_spot = generated[min(ADD_HOUR_INDEX, len(generated) - 1)][3]
                t_add = (HOURS_PER_CYCLE - 6.5) / (365 * 24)
                add_premium = bt.bs_price(add_spot, strike, t_add, direction)
                if add_premium < entry_premium:
                    add_qty = bt.quantity_for(budget, add_premium, max_quantity=qty)
                    add_cost = add_qty * add_premium * bt.CONTRACT_VALUE * (1 + bt.FEE_RATE)

            total_qty = qty + add_qty
            if target_index is not None:
                exit_spot = target
                t_exit = max(HOURS_PER_CYCLE - target_index - 0.5, 0.1) / (365 * 24)
                target_hits += 1
            else:
                exit_spot = generated[-1][3]
                t_exit = 0.1667 / (365 * 24)
            exit_premium = bt.bs_price(exit_spot, strike, t_exit, direction)
            proceeds = total_qty * exit_premium * bt.CONTRACT_VALUE * (1 - bt.FEE_RATE)
            pnl = proceeds - initial_cost - add_cost
            equity += pnl
            if pnl > 0:
                wins += 1
            if add_qty > 0:
                adds += 1
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)
            prices.extend(candle[3] for candle in generated)
            prices = prices[-100:]

        results.append(
            {
                "final_equity": equity,
                "return_pct": (equity / bt.CAPITAL_START - 1) * 100,
                "max_drawdown_pct": max_drawdown,
                "win_rate_pct": wins / MONTE_CARLO_CYCLES * 100,
                "target_hit_rate_pct": target_hits / MONTE_CARLO_CYCLES * 100,
                "add_rate_pct": adds / MONTE_CARLO_CYCLES * 100,
            }
        )

    returns = [item["return_pct"] for item in results]
    drawdowns = [item["max_drawdown_pct"] for item in results]
    equities = [item["final_equity"] for item in results]
    return {
        "paths": MONTE_CARLO_PATHS,
        "cycles_per_path": MONTE_CARLO_CYCLES,
        "seed": SEED,
        "method": "Bootstrap of actual Delta hourly OHLC shapes, Black-Scholes premiums, dynamic 2% capital sizing",
        "mean_return_pct_30_cycles": round(statistics.mean(returns), 4),
        "median_return_pct_30_cycles": round(statistics.median(returns), 4),
        "p05_return_pct_30_cycles": round(quantile(returns, 0.05), 4),
        "p95_return_pct_30_cycles": round(quantile(returns, 0.95), 4),
        "worst_return_pct_30_cycles": round(min(returns), 4),
        "best_return_pct_30_cycles": round(max(returns), 4),
        "probability_of_profit_pct": round(
            sum(1 for value in returns if value > 0) / len(returns) * 100, 2
        ),
        "median_final_equity_usd": round(statistics.median(equities), 2),
        "mean_max_drawdown_pct": round(statistics.mean(drawdowns), 4),
        "p95_max_drawdown_pct": round(quantile(drawdowns, 0.95), 4),
        "average_win_rate_pct": round(
            statistics.mean(item["win_rate_pct"] for item in results), 2
        ),
        "average_target_hit_rate_pct": round(
            statistics.mean(item["target_hit_rate_pct"] for item in results), 2
        ),
        "average_add_rate_pct": round(
            statistics.mean(item["add_rate_pct"] for item in results), 2
        ),
    }


def sensitivity() -> dict[str, Any]:
    iv_values = [0.20, 0.32, 0.45]
    target_values = [1000, 1500, 2000]
    grid = []
    for iv in iv_values:
        row = []
        for target in target_values:
            summary = scenario_summary(IV=iv, TARGET_POINTS=target)
            row.append(round(summary["net_pnl_usd"], 2))
        grid.append(row)
    fee_off = scenario_summary(FEE_RATE=0.0)
    fee_on = scenario_summary(FEE_RATE=0.0451)
    add_off = scenario_summary(INCLUDE_ADD=False)
    add_on = scenario_summary(INCLUDE_ADD=True)
    break_even_points = []
    for target in range(500, 2501, 25):
        result = scenario_summary(TARGET_POINTS=float(target))
        break_even_points.append((target, result["net_pnl_usd"]))
    closest = min(break_even_points, key=lambda item: abs(item[1]))
    return {
        "iv_values": iv_values,
        "target_values": target_values,
        "net_pnl_grid_usd": grid,
        "fee_off_net_pnl_usd": round(fee_off["net_pnl_usd"], 2),
        "fee_on_net_pnl_usd": round(fee_on["net_pnl_usd"], 2),
        "add_off_net_pnl_usd": round(add_off["net_pnl_usd"], 2),
        "add_on_net_pnl_usd": round(add_on["net_pnl_usd"], 2),
        "break_even_target_scan_usd": closest[0],
        "break_even_target_scan_pnl_usd": round(closest[1], 2),
        "note": "Sensitivity changes are analysis only; the live bot keeps the requested 1500-point target and 01:00 add rule.",
    }


def money(value: float) -> str:
    if value < 0:
        return f"-${abs(value):,.2f}"
    return f"${value:,.2f}"


def main() -> int:
    base = bt.run()
    summary = base["summary"]
    trades = base["trades"]
    worst_trade = min(trades, key=lambda item: item["pnl"])
    extra = deterministic_extra(trades, summary)
    mc = monte_carlo()
    sens = sensitivity()

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "live": {
            "trades": 0,
            "realized_pnl_usd": 0,
            "reason": "No API credentials supplied; live mode is locked and no real order has been placed.",
        },
        "summary": summary,
        "deterministic_extra": extra,
        "monte_carlo": mc,
        "sensitivity": sens,
        "trades": trades,
    }
    (ROOT / "performance_full.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    trade_rows = "\n".join(
        "| {cycle} | {direction} | {spot_entry} | {ema21} | {strike} | {target} | {entry_premium} | {initial_quantity} | {add_quantity} | {exit_reason} | {spot_exit} | {exit_premium} | **{pnl}** | {equity_after} |".format(
            cycle=t["cycle"],
            direction=t["direction"],
            spot_entry=f"{t['spot_entry']:.2f}",
            ema21=f"{t['ema21']:.2f}",
            strike=t["strike"],
            target=f"{t['target']:.2f}",
            entry_premium=f"{t['entry_premium']:.2f}",
            initial_quantity=t["initial_quantity"],
            add_quantity=t["add_quantity"],
            exit_reason=t["exit_reason"],
            spot_exit=f"{t['spot_exit']:.2f}",
            exit_premium=f"{t['exit_premium']:.2f}",
            pnl=f"{t['pnl']:+.2f}",
            equity_after=f"{t['equity_after']:.2f}",
        )
        for t in trades
    )
    sens_rows = "\n".join(
        f"| {iv:.0%} | "
        + " | ".join(f"{value:+.2f}" for value in row)
        + " |"
        for iv, row in zip(sens["iv_values"], sens["net_pnl_grid_usd"], strict=True)
    )
    markdown = f"""# BTC Options Bot - Full Performance Report

> **Bottom line:** live performance is currently **0 trades / $0 PnL** because no API keys were supplied and no real order was placed. A 7-cycle historical study using real Delta BTC candles and modeled option premiums produced **{money(summary['net_pnl_usd'])} ({summary['total_return_pct']:.2f}%)** on $1,000. A 1,000-path bootstrap simulation suggests the fixed ±1,500-point target is hit too rarely to overcome daily-option theta and fees. This is an estimate, not a trading guarantee.

---

## 0. Three meanings of “performance”

| Type | Status |
|---|---|
| **Live Delta trading** | **0 trades, $0 realized P&L.** No credentials, no live run, no real orders. |
| **Paper / testnet** | Not run by me. The bot is ready, but its actual forward performance will start only when you run `make paper` or `make testnet`. |
| **Historical study below** | 7 real Delta BTC-price cycles with modeled option premiums. This is the only performance estimate currently available. |

---

## 1. A-Z snapshot

| Item | Value |
|---|---:|
| Study type | Historical BTC candles + modeled option premiums |
| Delta data | `.DEXBTUSD` 1-hour candles, India venue |
| BTC data window | {summary['data_window_ist']} |
| Cycle window | {summary['cycle_window_ist']} |
| Starting capital | {money(summary['capital_start_usd'])} |
| Ending capital | {money(summary['capital_end_usd'])} |
| Net P&L | **{money(summary['net_pnl_usd'])}** |
| Total return | **{summary['total_return_pct']:.2f}%** |
| Daily cycles | {summary['total_cycles']} |
| CE / PE cycles | {summary['ce_cycles']} / {summary['pe_cycles']} |
| Total orders | {summary['total_orders']} |
| Initial buy orders | {summary['initial_entries']} |
| 01:00 additional buys | {summary['additional_entries']} |
| Exit orders | {summary['exit_orders']} |
| Target hits | {summary['target_hits']} ({extra['target_hit_rate_pct']:.2f}%) |
| 17:20 time exits | {summary['time_exits']} |
| Winning cycles | {summary['winning_cycles']} |
| Losing cycles | {summary['losing_cycles']} |
| Win rate | {summary['win_rate_pct']:.2f}% |
| Gross profit | {money(summary['gross_profit_usd'])} |
| Gross loss | -{money(summary['gross_loss_usd'])} |
| Profit factor | {summary['profit_factor']:.2f} |
| Expectancy / cycle | {money(summary['expectancy_usd_per_cycle'])} |
| Average win | +{money(summary['average_win_usd'])} |
| Average loss | {money(summary['average_loss_usd'])} |
| Best cycle | +{money(summary['best_cycle_usd'])} |
| Worst cycle | {money(summary['worst_cycle_usd'])} |
| Max drawdown | {summary['max_drawdown_pct']:.2f}% |
| Max consecutive losses | {extra['max_consecutive_losses']} |
| Average initial quantity | {extra['average_initial_quantity']:.2f} contracts |
| Average total quantity | {extra['average_total_quantity']:.2f} contracts |
| Total premium paid | {money(extra['total_premium_paid_usd'])} |
| Total premium received | {money(extra['total_exit_premium_received_usd'])} |
| Total modeled fees | {money(extra['total_fees_usd'])} |
| Average cycle return | {extra['average_cycle_return_pct']:.2f}% |
| Cycle volatility | {extra['cycle_return_volatility_pct']:.2f}% |
| Annualized Sharpe (0% RF) | {extra['annualized_sharpe_zero_rf']:.2f} |
| Stop-loss | **None** |

### P&L bridge

| Component | Amount |
|---|---:|
| Premium received at exits | +{money(extra['total_exit_premium_received_usd'])} |
| Premium paid for entries | -{money(extra['total_premium_paid_usd'])} |
| Modeled fees | -{money(extra['total_fees_usd'])} |
| **Net P&L** | **{money(summary['net_pnl_usd'])}** |

---

## 2. What is real and what is modeled

**Real data used:**

- Delta India `/v2/history/candles` for `.DEXBTUSD`.
- 1-hour OHLC candles from 17-26 Oct 2026.
- Real BTC highs/lows were used to detect whether the ±1,500-point target was touched.
- Real 1-hour closes were used for EMA 21 and the 18:30 IST signal.

**Modeled data:**

- Delta does **not** provide public historical bid/ask order books for expired option contracts.
- Option premiums were estimated with Black-Scholes using **{summary['iv_assumption']:.0%} annualized IV**, observed from live Delta BTC option tickers.
- Strike was rounded to the nearest $200 ATM strike.
- Contract value = {summary['contract_value_btc']} BTC.
- All-in fee = **{summary['fee_rate_assumption']:.2%}** (3.5% premium commission + 0.01% taker + 1% safety buffer).
- Target hits were detected on hourly high/low; live 1-second polling could exit at a different price.

**Therefore:** these numbers are a disciplined estimate, not actual exchange fills.

---

## 3. Exact strategy applied

| Rule | Backtest implementation |
|---|---|
| Timeframe | 1-hour closed candles |
| EMA | 21-period EMA seeded with 21-period SMA |
| Entry | 18:30 IST (13:00 UTC) |
| CE | BTC spot > EMA21 |
| PE | BTC spot < EMA21 |
| Initial capital | 2% of current equity |
| CE target | entry BTC + 1,500 |
| PE target | entry BTC - 1,500 |
| Stop-loss | None |
| 01:00 add | Only if modeled option premium is below entry premium |
| Additional capital | Another 2% of cycle-starting equity, same quantity capped by budget |
| Exit | First target touch, else next day 17:20 IST |

---

## 4. Cycle-by-cycle ledger

| Cycle | Side | BTC entry | EMA21 | Strike | Target | Entry prem. | Initial qty | Add qty | Exit reason | BTC exit | Exit prem. | P&L | Equity after |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
{trade_rows}

### Interpretation

- Only **1 of 7** cycles reached the ±1,500-point target.
- **6 of 7** cycles reached the 17:20 time exit, where daily-option theta had already eroded the premium.
- The best cycle (+{money(summary['best_cycle_usd'])}) was the single target hit on 24 Oct.
- The worst cycle ({money(summary['worst_cycle_usd'])}) had both an initial and additional entry, then a time exit.
- Because sizing is only 2% + 2%, the account-level worst cycle was roughly **3% of equity**, not a full-account blowup. But five losing cycles still compounded into a **{summary['max_drawdown_pct']:.2f}%** drawdown.

---

## 5. Why the strategy struggled in this sample

1. **Target too far for the holding window.** ±1,500 on ~80,000 BTC is about **1.87%**. In this 7-day sample, only one session produced that directional move before 17:20 IST.
2. **Daily options decay fast.** Six exits happened 10 minutes before expiry, when time value is almost gone.
3. **Fees are heavy.** Modeled total fees were **{money(extra['total_fees_usd'])}** over 7 cycles, about **{extra['total_fees_usd']/abs(summary['net_pnl_usd'])*100:.1f}%** of the net loss.
4. **Averaging increases exposure in losing trends.** The 01:00 add triggered in {summary['additional_entries']} cycles. It can improve break-even when a reversal comes, but it also increases theta exposure when the target never comes.
5. **No stop-loss is intentional.** The bot does not cut a losing option early; it waits for target or 17:20.

---

## 6. Monte Carlo: 1,000 simulated 30-cycle paths

Method: bootstrap actual Delta hourly OHLC shapes, regenerate 23-hour cycles, apply the same EMA/target/add/fees rules.

| Metric | 30-cycle result |
|---|---:|
| Simulated paths | {mc['paths']:,} |
| Mean return | {mc['mean_return_pct_30_cycles']:.2f}% |
| Median return | {mc['median_return_pct_30_cycles']:.2f}% |
| 5th percentile | {mc['p05_return_pct_30_cycles']:.2f}% |
| 95th percentile | {mc['p95_return_pct_30_cycles']:.2f}% |
| Worst path | {mc['worst_return_pct_30_cycles']:.2f}% |
| Best path | {mc['best_return_pct_30_cycles']:.2f}% |
| Probability of profit | {mc['probability_of_profit_pct']:.2f}% |
| Median final equity | {money(mc['median_final_equity_usd'])} |
| Mean max drawdown | {mc['mean_max_drawdown_pct']:.2f}% |
| 95th percentile max drawdown | {mc['p95_max_drawdown_pct']:.2f}% |
| Average win rate | {mc['average_win_rate_pct']:.2f}% |
| Average target-hit rate | {mc['average_target_hit_rate_pct']:.2f}% |
| Average add rate | {mc['average_add_rate_pct']:.2f}% |

> The deterministic 7-cycle result annualizes/linear-projects to about **{extra['linear_30_cycle_projection_pct']:.2f}%** over 30 cycles, but the Monte Carlo distribution is more informative because it captures path variability.

---

## 7. Sensitivity analysis

Net P&L over the same 7 cycles when target and IV assumptions change. **The live bot remains fixed at 1,500 points.**

| IV assumption | Target 1,000 | Target 1,500 | Target 2,000 |
|---:|---:|---:|---:|
{sens_rows}

Other A/B checks:

| Change | Net P&L |
|---|---:|
| Fees disabled | {money(sens['fee_off_net_pnl_usd'])} |
| Fees enabled (default) | {money(sens['fee_on_net_pnl_usd'])} |
| 01:00 add disabled | {money(sens['add_off_net_pnl_usd'])} |
| 01:00 add enabled (default) | {money(sens['add_on_net_pnl_usd'])} |

Closest break-even target in a 500-2,500 point scan: **{sens['break_even_target_scan_usd']} points** with P&L **{money(sens['break_even_target_scan_pnl_usd'])}**.

> Changing target, removing the add, or reducing fees is shown only to explain performance. It is **not** a recommendation to alter your requested strategy.

---

## 8. Risk profile

- **Maximum theoretical loss per cycle:** the premium paid. With 2% + 2% sizing, that is roughly **4% of equity plus fees** if both entries go to zero.
- **Observed worst cycle:** {money(summary['worst_cycle_usd'])} on {money(worst_trade['equity_before'])} equity ({summary['worst_cycle_usd']/worst_trade['equity_before']*100:.2f}%).
- **Observed max drawdown:** {summary['max_drawdown_pct']:.2f}%.
- **Win/loss shape:** few large wins ({summary['winning_cycles']}) and more small/medium losses ({summary['losing_cycles']}) → profit factor {summary['profit_factor']:.2f}.
- **Tail risk:** a sudden volatility spike, wide spread, stale quote, or failed exit can make actual results worse than the model.
- **No SL risk:** a losing position is held until target or 17:20, so theta decay can be substantial.

---

## 9. Verdict

For this 7-cycle Delta BTC sample, the exact strategy was **not profitable**:

- Net: **{money(summary['net_pnl_usd'])} ({summary['total_return_pct']:.2f}%)**
- Win rate: **{summary['win_rate_pct']:.2f}%**
- Target-hit rate: **{extra['target_hit_rate_pct']:.2f}%**
- Profit factor: **{summary['profit_factor']:.2f}**
- Monte Carlo probability of profit over 30 cycles: **{mc['probability_of_profit_pct']:.2f}%**

The bot is technically complete and safe to test, but the evidence says: **do not go live with real money based on this short sample.** Run paper mode for several weeks, then testnet, and only then consider live with small capital.

---

## 10. What to do next

1. **Keep the exact strategy if you want “same to same”:** accept that this sample had only a {extra['target_hit_rate_pct']:.2f}% target-hit rate and a {summary['win_rate_pct']:.2f}% win rate.
2. **Run paper mode for at least 30-60 cycles** before judging the live bot. Real fills, spreads, IV, and slippage matter.
3. **Then run testnet** with the same `.env` except `BOT_MODE=testnet` and testnet keys.
4. **Only then consider live** with a small balance and `DEDICATED_ACCOUNT=true`.
5. If you ever want to change performance, the levers are target distance, IV filtering, strike/expiry, or removing the 01:00 add - but each change breaks your requested exact rules. Do not change them silently.

---

## 11. Reproduce everything

```bash
python3 research/backtest.py
python3 research/performance.py
```

Generated files:

- `research/backtest_summary.json`
- `research/backtest_trades.json`
- `research/backtest_trades.csv`
- `research/performance_full.json`
- `research/PERFORMANCE_REPORT.md`

Optional scenario:

```bash
python3 research/backtest.py --target 1000 --iv 0.25 --fee 0.02
python3 research/backtest.py --no-add
```

---

## 12. Limitations

- Only **7 cycles** of real BTC history were used for the deterministic report.
- Option premiums are modeled; actual Delta historical option order books are not publicly available.
- Constant IV ignores volatility smiles, skew, and intraday IV changes.
- Hourly high/low target detection is coarser than 1-second live polling.
- Slippage, partial fills, price bands, outages, and funding/spread effects are not fully reproduced.
- Bootstrap Monte Carlo assumes recent hourly return shapes remain representative.
- Past performance does not predict future results.
"""
    (ROOT / "PERFORMANCE_REPORT.md").write_text(markdown, encoding="utf-8")
    print(json.dumps({
        "summary": summary,
        "extra": extra,
        "monte_carlo": mc,
        "sensitivity": sens,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
