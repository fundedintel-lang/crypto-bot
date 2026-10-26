import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from btc_options_bot import cli
from btc_options_bot.config import LIVE_CONFIRMATION
from tests.support import make_config


class CLITests(unittest.TestCase):
    def test_status_pause_resume_work_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = make_config(root)
            env_file = root / ".env"
            env_file.write_text(
                "\n".join(
                    [
                        "BOT_MODE=paper",
                        "VENUE=india",
                        f"DATA_DIR={config.data_dir}",
                        f"LOG_DIR={config.log_dir}",
                        f"STATE_DB={config.state_db}",
                        f"PAUSE_FILE={config.pause_file}",
                        f"KILL_FILE={config.kill_file}",
                        "WEB_ENABLED=false",
                    ]
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(env_file=str(env_file), mode=None)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(cli.cmd_status(args), 0)
            status = json.loads(output.getvalue())
            self.assertEqual(status["mode"], "paper")
            self.assertFalse(status["paused"])

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.cmd_pause(args), 0)
                self.assertEqual(cli.cmd_resume(args), 0)
            self.assertFalse(config.pause_file.exists())
            self.assertFalse(config.kill_file.exists())

    @patch("builtins.input", side_effect=["live", "india", "key", "net_equity", "1000", "no"])
    @patch("btc_options_bot.cli.getpass.getpass", return_value="secret")
    def test_init_creates_live_confirmation_and_private_env(self, _getpass, _input):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            args = argparse.Namespace(env_file=str(env_file))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(cli.cmd_init(args), 0)
            text = env_file.read_text(encoding="utf-8")
            self.assertIn("BOT_MODE=live", text)
            self.assertIn(f"LIVE_TRADING_CONFIRMATION={LIVE_CONFIRMATION}", text)
            self.assertIn("DELTA_API_KEY=key", text)
            self.assertIn("DELTA_API_SECRET=secret", text)


if __name__ == "__main__":
    unittest.main()
