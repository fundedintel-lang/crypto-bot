from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from btc_options_bot.clock import FrozenClock
from btc_options_bot.config import BotConfig
from btc_options_bot.engine import BotEngine
from btc_options_bot.models import (
    Candle,
    ExchangeOrder,
    Instrument,
    OrderBook,
    OrderRequest,
    Position,
    Ticker,
    WalletSnapshot,
)
from btc_options_bot.state import StateStore
from btc_options_bot.utils import UTC

TEST_NOW = datetime(2026, 10, 26, 13, 0, 0, tzinfo=UTC)  # 18:30 IST


class TestNotifier:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str, dict[str, Any]]] = []

    def notify(self, level: str, title: str, message: str, *, context=None) -> None:
        self.events.append((level, title, message, dict(context or {})))

    def close(self) -> None:
        return None


def make_config(tmp: str | Path | None = None, **overrides: str) -> BotConfig:
    if tmp is None:
        tmp = tempfile.mkdtemp(prefix="btc-bot-test-")
    root = Path(tmp)
    values: dict[str, str] = {
        "BOT_MODE": "paper",
        "VENUE": "india",
        "CAPITAL_MODE": "net_equity",
        "CAPITAL_ASSET": "USD",
        "PAPER_CAPITAL": "1000",
        "PRICE_SYMBOL": "BTCUSD",
        "CANDLE_SYMBOL": ".DEXBTUSD",
        "DATA_DIR": str(root / "data"),
        "LOG_DIR": str(root / "logs"),
        "STATE_DB": str(root / "data" / "state.sqlite3"),
        "PAPER_STATE_FILE": str(root / "data" / "paper.json"),
        "PAUSE_FILE": str(root / "data" / "PAUSE"),
        "KILL_FILE": str(root / "data" / "KILL"),
        "LOCK_FILE": str(root / "data" / "bot.lock"),
        "LOG_FILE": str(root / "logs" / "bot.log"),
        "WEB_ENABLED": "false",
        "POLL_SECONDS": "0.01",
        "CATCHUP_MINUTES": "10",
        "MAX_MARKET_DATA_AGE_SECONDS": "3600",
        "MIN_EXPIRY_BUFFER_MINUTES": "5",
        "MAX_EXPIRY_DAYS": "8",
        "MIN_WALLET_EQUITY": "0",
    }
    values.update(overrides)
    config = BotConfig.from_env(
        env_file=root / "does-not-exist.env",
        environ=values,
        require_credentials=False,
    )
    config.ensure_directories()
    return config


def make_candles(now: datetime, count: int = 120, *, trend: str = "up") -> list[Candle]:
    bucket = int(now.timestamp()) // 3600 * 3600
    candles: list[Candle] = []
    for index in range(count):
        start = bucket - (count - index) * 3600
        if trend == "up":
            close = Decimal("79000") + Decimal(index) * Decimal("10")
        elif trend == "down":
            close = Decimal("81000") - Decimal(index) * Decimal("10")
        else:
            close = Decimal("80000")
        candles.append(
            Candle(
                time=start,
                open=close,
                high=close + 5,
                low=close - 5,
                close=close,
                volume=Decimal("100"),
            )
        )
    return candles


def make_instrument(
    *,
    product_id: int,
    direction: str,
    strike: str,
    settlement: datetime,
    symbol: str | None = None,
    state: str = "live",
    trading_status: str = "operational",
) -> Instrument:
    prefix = "C" if direction == "CE" else "P"
    date_code = settlement.strftime("%d%m%y")
    return Instrument(
        product_id=product_id,
        symbol=symbol or f"{prefix}-BTC-{strike}-{date_code}",
        contract_type="call_options" if direction == "CE" else "put_options",
        strike_price=Decimal(strike),
        settlement_time=settlement.astimezone(UTC),
        contract_value=Decimal("0.001"),
        tick_size=Decimal("0.1"),
        underlying_asset_symbol="BTC",
        settling_asset_symbol="USD",
        state=state,
        trading_status=trading_status,
        position_size_limit=50000,
        premium_commission_rate=Decimal("0.035"),
        taker_commission_rate=Decimal("0.0001"),
    )


