from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from .broker import Broker
from .config import BotConfig
from .errors import MarketDataError, RiskCheckError
from .indicators import calculate_closed_candle_ema
from .models import (
    Candle,
    Instrument,
    OrderBookLevel,
    OrderPlan,
    Signal,
    Ticker,
    TradePlan,
)
from .option_selector import select_option
from .schedule import SessionSchedule
from .state import CycleState
from .utils import (
    RESOLUTION_SECONDS,
    UTC,
    clamp_to_band,
    decimal_str,
    to_decimal,
)

logger = logging.getLogger("btc_options_bot.strategy")


@dataclass(frozen=True)
class PlanningResult:
    signal: Signal
    plan: TradePlan | None
    reason: str = ""


@dataclass(frozen=True)
class BookWalk:
    quantity: int
    vwap: Decimal
    worst_price: Decimal


def current_spot_price(
    broker: Broker,
    config: BotConfig,
    *,
    now: datetime,
    require_fresh: bool = True,
) -> Decimal:
    ticker = broker.get_ticker(config.price_symbol)
    spot = ticker.spot_price
    if spot is None or spot <= 0:
        spot = ticker.close or ticker.mark_price
    if spot <= 0:
        raise MarketDataError(f"no positive BTC spot price from {config.price_symbol}")
    if require_fresh and ticker.timestamp is not None:
        age = (now.astimezone(UTC) - ticker.timestamp).total_seconds()
        if age > config.max_market_data_age_seconds:
            raise MarketDataError(
                f"BTC ticker is stale ({int(age)}s > {config.max_market_data_age_seconds}s)"
            )
    return spot


def get_signal(
    broker: Broker,
    config: BotConfig,
    *,
    now: datetime,
) -> Signal:
    spot = current_spot_price(broker, config, now=now)
    interval = RESOLUTION_SECONDS[config.candle_resolution]
    lookback_candles = max(config.ema_period * 10, 240)
    start = int(now.timestamp()) - lookback_candles * interval
    end = int(now.timestamp()) + interval
    raw_candles: list[Candle] = broker.get_candles(
        config.candle_symbol,
        config.candle_resolution,
        start,
        end,
    )
    ema_result = calculate_closed_candle_ema(
        raw_candles,
        period=config.ema_period,
        resolution=config.candle_resolution,
        now=now,
    )
    if spot > ema_result.value:
        direction = "CE"
    elif spot < ema_result.value:
        direction = "PE"
    else:
        direction = "NONE"
    return Signal(
        spot_price=spot,
        ema21=ema_result.value,
        direction=direction,
        last_closed_candle_time=ema_result.last_closed_candle_time,
        candles_used=ema_result.candles_used,
    )


def capital_snapshot(broker: Broker, config: BotConfig) -> Decimal:
    if config.capital_mode == "fixed":
        assert config.fixed_capital is not None
        capital = config.fixed_capital
    else:
        wallet = broker.get_wallet_balances()
        if config.capital_mode == "available_balance":
            capital = wallet.available_balance(config.capital_asset)
        else:
            capital = wallet.net_equity
            if capital is None:
                capital = wallet.wallet_balance(config.capital_asset)
    if capital <= 0:
        raise RiskCheckError(f"capital is not positive: {capital}")
    if capital < config.min_wallet_equity:
        raise RiskCheckError(
            f"capital {decimal_str(capital)} is below MIN_WALLET_EQUITY "
            f"{decimal_str(config.min_wallet_equity)}"
        )
    return capital


def fee_rate_for(instrument: Instrument, config: BotConfig) -> Decimal:
    explicit_fee = max(instrument.premium_commission_rate, instrument.taker_commission_rate)
    return explicit_fee + config.fee_buffer_pct


def walk_book(
    levels: tuple[OrderBookLevel, ...],
    quantity: int,
    *,
    side: str,
    price_limit: Decimal,
) -> BookWalk:
    if quantity <= 0:
        return BookWalk(0, Decimal("0"), Decimal("0"))
    ordered = sorted(levels, key=lambda item: item.price, reverse=(side == "sell"))
    remaining = quantity
    notional = Decimal("0")
    filled = 0
    worst = Decimal("0")
    for level in ordered:
        if level.size <= 0 or level.price <= 0:
            continue
        if side == "buy" and level.price > price_limit:
            break
        if side == "sell" and level.price < price_limit:
            break
        take = min(remaining, level.size)
        notional += level.price * Decimal(take)
        filled += take
        remaining -= take
        worst = level.price
        if remaining <= 0:
            break
    vwap = notional / Decimal(filled) if filled else Decimal("0")
    return BookWalk(filled, vwap, worst)


def _floor(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_FLOOR))


