# BTC Options Bot - Full Performance Report

> **Bottom line:** live performance is currently **0 trades / $0 PnL** because no API keys were supplied and no real order was placed. A 7-cycle historical study using real Delta BTC candles and modeled option premiums produced **-$69.48 (-6.95%)** on $1,000. A 1,000-path bootstrap simulation suggests the fixed ±1,500-point target is hit too rarely to overcome daily-option theta and fees. This is an estimate, not a trading guarantee.

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
| BTC data window | 2026-10-17 17:30 IST to 2026-10-26 15:30 IST |
| Cycle window | 2026-10-19 18:30 IST to 2026-10-25 18:30 IST |
| Starting capital | $1,000.00 |
| Ending capital | $930.52 |
| Net P&L | **-$69.48** |
| Total return | **-6.95%** |
| Daily cycles | 7 |
| CE / PE cycles | 2 / 5 |
| Total orders | 17 |
| Initial buy orders | 7 |
| 01:00 additional buys | 3 |
| Exit orders | 7 |
| Target hits | 1 (14.29%) |
| 17:20 time exits | 6 |
| Winning cycles | 2 |
| Losing cycles | 5 |
| Win rate | 28.57% |
| Gross profit | $47.16 |
| Gross loss | -$116.64 |
| Profit factor | 0.40 |
| Expectancy / cycle | -$9.93 |
| Average win | +$23.58 |
| Average loss | -$23.33 |
| Best cycle | +$30.51 |
| Worst cycle | -$29.66 |
| Max drawdown | 8.70% |
| Max consecutive losses | 4 |
| Average initial quantity | 34.29 contracts |
| Average total quantity | 49.43 contracts |
| Total premium paid | $149.00 |
| Total premium received | $90.31 |
| Total modeled fees | $10.79 |
| Average cycle return | -1.00% |
| Cycle volatility | 2.49% |
| Annualized Sharpe (0% RF) | -7.65 |
| Stop-loss | **None** |

### P&L bridge

| Component | Amount |
|---|---:|
| Premium received at exits | +$90.31 |
| Premium paid for entries | -$149.00 |
| Modeled fees | -$10.79 |
| **Net P&L** | **-$69.48** |

---

## 2. What is real and what is modeled

**Real data used:**

- Delta India `/v2/history/candles` for `.DEXBTUSD`.
- 1-hour OHLC candles from 17-26 Oct 2026.
- Real BTC highs/lows were used to detect whether the ±1,500-point target was touched.
- Real 1-hour closes were used for EMA 21 and the 18:30 IST signal.

**Modeled data:**

- Delta does **not** provide public historical bid/ask order books for expired option contracts.
- Option premiums were estimated with Black-Scholes using **32% annualized IV**, observed from live Delta BTC option tickers.
- Strike was rounded to the nearest $200 ATM strike.
- Contract value = 0.001 BTC.
- All-in fee = **4.51%** (3.5% premium commission + 0.01% taker + 1% safety buffer).
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
| 2026-10-19 | CE | 82600.60 | 82288.88 | 82600 | 84100.60 | 540.62 | 35 | 35 | time_exit | 81501.50 | 0.00 | **-26.43** | 973.57 |
| 2026-10-20 | PE | 81617.60 | 81798.67 | 81600 | 80117.60 | 525.08 | 35 | 35 | time_exit | 83500.30 | 0.00 | **-23.51** | 950.06 |
| 2026-10-21 | CE | 83504.50 | 82966.96 | 83600 | 85004.50 | 500.12 | 36 | 0 | time_exit | 81751.10 | 0.00 | **-18.82** | 931.24 |
| 2026-10-22 | PE | 81687.50 | 82799.39 | 81600 | 80187.50 | 491.45 | 36 | 0 | time_exit | 81726.10 | 7.72 | **-18.22** | 913.02 |
| 2026-10-23 | PE | 81739.30 | 81773.39 | 81800 | 80239.30 | 565.78 | 30 | 0 | time_exit | 80599.70 | 1200.30 | **+16.65** | 929.66 |
| 2026-10-24 | PE | 80773.80 | 80976.77 | 80800 | 79273.80 | 541.66 | 32 | 0 | target_hit | 79273.80 | 1591.28 | **+30.51** | 960.17 |
| 2026-10-25 | PE | 80453.10 | 80597.29 | 80400 | 78953.10 | 499.97 | 36 | 36 | time_exit | 80403.30 | 43.14 | **-29.66** | 930.52 |

