"""
A live session's start and stop, at the money manager: no signal from a bar
that closed before the session started, and "stop" as stop and close
everything (StatusEvent('LIQUIDATE'), sent by scripts/live.py on SIGTERM).
"""

import datetime
import unittest

from parity_deriva.event.event import (ClientOrderEvent, SignalEvent, StatusEvent,
									   TransactionEvent)
from parity_deriva.portfolio.moneymanager import MoneyManager, SESSION_STOP

START = datetime.datetime(2026, 9, 24, 9, 13)


class Queue(list):
	def put(self, event):
		self.append(event)


def signal(when, price=1.1, number='s1'):
	se = SignalEvent()
	se.signalNumber = number
	se.signalType = 'EXCLUSIVE'
	se.orderType = 'STOP'
	se.instrument = 'EUR_USD'
	se.time = when
	se.price = price
	se.stopLoss = price - 0.002
	se.takeProfit = price + 0.002
	se.units = 1
	return se


class StopTest(unittest.TestCase):

	def setUp(self):
		# H1: the first bar that may signal opened at START - 1h
		self.mm = MoneyManager(pairs=['EUR_USD'], notBefore=START - datetime.timedelta(hours=1))
		self.queue = Queue()
		self.mm.set_queue(self.queue)

	def orders(self):
		return [e for e in self.queue if str(e) == 'ORDER']

	def test_bar_closed_before_the_start_is_ignored(self):
		self.mm.execute_event(signal(datetime.datetime(2026, 9, 24, 8, 0)))
		self.assertEqual(self.orders(), [])

	def test_bar_still_forming_at_the_start_is_taken(self):
		self.mm.execute_event(signal(datetime.datetime(2026, 9, 24, 9, 0)))
		self.assertEqual(len(self.orders()), 1)

	def place(self):
		self.mm.execute_event(signal(datetime.datetime(2026, 9, 24, 10, 0)))
		# the broker's acknowledgement gives the order its id, 7
		ack = ClientOrderEvent({'id': 7, 'batchID': 0, 'price': 1.1})
		ack.signalNumber = 's1'
		self.mm.execute_event(ack)

	def test_stop_cancels_resting_and_closes(self):
		self.place()
		del self.queue[:]
		self.mm.execute_event(StatusEvent('LIQUIDATE'))
		kinds = [str(e) for e in self.queue]
		self.assertIn('ORDERCANCEL', kinds)
		close = [e for e in self.queue if str(e) == 'CLOSETRADE'][0]
		self.assertEqual((close.instrument, close.reason), ('EUR_USD', SESSION_STOP))
		# the cancel coming back through the bus settles it
		self.mm.execute_event([e for e in self.queue if str(e) == 'ORDERCANCEL'][0])
		self.assertTrue(self.mm.settled())
		# and nothing new is taken
		del self.queue[:]
		self.mm.execute_event(signal(datetime.datetime(2026, 9, 24, 11, 0), number='s2'))
		self.assertEqual(self.orders(), [])

	def test_fill_after_the_stop_is_closed_too(self):
		self.place()
		self.mm.execute_event(StatusEvent('LIQUIDATE'))
		del self.queue[:]
		self.mm.execute_event(TransactionEvent({'type': 'ORDER_FILL', 'orderID': 7,
												'instrument': 'EUR_USD', 'price': 1.1}))
		self.assertFalse(self.mm.settled())
		self.assertIn('CLOSETRADE', [str(e) for e in self.queue])
		self.mm.execute_event(TransactionEvent({'type': 'ORDER_FILL', 'orderID': 7,
												'tradesClosed': [{'tradeID': 7}]}))
		self.assertTrue(self.mm.settled())


if __name__ == '__main__':
	unittest.main()