def plan_buy_order(
    instrument: Instrument,
    ticker: Ticker,
    order_book: Any,
    *,
    budget: Decimal,
    max_quantity: int,
    config: BotConfig,
) -> OrderPlan:
    if budget <= 0:
        raise RiskCheckError("allocation budget is not positive")
    ask = ticker.best_ask
    if ask <= 0:
        raise RiskCheckError(f"no valid best ask for {instrument.symbol}")
    fee_rate = fee_rate_for(instrument, config)
    unit_cost = ask * instrument.contract_value * (Decimal("1") + fee_rate)
    quantity = _floor(budget / unit_cost)
    quantity = min(quantity, max_quantity, instrument.position_size_limit)
    if quantity < 1:
        raise RiskCheckError(
            f"2% allocation cannot buy even one {instrument.symbol} contract "
            f"(budget={decimal_str(budget)}, ask={decimal_str(ask)})"
        )

    max_book_price = ask * (Decimal("1") + config.max_book_slippage_pct)
    book = walk_book(
        order_book.sell,
        quantity,
        side="buy",
        price_limit=max_book_price,
    )
    if book.quantity < 1:
        raise RiskCheckError(f"no acceptable ask depth for {instrument.symbol}")
    if book.quantity < quantity:
        quantity = book.quantity

    estimated_price = max(ask, book.vwap if book.vwap > 0 else ask)
    conservative_unit_cost = estimated_price * instrument.contract_value * (Decimal("1") + fee_rate)
    recalculated = _floor(budget / conservative_unit_cost)
    if recalculated < quantity:
        quantity = recalculated
        book = walk_book(
            order_book.sell,
            quantity,
            side="buy",
            price_limit=max_book_price,
        )
        estimated_price = max(ask, book.vwap if book.vwap > 0 else ask)
    if quantity < 1:
        raise RiskCheckError("quantity became zero after order-book cost check")

    raw_limit = estimated_price * (Decimal("1") + config.order_price_buffer_pct)
    limit_price = clamp_to_band(
        raw_limit,
        lower=ticker.price_band_lower,
        upper=ticker.price_band_upper,
        tick_size=instrument.tick_size,
        direction="up",
    )
    if limit_price <= 0:
        raise RiskCheckError("calculated buy limit price is not positive")
    if limit_price < ask:
        raise RiskCheckError(
            f"buy limit {decimal_str(limit_price)} cannot cross ask {decimal_str(ask)} "
            "within the exchange price band"
        )
    estimated_cost = (
        Decimal(quantity)
        * estimated_price
        * instrument.contract_value
        * (Decimal("1") + fee_rate)
    )
    if estimated_cost > budget * Decimal("1.001"):
        raise RiskCheckError(
            f"estimated cost {decimal_str(estimated_cost)} exceeds budget {decimal_str(budget)}"
        )
    return OrderPlan(
        quantity=quantity,
        limit_price=limit_price,
        estimated_price=estimated_price,
        estimated_cost=estimated_cost,
        fee_rate=fee_rate,
        book_filled_quantity=book.quantity,
    )


def plan_sell_order(
    instrument: Instrument,
    ticker: Ticker,
    order_book: Any,
    *,
    quantity: int,
    config: BotConfig,
) -> OrderPlan:
    if quantity <= 0:
        raise RiskCheckError("exit quantity must be positive")
    bid = ticker.best_bid
    if bid <= 0 and order_book.buy:
        bid = order_book.buy[0].price
    if bid <= 0:
        bid = ticker.mark_price or ticker.close
    if bid <= 0:
        raise RiskCheckError(f"no valid bid/mark price for exit of {instrument.symbol}")

    floor_price = max(instrument.tick_size, bid * (Decimal("1") - config.max_book_slippage_pct))
    book = walk_book(
        order_book.buy,
        quantity,
        side="sell",
        price_limit=floor_price,
    )
    if book.vwap > 0:
        estimated_price = min(bid, book.vwap)
    elif book.worst_price > 0:
        estimated_price = min(bid, book.worst_price)
    else:
        estimated_price = bid
    raw_limit = estimated_price * (Decimal("1") - config.exit_price_buffer_pct)
    if raw_limit <= 0:
        raw_limit = instrument.tick_size
    limit_price = clamp_to_band(
        raw_limit,
        lower=ticker.price_band_lower,
        upper=ticker.price_band_upper,
        tick_size=instrument.tick_size,
        direction="down",
    )
    if limit_price <= 0:
        raise RiskCheckError("calculated sell limit price is not positive")
    if limit_price > bid:
        raise RiskCheckError(
            f"sell limit {decimal_str(limit_price)} cannot cross bid {decimal_str(bid)} "
            "within the exchange price band; use market-order fallback"
        )
    fee_rate = fee_rate_for(instrument, config)
    estimated_proceeds = (
        Decimal(quantity)
        * estimated_price
        * instrument.contract_value
        * (Decimal("1") - fee_rate)
    )
    return OrderPlan(
        quantity=quantity,
        limit_price=limit_price,
        estimated_price=estimated_price,
        estimated_cost=estimated_proceeds,
        fee_rate=fee_rate,
        book_filled_quantity=book.quantity,
    )


