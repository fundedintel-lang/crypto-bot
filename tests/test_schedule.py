import unittest
from datetime import datetime, timedelta

from btc_options_bot.schedule import (
    build_session,
    candidate_sessions,
    next_entry_session,
    within_catchup,
)
from btc_options_bot.utils import IST, UTC
from tests.support import TEST_NOW, make_config


class ScheduleTests(unittest.TestCase):
    def test_exact_ist_schedule_maps_to_expected_utc(self):
        config = make_config()
        session = build_session(datetime(2026, 10, 26, tzinfo=IST).date(), config)
        self.assertEqual(session.entry_at, datetime(2026, 10, 26, 13, 0, tzinfo=UTC))
        self.assertEqual(session.add_at, datetime(2026, 10, 26, 19, 30, tzinfo=UTC))
        self.assertEqual(session.exit_at, datetime(2026, 10, 27, 11, 50, tzinfo=UTC))
        self.assertEqual(session.cycle_id, "20261026")

    def test_catchup_window(self):
        self.assertTrue(within_catchup(TEST_NOW, TEST_NOW, 10))
        self.assertTrue(within_catchup(TEST_NOW + timedelta(minutes=9), TEST_NOW, 10))
        self.assertFalse(within_catchup(TEST_NOW - timedelta(seconds=1), TEST_NOW, 10))
        self.assertFalse(within_catchup(TEST_NOW + timedelta(minutes=11), TEST_NOW, 10))

    def test_candidate_sessions_and_next_entry(self):
        config = make_config()
        sessions = candidate_sessions(TEST_NOW, config)
        self.assertEqual([item.cycle_id for item in sessions], ["20261026", "20261025"])
        self.assertEqual(next_entry_session(TEST_NOW, config).cycle_id, "20261026")
        later = TEST_NOW + timedelta(minutes=1)
        self.assertEqual(next_entry_session(later, config).cycle_id, "20261027")


if __name__ == "__main__":
    unittest.main()
