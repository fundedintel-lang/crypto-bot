import tempfile
import unittest
from decimal import Decimal

from btc_options_bot.models import OrderRequest
from btc_options_bot.paper import PaperBroker
from tests.support import TEST_NOW, FakeBroker, make_config


class PaperBrokerTests(unittest.TestCase):
    def test_simulated_buy_and_reduce_only_sell(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(tmp)
            public = FakeBroker(now=TEST_NOW)
            broker = PaperBroker(config, public)  # type: ignore[arg-type]
            instrument = public.products[0]

            wallet = broker.get_wallet_balances()
            self.assertEqual(wallet.net_equity, Decimal("1000"))

            buy = OrderRequest(
                client_order_id="paper-buy-1",
                product_id=instrument.product_id,
                product_symbol=instrument.symbol,
                side="buy",
                size=10,
                limit_price=Decimal("500"),
                contract_value=Decimal("0.001"),
                metadata={"fee_rate": "0.01"},
            )
            order = broker.place_order(buy)
            self.assertEqual(order.state, "closed")
            self.assertEqual(order.filled_size, 10)
            position = broker.get_position(instrument.product_id)
            self.assertIsNotNone(position)
            assert position is not None
            self.assertEqual(position.size, 10)
            self.assertEqual(position.entry_price, Decimal("500"))
            self.assertLess(position.unrealized_pnl or Decimal("0"), Decimal("0"))

            sell = OrderRequest(
                client_order_id="paper-sell-1",
                product_id=instrument.product_id,
                product_symbol=instrument.symbol,
                side="sell",
                size=10,
                limit_price=Decimal("490"),
                reduce_only=True,
                contract_value=Decimal("0.001"),
                metadata={"fee_rate": "0.01"},
            )
            broker.place_order(sell)
            self.assertIsNone(broker.get_position(instrument.product_id))
            self.assertIsNotNone(broker.get_order_by_client_order_id("paper-sell-1"))

            broker.reset()
            self.assertEqual(broker.get_wallet_balances().net_equity, Decimal("1000"))
            self.assertIsNone(broker.get_position(instrument.product_id))


if __name__ == "__main__":
    unittest.main()
