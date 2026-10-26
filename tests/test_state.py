import tempfile
import unittest
from pathlib import Path

from btc_options_bot.order_ids import client_order_id
from btc_options_bot.state import CycleState, OrderRecord, StateStore


class StateTests(unittest.TestCase):
    def test_cycle_and_order_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.db")
            cycle = CycleState(
                cycle_id="20261026",
                session_date="2026-10-26",
                direction="CE",
                product_id=1001,
                product_symbol="C-BTC-80000-271026",
                exit_order_ids=["a", "b"],
            )
            state.save_cycle(cycle)
            loaded = state.get_cycle("20261026")
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.direction, "CE")
            self.assertEqual(loaded.exit_order_ids, ["a", "b"])
            order = OrderRecord(
                client_order_id="abc",
                cycle_id="20261026",
                action="entry",
                size=38,
                filled_size=38,
                status="FILLED",
            )
            state.save_order(order)
            self.assertEqual(state.get_order("abc").filled_size, 38)
            self.assertEqual(len(state.list_orders_for_cycle("20261026")), 1)
            self.assertEqual(len(state.list_active_cycles()), 1)

    def test_decision_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.db")
            self.assertTrue(state.set_decision_if_absent("2026-10-26", "skip", "flat"))
            self.assertFalse(state.set_decision_if_absent("2026-10-26", "entered", "CE"))
            self.assertEqual(state.get_decision("2026-10-26")["decision"], "skip")

    def test_events_runtime_and_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = StateStore(root / "state.db")
            state.add_event("TRADE", "Test", "message", {"x": 1})
            state.set_runtime("last_tick", "now")
            self.assertEqual(state.get_runtime("last_tick"), "now")
            self.assertEqual(state.list_events()[0]["title"], "Test")
            target = state.backup(root / "backup.db")
            self.assertTrue(target.exists())
            self.assertTrue(StateStore(target).health_check())

    def test_client_order_ids_are_deterministic_and_short(self):
        first = client_order_id("20261026", "entry")
        second = client_order_id("20261026", "entry")
        third = client_order_id("20261026", "exit", attempt=1)
        self.assertEqual(first, second)
        self.assertNotEqual(first, third)
        self.assertLessEqual(len(first), 32)


if __name__ == "__main__":
    unittest.main()
