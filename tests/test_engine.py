import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from btc_options_bot.engine import BotEngine
from tests.support import make_harness


class EngineTests(unittest.TestCase):
    def _open_initial_position(self, harness, *, spot="80200"):
        harness.engine.tick()
        harness.engine.tick()
        cycle = harness.state.get_cycle("20261026")
        self.assertIsNotNone(cycle)
        assert cycle is not None
        self.assertEqual(cycle.status, "ACTIVE")
        self.assertEqual(cycle.direction, "CE" if Decimal(spot) > 79700 else "PE")
        self.assertEqual(cycle.filled_quantity, 38)
        self.assertEqual(len(harness.broker.place_calls), 1)
        return cycle

    def test_ce_initial_entry_and_immediate_target_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = make_harness(Path(tmp))
            cycle = self._open_initial_position(h)
            self.assertEqual(cycle.target_price, "81700")
            self.assertEqual(cycle.initial_limit_price, "505")

            # 00:45 IST: position is in loss but it is not the 01:00 add window yet.
            now = h.clock.advance(timedelta(hours=6, minutes=15))
            h.broker.set_market(now=now, spot="80000", option_mark="480", option_ask="480", option_bid="470")
            h.engine.tick()
            self.assertEqual(len(h.broker.place_calls), 1)
            self.assertEqual(h.state.get_cycle("20261026").status, "ACTIVE")

            # BTC reaches entry + 1500; exit must happen immediately.
            h.broker.set_market(spot="81700", option_mark="600", option_ask="600", option_bid="590")
            h.engine.tick()
            h.engine.tick()
            closed = h.state.get_cycle("20261026")
            self.assertEqual(closed.status, "CLOSED")
            self.assertEqual(closed.exit_reason, "target_hit")
            self.assertEqual(closed.closed_quantity, 38)
            self.assertEqual(len(h.broker.place_calls), 2)
            self.assertEqual(h.broker.place_calls[1].side, "sell")
            self.assertTrue(h.broker.place_calls[1].reduce_only)

    def test_pe_loss_at_1am_adds_same_quantity_and_exits_at_1720(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = make_harness(Path(tmp), spot="79000")
            cycle = self._open_initial_position(h, spot="79000")
            self.assertEqual(cycle.target_price, "77500")

            # Exactly 01:00 IST on the next date; option premium has fallen => position loss.
            now = h.clock.advance(timedelta(hours=6, minutes=30))
            h.broker.set_market(
                now=now,
                spot="78500",
                option_mark="480",
                option_ask="480",
                option_bid="470",
            )
            h.engine.tick()
            h.engine.tick()
            cycle = h.state.get_cycle("20261026")
            self.assertTrue(cycle.additional_started)
            self.assertTrue(cycle.additional_done)
            self.assertEqual(cycle.initial_quantity, 38)
            self.assertEqual(cycle.additional_quantity, 38)
            self.assertEqual(cycle.filled_quantity, 76)
            self.assertEqual(len(h.broker.place_calls), 2)
            self.assertEqual(h.broker.place_calls[1].size, 38)
            self.assertEqual(h.broker.place_calls[1].side, "buy")

            # No target; mandatory 17:20 IST exit on the following day.
            now = h.clock.advance(timedelta(hours=16, minutes=20))
            h.broker.set_market(
                now=now,
                spot="78000",
                option_mark="500",
                option_ask="500",
                option_bid="490",
            )
            h.engine.tick()
            h.engine.tick()
            closed = h.state.get_cycle("20261026")
            self.assertEqual(closed.status, "CLOSED")
            self.assertEqual(closed.exit_reason, "time_exit")
            self.assertEqual(closed.closed_quantity, 76)
            self.assertEqual(len(h.broker.place_calls), 3)
            self.assertEqual(h.broker.place_calls[2].side, "sell")
            self.assertEqual(h.broker.place_calls[2].size, 76)

    def test_no_additional_entry_when_position_is_profitable(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = make_harness(Path(tmp), spot="79000")
            self._open_initial_position(h, spot="79000")
            now = h.clock.advance(timedelta(hours=6, minutes=30))
            h.broker.set_market(
                now=now,
                spot="78500",
                option_mark="520",
                option_ask="520",
                option_bid="510",
            )
            h.engine.tick()
            cycle = h.state.get_cycle("20261026")
            self.assertTrue(cycle.additional_done)
            self.assertEqual(cycle.additional_skip_reason, "position_not_in_loss")
            self.assertEqual(cycle.filled_quantity, 38)
            self.assertEqual(len(h.broker.place_calls), 1)

    def test_no_stop_loss_order_is_created_on_adverse_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = make_harness(Path(tmp), spot="79000")
            self._open_initial_position(h, spot="79000")
            now = h.clock.advance(timedelta(hours=6, minutes=30))
            h.broker.set_market(now=now, spot="78500", option_mark="100", option_ask="100", option_bid="90")
            h.engine.tick()
            h.engine.tick()
            h.engine.tick()
            cycle = h.state.get_cycle("20261026")
            self.assertEqual(cycle.status, "ACTIVE")
            self.assertEqual(cycle.exit_reason, "")
            self.assertFalse(cycle.exit_order_ids)

    def test_restart_reconciles_without_duplicate_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = make_harness(Path(tmp))
            self._open_initial_position(h)
            restarted = BotEngine(
                h.config,
                h.broker,
                h.state,
                h.notifier,
                clock=h.clock,
            )
            restarted.startup_reconcile()
            restarted.tick()
            self.assertEqual(len(h.broker.place_calls), 1)
            cycle = h.state.get_cycle("20261026")
            self.assertEqual(cycle.status, "ACTIVE")
            self.assertEqual(cycle.filled_quantity, 38)

    def test_kill_switch_closes_active_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = make_harness(Path(tmp))
            self._open_initial_position(h)
            h.config.kill_file.touch()
            h.engine.tick()
            h.engine.tick()
            cycle = h.state.get_cycle("20261026")
            self.assertEqual(cycle.status, "CLOSED")
            self.assertEqual(cycle.exit_reason, "kill_switch")
            self.assertEqual(cycle.closed_quantity, 38)

    def test_pause_prevents_new_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = make_harness(Path(tmp))
            h.config.pause_file.touch()
            h.engine.tick()
            self.assertIsNone(h.state.get_cycle("20261026"))
            self.assertEqual(len(h.broker.place_calls), 0)


if __name__ == "__main__":
    unittest.main()
