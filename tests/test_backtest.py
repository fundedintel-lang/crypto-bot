import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKTEST_PATH = ROOT / "research" / "backtest.py"


def load_backtest_module():
    spec = importlib.util.spec_from_file_location("research_backtest", BACKTEST_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class BacktestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bt = load_backtest_module()

    def test_black_scholes_respects_intrinsic_value(self):
        call = self.bt.bs_price(100, 90, 0.1, "CE", sigma=0.3)
        put = self.bt.bs_price(100, 110, 0.1, "PE", sigma=0.3)
        self.assertGreaterEqual(call, 10)
        self.assertGreaterEqual(put, 10)

    def test_quantity_uses_integer_contracts_and_fee_buffer(self):
        qty = self.bt.quantity_for(20, 500)
        self.assertIsInstance(qty, int)
        self.assertGreater(qty, 0)
        self.assertLessEqual(qty * 500 * self.bt.CONTRACT_VALUE * (1 + self.bt.FEE_RATE), 20)

    def test_bundled_backtest_produces_seven_cycles(self):
        result = self.bt.run()
        summary = result["summary"]
        self.assertEqual(summary["total_cycles"], 7)
        self.assertEqual(summary["initial_entries"], 7)
        self.assertEqual(summary["exit_orders"], 7)
        self.assertFalse(summary["stop_loss"])
        self.assertEqual(len(result["trades"]), 7)


if __name__ == "__main__":
    unittest.main()
