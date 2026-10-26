import tempfile
import unittest
from pathlib import Path

from btc_options_bot.config import LIVE_CONFIRMATION, BotConfig
from btc_options_bot.errors import ConfigError
from tests.support import make_config


class ConfigTests(unittest.TestCase):
    def test_exact_strategy_defaults_are_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            self.assertEqual(config.ema_period, 21)
            self.assertEqual(config.candle_resolution, "1h")
            self.assertEqual(str(config.target_points), "1500")
            self.assertEqual(str(config.initial_capital_pct), "0.02")
            self.assertEqual(str(config.additional_capital_pct), "0.02")
            self.assertEqual(config.entry_time.strftime("%H:%M"), "18:30")
            self.assertEqual(config.additional_time.strftime("%H:%M"), "01:00")
            self.assertEqual(config.exit_time.strftime("%H:%M"), "17:20")
            self.assertTrue(config.no_stop_loss)

    def test_strategy_changes_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConfigError):
                make_config(tmp, TARGET_POINTS="1200")
            with self.assertRaises(ConfigError):
                make_config(tmp, NO_STOP_LOSS="false")
            with self.assertRaises(ConfigError):
                make_config(tmp, ENTRY_TIME_IST="18:31")

    def test_live_mode_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            values = {
                "BOT_MODE": "live",
                "VENUE": "india",
                "DELTA_API_KEY": "key",
                "DELTA_API_SECRET": "secret",
                "DATA_DIR": str(Path(tmp) / "data"),
                "LOG_DIR": str(Path(tmp) / "logs"),
                "WEB_ENABLED": "false",
            }
            with self.assertRaises(ConfigError):
                BotConfig.from_env(env_file=Path(tmp) / "none", environ=values)
            values["LIVE_TRADING_CONFIRMATION"] = LIVE_CONFIRMATION
            config = BotConfig.from_env(env_file=Path(tmp) / "none", environ=values)
            self.assertTrue(config.is_live)
            self.assertEqual(config.base_url, "https://api.india.delta.exchange")

    def test_secret_file_and_safe_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret_file = root / "secret.txt"
            secret_file.write_text("super-secret\n", encoding="utf-8")
            env_file = root / ".env"
            env_file.write_text(
                "BOT_MODE=testnet\nVENUE=global\nDELTA_API_KEY_FILE="
                f"{secret_file}\nDELTA_API_SECRET=abc\nWEB_ENABLED=false\n",
                encoding="utf-8",
            )
            config = BotConfig.from_env(env_file=env_file, environ={})
            self.assertEqual(config.api_secret, "abc")
            self.assertEqual(config.api_key, "super-secret")
            self.assertEqual(config.base_url, "https://testnet-api.delta.exchange")
            self.assertEqual(config.safe_dict()["api_key"], "***")
            self.assertEqual(config.safe_dict()["api_secret"], "***")


if __name__ == "__main__":
    unittest.main()
