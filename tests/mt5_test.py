"""
MT5 without a terminal: a fake api stands in for lib/mt5.MT5, so these run
anywhere. The real round trip - login, orders, fills, closes on the demo
account - needs scripts/mt5_bridge.sh and is not a unit test.
"""

import datetime
import os
import tempfile
import types
import unittest

from parity_deriva.data.mt5 import MT5Transactions
from parity_deriva.event.event import ClientOrderEvent, OrderEvent
from parity_deriva.execution.mt5 import MT5ExecutionHandler
from parity_deriva.lib import mt5 as lib

INFO = {'trade_contract_size': 100000.0, 'volume_step': 0.01, 'volume_min': 0.01,
		'volume_max': 100.0, 'visible': True, 'filling_mode': 1, 'expiration_mode': 15}


class FakeAPI(object):
	def __init__(self):
		self.sent = []
		self.deals = []
		self.open = True

	def symbol_info(self, sym):
		return dict(INFO), None

	def symbol_info_tick(self, sym):
		return {'bid': 1.1000, 'ask': 1.1002}, None

	def toServer(self, when, sym):
		return 12345

	def toUTC(self, seconds, sym):
		return datetime.datetime(2026, 9, 24)

	def order_send(self, request):
		self.sent.append(request)
		return {'retcode': lib.TRADE_RETCODE_DONE, 'order': 77}, (1, 'Success')

	def orders_get(self, ticket):
		return [], None

	def history_orders_get(self, ticket):
		return [{'state': lib.ORDER_STATE_FILLED, 'position_id': 77}], None

	def history_deals_get(self, position):
		return self.deals, None

	def positions_get(self, ticket):
		return ([{'symbol': 'EURUSD', 'tp': 1.2}] if self.open else []), None

	def account_info(self):
		return {'balance': 1000.0}, None


class Queue(list):
	def put(self, event):
		self.append(event)


def deal(**k):
	row = {'ticket': 1, 'order': 77, 'entry': 0, 'type': 0, 'volume': 0.01,
		   'price': 1.1002, 'time': 0, 'symbol': 'EURUSD', 'reason': 0,
		   'profit': 0.0, 'swap': 0.0, 'commission': 0.0, 'fee': 0.0}
	row.update(k)
	return row


class ExecutionTest(unittest.TestCase):

	def setUp(self):
		self.api = FakeAPI()
		self.handler = MT5ExecutionHandler(api=self.api, setup=types.SimpleNamespace())
		self.queue = Queue()
		self.handler.set_queue(self.queue)

	def order(self, **k):
		event = OrderEvent(dict(instrument='EUR_USD', gtdTime=datetime.datetime(2026, 9, 25),
								stopLoss=1.09, takeProfit=1.12, **k))
		event.signalNumber = 's1'
		return event

	def test_sell_stop_is_pending_with_expiry_and_lots(self):
		self.handler.execute_event(self.order(units=-12345, orderType='STOP', price=1.095))
		sent = self.api.sent[0]
		self.assertEqual((sent['action'], sent['type'], sent['volume']),
						 (lib.TRADE_ACTION_PENDING, lib.ORDER_TYPE_SELL_STOP, 0.12))
		self.assertEqual((sent['type_time'], sent['expiration']), (lib.ORDER_TIME_SPECIFIED, 12345))
		self.assertEqual(str(self.queue[0]), 'CLIENTORDER')

	def test_comment_fits_the_package(self):
		event = self.order(units=1000, orderType='MARKET', price=0)
		event.signalNumber = 'AG01:EUR_USD:H1:20260924T080000'
		self.handler.execute_event(event)
		self.assertLessEqual(len(self.api.sent[0]['comment']), 29)

	def test_market_buy_takes_the_ask(self):
		self.handler.execute_event(self.order(units=1000, orderType='MARKET', price=0))
		self.assertEqual(self.api.sent[0]['price'], 1.1002)

	def test_under_minimum_lot_is_rejected_not_sent(self):
		self.handler.execute_event(self.order(units=10, orderType='LIMIT', price=1.09))
		self.assertEqual(self.api.sent, [])
		self.assertEqual(self.queue[0].type, 'ORDER_REJECT')


