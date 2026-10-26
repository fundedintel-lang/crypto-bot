import unittest
from datetime import timedelta
from decimal import Decimal

from btc_options_bot.errors import MarketDataError
from btc_options_bot.indicators import calculate_closed_candle_ema, exponential_moving_average
from btc_options_bot.models import Candle
from tests.support import TEST_NOW, make_candles


class IndicatorTests(unittest.TestCase):
    def test_ema_uses_sma_seed_and_recursive_formula(self):
        values = [Decimal("10"), Decimal("11"), Decimal("12"), Decimal("13")]
        # Seed = mean(10, 11) = 10.5; alpha = 2/3.
        # EMA after 12 = 11.5; EMA after 13 = 13*(2/3) + 11.5*(1/3).
        expected = Decimal("13") * Decimal(2) / Decimal(3) + Decimal("11.5") * Decimal(1) / Decimal(3)
        self.assertAlmostEqual(float(exponential_moving_average(values, 2)), float(expected), places=10)

    def test_closed_candles_are_filtered_and_ordered(self):
        candles = make_candles(TEST_NOW, count=60)
        # Add the currently-forming candle; it must not affect EMA21.
        forming = Candle(
            time=int(TEST_NOW.timestamp()) // 3600 * 3600,
            open=Decimal("999999"),
            high=Decimal("999999"),
            low=Decimal("999999"),
            close=Decimal("999999"),
        )
        result = calculate_closed_candle_ema(
            [*candles, forming],
            period=21,
            resolution="1h",
            now=TEST_NOW,
        )
        self.assertEqual(result.candles_used, 60)
        self.assertLess(result.value, Decimal("81000"))

    def test_stale_candles_are_rejected(self):
        candles = make_candles(TEST_NOW, count=60)
        with self.assertRaises(MarketDataError):
            calculate_closed_candle_ema(
                candles,
                period=21,
                resolution="1h",
                now=TEST_NOW + timedelta(hours=5),
                max_age_seconds=3600,
            )

    def test_insufficient_candles_are_rejected(self):
        with self.assertRaises(MarketDataError):
            calculate_closed_candle_ema(
                make_candles(TEST_NOW, count=10),
                period=21,
                resolution="1h",
                now=TEST_NOW,
            )


if __name__ == "__main__":
    unittest.main()
