from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from .broker import Broker
from .config import BotConfig
from .errors import MarketDataError, OptionSelectionError
from .models import Instrument, SelectedOption, Ticker
from .utils import UTC

logger = logging.getLogger("btc_options_bot.options")


def contract_type_for(direction: str) -> str:
    if direction == "CE":
        return "call_options"
    if direction == "PE":
        return "put_options"
    raise ValueError(f"unknown direction: {direction}")


def _expiry_date_for_chain(settlement_time: datetime) -> str:
    return settlement_time.astimezone(UTC).strftime("%d-%m-%Y")


def _ticker_is_acceptable(ticker: Ticker, config: BotConfig, now: datetime) -> bool:
    if ticker.product_trading_status != "operational":
        return False
    if ticker.best_ask <= 0 or ticker.best_bid <= 0 or ticker.best_ask < ticker.best_bid:
        return False
    if ticker.spread_pct > config.max_spread_pct:
        return False
    if ticker.turnover_usd < config.min_option_turnover_usd:
        return False
    if ticker.timestamp is not None:
        age = (now.astimezone(UTC) - ticker.timestamp).total_seconds()
        # Option quotes can update less often than the underlying BTC ticker.
        max_age = max(config.max_market_data_age_seconds, 300)
        if age > max_age:
            return False
    return True


def _manual_selection(
    broker: Broker,
    config: BotConfig,
    direction: str,
    exit_at: datetime,
    now: datetime,
) -> SelectedOption:
    instrument = broker.get_product_by_symbol(config.manual_option_symbol)
    if instrument.contract_type != contract_type_for(direction):
        raise OptionSelectionError(
            f"manual option {instrument.symbol} is {instrument.contract_type}, "
            f"but {direction} needs {contract_type_for(direction)}"
        )
    if instrument.settlement_time <= exit_at + timedelta(
        minutes=config.min_expiry_buffer_minutes
    ):
        raise OptionSelectionError(
            f"manual option expires at {instrument.settlement_time.isoformat()}, "
            "too close to or before the scheduled exit"
        )
    ticker = broker.get_ticker(instrument.symbol)
    if not _ticker_is_acceptable(ticker, config, now):
        raise OptionSelectionError(
            f"manual option {instrument.symbol} failed liquidity/freshness checks"
        )
    return SelectedOption(
        instrument=instrument,
        ticker=ticker,
        order_book=broker.get_order_book(instrument.symbol),
    )


def select_option(
    broker: Broker,
    config: BotConfig,
    *,
    direction: str,
    spot_price: Decimal,
    exit_at: datetime,
    now: datetime,
) -> SelectedOption:
    """Pick the nearest liquid ATM option whose expiry safely follows the exit."""
    if config.option_expiry_mode == "manual":
        return _manual_selection(broker, config, direction, exit_at, now)

    wanted_type = contract_type_for(direction)
    products = broker.list_option_products()
    min_expiry = exit_at + timedelta(minutes=config.min_expiry_buffer_minutes)
    max_expiry = exit_at + timedelta(days=config.max_expiry_days)
    eligible: list[Instrument] = []
    for product in products:
        if product.contract_type != wanted_type:
            continue
        if product.underlying_asset_symbol != config.underlying_asset_symbol:
            continue
        if product.state != "live" or product.trading_status != "operational":
            continue
        if product.settlement_time < min_expiry or product.settlement_time > max_expiry:
            continue
        if product.strike_price <= 0 or product.contract_value <= 0:
            continue
        eligible.append(product)
    if not eligible:
        raise OptionSelectionError(
            "no live BTC option expires safely after the scheduled 17:20 IST exit"
        )

    earliest_expiry = min(product.settlement_time for product in eligible)
    expiry_group = [
        product for product in eligible if product.settlement_time == earliest_expiry
    ]
    expiry_date = _expiry_date_for_chain(earliest_expiry)

    chain: dict[int, Ticker] = {}
    try:
        chain = broker.get_option_chain(config.underlying_asset_symbol, expiry_date)
    except Exception as exc:
        logger.warning("option-chain request failed (%s); falling back to individual tickers", exc)

    # For calls, higher strikes are OTM; for puts, lower strikes are OTM.
    ordered = sorted(expiry_group, key=lambda item: item.strike_price)
    if direction == "PE":
        ordered.reverse()
    atm_index = min(
        range(len(ordered)),
        key=lambda index: abs(ordered[index].strike_price - spot_price),
    )
    offset = config.option_strike_offset if config.option_strike_mode == "offset" else 0
    target_index = max(0, min(len(ordered) - 1, atm_index + offset))
    candidates = sorted(
        enumerate(ordered),
        key=lambda pair: (
            abs(pair[0] - target_index),
            abs(pair[1].strike_price - spot_price),
        ),
    )

    checked = 0
    errors: list[str] = []
    for _, instrument in candidates:
        ticker = chain.get(instrument.product_id)
        if ticker is None:
            if checked >= 15:
                break
            checked += 1
            try:
                ticker = broker.get_ticker(instrument.symbol)
            except MarketDataError as exc:
                errors.append(f"{instrument.symbol}: {exc}")
                continue
        if not _ticker_is_acceptable(ticker, config, now):
            errors.append(
                f"{instrument.symbol}: spread={ticker.spread_pct:.4f} or stale/illiquid quote"
            )
            continue
        try:
            order_book = broker.get_order_book(instrument.symbol)
        except Exception as exc:
            errors.append(f"{instrument.symbol}: order book unavailable ({exc})")
            continue
        if not order_book.sell:
            errors.append(f"{instrument.symbol}: empty ask book")
            continue
        return SelectedOption(instrument=instrument, ticker=ticker, order_book=order_book)

    raise OptionSelectionError(
        "no liquid option passed safety checks for "
        f"{direction} expiry {earliest_expiry.isoformat()}: " + "; ".join(errors[:5])
    )
