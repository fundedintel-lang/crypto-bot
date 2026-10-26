from __future__ import annotations

from typing import Any, Protocol

from .models import (
    Candle,
    ExchangeOrder,
    Instrument,
    OrderBook,
    OrderRequest,
    Position,
    Ticker,
    WalletSnapshot,
)


class Broker(Protocol):
    """Exchange adapter used by the strategy engine."""

    name: str

    def get_ticker(self, symbol: str) -> Ticker: ...

    def get_candles(
        self,
        symbol: str,
        resolution: str,
        start: int,
        end: int,
    ) -> list[Candle]: ...

    def list_option_products(self) -> list[Instrument]: ...

    def get_option_chain(self, underlying: str, expiry_date: str) -> dict[int, Ticker]: ...

    def get_product_by_symbol(self, symbol: str) -> Instrument: ...

    def get_order_book(self, symbol: str) -> OrderBook: ...

    def get_wallet_balances(self) -> WalletSnapshot: ...

    def get_position(self, product_id: int) -> Position | None: ...

    def get_positions_margined(self, product_ids: list[int] | None = None) -> list[Position]: ...

    def get_active_orders(self, product_ids: list[int] | None = None) -> list[ExchangeOrder]: ...

    def get_order(self, order_id: str) -> ExchangeOrder | None: ...

    def get_order_by_client_order_id(self, client_order_id: str) -> ExchangeOrder | None: ...

    def place_order(self, request: OrderRequest) -> ExchangeOrder: ...

    def cancel_order(
        self,
        *,
        product_id: int,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> ExchangeOrder | None: ...

    def health_check(self) -> dict[str, Any]: ...