class TransactionsTest(unittest.TestCase):

	def test_fill_then_broker_stop_close(self):
		api = FakeAPI()
		tx = MT5Transactions(api=api, setup=types.SimpleNamespace())
		queue = Queue()
		tx.set_queue(queue)
		tx.execute_event(ClientOrderEvent({'id': 77, 'instrument': 'EUR_USD', 'price': 1.1,
										   'orderType': 'STOP', 'contractSize': 100000}))
		api.deals = [deal()]
		tx.poll()
		self.assertEqual(queue[0].tradeOpened['tradeID'], 77)
		self.assertEqual((queue[0].units, queue[0].reason), (1000.0, 'STOP_ORDER'))

		api.open = False
		api.deals.append(deal(ticket=2, entry=1, type=1, price=1.09,
							  reason=lib.DEAL_REASON_SL, profit=-10.0))
		tx.poll()
		self.assertEqual((queue[1].reason, queue[1].pl), ('STOP_LOSS_ORDER', -10.0))
		self.assertEqual(tx.positions, {})


class ServerOffsetTest(unittest.TestCase):
	"""The broker's clock, from a tick when one is live, else from settings."""

	def offset(self, tick_age_hours, entry=None, configured=3):
		api = lib.MT5(setup=types.SimpleNamespace(MT5_SERVER_UTC_OFFSET=configured),
					  entry=entry or {})
		now = datetime.datetime.now(datetime.timezone.utc).timestamp()
		# a tick on a UTC+2 server, tick_age_hours old
		api.call = lambda name, sym: ({'time': now + 2 * 3600 - tick_age_hours * 3600}, None)
		return api.serverOffset('EURUSD').total_seconds() / 3600, api.offset

	def test_a_live_tick_says_the_offset_and_is_kept(self):
		hours, kept = self.offset(0, entry={'utc_offset': 2})
		self.assertEqual(hours, 2)
		self.assertIsNotNone(kept)

	def test_a_live_tick_moves_it_by_the_dst_hour(self):
		self.assertEqual(self.offset(0, configured=3)[0], 2)

	def test_a_stale_tick_is_not_believed(self):
		"""Friday's 21:00 UTC tick read on Saturday at 12:30: 15.5 hours old."""
		hours, kept = self.offset(15.5, entry={'utc_offset': 2})
		self.assertEqual(hours, 2)
		self.assertIsNone(kept)

	def test_the_terminal_offset_wins_over_the_global_one(self):
		self.assertEqual(self.offset(15.5, entry={'utc_offset': 2}, configured=3)[0], 2)
		self.assertEqual(self.offset(15.5, entry={}, configured=3)[0], 3)


class CredentialsTest(unittest.TestCase):

	def credentials(self, text):
		with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as f:
			f.write(text)
		try:
			return lib.credentials(types.SimpleNamespace(MT5_CREDENTIALS=f.name))
		finally:
			os.unlink(f.name)

	def test_key_value_and_bare_lines(self):
		self.assertEqual(self.credentials("Login: 42\nPassword=pw\nserver: S-Demo\n"),
						 (42, 'pw', 'S-Demo'))
		self.assertEqual(self.credentials("42\npw\nS-Demo\n"), (42, 'pw', 'S-Demo'))

	def test_missing_field_is_named_not_shown(self):
		with self.assertRaises(lib.MT5Error) as caught:
			self.credentials("login: 42\npassword: secret\n")
		self.assertIn('server', str(caught.exception))
		self.assertNotIn('secret', str(caught.exception))


class SuffixTest(unittest.TestCase):

	def test_the_trading_terminals_suffix(self):
		setup = types.SimpleNamespace(MT5_ACCOUNT='7', MT5_TERMINALS=[
			{'login': '6', 'password': 'p', 'server': 's'},
			{'login': '7', 'password': 'p', 'server': 's', 'suffix': '.pro'}])
		self.assertEqual(lib.symbol('EUR_USD', setup), 'EURUSD.pro')
		self.assertEqual(lib.instrumentName('EURUSD.pro', setup), 'EUR_USD')
		setup.MT5_ACCOUNT = '6'
		self.assertEqual(lib.symbol('EUR_USD', setup), 'EURUSD')


if __name__ == '__main__':
	unittest.main()