def market_exit_plan(instrument: Instrument, ticker: Ticker, quantity: int, config: BotConfig) -> OrderPlan:
    if quantity <= 0:
        raise RiskCheckError("exit quantity must be positive")
    return OrderPlan(
        quantity=quantity,
        limit_price=Decimal("0"),
        estimated_price=ticker.mark_price or ticker.close,
        estimated_cost=Decimal("0"),
        fee_rate=fee_rate_for(instrument, config),
        book_filled_quantity=0,
        order_type="market_order",
    )


def build_trade_plan(
    broker: Broker,
    config: BotConfig,
    session: SessionSchedule,
    *,
    now: datetime,
) -> PlanningResult:
    signal = get_signal(broker, config, now=now)
    if not signal.has_trade:
        return PlanningResult(signal=signal, plan=None, reason="BTC is exactly equal to EMA21")

    selected = select_option(
        broker,
        config,
        direction=signal.direction,
        spot_price=signal.spot_price,
        exit_at=session.exit_at,
        now=now,
    )
    if selected.instrument.settling_asset_symbol != config.capital_asset:
        raise RiskCheckError(
            f"option settles in {selected.instrument.settling_asset_symbol}, "
            f"but capital asset is {config.capital_asset}"
        )

    existing = broker.get_position(selected.instrument.product_id)
    if existing and existing.is_open and not config.allow_existing_position:
        raise RiskCheckError(
            f"existing position of {existing.size} found in {selected.instrument.symbol}; "
            "refusing to mix strategies. Set ALLOW_EXISTING_POSITION=true only on a dedicated account."
        )

    capital = capital_snapshot(broker, config)
    budget = capital * config.initial_capital_pct
    order_plan = plan_buy_order(
        selected.instrument,
        selected.ticker,
        selected.order_book,
        budget=budget,
        max_quantity=selected.instrument.position_size_limit,
        config=config,
    )
    target = (
        signal.spot_price + config.target_points
        if signal.direction == "CE"
        else signal.spot_price - config.target_points
    )
    plan = TradePlan(
        signal=signal,
        selected=selected,
        entry_btc_price=signal.spot_price,
        target_btc_price=target,
        capital=capital,
        budget=budget,
        order_plan=order_plan,
        entry_at=session.entry_at,
        add_at=session.add_at,
        exit_at=session.exit_at,
        cycle_id=session.cycle_id,
    )
    return PlanningResult(signal=signal, plan=plan)


def build_additional_plan(
    broker: Broker,
    config: BotConfig,
    cycle: CycleState,
    *,
    now: datetime,
) -> OrderPlan:
    instrument = cycle.instrument()
    ticker = broker.get_ticker(instrument.symbol)
    order_book = broker.get_order_book(instrument.symbol)
    capital = to_decimal(cycle.initial_capital)
    budget = capital * config.additional_capital_pct
    max_quantity = int(cycle.initial_quantity)
    if max_quantity <= 0:
        raise RiskCheckError("cannot add because initial filled quantity is zero")
    return plan_buy_order(
        instrument,
        ticker,
        order_book,
        budget=budget,
        max_quantity=max_quantity,
        config=config,
    )


def build_exit_plan(
    broker: Broker,
    config: BotConfig,
    cycle: CycleState,
    *,
    quantity: int,
    allow_market_fallback: bool = True,
) -> tuple[OrderPlan, Ticker]:
    instrument = cycle.instrument()
    ticker = broker.get_ticker(instrument.symbol)
    try:
        order_book = broker.get_order_book(instrument.symbol)
        plan = plan_sell_order(
            instrument,
            ticker,
            order_book,
            quantity=quantity,
            config=config,
        )
        return plan, ticker
    except Exception:
        if not allow_market_fallback:
            raise
        logger.warning("limit-exit planning failed for %s; using market-order fallback", instrument.symbol)
        return market_exit_plan(instrument, ticker, quantity, config), ticker


def target_reached(direction: str, spot: Decimal, target: Decimal) -> bool:
    if direction == "CE":
        return spot >= target
    if direction == "PE":
        return spot <= target
    return False


def position_is_losing(
    broker: Broker,
    cycle: CycleState,
    *,
    fallback_mark_price: Decimal | None = None,
) -> bool:
    cycle.instrument()
    position = broker.get_position(int(cycle.product_id or 0))
    if position is None or not position.is_open:
        return False
    pnl = position.unrealized_cashflow
    if pnl is None:
        pnl = position.unrealized_pnl
    if pnl is not None:
        return pnl < 0
    mark_price = position.mark_price or fallback_mark_price
    avg_entry = to_decimal(cycle.avg_entry_premium, default=Decimal("0"))
    if mark_price is None or avg_entry <= 0:
        return False
    return mark_price < avg_entry
