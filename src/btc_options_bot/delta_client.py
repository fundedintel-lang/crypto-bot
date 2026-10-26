from __future__ import annotations

import contextlib
import email.utils
import hashlib
import hmac
import json
import logging
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from .config import BotConfig
from .errors import DeltaAPIError, MarketDataError
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
from .utils import UTC, parse_timestamp

logger = logging.getLogger("btc_options_bot.delta")


@dataclass(frozen=True)
class APIResponse:
    result: Any
    meta: dict[str, Any]
    status_code: int
    headers: dict[str, str]
    raw: dict[str, Any]


class SignedHttpClient:
    """Thread-safe, retrying Delta REST transport with exact HMAC signing."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        api_secret: str = "",
        timeout: float = 15.0,
        retries: int = 4,
        min_request_interval: float = 0.05,
        opener: Any | None = None,
        user_agent: str = "btc-options-bot/1.0 (python-urllib)",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.timeout = timeout
        self.retries = retries
        self.min_request_interval = min_request_interval
        self.opener = opener or urllib.request.build_opener()
        self.user_agent = user_agent
        self._last_request = 0.0
        self._time_offset = 0.0
        self._lock = threading.RLock()

    @staticmethod
    def generate_signature(secret: str, message: str) -> str:
        return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()

    def _encode_query(self, query: Mapping[str, Any] | None) -> str:
        if not query:
            return ""
        pairs: list[tuple[str, str]] = []
        for key, value in query.items():
            if value is None:
                continue
            if isinstance(value, (list, tuple, set)):
                text = ",".join(str(item) for item in value)
            elif isinstance(value, bool):
                text = "true" if value else "false"
            elif isinstance(value, Decimal):
                text = format(value, "f")
            else:
                text = str(value)
            pairs.append((str(key), text))
        return urllib.parse.urlencode(pairs, doseq=False, safe=",.:", quote_via=urllib.parse.quote)

    def _wait_for_slot(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_request
            if elapsed < self.min_request_interval:
                time.sleep(self.min_request_interval - elapsed)
            self._last_request = time.monotonic()

    def _update_clock_skew(self, headers: dict[str, str]) -> None:
        date_header = headers.get("Date") or headers.get("date")
        if not date_header:
            return
        try:
            server_time = email.utils.parsedate_to_datetime(date_header)
            if server_time.tzinfo is None:
                server_time = server_time.replace(tzinfo=UTC)
            local = datetime.now(UTC)
            offset = (server_time - local).total_seconds()
            # HTTP Date has one-second resolution; only correct meaningful skew.
            if abs(offset) >= 2.0 and abs(offset) < 300:
                self._time_offset = 0.8 * self._time_offset + 0.2 * offset
        except (TypeError, ValueError):
            return

    def _headers(
        self,
        method: str,
        path: str,
        query_string: str,
        body: str,
        *,
        auth: bool,
    ) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": self.user_agent,
        }
        if not auth:
            return headers
        if not self.api_key or not self.api_secret:
            raise DeltaAPIError("API credentials are required for this request", code="missing_credentials")
        timestamp = str(int(time.time() + self._time_offset))
        prehash = method.upper() + timestamp + path + query_string + body
        headers.update(
            {
                "api-key": self.api_key,
                "timestamp": timestamp,
                "signature": self.generate_signature(self.api_secret, prehash),
            }
        )
        return headers

    def _parse_response(
        self,
        status_code: int,
        body: bytes,
        headers: dict[str, str],
        *,
        allow_not_found: bool,
    ) -> APIResponse:
        self._update_clock_skew(headers)
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DeltaAPIError(
                f"invalid JSON response from Delta (HTTP {status_code})",
                status_code=status_code,
                code="invalid_json",
                payload=body[:500],
            ) from exc

        if status_code >= 400 or payload.get("success") is False:
            error = payload.get("error") or {}
            if isinstance(error, str):
                code = error
                message = error
            else:
                code = str(error.get("code", "unknown_error"))
                message = str(error.get("message") or error.get("code") or payload)
            error_payload = dict(payload)
            error_payload["_headers"] = headers
            api_error = DeltaAPIError(
                f"Delta API error: {code}: {message}",
                status_code=status_code,
                code=code,
                payload=error_payload,
            )
            if allow_not_found and api_error.is_not_found:
                return APIResponse(None, {}, status_code, headers, payload)
            raise api_error

        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        return APIResponse(payload.get("result"), meta, status_code, headers, payload)

    def _send_once(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body_bytes: bytes | None,
        *,
        allow_not_found: bool,
    ) -> APIResponse:
        request = urllib.request.Request(
            url,
            data=body_bytes,
            headers=headers,
            method=method.upper(),
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                status = int(getattr(response, "status", getattr(response, "getcode", lambda: 200)()))
                response_headers = dict(response.headers.items()) if hasattr(response, "headers") else {}
                raw = response.read()
                return self._parse_response(
                    status, raw, response_headers, allow_not_found=allow_not_found
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            response_headers = dict(exc.headers.items()) if hasattr(exc, "headers") else {}
            return self._parse_response(
                exc.code, raw, response_headers, allow_not_found=allow_not_found
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DeltaAPIError(
                f"network error calling Delta: {exc}",
                status_code=0,
                code="network_error",
            ) from exc

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Mapping[str, Any] | None = None,
        auth: bool = True,
        allow_not_found: bool = False,
    ) -> APIResponse:
        encoded_query = self._encode_query(query)
        query_string = f"?{encoded_query}" if encoded_query else ""
        body_string = json.dumps(body or {}, separators=(",", ":"), sort_keys=True) if body is not None else ""
        body_bytes = body_string.encode("utf-8") if body is not None else None

        last_error: DeltaAPIError | None = None
        for attempt in range(self.retries + 1):
            self._wait_for_slot()
            # Rebuild auth headers on every attempt; signatures are valid for only 5 seconds.
            headers = self._headers(method, path, query_string, body_string, auth=auth)
            url = f"{self.base_url}{path}{query_string}"
            try:
                return self._send_once(
                    method, url, headers, body_bytes, allow_not_found=allow_not_found
                )
            except DeltaAPIError as exc:
                last_error = exc
                if not exc.is_retryable or attempt >= self.retries:
                    raise
                delay = min(4.0, 0.25 * (2**attempt)) + random.uniform(0.0, 0.15)
                response_headers = (
                    exc.payload.get("_headers") or {} if isinstance(exc.payload, dict) else {}
                )
                reset_value = response_headers.get("X-RATE-LIMIT-RESET")
                if reset_value:
                    with contextlib.suppress(TypeError, ValueError):
                        delay = max(delay, min(5.0, float(reset_value) / 1000.0))
                logger.warning(
                    "Delta request failed (attempt %d/%d), retrying in %.2fs: %s",
                    attempt + 1,
                    self.retries + 1,
                    delay,
                    exc,
                )
                time.sleep(delay)
        assert last_error is not None
        raise last_error


class DeltaBroker:
    """Live/testnet Delta Exchange REST adapter."""

    name = "delta"

    def __init__(self, config: BotConfig, http_client: SignedHttpClient | None = None) -> None:
        self.config = config
        self.http = http_client or SignedHttpClient(
            base_url=config.base_url,
            api_key=config.api_key,
            api_secret=config.api_secret,
            timeout=float(config.http_timeout_seconds),
            retries=config.http_retries,
        )
        self._product_cache: dict[int, Instrument] = {}

    @staticmethod
    def _path_symbol(symbol: str) -> str:
        return urllib.parse.quote(symbol, safe="-._")

    def get_ticker(self, symbol: str) -> Ticker:
        path = f"/v2/tickers/{self._path_symbol(symbol)}"
        response = self.http.request("GET", path, auth=False)
        result = response.result
        if isinstance(result, list):
            if not result:
                raise MarketDataError(f"no ticker for {symbol}")
            result = result[0]
        if not isinstance(result, dict):
            raise MarketDataError(f"no ticker for {symbol}")
        return Ticker.from_raw(result)

    def get_candles(
        self,
        symbol: str,
        resolution: str,
        start: int,
        end: int,
    ) -> list[Candle]:
        response = self.http.request(
            "GET",
            "/v2/history/candles",
            query={
                "resolution": resolution,
                "symbol": symbol,
                "start": int(start),
                "end": int(end),
            },
            auth=False,
        )
        result = response.result or []
        if not isinstance(result, list):
            raise MarketDataError(f"invalid candle response for {symbol}")
        return [Candle.from_raw(item) for item in result]

    def list_option_products(self) -> list[Instrument]:
        products: list[Instrument] = []
        after: str | None = None
        for _page in range(100):
            query: dict[str, Any] = {
                "contract_types": "call_options,put_options",
                "states": "live",
                "page_size": 100,
            }
            if after:
                query["after"] = after
            response = self.http.request("GET", "/v2/products", query=query, auth=False)
            batch = response.result or []
            if not isinstance(batch, list) or not batch:
                break
            for raw in batch:
                try:
                    product = Instrument.from_product(raw)
                except (KeyError, TypeError, ValueError):
                    continue
                products.append(product)
                self._product_cache[product.product_id] = product
            after = response.meta.get("after")
            if not after or len(batch) < 100:
                break
        else:
            logger.warning("option product pagination hit safety limit after %d pages", len(products))
        return products

    def get_option_chain(self, underlying: str, expiry_date: str) -> dict[int, Ticker]:
        response = self.http.request(
            "GET",
            "/v2/tickers",
            query={
                "contract_types": "call_options,put_options",
                "underlying_asset_symbols": underlying,
                "expiry_date": expiry_date,
            },
            auth=False,
        )
        result = response.result or []
        if isinstance(result, dict):
            result = [result]
        chain: dict[int, Ticker] = {}
        for raw in result:
            if not isinstance(raw, dict) or raw.get("product_id") in (None, 0):
                continue
            ticker = Ticker.from_raw(raw)
            chain[ticker.product_id] = ticker
        return chain

    def get_product_by_symbol(self, symbol: str) -> Instrument:
        path = f"/v2/products/{self._path_symbol(symbol)}"
        response = self.http.request("GET", path, auth=False)
        if not isinstance(response.result, dict):
            raise MarketDataError(f"no product for symbol {symbol}")
        product = Instrument.from_product(response.result)
        self._product_cache[product.product_id] = product
        return product

    def get_order_book(self, symbol: str) -> OrderBook:
        path = f"/v2/l2orderbook/{self._path_symbol(symbol)}"
        response = self.http.request("GET", path, auth=False)
        if not isinstance(response.result, dict):
            raise MarketDataError(f"no order book for {symbol}")
        return OrderBook.from_raw(response.result)

    def get_wallet_balances(self) -> WalletSnapshot:
        response = self.http.request("GET", "/v2/wallet/balances", auth=True)
        return WalletSnapshot.from_response(response.result, response.meta)

    def get_positions_margined(self, product_ids: list[int] | None = None) -> list[Position]:
        query: dict[str, Any] = {}
        if product_ids:
            query["product_ids"] = ",".join(str(item) for item in product_ids[:10])
        response = self.http.request("GET", "/v2/positions/margined", query=query, auth=True)
        result = response.result or []
        if isinstance(result, dict):
            result = [result]
        return [Position.from_raw(item) for item in result if isinstance(item, dict)]

    def get_position(self, product_id: int) -> Position | None:
        positions = self.get_positions_margined([product_id])
        for position in positions:
            if position.product_id == product_id and position.is_open:
                return position
        # Real-time endpoint catches positions that have not reached the margined API yet.
        response = self.http.request(
            "GET",
            "/v2/positions",
            query={"product_id": product_id},
            auth=True,
        )
        result = response.result
        if isinstance(result, list):
            result = result[0] if result else None
        if not isinstance(result, dict) or int(float(result.get("size", 0) or 0)) == 0:
            return None
        raw = dict(result)
        raw.setdefault("product_id", product_id)
        return Position.from_raw(raw)

    def get_active_orders(self, product_ids: list[int] | None = None) -> list[ExchangeOrder]:
        query: dict[str, Any] = {"states": "open,pending", "page_size": 100}
        if product_ids:
            query["product_ids"] = ",".join(str(item) for item in product_ids[:10])
        response = self.http.request("GET", "/v2/orders", query=query, auth=True)
        result = response.result or []
        if isinstance(result, dict):
            result = [result]
        return [ExchangeOrder.from_raw(item) for item in result if isinstance(item, dict)]

    def get_order(self, order_id: str) -> ExchangeOrder | None:
        path = f"/v2/orders/{urllib.parse.quote(str(order_id), safe='')}"
        response = self.http.request("GET", path, auth=True, allow_not_found=True)
        if not isinstance(response.result, dict):
            return None
        return ExchangeOrder.from_raw(response.result)

    def get_order_by_client_order_id(self, client_order_id: str) -> ExchangeOrder | None:
        path = (
            "/v2/orders/client_order_id/"
            + urllib.parse.quote(str(client_order_id), safe="")
        )
        response = self.http.request("GET", path, auth=True, allow_not_found=True)
        result = response.result
        if isinstance(result, list):
            result = result[0] if result else None
        if not isinstance(result, dict):
            return None
        return ExchangeOrder.from_raw(result)

    def place_order(self, request: OrderRequest) -> ExchangeOrder:
        try:
            response = self.http.request(
                "POST",
                "/v2/orders",
                body=request.to_api_payload(),
                auth=True,
            )
        except DeltaAPIError as exc:
            text = f"{exc.code} {exc}".lower()
            if "client_order_id" not in text and "duplicate" not in text:
                raise
            existing = self.get_order_by_client_order_id(request.client_order_id)
            if existing is None:
                raise
            return existing
        result = response.result
        if isinstance(result, list):
            result = result[0] if result else None
        if not isinstance(result, dict):
            raise DeltaAPIError("place_order returned no order", payload=response.raw)
        return ExchangeOrder.from_raw(result)

    def cancel_order(
        self,
        *,
        product_id: int,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> ExchangeOrder | None:
        if not order_id and not client_order_id:
            raise ValueError("either order_id or client_order_id is required")
        body: dict[str, Any] = {"product_id": int(product_id)}
        if order_id:
            body["id"] = int(order_id)
        else:
            body["client_order_id"] = client_order_id
        try:
            response = self.http.request(
                "DELETE",
                "/v2/orders",
                body=body,
                auth=True,
                allow_not_found=True,
            )
        except DeltaAPIError as exc:
            if exc.is_not_found:
                return None
            raise
        result = response.result
        if isinstance(result, list):
            result = result[0] if result else None
        if not isinstance(result, dict):
            return None
        return ExchangeOrder.from_raw(result)

    def health_check(self) -> dict[str, Any]:
        ticker = self.get_ticker(self.config.price_symbol)
        wallet_ok: bool | None = None
        wallet_equity: str | None = None
        if self.config.private_api_required:
            wallet = self.get_wallet_balances()
            wallet_ok = True
            wallet_equity = format(wallet.net_equity or Decimal("0"), "f")
        return {
            "base_url": self.config.base_url,
            "mode": self.config.mode,
            "venue": self.config.venue,
            "price_symbol": ticker.symbol,
            "spot_price": format(ticker.spot_price or ticker.close, "f"),
            "ticker_time": (
                parse_timestamp(ticker.raw.get("timestamp"), unit="us").isoformat()
                if parse_timestamp(ticker.raw.get("timestamp"), unit="us")
                else None
            ),
            "wallet_authenticated": wallet_ok,
            "wallet_net_equity": wallet_equity,
        }
