import socket
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from btc_options_bot.engine import BotEngine
from btc_options_bot.health import HealthServer
from btc_options_bot.state import StateStore
from tests.support import TEST_NOW, FakeBroker, TestNotifier, make_config


class HealthTests(unittest.TestCase):
    def test_dashboard_status_and_token_protected_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            probe = socket.socket()
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
            probe.close()
            config = make_config(
                tmp,
                WEB_ENABLED="true",
                WEB_HOST="127.0.0.1",
                WEB_PORT=str(port),
                WEB_TOKEN="secret",
            )
            state = StateStore(config.state_db)
            broker = FakeBroker(now=TEST_NOW)
            engine = BotEngine(config, broker, state, TestNotifier())  # type: ignore[arg-type]
            server = HealthServer(config, engine)
            server.start()
            try:
                base = f"http://127.0.0.1:{port}"
                response = None
                for _ in range(30):
                    try:
                        response = urllib.request.urlopen(base + "/healthz", timeout=1)
                        break
                    except urllib.error.URLError:
                        time.sleep(0.02)
                self.assertIsNotNone(response)
                assert response is not None
                self.assertEqual(response.status, 200)
                self.assertIn(b'"ok":true', response.read())

                with urllib.request.urlopen(base + "/api/status", timeout=2) as status_response:
                    self.assertEqual(status_response.status, 200)
                    self.assertIn(b'"mode":"paper"', status_response.read())

                unauthorized = urllib.request.Request(
                    base + "/api/pause", data=b"", method="POST"
                )
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(unauthorized, timeout=2)
                self.assertEqual(ctx.exception.code, 403)

                authorized = urllib.request.Request(
                    base + "/api/pause",
                    data=b"",
                    method="POST",
                    headers={"X-Web-Token": "secret"},
                )
                with urllib.request.urlopen(authorized, timeout=2) as control_response:
                    self.assertEqual(control_response.status, 200)
                self.assertTrue(config.pause_file.exists())
            finally:
                server.stop()


if __name__ == "__main__":
    unittest.main()
