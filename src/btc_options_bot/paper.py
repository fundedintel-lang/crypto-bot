from __future__ import annotations

import json
import os
import threading
from decimal import Decimal
from pathlib import Path
from typing import Any

from .config import BotConfig
from .delta_client import DeltaBroker
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
from .utils import decimal_str, iso_utc, to_decimal, utc_now


class PaperBroker:
    """Paper adapter: real public market data, simulated fills and wallet."""

    name = "paper"

    def __init__(self, config: BotConfig, public_broker: DeltaBroker | None = None) -> None:
        self.config = config
        self.public = public_broker or DeltaBroker(config)
        self.state_file = Path(config.paper_state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._state = self._load()

    def _default_state(self) -> dict[str, Any]:
        return {
            "version": 1,
            "initial_cash": decimal_str(self.config.paper_capital),
            "cash": decimal_str(self.config.paper_capital),
            "realized_pnl": "0",
            "positions": {},
            "orders": {},
            "next_order_id": 1,
            "created_at": iso_utc(utc_now()),
            "updated_at": iso_utc(utc_now()),
        }

    def _load(self) -> dict[str, Any]:
        if not self.state_file.exists():
            state = self._default_state()
            self._write(state)
            return state
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = self._default_state()
            self._write(state)
        state.setdefault("positions", {})
        state.setdefault("orders", {})
        state.setdefault("next_order_id", 1)
        state.setdefault("realized_pnl", "0")
        return state

    def _write(self, state: dict[str, Any]) -> None:
        state["updated_at"] = iso_utc(utc_now())
        temp = self.state_file.with_suffix(".tmp")
        temp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temp, self.state_file)

    def _save(self) -> None:
        self._write(self._state)

    def reset(self) -> None:
        with self._lock:
            self._state = self._default_state()
            self._save()

    # --------------------------------------------------------------- public data
    def get_ticker(self, symbol: str) -> Ticker:
        return self.public.get_ticker(symbol)

    def get_candles(
        self,
        symbol: str,
        resolution: str,
        start: int,
        end: int,
    ) -> list[Candle]:
        return self.public.get_candles(symbol, resolution, start, end)

    def list_option_products(self) -> list[Instrument]:
        return self.public.list_option_products()

    def get_option_chain(self, underlying: str, expiry_date: str) -> dict[int, Ticker]:
        return self.public.get_option_chain(underlying, expiry_date)

    def get_product_by_symbol(self, symbol: str) -> Instrument:
        return self.public.get_product_by_symbol(symbol)

    def get_order_book(self, symbol: str) -> OrderBook:
        return self.public.get_order_book(symbol)

    # -------------------------------------------------------------- private data
    def _mark_price(self, product_symbol: str, fallback: Decimal) -> Decimal:
        try:
            ticker = self.public.get_ticker(product_symbol)
            candidate = ticker.mark_price or ticker.mid_price or ticker.close
            return candidate if candidate > 0 else fallback
        except Exception:
            return fallback

    def get_position(self, product_id: int) -> Position | None:
        with self._lock:
            raw = self._state["positions"].get(str(product_id))
        if not raw or int(raw.get("size", 0)) == 0:
            return None
        entry_price = to_decimal(raw["entry_price"])
        size = int(raw["size"])
        contract_value = to_decimal(raw["contract_value"])
        mark_price = self._mark_price(raw["product_symbol"], entry_price)
        unrealized = (mark_price - entry_price) * Decimal(size) * contract_value
        return Position(
            product_id=product_id,
            product_symbol=str(raw["product_symbol"]),
            size=size,
            entry_price=entry_price,
            mark_price=mark_price,
            unrealized_pnl=unrealized,
            unrealized_cashflow=unrealized,
            raw=dict(raw),
        )

    def get_positions_margined(self, product_ids: list[int] | None = None) -> list[Position]:
        with self._lock:
            ids = [str(product_id) for product_id in (product_ids or self._state["positions"].keys())]
        positions: list[Position] = []
        for item in ids:
            position = self.get_position(int(item))
            if position and position.is_open:
                positions.append(position)
        return positions

    def get_wallet_balances(self) -> WalletSnapshot:
        cash = to_decimal(self._state["cash"])
        equity = cash
        for position in self.get_positions_margined():
            raw = position.raw
            contract_value = to_decimal(raw["contract_value"])
            equity += position.mark_price * Decimal(position.size) * contract_value
        balances = [
            {
                "asset_symbol": self.config.capital_asset,
                "balance": decimal_str(equity),
                "available_balance": decimal_str(cash),
            }
        ]
        return WalletSnapshot.from_response(
            balances,
            {"net_equity": decimal_str(equity)},
        )

    def get_active_orders(self, product_ids: list[int] | None = None) -> list[ExchangeOrder]:
        with self._lock:
            raws = list(self._state["orders"].values())
        orders = [ExchangeOrder.from_raw(raw) for raw in raws]
        return [order for order in orders if order.is_working]

    def get_order(self, order_id: str) -> ExchangeOrder | None:
        with self._lock:
            for raw in self._state["orders"].values():
                if str(raw.get("id")) == str(order_id):
                    return ExchangeOrder.from_raw(raw)
        return None

    def get_order_by_client_order_id(self, client_order_id: str) -> ExchangeOrder | None:
        with self._lock:
            raw = self._state["orders"].get(client_order_id)
        return ExchangeOrder.from_raw(raw) if raw else None

    def _fill_price(self, request: OrderRequest) -> Decimal:
        if request.limit_price is not None:
            # Simulate a realistic fill at the current touch, not the aggressive limit.
            try:
                ticker = self.public.get_ticker(request.product_symbol)
                touch = ticker.best_ask if request.side == "buy" else ticker.best_bid
                if touch > 0:
                    if request.side == "buy":
                        return min(request.limit_price, touch)
                    return max(request.limit_price, touch)
            except Exception:
                pass
            return request.limit_price
        ticker = self.public.get_ticker(request.product_symbol)
        return ticker.best_ask if request.side == "buy" else ticker.best_bid

    def place_order(self, request: OrderRequest) -> ExchangeOrder:
        existing = self.get_order_by_client_order_id(request.client_order_id)
        if existing is not None:
            return existing

        fill_price = self._fill_price(request)
        if fill_price <= 0:
            raise ValueError("paper order has no valid fill price")
        contract_value = request.contract_value or Decimal("0.001")
        fee_rate = to_decimal(request.metadata.get("fee_rate", 0), default=Decimal("0"))
        notional = fill_price * Decimal(request.size) * contract_value
        fee = notional * fee_rate

        with self._lock:
            state = self._state
            cash = to_decimal(state["cash"])
            realized = to_decimal(state.get("realized_pnl", 0))
            key = str(request.product_id)
            position = dict(state["positions"].get(key, {}))
            if request.side == "buy":
                cash -= notional + fee
                old_size = int(position.get("size", 0))
                old_entry = to_decimal(position.get("entry_price", 0))
                new_size = old_size + request.size
                new_entry = (
                    (old_entry * Decimal(old_size) + fill_price * Decimal(request.size))
                    / Decimal(new_size)
                    if new_size
                    else Decimal("0")
                )
                position = {
                    "product_id": request.product_id,
                    "product_symbol": request.product_symbol,
                    "size": new_size,
                    "entry_price": decimal_str(new_entry),
                    "contract_value": decimal_str(contract_value),
                }
                state["positions"][key] = position
            elif request.side == "sell":
                current_size = int(position.get("size", 0))
                if request.reduce_only and request.size > current_size:
                    raise ValueError("paper reduce-only order exceeds simulated position")
                cash += notional - fee
                entry = to_decimal(position.get("entry_price", 0))
                realized += (fill_price - entry) * Decimal(request.size) * contract_value - fee
                remaining = current_size - request.size
                if remaining <= 0:
                    state["positions"].pop(key, None)
                else:
                    position["size"] = remaining
                    state["positions"][key] = position
            else:
                raise ValueError(f"unsupported order side: {request.side}")

            state["cash"] = decimal_str(cash)
            state["realized_pnl"] = decimal_str(realized)
            order_id = int(state["next_order_id"])
            state["next_order_id"] = order_id + 1
            now_us = int(utc_now().timestamp() * 1_000_000)
            raw_order = {
                "id": f"paper-{order_id}",
                "client_order_id": request.client_order_id,
                "product_id": request.product_id,
                "product_symbol": request.product_symbol,
                "side": request.side,
                "size": request.size,
                "unfilled_size": 0,
                "average_fill_price": decimal_str(fill_price),
                "limit_price": decimal_str(request.limit_price) if request.limit_price else None,
                "state": "closed",
                "order_type": request.order_type,
                "reduce_only": request.reduce_only,
                "created_at": str(now_us),
                "updated_at": str(now_us),
                "paid_commission": decimal_str(fee),
            }
            state["orders"][request.client_order_id] = raw_order
            self._save()
        return ExchangeOrder.from_raw(raw_order)

    def cancel_order(
        self,
        *,
        product_id: int,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> ExchangeOrder | None:
        with self._lock:
            key = client_order_id
            if key is None and order_id:
                for cid, raw in self._state["orders"].items():
                    if str(raw.get("id")) == str(order_id):
                        key = cid
                        break
            if not key or key not in self._state["orders"]:
                return None
            raw = self._state["orders"][key]
            if raw.get("state") in {"open", "pending"}:
                raw["state"] = "cancelled"
                raw["cancellation_reason"] = "cancelled_by_user"
                self._save()
            return ExchangeOrder.from_raw(raw)

    def health_check(self) -> dict[str, Any]:
        return {
            "base_url": self.config.base_url,
            "mode": "paper",
            "venue": self.config.venue,
            "wallet_authenticated": None,
            "paper_cash": self._state["cash"],
            "paper_realized_pnl": self._state.get("realized_pnl", "0"),
        }