def make_ticker(
    *,
    product_id: int,
    symbol: str,
    now: datetime,
    best_ask: str = "500",
    best_bid: str = "490",
    mark_price: str | None = None,
    spot_price: str | None = None,
    product_trading_status: str = "operational",
    turnover_usd: str = "10000",
    strike_price: str | None = None,
) -> Ticker:
    raw: dict[str, Any] = {
        "product_id": product_id,
        "symbol": symbol,
        "contract_type": "call_options" if symbol.startswith("C-") else "put_options",
        "close": best_bid,
        "mark_price": mark_price or best_bid,
        "spot_price": spot_price or "0",
        "quotes": {
            "best_ask": best_ask,
            "best_bid": best_bid,
            "ask_size": "1000",
            "bid_size": "1000",
        },
        "tick_size": "0.1",
        "price_band": {"lower_limit": "100", "upper_limit": "1000"},
        "turnover_usd": turnover_usd,
        "oi_contracts": "1000",
        "timestamp": int(now.timestamp() * 1_000_000),
        "product_trading_status": product_trading_status,
    }
    if strike_price:
        raw["strike_price"] = strike_price
    return Ticker.from_raw(raw)


def make_underlying_ticker(now: datetime, spot: str = "80200") -> Ticker:
    return Ticker.from_raw(
        {
            "product_id": 27,
            "symbol": "BTCUSD",
            "contract_type": "perpetual_futures",
            "close": spot,
            "mark_price": spot,
            "spot_price": spot,
            "quotes": {
                "best_ask": str(Decimal(spot) + 1),
                "best_bid": str(Decimal(spot) - 1),
            },
            "tick_size": "0.5",
            "timestamp": int(now.timestamp() * 1_000_000),
            "product_trading_status": "operational",
        }
    )


def make_order_book(symbol: str, *, ask: str = "500", bid: str = "490") -> OrderBook:
    return OrderBook.from_raw(
        {
            "symbol": symbol,
            "sell": [{"price": ask, "size": 1000}, {"price": "505", "size": 1000}],
            "buy": [{"price": bid, "size": 1000}, {"price": "485", "size": 1000}],
            "last_updated_at": int(TEST_NOW.timestamp() * 1_000_000),
        }
    )


