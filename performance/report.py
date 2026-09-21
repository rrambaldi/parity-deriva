"""
What a list of closed trades adds up to.

Pure arithmetic over records that already exist: it reads, it does not run
anything and it does not fetch anything. That is the difference from
performance/analyze.py, which asks OANDA for the account's closed trades and
prints its findings into the log - the same family of numbers, computed for a
different input and delivered a different way. The two were not merged because
analyze.py's figures are woven into its log lines and its input is OANDA's
trade JSON; unpicking that is its own change, and doing it as a side effect of
adding a report here would put a live-account path at risk for a backtest
view.

One thing to be clear about before reading any number out of this: **P&L here
is in price units times units, not in account currency.** The simulator
computes a close as (exit - entry) x units and adds it to a balance that
started at 100000, so a one-unit EUR_USD trade that ran 29 pips reports
0.0029. Turning that into money needs a contract size and a conversion this
project does not model, and printing a euro sign on a number that is not euros
is the kind of quiet wrongness everything else here is written to avoid.
"""


#: outcomes the simulator reports, in the vocabulary trading/parity.py shares
TAKE_PROFIT = 'TAKE_PROFIT_ORDER'
STOP_LOSS = 'STOP_LOSS_ORDER'


def _pl(trade):
	value = trade.get('pl')
	try:
		return float(value)
	except (TypeError, ValueError):
		return None


def closed(trades):
	"""The trades with a realised result, which are the only ones to count."""
	return [t for t in trades if _pl(t) is not None]


def runs(values):
	"""
	(longest run of positives, longest run of negatives).

	A lone winner is a run of one, which is the reading performance/analyze.py
	already takes and the one a person means by "how many in a row".
	"""
	best_up = best_down = up = down = 0
	for value in values:
		if value > 0:
			up, down = up + 1, 0
		elif value < 0:
			down, up = down + 1, 0
		else:
			up = down = 0
		best_up = max(best_up, up)
		best_down = max(best_down, down)
	return best_up, best_down


def equity(values, start=0.0):
	"""The running total after each trade, starting from `start`."""
	out = []
	total = start
	for value in values:
		total += value
		out.append(total)
	return out


def drawdown(curve, start=0.0):
	"""
	(deepest fall from a peak, index where it bottomed).

	Measured on the running total rather than on the account balance, so it is
	the strategy's own worst stretch and does not move when the starting
	balance does.
	"""
	peak = start
	worst = 0.0
	at = None
	for i, value in enumerate(curve):
		peak = max(peak, value)
		fall = peak - value
		if fall > worst:
			worst, at = fall, i
	return worst, at


def outcomes(trades):
	"""How many trades ended each way, including the ones that never ended."""
	tally = {}
	for trade in trades:
		name = trade.get('outcome') or 'UNKNOWN'
		tally[name] = tally.get(name, 0) + 1
	return tally


def report(trades):
	"""
	The summary of a backtest, as plain numbers.

	Every ratio that could divide by zero is None rather than 0.0 when its
	denominator is empty. A run of nothing but winners is exactly the run
	somebody wants a report for, and a profit factor printed as 0.00 there
	would read as the worst possible result rather than as the best.
	"""
	done = closed(trades)
	values = [_pl(t) for t in done]
	wins = [v for v in values if v > 0]
	losses = [v for v in values if v < 0]
	flat = [v for v in values if v == 0]

	gross_profit = sum(wins)
	gross_loss = -sum(losses)
	curve = equity(values)
	worst, worst_at = drawdown(curve)
	best_up, best_down = runs(values)

	return {
		# what was counted, and what was not
		'trades': len(trades),
		'closedTrades': len(done),
		'openTrades': len(trades) - len(done),
		'outcomes': outcomes(trades),

		'wins': len(wins),
		'losses': len(losses),
		'flat': len(flat),
		'winRate': (float(len(wins)) / len(done)) if done else None,

		'grossProfit': gross_profit,
		'grossLoss': gross_loss,
		'net': gross_profit - gross_loss,
		'profitFactor': (gross_profit / gross_loss) if gross_loss else None,
		'expectancy': (sum(values) / len(done)) if done else None,

		'averageWin': (gross_profit / len(wins)) if wins else None,
		'averageLoss': (gross_loss / len(losses)) if losses else None,
		'largestWin': max(wins) if wins else None,
		'largestLoss': min(losses) if losses else None,

		'maxConsecutiveWins': best_up,
		'maxConsecutiveLosses': best_down,

		'equity': curve,
		'maxDrawdown': worst,
		'maxDrawdownAt': worst_at,

		# said in the payload rather than only in this docstring, so a
		# consumer cannot label it as money by accident
		'unit': 'price x units',
	}
