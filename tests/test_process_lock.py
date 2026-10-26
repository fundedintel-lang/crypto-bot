import tempfile
import unittest
from pathlib import Path

from btc_options_bot.process_lock import SingleInstanceLock


class ProcessLockTests(unittest.TestCase):
    def test_second_process_cannot_hold_same_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bot.lock"
            with SingleInstanceLock(path):
                self.assertTrue(path.exists())
                with self.assertRaises(RuntimeError), SingleInstanceLock(path):
                    pass
            # Lock is released after the context manager.
            with SingleInstanceLock(path):
                pass


if __name__ == "__main__":
    unittest.main()