class FakeBroker:
    """Deterministic in-memory exchange for engine and strategy tests."""

    name = "fake"

    def __init__(self, *, now: datetime = TEST_NOW, spot: str = "80200") -> None:
        self.now = now
        self.spot = Decimal(spot)
        self.option_ask = Decimal("500")
        self.option_bid = Decimal("490")
        self.option_mark = Decimal("495")
        self.wallet_equity = Decimal("1000")
        self.candles = make_candles(now)
        self.products = self._make_products(now)
        self.tickers: dict[int, Ticker] = {}
        self.books: dict[str, OrderBook] = {}
        self._rebuild_quotes()
        self.orders: dict[str, dict[str, Any]] = {}
        self.positions: dict[int, dict[str, Any]] = {}
        self.order_counter = 1
        self.place_calls: list[OrderRequest] = []
        self.cancel_calls: list[dict[str, Any]] = []
        self.hold_orders = False
        self.unrealized_pnl_override: Decimal | None = None

    def _make_products(self, now: datetime) -> list[Instrument]:
        next_expiry = datetime(2026, 10, 27, 12, 0, 0, tzinfo=UTC)
        later_expiry = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
        products: list[Instrument] = []
        product_id = 1000
        for expiry in (next_expiry, later_expiry):
            for direction in ("CE", "PE"):
                for strike in ("79000", "80000", "81000"):
                    products.append(
                        make_instrument(
                            product_id=product_id,
                            direction=direction,
                            strike=strike,
                            settlement=expiry,
                            symbol=(
                                f"{'C' if direction == 'CE' else 'P'}-BTC-{strike}-"
                                f"{expiry.strftime('%d%m%y')}"
                            ),
                        )
                    )
                    product_id += 1
        return products

    def _rebuild_quotes(self) -> None:
        self.tickers = {}
        self.books = {}
        for product in self.products:
            ticker = make_ticker(
                product_id=product.product_id,
                symbol=product.symbol,
                now=self.now,
                best_ask=str(self.option_ask),
                best_bid=str(self.option_bid),
                mark_price=str(self.option_mark),
                strike_price=str(product.strike_price),
            )
            self.tickers[product.product_id] = ticker
            self.books[product.symbol] = make_order_book(
                product.symbol,
                ask=str(self.option_ask),
                bid=str(self.option_bid),
            )

    def set_market(
        self,
        *,
        spot: Decimal | str | None = None,
        option_mark: Decimal | str | None = None,
        option_ask: Decimal | str | None = None,
        option_bid: Decimal | str | None = None,
        now: datetime | None = None,
    ) -> None:
        if spot is not None:
            self.spot = Decimal(str(spot))
        if option_mark is not None:
            self.option_mark = Decimal(str(option_mark))
        if option_ask is not None:
            self.option_ask = Decimal(str(option_ask))
        if option_bid is not None:
            self.option_bid = Decimal(str(option_bid))
        if now is not None:
            self.now = now
        self._rebuild_quotes()

    def get_ticker(self, symbol: str) -> Ticker:
        if symbol in {"BTCUSD", "BTCUSDT"}:
            return make_underlying_ticker(self.now, str(self.spot))
        for product in self.products:
            if product.symbol == symbol:
                return self.tickers[product.product_id]
        raise KeyError(symbol)

    def get_candles(self, symbol: str, resolution: str, start: int, end: int) -> list[Candle]:
        return [candle for candle in self.candles if start <= candle.time <= end]

    def list_option_products(self) -> list[Instrument]:
        return list(self.products)

    def get_option_chain(self, underlying: str, expiry_date: str) -> dict[int, Ticker]:
        matching = {
            product.product_id
            for product in self.products
            if product.settlement_time.strftime("%d-%m-%Y") == expiry_date
        }
        return {pid: self.tickers[pid] for pid in matching}

    def get_product_by_symbol(self, symbol: str) -> Instrument:
        for product in self.products:
            if product.symbol == symbol:
                return product
        raise KeyError(symbol)

    def get_order_book(self, symbol: str) -> OrderBook:
        return self.books[symbol]

    def get_wallet_balances(self) -> WalletSnapshot:
        return WalletSnapshot.from_response(
            [
                {
                    "asset_symbol": "USD",
                    "balance": str(self.wallet_equity),
                    "available_balance": str(self.wallet_equity),
                }
            ],
            {"net_equity": str(self.wallet_equity)},
        )

    def get_positions_margined(self, product_ids: list[int] | None = None) -> list[Position]:
        result = []
        for product_id, position in self.positions.items():
            if product_ids and product_id not in product_ids:
                continue
            if position["size"] <= 0:
                continue
            result.append(self._position_object(product_id, position))
        return result

    def get_position(self, product_id: int) -> Position | None:
        position = self.positions.get(product_id)
        if not position or position["size"] <= 0:
            return None
        return self._position_object(product_id, position)

    def _position_object(self, product_id: int, position: dict[str, Any]) -> Position:
        size = int(position["size"])
        entry = Decimal(position["entry_price"])
        pnl = (self.option_mark - entry) * Decimal(size) * Decimal("0.001")
        if self.unrealized_pnl_override is not None:
            pnl = self.unrealized_pnl_override
        return Position.from_raw(
            {
                "product_id": product_id,
                "product_symbol": position["symbol"],
                "size": size,
                "entry_price": str(entry),
                "mark_price": str(self.option_mark),
                "unrealized_pnl": str(pnl),
                "unrealized_cashflow": str(pnl),
            }
        )

    def get_active_orders(self, product_ids: list[int] | None = None) -> list[ExchangeOrder]:
        result = []
        for raw in self.orders.values():
            if raw.get("state") in {"open", "pending"}:
                order = ExchangeOrder.from_raw(raw)
                if not product_ids or order.product_id in product_ids:
                    result.append(order)
        return result

    def get_order(self, order_id: str) -> ExchangeOrder | None:
        for raw in self.orders.values():
            if str(raw.get("id")) == str(order_id):
                return ExchangeOrder.from_raw(raw)
        return None

    def get_order_by_client_order_id(self, client_order_id: str) -> ExchangeOrder | None:
        raw = self.orders.get(client_order_id)
        return ExchangeOrder.from_raw(raw) if raw else None

    def place_order(self, request: OrderRequest) -> ExchangeOrder:
        if request.client_order_id in self.orders:
            return ExchangeOrder.from_raw(self.orders[request.client_order_id])
        self.place_calls.append(request)
        order_id = self.order_counter
        self.order_counter += 1
        price = (
            self.option_ask
            if request.side == "buy"
            else self.option_bid
        )
        if request.order_type == "limit_order" and request.limit_price:
            price = min(request.limit_price, self.option_ask) if request.side == "buy" else max(
                request.limit_price, self.option_bid
            )
        raw = {
            "id": str(order_id),
            "client_order_id": request.client_order_id,
            "product_id": request.product_id,
            "product_symbol": request.product_symbol,
            "side": request.side,
            "size": request.size,
            "unfilled_size": request.size if self.hold_orders else 0,
            "average_fill_price": str(price) if not self.hold_orders else None,
            "limit_price": str(request.limit_price) if request.limit_price else None,
            "state": "open" if self.hold_orders else "closed",
            "order_type": request.order_type,
            "reduce_only": request.reduce_only,
            "created_at": int(self.now.timestamp() * 1_000_000),
            "updated_at": int(self.now.timestamp() * 1_000_000),
        }
        self.orders[request.client_order_id] = raw
        if not self.hold_orders:
            self._apply_fill(request, price)
        return ExchangeOrder.from_raw(raw)

    def _apply_fill(self, request: OrderRequest, price: Decimal) -> None:
        product_id = request.product_id
        position = self.positions.setdefault(
            product_id,
            {"symbol": request.product_symbol, "size": 0, "entry_price": "0"},
        )
        if request.side == "buy":
            old_size = int(position["size"])
            old_entry = Decimal(position["entry_price"])
            new_size = old_size + request.size
            new_entry = (old_entry * old_size + price * request.size) / new_size
            position["size"] = new_size
            position["entry_price"] = str(new_entry)
        else:
            position["size"] = max(0, int(position["size"]) - request.size)
            if position["size"] == 0:
                position["entry_price"] = "0"

    def fill_open_orders(self) -> None:
        for _cid, raw in list(self.orders.items()):
            if raw.get("state") != "open":
                continue
            request_like = SimpleOrderRequest(raw)
            price = self.option_ask if raw["side"] == "buy" else self.option_bid
            raw["unfilled_size"] = 0
            raw["average_fill_price"] = str(price)
            raw["state"] = "closed"
            self._apply_fill(request_like, price)

    def cancel_order(
        self,
        *,
        product_id: int,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> ExchangeOrder | None:
        self.cancel_calls.append(
            {"product_id": product_id, "order_id": order_id, "client_order_id": client_order_id}
        )
        for raw in self.orders.values():
            if (order_id and str(raw.get("id")) == str(order_id)) or (
                client_order_id and raw.get("client_order_id") == client_order_id
            ):
                if raw.get("state") in {"open", "pending"}:
                    raw["state"] = "cancelled"
                    raw["cancellation_reason"] = "cancelled_by_user"
                return ExchangeOrder.from_raw(raw)
        return None

    def health_check(self) -> dict[str, Any]:
        return {"mode": "fake", "spot": str(self.spot)}


@dataclass
class SimpleOrderRequest:
    raw: dict[str, Any]

    @property
    def product_id(self) -> int:
        return int(self.raw["product_id"])

    @property
    def product_symbol(self) -> str:
        return str(self.raw["product_symbol"])

    @property
    def side(self) -> str:
        return str(self.raw["side"])

    @property
    def size(self) -> int:
        return int(self.raw["size"])


@dataclass
class Harness:
    config: BotConfig
    clock: FrozenClock
    broker: FakeBroker
    state: StateStore
    notifier: TestNotifier
    engine: BotEngine
    tmp: Path


def make_harness(
    tmp: Path,
    *,
    now: datetime = TEST_NOW,
    spot: str = "80200",
    **config_overrides: str,
) -> Harness:
    config = make_config(tmp, **config_overrides)
    clock = FrozenClock(now)
    broker = FakeBroker(now=now, spot=spot)
    state = StateStore(config.state_db)
    notifier = TestNotifier()
    engine = BotEngine(config, broker, state, notifier, clock=clock)
    return Harness(config, clock, broker, state, notifier, engine, tmp)
