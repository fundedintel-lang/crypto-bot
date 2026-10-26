import unittest
from datetime import datetime
from decimal import Decimal

from btc_options_bot.errors import RiskCheckError
from btc_options_bot.models import OrderRequest
from btc_options_bot.schedule import build_session
from btc_options_bot.state import CycleState
from btc_options_bot.strategy import (
    build_trade_plan,
    get_signal,
    plan_buy_order,
    position_is_losing,
    target_reached,
)
from btc_options_bot.utils import IST
from tests.support import TEST_NOW, FakeBroker, make_config, make_order_book


class StrategyTests(unittest.TestCase):
    def test_signal_is_ce_above_ema_and_pe_below_ema(self):
        config = make_config()
        broker = FakeBroker(now=TEST_NOW, spot="80200")
        self.assertEqual(get_signal(broker, config, now=TEST_NOW).direction, "CE")
        broker.set_market(spot="79000")
        self.assertEqual(get_signal(broker, config, now=TEST_NOW).direction, "PE")

    def test_trade_plan_uses_exact_2_percent_and_1500_target(self):
        config = make_config()
        broker = FakeBroker(now=TEST_NOW, spot="80200")
        session = build_session(datetime(2026, 10, 26, tzinfo=IST).date(), config)
        result = build_trade_plan(broker, config, session, now=TEST_NOW)
        self.assertIsNotNone(result.plan)
        plan = result.plan
        assert plan is not None
        self.assertEqual(plan.budget, Decimal("20"))
        self.assertEqual(plan.order_plan.quantity, 38)
        self.assertEqual(plan.target_btc_price, Decimal("81700"))
        self.assertEqual(plan.order_plan.limit_price, Decimal("505.0"))
        self.assertLessEqual(plan.order_plan.estimated_cost, Decimal("20"))
        self.assertEqual(plan.selected.instrument.symbol, "C-BTC-80000-271026")

    def test_pe_target_is_1500_points_below_entry(self):
        config = make_config()
        broker = FakeBroker(now=TEST_NOW, spot="79000")
        session = build_session(datetime(2026, 10, 26, tzinfo=IST).date(), config)
        result = build_trade_plan(broker, config, session, now=TEST_NOW)
        assert result.plan is not None
        self.assertEqual(result.plan.signal.direction, "PE")
        self.assertEqual(result.plan.target_btc_price, Decimal("77500"))

    def test_insufficient_budget_is_rejected(self):
        config = make_config()
        broker = FakeBroker(now=TEST_NOW)
        instrument = broker.products[0]
        ticker = broker.tickers[instrument.product_id]
        book = make_order_book(instrument.symbol)
        with self.assertRaises(RiskCheckError):
            plan_buy_order(
                instrument,
                ticker,
                book,
                budget=Decimal("0.10"),
                max_quantity=1000,
                config=config,
            )

    def test_target_conditions_are_strictly_directional(self):
        self.assertTrue(target_reached("CE", Decimal("81700"), Decimal("81700")))
        self.assertFalse(target_reached("CE", Decimal("81699"), Decimal("81700")))
        self.assertTrue(target_reached("PE", Decimal("77500"), Decimal("77500")))
        self.assertFalse(target_reached("PE", Decimal("77501"), Decimal("77500")))

    def test_position_loss_uses_exchange_unrealized_pnl(self):
        make_config()
        broker = FakeBroker(now=TEST_NOW)
        instrument = broker.products[0]
        cycle = CycleState(
            cycle_id="20261026",
            session_date="2026-10-26",
            product_id=instrument.product_id,
            product_symbol=instrument.symbol,
            filled_quantity=10,
            avg_entry_premium="500",
        )
        cycle.set_instrument(instrument)
        broker.place_order(
            OrderRequest(
                client_order_id="loss-test",
                product_id=instrument.product_id,
                product_symbol=instrument.symbol,
                side="buy",
                size=10,
                limit_price=Decimal("500"),
            )
        )
        broker.set_market(option_mark="480")
        self.assertTrue(position_is_losing(broker, cycle))
        broker.set_market(option_mark="520")
        self.assertFalse(position_is_losing(broker, cycle))


if __name__ == "__main__":
    unittest.main()
