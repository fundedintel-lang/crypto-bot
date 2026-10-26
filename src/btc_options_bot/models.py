from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from .utils import (
    decimal_str,
    iso_utc,
    parse_iso_datetime,
    parse_timestamp,
    to_decimal,
)


@dataclass(frozen=True)
class Candle:
    time: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> Candle:
        return cls(
            time=int(raw["time"]),
            open=to_decimal(raw.get("open", 0)),
            high=to_decimal(raw.get("high", 0)),
            low=to_decimal(raw.get("low", 0)),
            close=to_decimal(raw.get("close", 0)),
            volume=(to_decimal(raw["volume"]) if raw.get("volume") is not None else None),
        )


@dataclass(frozen=True)
class Instrument:
    product_id: int
    symbol: str
    contract_type: str
    strike_price: Decimal
    settlement_time: datetime
    contract_value: Decimal
    tick_size: Decimal
    underlying_asset_symbol: str
    settling_asset_symbol: str
    state: str
    trading_status: str
    position_size_limit: int
    premium_commission_rate: Decimal
    taker_commission_rate: Decimal
    is_quanto: bool = False

    @classmethod
    def from_product(cls, raw: dict[str, Any]) -> Instrument:
        settlement = parse_iso_datetime(raw.get("settlement_time"))
        if settlement is None:
            raise ValueError(f"option {raw.get('symbol')} has no settlement_time")
        underlying = raw.get("underlying_asset") or {}
        settling = raw.get("settling_asset") or raw.get("quoting_asset") or {}
        product_specs = raw.get("product_specs") or {}
        return cls(
            product_id=int(raw["id"]),
            symbol=str(raw["symbol"]),
            contract_type=str(raw.get("contract_type", "")),
            strike_price=to_decimal(raw.get("strike_price", 0)),
            settlement_time=settlement,
            contract_value=to_decimal(raw.get("contract_value", "0.001")),
            tick_size=to_decimal(raw.get("tick_size", "0.1")),
            underlying_asset_symbol=str(underlying.get("symbol", "")).upper(),
            settling_asset_symbol=str(settling.get("symbol", "")).upper(),
            state=str(raw.get("state", "")),
            trading_status=str(raw.get("trading_status", "")),
            position_size_limit=int(raw.get("position_size_limit") or 1_000_000),
            premium_commission_rate=to_decimal(product_specs.get("premium_commission_rate", 0)),
            taker_commission_rate=to_decimal(raw.get("taker_commission_rate", 0)),
            is_quanto=bool(raw.get("is_quanto", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "symbol": self.symbol,
            "contract_type": self.contract_type,
            "strike_price": decimal_str(self.strike_price),
            "settlement_time": iso_utc(self.settlement_time),
            "contract_value": decimal_str(self.contract_value),
            "tick_size": decimal_str(self.tick_size),
            "underlying_asset_symbol": self.underlying_asset_symbol,
            "settling_asset_symbol": self.settling_asset_symbol,
            "state": self.state,
            "trading_status": self.trading_status,
            "position_size_limit": self.position_size_limit,
            "premium_commission_rate": decimal_str(self.premium_commission_rate),
            "taker_commission_rate": decimal_str(self.taker_commission_rate),
            "is_quanto": self.is_quanto,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Instrument:
        settlement = parse_iso_datetime(raw["settlement_time"])
        assert settlement is not None
        return cls(
            product_id=int(raw["product_id"]),
            symbol=str(raw["symbol"]),
            contract_type=str(raw["contract_type"]),
            strike_price=to_decimal(raw["strike_price"]),
            settlement_time=settlement,
            contract_value=to_decimal(raw["contract_value"]),
            tick_size=to_decimal(raw["tick_size"]),
            underlying_asset_symbol=str(raw["underlying_asset_symbol"]),
            settling_asset_symbol=str(raw["settling_asset_symbol"]),
            state=str(raw["state"]),
            trading_status=str(raw["trading_status"]),
            position_size_limit=int(raw["position_size_limit"]),
            premium_commission_rate=to_decimal(raw.get("premium_commission_rate", 0)),
            taker_commission_rate=to_decimal(raw.get("taker_commission_rate", 0)),
            is_quanto=bool(raw.get("is_quanto", False)),
        )


@dataclass(frozen=True)
class Ticker:
    product_id: int
    symbol: str
    contract_type: str
    close: Decimal
    mark_price: Decimal
    spot_price: Decimal | None
    best_ask: Decimal
    best_bid: Decimal
    ask_size: Decimal
    bid_size: Decimal
    tick_size: Decimal
    price_band_lower: Decimal | None
    price_band_upper: Decimal | None
    turnover_usd: Decimal
    oi_contracts: Decimal
    timestamp: datetime | None
    product_trading_status: str
    strike_price: Decimal | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> Ticker:
        quotes = raw.get("quotes") or {}
        price_band = raw.get("price_band") or {}
        spot_value = raw.get("spot_price")
        close_value = raw.get("close")
        return cls(
            product_id=int(raw.get("product_id") or 0),
            symbol=str(raw.get("symbol", "")),
            contract_type=str(raw.get("contract_type", "")),
            close=to_decimal(close_value, default=Decimal("0")),
            mark_price=to_decimal(raw.get("mark_price", 0), default=Decimal("0")),
            spot_price=to_decimal(spot_value) if spot_value not in (None, "") else None,
            best_ask=to_decimal(quotes.get("best_ask", 0), default=Decimal("0")),
            best_bid=to_decimal(quotes.get("best_bid", 0), default=Decimal("0")),
            ask_size=to_decimal(quotes.get("ask_size", 0), default=Decimal("0")),
            bid_size=to_decimal(quotes.get("bid_size", 0), default=Decimal("0")),
            tick_size=to_decimal(raw.get("tick_size", "0.1"), default=Decimal("0.1")),
            price_band_lower=(
                to_decimal(price_band["lower_limit"])
                if price_band.get("lower_limit") not in (None, "")
                else None
            ),
            price_band_upper=(
                to_decimal(price_band["upper_limit"])
                if price_band.get("upper_limit") not in (None, "")
                else None
            ),
            turnover_usd=to_decimal(raw.get("turnover_usd", 0), default=Decimal("0")),
            oi_contracts=to_decimal(raw.get("oi_contracts", 0), default=Decimal("0")),
            timestamp=parse_timestamp(raw.get("timestamp"), unit="us")
            or parse_iso_datetime(raw.get("time")),
            product_trading_status=str(raw.get("product_trading_status", "operational")),
            strike_price=(
                to_decimal(raw["strike_price"])
                if raw.get("strike_price") not in (None, "")
                else None
            ),
            raw=dict(raw),
        )

    @property
    def mid_price(self) -> Decimal:
        if self.best_ask > 0 and self.best_bid > 0:
            return (self.best_ask + self.best_bid) / 2
        if self.mark_price > 0:
            return self.mark_price
        return self.close

    @property
    def spread_pct(self) -> Decimal:
        mid = self.mid_price
        if self.best_ask <= 0 or self.best_bid <= 0 or mid <= 0:
            return Decimal("1")
        return (self.best_ask - self.best_bid) / mid


@dataclass(frozen=True)
class OrderBookLevel:
    price: Decimal
    size: int

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> OrderBookLevel:
        return cls(price=to_decimal(raw["price"]), size=int(float(raw.get("size", 0))))


@dataclass(frozen=True)
class OrderBook:
    symbol: str
    buy: tuple[OrderBookLevel, ...]
    sell: tuple[OrderBookLevel, ...]
    timestamp: datetime | None

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> OrderBook:
        buy = tuple(
            sorted(
                (OrderBookLevel.from_raw(level) for level in raw.get("buy", [])),
                key=lambda item: item.price,
                reverse=True,
            )
        )
        sell = tuple(
            sorted(
                (OrderBookLevel.from_raw(level) for level in raw.get("sell", [])),
                key=lambda item: item.price,
            )
        )
        return cls(
            symbol=str(raw.get("symbol", "")),
            buy=buy,
            sell=sell,
            timestamp=parse_timestamp(raw.get("last_updated_at"), unit="us"),
        )


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str
    product_id: int
    side: str
    size: int
    order_type: str = "limit_order"
    limit_price: Decimal | None = None
    reduce_only: bool = False
    time_in_force: str = "gtc"
    product_symbol: str = ""
    contract_value: Decimal = Decimal("0.001")
    metadata: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_api_payload(self) -> dict[str, Any]:
        if len(self.client_order_id) > 32:
            raise ValueError("client_order_id must be at most 32 characters")
        if self.size <= 0:
            raise ValueError("order size must be positive")
        payload: dict[str, Any] = {
            "product_id": self.product_id,
            "size": self.size,
            "side": self.side,
            "order_type": self.order_type,
            "client_order_id": self.client_order_id,
            "reduce_only": self.reduce_only,
            "time_in_force": self.time_in_force,
        }
        if self.order_type == "limit_order":
            if self.limit_price is None or self.limit_price <= 0:
                raise ValueError("limit_price is required for a limit_order")
            payload["limit_price"] = decimal_str(self.limit_price)
        return payload


@dataclass(frozen=True)
class ExchangeOrder:
    id: str
    client_order_id: str
    product_id: int
    product_symbol: str
    side: str
    size: int
    unfilled_size: int
    average_fill_price: Decimal | None
    limit_price: Decimal | None
    state: str
    order_type: str
    reduce_only: bool
    created_at: datetime | None
    updated_at: datetime | None
    cancellation_reason: str | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def filled_size(self) -> int:
        return max(0, self.size - self.unfilled_size)

    @property
    def is_filled(self) -> bool:
        return self.state == "closed" or self.unfilled_size <= 0

    @property
    def is_cancelled(self) -> bool:
        return self.state == "cancelled"

    @property
    def is_working(self) -> bool:
        return self.state in {"open", "pending"} and self.unfilled_size > 0

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> ExchangeOrder:
        size = int(float(raw.get("size", 0)))
        unfilled = int(float(raw.get("unfilled_size", 0)))
        avg = raw.get("average_fill_price")
        limit = raw.get("limit_price")
        return cls(
            id=str(raw.get("id", "")),
            client_order_id=str(raw.get("client_order_id", "")),
            product_id=int(raw.get("product_id") or 0),
            product_symbol=str(raw.get("product_symbol", "")),
            side=str(raw.get("side", "")),
            size=size,
            unfilled_size=unfilled,
            average_fill_price=(to_decimal(avg) if avg not in (None, "") else None),
            limit_price=(to_decimal(limit) if limit not in (None, "") else None),
            state=str(raw.get("state", "unknown")),
            order_type=str(raw.get("order_type", "")),
            reduce_only=bool(raw.get("reduce_only", False)),
            created_at=parse_iso_datetime(raw.get("created_at")),
            updated_at=parse_iso_datetime(raw.get("updated_at")),
            cancellation_reason=(
                str(raw["cancellation_reason"]) if raw.get("cancellation_reason") else None
            ),
            raw=dict(raw),
        )


@dataclass(frozen=True)
class Position:
    product_id: int
    product_symbol: str
    size: int
    entry_price: Decimal
    mark_price: Decimal
    unrealized_pnl: Decimal | None
    unrealized_cashflow: Decimal | None
    margin: Decimal | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def abs_size(self) -> int:
        return abs(self.size)

    @property
    def is_open(self) -> bool:
        return self.size != 0

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> Position:
        def optional_decimal(name: str) -> Decimal | None:
            value = raw.get(name)
            return to_decimal(value) if value not in (None, "") else None

        return cls(
            product_id=int(raw.get("product_id") or 0),
            product_symbol=str(raw.get("product_symbol", "")),
            size=int(float(raw.get("size", 0) or 0)),
            entry_price=to_decimal(raw.get("entry_price", 0), default=Decimal("0")),
            mark_price=to_decimal(raw.get("mark_price", 0), default=Decimal("0")),
            unrealized_pnl=optional_decimal("unrealized_pnl"),
            unrealized_cashflow=optional_decimal("unrealized_cashflow"),
            margin=optional_decimal("margin"),
            raw=dict(raw),
        )


@dataclass(frozen=True)
class WalletSnapshot:
    net_equity: Decimal | None
    balances: tuple[dict[str, Any], ...]
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def available_balance(self, asset_symbol: str) -> Decimal:
        for balance in self.balances:
            if str(balance.get("asset_symbol", "")).upper() == asset_symbol.upper():
                return to_decimal(balance.get("available_balance", 0), default=Decimal("0"))
        return Decimal("0")

    def wallet_balance(self, asset_symbol: str) -> Decimal:
        for balance in self.balances:
            if str(balance.get("asset_symbol", "")).upper() == asset_symbol.upper():
                return to_decimal(balance.get("balance", 0), default=Decimal("0"))
        return Decimal("0")

    @classmethod
    def from_response(cls, result: Any, meta: dict[str, Any]) -> WalletSnapshot:
        balances = tuple(dict(item) for item in (result or []) if isinstance(item, dict))
        net_equity = None
        if meta.get("net_equity") not in (None, ""):
            net_equity = to_decimal(meta["net_equity"])
        return cls(
            net_equity=net_equity,
            balances=balances,
            raw={"result": result, "meta": meta},
        )


@dataclass(frozen=True)
class Signal:
    spot_price: Decimal
    ema21: Decimal
    direction: str
    last_closed_candle_time: datetime
    candles_used: int

    @property
    def has_trade(self) -> bool:
        return self.direction in {"CE", "PE"}


@dataclass(frozen=True)
class OrderPlan:
    quantity: int
    limit_price: Decimal
    estimated_price: Decimal
    estimated_cost: Decimal
    fee_rate: Decimal
    book_filled_quantity: int
    order_type: str = "limit_order"

    def to_dict(self) -> dict[str, Any]:
        return {
            "quantity": self.quantity,
            "limit_price": decimal_str(self.limit_price),
            "estimated_price": decimal_str(self.estimated_price),
            "estimated_cost": decimal_str(self.estimated_cost),
            "fee_rate": decimal_str(self.fee_rate),
            "book_filled_quantity": self.book_filled_quantity,
            "order_type": self.order_type,
        }


@dataclass(frozen=True)
class SelectedOption:
    instrument: Instrument
    ticker: Ticker
    order_book: OrderBook


@dataclass(frozen=True)
class TradePlan:
    signal: Signal
    selected: SelectedOption
    entry_btc_price: Decimal
    target_btc_price: Decimal
    capital: Decimal
    budget: Decimal
    order_plan: OrderPlan
    entry_at: datetime
    add_at: datetime
    exit_at: datetime
    cycle_id: str

    def to_dict(self) -> dict[str, Any]:
        option = self.selected
        return {
            "cycle_id": self.cycle_id,
            "direction": self.signal.direction,
            "spot_price": decimal_str(self.signal.spot_price),
            "ema21": decimal_str(self.signal.ema21),
            "last_candle": iso_utc(self.signal.last_closed_candle_time),
            "candles_used": self.signal.candles_used,
            "option_symbol": option.instrument.symbol,
            "product_id": option.instrument.product_id,
            "strike": decimal_str(option.instrument.strike_price),
            "expiry": iso_utc(option.instrument.settlement_time),
            "contract_value": decimal_str(option.instrument.contract_value),
            "best_ask": decimal_str(option.ticker.best_ask),
            "best_bid": decimal_str(option.ticker.best_bid),
            "spread_pct": decimal_str(option.ticker.spread_pct),
            "entry_btc_price": decimal_str(self.entry_btc_price),
            "target_btc_price": decimal_str(self.target_btc_price),
            "capital": decimal_str(self.capital),
            "budget": decimal_str(self.budget),
            "quantity": self.order_plan.quantity,
            "limit_price": decimal_str(self.order_plan.limit_price),
            "estimated_cost": decimal_str(self.order_plan.estimated_cost),
            "entry_at": iso_utc(self.entry_at),
            "add_at": iso_utc(self.add_at),
            "exit_at": iso_utc(self.exit_at),
        }
