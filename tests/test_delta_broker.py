import json
import tempfile
import unittest
import urllib.parse
from decimal import Decimal

from btc_options_bot.delta_client import DeltaBroker, SignedHttpClient
from btc_options_bot.models import OrderRequest
from tests.support import TEST_NOW, make_config


class FakeResponse:
    def __init__(self, payload: dict):
        self.status = 200
        self.headers = {}
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class PathOpener:
    def __init__(self):
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        path = urllib.parse.urlparse(request.full_url).path
        if path == "/v2/tickers/BTCUSD":
            payload = {
                "success": True,
                "result": {
                    "product_id": 27,
                    "symbol": "BTCUSD",
                    "close": 80200,
                    "mark_price": "80200",
                    "spot_price": "80200",
                    "quotes": {"best_ask": "80201", "best_bid": "80199"},
                    "tick_size": "0.5",
                    "timestamp": int(TEST_NOW.timestamp() * 1_000_000),
                    "product_trading_status": "operational",
                },
                "meta": {},
            }
        elif path == "/v2/wallet/balances":
            payload = {
                "success": True,
                "result": [{"asset_symbol": "USD", "balance": "1000", "available_balance": "950"}],
                "meta": {"net_equity": "1000"},
            }
        elif path == "/v2/positions/margined":
            payload = {
                "success": True,
                "result": [
                    {
                        "product_id": 123,
                        "product_symbol": "C-BTC-80000-271026",
                        "size": 10,
                        "entry_price": "500",
                        "mark_price": "480",
                        "unrealized_pnl": "-0.2",
                        "unrealized_cashflow": "-0.2",
                    }
                ],
                "meta": {},
            }
        elif path == "/v2/orders":
            if request.get_method() == "POST":
                sent = json.loads(request.data.decode("utf-8"))
                payload = {
                    "success": True,
                    "result": {
                        "id": 99,
                        "client_order_id": sent["client_order_id"],
                        "product_id": sent["product_id"],
                        "product_symbol": "C-BTC-80000-271026",
                        "side": sent["side"],
                        "size": sent["size"],
                        "unfilled_size": sent["size"],
                        "state": "open",
                        "order_type": sent["order_type"],
                        "limit_price": sent.get("limit_price"),
                        "reduce_only": sent.get("reduce_only", False),
                    },
                    "meta": {},
                }
            else:
                payload = {
                    "success": True,
                    "result": {
                        "id": 99,
                        "client_order_id": "abc",
                        "product_id": 123,
                        "product_symbol": "C-BTC-80000-271026",
                        "side": "buy",
                        "size": 10,
                        "unfilled_size": 10,
                        "state": "cancelled",
                        "order_type": "limit_order",
                        "limit_price": "500",
                        "reduce_only": False,
                    },
                    "meta": {},
                }
        else:
            payload = {"success": True, "result": None, "meta": {}}
        return FakeResponse(payload)


class DeltaBrokerTests(unittest.TestCase):
    def test_public_and_private_endpoints_parse_and_sign(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(
                tmp,
                BOT_MODE="testnet",
                DELTA_API_KEY="key",
                DELTA_API_SECRET="secret",
            )
            opener = PathOpener()
            http = SignedHttpClient(
                base_url=config.base_url,
                api_key=config.api_key,
                api_secret=config.api_secret,
                opener=opener,
                min_request_interval=0,
            )
            broker = DeltaBroker(config, http)

            ticker = broker.get_ticker("BTCUSD")
            self.assertEqual(ticker.spot_price, Decimal("80200"))
            self.assertIsNone(opener.requests[-1].get_header("Signature"))

            wallet = broker.get_wallet_balances()
            self.assertEqual(wallet.net_equity, Decimal("1000"))
            self.assertIsNotNone(opener.requests[-1].get_header("Signature"))

            position = broker.get_position(123)
            self.assertIsNotNone(position)
            assert position is not None
            self.assertEqual(position.size, 10)
            self.assertEqual(position.unrealized_pnl, Decimal("-0.2"))

            order = broker.place_order(
                OrderRequest(
                    client_order_id="abc",
                    product_id=123,
                    product_symbol="C-BTC-80000-271026",
                    side="buy",
                    size=10,
                    limit_price=Decimal("500"),
                )
            )
            self.assertEqual(order.id, "99")
            self.assertEqual(order.state, "open")
            posted = json.loads(opener.requests[-1].data.decode("utf-8"))
            self.assertEqual(posted["side"], "buy")
            self.assertEqual(posted["size"], 10)
            self.assertEqual(posted["limit_price"], "500")
            self.assertFalse(posted["reduce_only"])

            cancelled = broker.cancel_order(product_id=123, order_id="99")
            self.assertIsNotNone(cancelled)
            assert cancelled is not None
            self.assertEqual(cancelled.state, "cancelled")
            self.assertEqual(opener.requests[-1].get_method(), "DELETE")


if __name__ == "__main__":
    unittest.main()
