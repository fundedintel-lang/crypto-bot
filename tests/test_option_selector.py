import unittest
from datetime import datetime
from decimal import Decimal

from btc_options_bot.errors import OptionSelectionError
from btc_options_bot.option_selector import select_option
from btc_options_bot.utils import UTC
from tests.support import TEST_NOW, FakeBroker, make_config

EXIT_AT = datetime(2026, 10, 27, 11, 50, 0, tzinfo=UTC)


class OptionSelectorTests(unittest.TestCase):
    def test_ce_selects_nearest_strike_of_next_safe_expiry(self):
        config = make_config()
        broker = FakeBroker(now=TEST_NOW)
        selected = select_option(
            broker,
            config,
            direction="CE",
            spot_price=Decimal("80200"),
            exit_at=EXIT_AT,
            now=TEST_NOW,
        )
        self.assertEqual(selected.instrument.symbol, "C-BTC-80000-271026")
        self.assertEqual(selected.instrument.settlement_time, datetime(2026, 10, 27, 12, 0, tzinfo=UTC))
        self.assertEqual(selected.instrument.contract_type, "call_options")

    def test_pe_selects_put_from_same_expiry(self):
        config = make_config()
        broker = FakeBroker(now=TEST_NOW)
        selected = select_option(
            broker,
            config,
            direction="PE",
            spot_price=Decimal("80200"),
            exit_at=EXIT_AT,
            now=TEST_NOW,
        )
        self.assertEqual(selected.instrument.symbol, "P-BTC-80000-271026")
        self.assertEqual(selected.instrument.contract_type, "put_options")

    def test_positive_offset_moves_out_of_the_money_for_both_directions(self):
        config = make_config(OPTION_STRIKE_MODE="offset", OPTION_STRIKE_OFFSET="1")
        broker = FakeBroker(now=TEST_NOW)
        ce = select_option(
            broker, config, direction="CE", spot_price=Decimal("80200"), exit_at=EXIT_AT, now=TEST_NOW
        )
        pe = select_option(
            broker, config, direction="PE", spot_price=Decimal("80200"), exit_at=EXIT_AT, now=TEST_NOW
        )
        self.assertEqual(ce.instrument.strike_price, Decimal("81000"))
        self.assertEqual(pe.instrument.strike_price, Decimal("79000"))

    def test_expiry_too_close_is_rejected(self):
        config = make_config(MIN_EXPIRY_BUFFER_MINUTES="1000", MAX_EXPIRY_DAYS="0")
        broker = FakeBroker(now=TEST_NOW)
        with self.assertRaises(OptionSelectionError):
            select_option(
                broker,
                config,
                direction="CE",
                spot_price=Decimal("80200"),
                exit_at=EXIT_AT,
                now=TEST_NOW,
            )

    def test_wide_spread_is_rejected(self):
        config = make_config()
        broker = FakeBroker(now=TEST_NOW)
        broker.set_market(option_ask="1000", option_bid="490", option_mark="700")
        with self.assertRaises(OptionSelectionError):
            select_option(
                broker,
                config,
                direction="CE",
                spot_price=Decimal("80200"),
                exit_at=EXIT_AT,
                now=TEST_NOW,
            )


if __name__ == "__main__":
    unittest.main()
