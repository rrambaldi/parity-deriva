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


import datetime


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


#: trading days a year, for annualising a daily Sharpe ratio
TRADING_DAYS = 252


def kpis(curve, start, dtfrom, dtto, summary=None):
	"""
	The ratios strategies are compared on, from the capital curve.

	`curve` is [(time, balance)] after each close, `start` the opening
	balance, `dtfrom`/`dtto` the window the run covered (datetimes), and
	`summary` what report() said about the same trades, for the ratios that
	are about trades rather than about the curve.

	Percentages are of the account, not price x units: these only mean
	something for a run sized off an account (risk set), which is what a
	sweep is. The daily series is one balance per weekday - the market is
	shut at the weekend, and zero returns there would flatter the Sharpe.
	The risk free rate is taken as zero.
	"""
	import math

	summary = summary or {}
	points = sorted(curve, key=lambda point: point[0])
	final = points[-1][1] if points else start
	years = max((dtto - dtfrom).total_seconds() / (365.25 * 86400), 1e-9)
	daily = [balance for _, balance in days(points, start, dtfrom, dtto)]

	# drawdown in per cent of the peak, on every close and not only daily
	peak, mdd = start, 0.0
	for _, value in points:
		peak = max(peak, value)
		if peak > 0:
			mdd = max(mdd, (peak - value) / peak * 100)

	peak, squares = start, []
	for value in daily:
		peak = max(peak, value)
		squares.append(((peak - value) / peak * 100) ** 2 if peak > 0 else 0.0)
	ulcer = math.sqrt(sum(squares) / len(squares)) if squares else None

	returns = [b / a - 1 for a, b in zip([start] + daily, daily) if a > 0]
	sharpe = None
	if len(returns) > 1:
		mean = sum(returns) / len(returns)
		sd = math.sqrt(sum((r - mean) ** 2 for r in returns) / (len(returns) - 1))
		sharpe = mean / sd * math.sqrt(TRADING_DAYS) if sd > 0 else None

	car = ((final / start) ** (1 / years) - 1) * 100 if start > 0 and final > 0 else None
	win, loss = summary.get('averageWin'), summary.get('averageLoss')
	out = {
		'roi': (final - start) / start * 100 if start else None,
		'car': car,
		'maxDrawdownPct': mdd,
		'carMdd': (car / mdd) if car is not None and mdd > 0 else None,
		'sharpe': sharpe,
		'ulcer': ulcer,
		'riskReward': (win / loss) if win and loss else None,
		'expectancy': summary.get('expectancy'),
		'profitFactor': summary.get('profitFactor'),
		'winRate': summary.get('winRate'),
		'years': years,
	}
	out['score'] = score(out, summary.get('closedTrades'))
	# an account that ran out of margin could not have traded the run at all
	if summary.get('margin') and not summary['margin'].get('ok'):
		out['score'] = 0.0
	return out


def days(curve, start, dtfrom, dtto):
	"""
	[(day, balance)], one a weekday from dtfrom to dtto: the last close on or
	before the day's end. `curve` is [(time, balance)] in time order.
	"""
	out, i, balance = [], 0, start
	day = dtfrom.replace(hour=0, minute=0, second=0, microsecond=0)
	while day <= dtto:
		end = day + datetime.timedelta(days=1)
		while i < len(curve) and curve[i][0] < end:
			balance = curve[i][1]
			i += 1
		if day.weekday() < 5:
			out.append((day, balance))
		day = end
	return out


def _position(trade, leverage, factor=1.0):
	"""(margin, risk to the stop) of a trade's position, or None without one."""
	try:
		size = abs(float(trade.get('units'))) * factor
		price = float(trade.get('entryPrice'))
	except (TypeError, ValueError):
		return None
	if trade.get('entryTime') is None or size <= 0:
		return None
	stop = trade.get('stopLoss')
	risk = abs(price - float(stop)) * size if stop is not None else 0.0
	return size * price / leverage, risk


class _Account(object):
	"""
	The margin account both margin() and together() walk: the closed
	balance, the margin the open positions hold and what they would lose at
	their stops.

	Free margin is the balance, less every open position's loss to its stop,
	less the margin they hold: what is left for the next trade on the worst
	close of all of them.
	"""

	def __init__(self, start, leverage):
		self.balance, self.used, self.risk, self.open = float(start), 0.0, 0.0, 0
		self.out = {'leverage': leverage, 'start': float(start), 'peakMargin': 0.0,
					'peakMarginPct': 0.0, 'minFree': None, 'minFreeAt': None,
					'maxOpen': 0, 'breachAt': None, 'negativeAt': None}

	def free(self, margin=0.0, risk=0.0):
		return self.balance - self.risk - risk - self.used - margin

	def enter(self, when, margin, risk):
		self.used += margin
		self.risk += risk
		self.open += 1
		out, free = self.out, self.free()
		out['peakMargin'] = max(out['peakMargin'], self.used)
		if self.balance > 0:
			out['peakMarginPct'] = max(out['peakMarginPct'], self.used / self.balance * 100)
		out['maxOpen'] = max(out['maxOpen'], self.open)
		if out['minFree'] is None or free < out['minFree']:
			out['minFree'], out['minFreeAt'] = free, when
		if free < 0 and out['breachAt'] is None:
			out['breachAt'] = when
		if self.balance - self.risk <= 0 and out['negativeAt'] is None:
			out['negativeAt'] = when

	def leave(self, when, margin, risk, pl):
		self.used -= margin
		self.risk -= risk
		self.open -= 1
		self.balance += pl
		if self.balance <= 0 and self.out['negativeAt'] is None:
			self.out['negativeAt'] = when

	def result(self):
		out = self.out
		out['ok'] = out['breachAt'] is None and out['negativeAt'] is None
		return out


