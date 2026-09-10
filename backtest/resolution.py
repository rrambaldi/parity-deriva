
"""
Resolve a trade's outcome from candles, and measure what a candle cannot say.

The simulator is candle-driven while a real account is tick-driven. A bar
reports only o/h/l/c, so when both the stop and the target of a trade fall
inside one bar's range, the bar does not say which was touched first - and the
simulator has to guess. That guess is a coin flip, and it is the floor of the
divergence any real-versus-simulated comparison will see: an alarm threshold
set below it fires on the resolution of the data rather than on the market.

The finer the bars, the smaller the floor, which is the whole reason to keep
M1 history for a strategy that signals on H1. This module resolves the same
trade at two granularities so the difference can be counted.

The fill rule matches backtest/oanda.py: a level is touched when it lies
within [low, high] inclusive, on the side of the book the order would trade
against - a long exits by selling, so its stop and target are read off the
bid; a short exits by buying, so off the ask.

M1 is the reference, not the truth: a minute in which both levels are
touchable is still ambiguous, and residual_ambiguity() reports how often that
happens so the reference's own limit is visible.
"""

TARGET = 'TARGET'
STOP = 'STOP'
AMBIGUOUS = 'AMBIGUOUS'
OPEN = 'OPEN'


def exit_side(units):
	"""Which side of the book closes a position of this direction."""
	return 'bid' if units > 0 else 'ask'


def touches(bar, level, side):
	"""Would a resting order at this level trade during this bar?"""
	return bar['%s_l' % side] <= level <= bar['%s_h' % side]


def first_touch(bars, level, side):
	"""Label of the first bar that reaches the level, or None."""
	for label, bar in bars.iterrows():
		if touches(bar, level, side):
			return label
	return None


def resolve_exit(bars, stop, target, units):
	"""
	Walk the bars after entry and report how the trade ended.

	Returns (outcome, label). AMBIGUOUS means a single bar reached both
	levels, so these bars cannot say which came first; OPEN means neither was
	reached within the bars given.
	"""
	side = exit_side(units)
	for label, bar in bars.iterrows():
		hit_stop = touches(bar, stop, side)
		hit_target = touches(bar, target, side)
		if hit_stop and hit_target:
			return AMBIGUOUS, label
		if hit_target:
			return TARGET, label
		if hit_stop:
			return STOP, label
	return OPEN, None


def refine(bars, stop, target, units, coarse_label, span):
	"""
	Resolve the same trade again over the finer bars covering one coarse bar.

	`span` is the width of the coarse bar, so [coarse_label, coarse_label +
	span) is the window the coarse resolution collapsed into a single reading.
	"""
	window = bars.loc[coarse_label:coarse_label + span]
	window = window[window.index < coarse_label + span]
	return resolve_exit(window, stop, target, units)


def residual_ambiguity(bars, stop, target, units):
	"""
	How many of these bars reach both levels on their own.

	At the finest granularity available this is the part that no amount of
	data in hand can resolve: the reference's own limit.
	"""
	side = exit_side(units)
	return sum(1 for _, bar in bars.iterrows()
			   if touches(bar, stop, side) and touches(bar, target, side))