### Interpretation

- Only **1 of 7** cycles reached the ±1,500-point target.
- **6 of 7** cycles reached the 17:20 time exit, where daily-option theta had already eroded the premium.
- The best cycle (+$30.51) was the single target hit on 24 Oct.
- The worst cycle (-$29.66) had both an initial and additional entry, then a time exit.
- Because sizing is only 2% + 2%, the account-level worst cycle was roughly **3% of equity**, not a full-account blowup. But five losing cycles still compounded into a **8.70%** drawdown.

---

## 5. Why the strategy struggled in this sample

1. **Target too far for the holding window.** ±1,500 on ~80,000 BTC is about **1.87%**. In this 7-day sample, only one session produced that directional move before 17:20 IST.
2. **Daily options decay fast.** Six exits happened 10 minutes before expiry, when time value is almost gone.
3. **Fees are heavy.** Modeled total fees were **$10.79** over 7 cycles, about **15.5%** of the net loss.
4. **Averaging increases exposure in losing trends.** The 01:00 add triggered in 3 cycles. It can improve break-even when a reversal comes, but it also increases theta exposure when the target never comes.
5. **No stop-loss is intentional.** The bot does not cut a losing option early; it waits for target or 17:20.

---

## 6. Monte Carlo: 1,000 simulated 30-cycle paths

Method: bootstrap actual Delta hourly OHLC shapes, regenerate 23-hour cycles, apply the same EMA/target/add/fees rules.

| Metric | 30-cycle result |
|---|---:|
| Simulated paths | 1,000 |
| Mean return | -15.56% |
| Median return | -16.64% |
| 5th percentile | -35.08% |
| 95th percentile | 8.92% |
| Worst path | -47.02% |
| Best path | 53.17% |
| Probability of profit | 12.10% |
| Median final equity | $833.62 |
| Mean max drawdown | 23.19% |
| 95th percentile max drawdown | 36.74% |
| Average win rate | 33.83% |
| Average target-hit rate | 18.39% |
| Average add rate | 59.97% |

> The deterministic 7-cycle result annualizes/linear-projects to about **-29.91%** over 30 cycles, but the Monte Carlo distribution is more informative because it captures path variability.

---

## 7. Sensitivity analysis

Net P&L over the same 7 cycles when target and IV assumptions change. **The live bot remains fixed at 1,500 points.**

| IV assumption | Target 1,000 | Target 1,500 | Target 2,000 |
|---:|---:|---:|---:|
| 20% | +126.60 | -16.79 | -80.75 |
| 32% | +22.94 | -69.48 | -110.46 |
| 45% | -39.42 | -112.73 | -142.64 |

Other A/B checks:

| Change | Net P&L |
|---|---:|
| Fees disabled | -$59.87 |
| Fees enabled (default) | -$69.48 |
| 01:00 add disabled | -$45.17 |
| 01:00 add enabled (default) | -$69.48 |

Closest break-even target in a 500-2,500 point scan: **575 points** with P&L **$0.49**.

> Changing target, removing the add, or reducing fees is shown only to explain performance. It is **not** a recommendation to alter your requested strategy.

---

## 8. Risk profile

- **Maximum theoretical loss per cycle:** the premium paid. With 2% + 2% sizing, that is roughly **4% of equity plus fees** if both entries go to zero.
- **Observed worst cycle:** -$29.66 on $960.17 equity (-3.09%).
- **Observed max drawdown:** 8.70%.
- **Win/loss shape:** few large wins (2) and more small/medium losses (5) → profit factor 0.40.
- **Tail risk:** a sudden volatility spike, wide spread, stale quote, or failed exit can make actual results worse than the model.
- **No SL risk:** a losing position is held until target or 17:20, so theta decay can be substantial.

---

## 9. Verdict

For this 7-cycle Delta BTC sample, the exact strategy was **not profitable**:

- Net: **-$69.48 (-6.95%)**
- Win rate: **28.57%**
- Target-hit rate: **14.29%**
- Profit factor: **0.40**
- Monte Carlo probability of profit over 30 cycles: **12.10%**

The bot is technically complete and safe to test, but the evidence says: **do not go live with real money based on this short sample.** Run paper mode for several weeks, then testnet, and only then consider live with small capital.

---

## 10. What to do next

1. **Keep the exact strategy if you want “same to same”:** accept that this sample had only a 14.29% target-hit rate and a 28.57% win rate.
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