# ponytail: closes and stop distances only - a gap past the stop, or the
# floating loss of a trade with no stop, is not seen; walk the candles if it matters
def margin(trades, start, leverage):
	"""
	Whether an account of `start` on `leverage`:1 could have carried these
	trades: it never went to zero, and every trade found the free margin to
	open. A position holds |units| x entry price / leverage.

	In the quote currency, as the rest of this module. {leverage, start,
	peakMargin, peakMarginPct, minFree, minFreeAt, maxOpen, breachAt,
	negativeAt, ok}; the times are the trades' own.
	"""
	events = []
	for trade in trades:
		held = _position(trade, leverage)
		if held is None:
			continue
		events.append((trade['entryTime'], 1, held, 0.0))
		if trade.get('exitTime') is not None:
			events.append((trade['exitTime'], 0, held, _pl(trade) or 0.0))
	# a close frees its margin before an entry of the same moment asks for it
	events.sort(key=lambda event: (event[0], event[1]))
	account = _Account(start, leverage)
	for when, entering, (held_margin, risk), pl in events:
		if entering:
			account.enter(when, held_margin, risk)
		else:
			account.leave(when, held_margin, risk, pl)
	return account.result()


# ponytail: a run sized off its capital is scaled by shared / own balance at
# each entry, where the money manager reviews its capital once a month; and a
# refused trade does not free the strategy for a signal it skipped meanwhile.
# Running the strategies on one engine is what would lift both
def together(runs, start, leverage):
	"""
	The runs traded on one account of `start`, from their trades: what
	a mix simulated together makes.

	`runs` is [{trades, start, scaled}]: a scaled run sized its trades off its
	own capital, so each is resized by the shared balance over the run's own
	at its entry; one of fixed units trades them as they were. A trade the
	free margin does not cover is refused, as a broker would refuse it.

	{curve [[time, balance]], steps (the money of each close), parts (each
	run's curve, [[time, start + its net]]), nets, taken, refused (both per
	run), margin (as margin() says it)}.
	"""
	events = []
	for i, run in enumerate(runs):
		for k, trade in enumerate(run['trades']):
			if trade.get('entryTime') is None:
				continue
			events.append((trade['entryTime'], 1, i, k, trade))
			if trade.get('exitTime') is not None:
				events.append((trade['exitTime'], 0, i, k, trade))
	events.sort(key=lambda event: (event[0], event[1]))
	account = _Account(start, leverage)
	own = [float(run['start']) for run in runs]
	held, curve, steps = {}, [], []
	nets, taken, refused = [0.0] * len(runs), [0] * len(runs), [0] * len(runs)
	parts = [[] for _ in runs]
	for when, entering, i, k, trade in events:
		if entering:
			factor = 1.0
			if runs[i].get('scaled'):
				factor = max(0.0, account.balance / own[i]) if own[i] > 0 else 0.0
			position = _position(trade, leverage, factor)
			if position is None:
				continue
			if account.free(*position) < 0:
				refused[i] += 1
				continue
			held[(i, k)] = position + (factor,)
			taken[i] += 1
			account.enter(when, *position)
		else:
			pl = _pl(trade) or 0.0
			own[i] += pl   # the run's own capital moves whether or not it was taken
			if (i, k) not in held:
				continue
			held_margin, risk, factor = held.pop((i, k))
			step = pl * factor
			account.leave(when, held_margin, risk, step)
			steps.append(step)
			nets[i] += step
			curve.append([when, account.balance])
			parts[i].append([when, float(start) + nets[i]])
	return {'curve': curve, 'steps': steps, 'parts': parts, 'nets': nets,
			'taken': taken, 'refused': refused, 'margin': account.result()}


def correlation(a, b):
	"""Pearson's r of two series of the same length, or None when one is flat."""
	import statistics
	try:
		return statistics.correlation(a, b)
	except statistics.StatisticsError:
		return None


#: where each KPI of the score is worth nothing and where it is worth all it
#: can: a straight line in between, held at the ends outside
SCORE_SCALE = {'car': (0.0, 30.0), 'maxDrawdownPct': (30.0, 5.0), 'ulcer': (10.0, 2.0),
			   'profitFactor': (1.0, 2.5), 'sharpe': (0.0, 2.5)}
#: the closed trades a score is believed in full from
SCORE_TRADES = 30


def score(kpi, trades):
	"""
	One number from 0 to 100 that ranks runs: the KPI table and the mix's
	pick of a run are ordered by it.

	Four parts, each KPI put on 0..1 by SCORE_SCALE: the gain 35% (CAR, the
	total gain on a year, so that sets of different length compare), the risk
	25% (max drawdown and Ulcer, half each), the quality 20% (profit factor),
	the steadiness 20% (Sharpe). ROI and expectancy are left out: over one
	window they say what the CAR says, and the gain counted three times would
	drown the rest. The sum is then multiplied by the confidence,
	sqrt(trades / SCORE_TRADES) up to 1: one lucky trade has a profit factor
	with no loss under it and no drawdown, and would come first on the ratios
	alone.

	A figure the run cannot have counts as the worst, except a profit factor
	with no losing trade under it, which is at its best.
	"""
	import math

	def part(key):
		value = kpi.get(key)
		if value is None:
			return 1.0 if key == 'profitFactor' and trades else 0.0
		bad, good = SCORE_SCALE[key]
		return min(1.0, max(0.0, (value - bad) / (good - bad)))

	total = (0.35 * part('car') + 0.25 * (part('maxDrawdownPct') + part('ulcer')) / 2
			 + 0.20 * part('profitFactor') + 0.20 * part('sharpe'))
	return 100 * total * min(1.0, math.sqrt((trades or 0) / SCORE_TRADES))
