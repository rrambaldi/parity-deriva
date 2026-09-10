
import logging

from parity_deriva.event.event import ClientOrderEvent
from parity_deriva.event.event import TransactionEvent
from parity_deriva.trading.handler import ExecutionHandler


class SimulatedBroker(ExecutionHandler):
	"""
	Promote the simulator's events to the ones a real broker would send.

	The simulator publishes SIMULATEDORDER and SIMULATEDFILL rather than the
	broker's own CLIENTORDER and TRANSACTION, because in the parallel
	deployment both it and the real execution handler sit on the same bus and
	a component that could not tell them apart would act on both - the money
	manager would set onTrade from a fill that never happened and cancel the
	surviving leg of a live straddle.

	Offline there is no broker, so the distinction has nothing to protect and
	the loop has to close somehow. Adding this handler to the wiring says "the
	simulator is the broker here", which keeps the mode in the wiring rather
	than as a flag inside the simulator: the component whose fidelity the
	whole comparison rests on then has exactly one behaviour.

	    live:    strategy -> money manager -> execution  -> OANDA stream
	                                       -> simulator  -> reconciler
	    offline: strategy -> money manager -> simulator  -> SimulatedBroker
	"""

	def __init__(self, **args):
		self.logger = logging.getLogger('parity_deriva.trading.trading')
		self._set(args, 'setup', None)

	def execute_event(self, event):
		kind = str(event)
		if kind == 'SIMULATEDORDER':
			self.queue_event(ClientOrderEvent(event.to_dict()))
			return
		if kind == 'SIMULATEDFILL':
			payload = event.to_dict()
			payload['type'] = 'ORDER_FILL'
			self.queue_event(TransactionEvent(payload))
			return
