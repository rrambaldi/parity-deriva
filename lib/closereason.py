"""
Which leg closed a trade, where the broker does not say.

OANDA states it: the transaction that closes a trade names the stop or the
target. eToro reports the rate the trade closed at and nothing more, and IG's
transaction history reports an open level and a close level. So on those two
the leg has to be read off the price, and the reading has to be honest about
when it cannot tell.

The rule is the whole of it: the stop and the target are the boundaries of
the interval the trade lived in, so a trade that closed at or beyond a level
reached it, and a trade that closed strictly between them reached neither and
was closed some other way - by hand, by a margin call, at a gap.

The appeal of stating it that way is that it needs no tolerance. Any rule of
the form "near enough to the level" has to say how near, and there is no
number in the data to answer with; the interval is already there.

Where it errs, it errs towards declining to judge. A stop always fills at or
through its level, so that side is exact. A target can fill slightly inside
its level on a fast market, and such a close is reported UNKNOWN rather than
as a target - which costs a comparison the parity monitor would have counted,
and never reports an outcome the account did not have.

trading/parity.py treats an UNKNOWN outcome as undecidable rather than as a
divergence, because our own ignorance is not the market disagreeing with the
simulator.
"""

#: what trading/parity.py compares, in OANDA's vocabulary so that both sides
#: of the comparison speak the same one whichever broker produced them
TAKE_PROFIT = 'TAKE_PROFIT_ORDER'
STOP_LOSS = 'STOP_LOSS_ORDER'
UNKNOWN = 'UNKNOWN'


def closeReason(closeRate, stopLoss, takeProfit):
	"""Which leg closed a trade, inferred from the rate it closed at."""
	try:
		rate = float(closeRate)
		sl = float(stopLoss)
		tp = float(takeProfit)
	except (TypeError, ValueError):
		return UNKNOWN

	if abs(tp - sl) <= 0:
		return UNKNOWN

	# min/max rather than a long/short flag: for a long the stop is the lower
	# boundary and for a short it is the upper one, and neither needs naming
	low, high = min(sl, tp), max(sl, tp)
	if rate <= low:
		return STOP_LOSS if sl == low else TAKE_PROFIT
	if rate >= high:
		return STOP_LOSS if sl == high else TAKE_PROFIT
	return UNKNOWN
