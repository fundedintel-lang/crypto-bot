import hashlib
import hmac
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from btc_options_bot.delta_client import SignedHttpClient
from btc_options_bot.errors import DeltaAPIError


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200, headers: dict[str, str] | None = None):
        self.status = status
        self.headers = headers or {}
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class CaptureOpener:
    def __init__(self, response: FakeResponse | None = None):
        self.response = response or FakeResponse(b'{"success":true,"result":{"ok":true},"meta":{}}')
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        return self.response


class SequenceOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def http_error(status: int, body: dict) -> urllib.error.HTTPError:
    raw = json.dumps(body).encode("utf-8")
    return urllib.error.HTTPError(
        "https://example.invalid/v2/orders",
        status,
        "error",
        {"Content-Type": "application/json"},
        io.BytesIO(raw),
    )


class DeltaClientTests(unittest.TestCase):
    def test_signature_prehash_includes_method_timestamp_path_query_and_body(self):
        opener = CaptureOpener()
        client = SignedHttpClient(
            base_url="https://example.invalid",
            api_key="key",
            api_secret="secret",
            opener=opener,
            min_request_interval=0,
        )
        body = {"size": 3, "side": "buy", "product_id": 16}
        response = client.request(
            "POST",
            "/v2/orders",
            query={"state": "open", "product_id": 1},
            body=body,
            auth=True,
        )
        self.assertTrue(response.raw["success"])
        request = opener.requests[0]
        self.assertEqual(request.get_header("Api-key"), "key")
        timestamp = request.get_header("Timestamp")
        signature = request.get_header("Signature")
        body_string = request.data.decode("utf-8")
        self.assertEqual(body_string, '{"product_id":16,"side":"buy","size":3}')
        self.assertTrue(request.full_url.startswith("https://example.invalid/v2/orders?state=open&product_id=1"))
        query_string = "?" + request.full_url.split("?", 1)[1]
        prehash = "POST" + timestamp + "/v2/orders" + query_string + body_string
        expected = hmac.new(b"secret", prehash.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(signature, expected)
        self.assertEqual(request.get_header("User-agent"), "btc-options-bot/1.0 (python-urllib)")

    def test_public_requests_omit_auth_headers(self):
        opener = CaptureOpener()
        client = SignedHttpClient(
            base_url="https://example.invalid",
            api_key="key",
            api_secret="secret",
            opener=opener,
            min_request_interval=0,
        )
        client.request("GET", "/v2/tickers/BTCUSD", auth=False)
        request = opener.requests[0]
        self.assertIsNone(request.get_header("Api-key"))
        self.assertIsNone(request.get_header("Signature"))

    def test_retryable_429_is_retried(self):
        opener = SequenceOpener(
            [
                http_error(429, {"success": False, "error": {"code": "too_many_requests"}}),
                FakeResponse(b'{"success":true,"result":{"retried":true},"meta":{}}'),
            ]
        )
        client = SignedHttpClient(
            base_url="https://example.invalid",
            api_key="key",
            api_secret="secret",
            opener=opener,
            retries=1,
            min_request_interval=0,
        )
        with patch("btc_options_bot.delta_client.time.sleep"):
            response = client.request("GET", "/v2/products", auth=False)
        self.assertEqual(response.result, {"retried": True})
        self.assertEqual(len(opener.requests), 2)

    def test_not_found_can_be_suppressed(self):
        opener = SequenceOpener(
            [http_error(404, {"success": False, "error": {"code": "order_not_found"}})]
        )
        client = SignedHttpClient(
            base_url="https://example.invalid",
            api_key="key",
            api_secret="secret",
            opener=opener,
            retries=0,
            min_request_interval=0,
        )
        response = client.request(
            "GET", "/v2/orders/client_order_id/missing", auth=True, allow_not_found=True
        )
        self.assertIsNone(response.result)

    def test_non_retryable_api_error_is_raised(self):
        opener = SequenceOpener(
            [http_error(400, {"success": False, "error": {"code": "insufficient_margin"}})]
        )
        client = SignedHttpClient(
            base_url="https://example.invalid",
            api_key="key",
            api_secret="secret",
            opener=opener,
            retries=3,
            min_request_interval=0,
        )
        with self.assertRaises(DeltaAPIError) as context:
            client.request("POST", "/v2/orders", body={}, auth=True)
        self.assertEqual(context.exception.code, "insufficient_margin")
        self.assertEqual(len(opener.requests), 1)


if __name__ == "__main__":
    unittest.main()
