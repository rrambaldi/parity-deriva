"""
AG01, minus the leg that would trade into a level.

AG01 brackets a colour change with two opposite stop orders and lets the
market pick one. This variant asks one more question before each of them is
sent: **is that order being placed into a support or a resistance?**

	A sell stop sits at the low of the two candles. If that low is inside the
	band of a support the candles are sitting on - they touched it and closed
	back above it - the order is a short into the level most likely to bounce
	price, and it is not sent. A buy stop under a resistance is the mirror
	image and is dropped the same way.

The other leg is unaffected. A setup where both legs are refused produces no
signal at all; one where neither is produces exactly AG01's pair, which is why
this is a subclass and not a copy: every price, stop, target and expiry is
AG01's, and the only thing here is the filter.

What a level is, and when it is one
-----------------------------------
A swing high is a bar whose high no bar within SWING_BARS either side reaches;
a swing low is the mirror. That is the rule the chart draws, and it is causal
by construction: a bar is not a swing until SWING_BARS more have printed after
it, so this knows nothing at bar i that bar i + SWING_BARS did not.

Three numbers are not in anybody's specification and are conjectures, written
down as such:

  * SWING_BARS = 5, the same count the viewer confirms a swing with, so a
    level this refuses a trade at is a level somebody can see on the chart.
  * NEAR_RANGE = 0.01 - the band around a level is one per cent of the range
    of the bars in the window, which is the viewer's rule made causal: it uses
    one per cent of the whole run, which a strategy cannot know. A band is not
    decoration here, it is the whole test: "the low is at the support" has no
    meaning at a single price.
  * KEEP = 400 bars of window, which is also how long a level is remembered.
    A level whose bars have scrolled out of the window is one nothing in view
    was made against, and remembering it forever would have a swing from two
    years ago refusing trades today.

The levels are not the ones drawn on the chart, and cannot be: the page scans
the whole run at once and takes one per band of its total range, which is a
picture of the finished backtest. These are what this strategy had in hand at
the bar it decided on.
"""

from parity_deriva.lib.streaming import Swings
from parity_deriva.strategy.AG01 import AG01


class AG01MOD(AG01):
	#: See AG01.DESCRIPTION.
	DESCRIPTION = (
		"AG01 con un filtro su ciascuna gamba: la vendita non parte se il suo "
		"prezzo - il minimo delle due candele - cade dentro la banda di un "
		"supporto su cui le candele stanno appoggiate (lo toccano e chiudono "
		"sopra), e l'acquisto non parte se il suo prezzo cade dentro la banda di "
		"una resistenza sotto cui stanno. Se hanno chiuso oltre il livello, "
		"quello è rotto e la gamba parte. Tutto il resto è AG01: stessa coppia "
		"di ordini stop, STOP all'estremo opposto della coppia, TAKE PROFIT a "
		"1,2 volte quella distanza più lo spread, stessa scadenza a fine "
		"giornata. I livelli sono gli swing confermati delle ultime 400 barre, "
		"cinque barre per lato, con una banda dell'uno per cento del range della "
		"finestra - calcolati barra per barra su quello che la strategia aveva "
		"visto, non sul run finito come quelli disegnati nel grafico.")

	#: what the signal key and the broker's extension say, since the class
	#: cannot be called this
	TAG = 'AG01-MOD'

	#: the viewer's own SWING_BARS, so a level this refused a trade at is a
	#: level somebody can find on the chart
	SWING_BARS = 5
	#: the band, as a share of the window's range
	NEAR_RANGE = 0.01
	#: bars of window, and how long a level outlives its bars
	KEEP = 400

	#: see H4.PARAM_HELP
	PARAM_HELP = {
		'swingBars': 'bars either side confirming a swing',
		'nearRange': 'level band, as a share of the window range',
		'keepBars': 'swings kept, in bars',
	}

	def __init__(self, **args):
		AG01.__init__(self, **args)
		self._set(args, 'swingBars', self.SWING_BARS)
		self._set(args, 'nearRange', self.NEAR_RANGE)
		self._set(args, 'keepBars', self.KEEP)
		self.swings = dict(
			(pair, Swings(bars=self.swingBars, keep=self.keepBars,
						  near=self.nearRange))
			for pair in self.pairs)

	def execute_event(self, event):
		"""Feed the levels first, then let AG01 decide as it always has."""
		if str(event) == 'CANDLE' and not self.otherStream(event) \
				and event.instrument in self.swings:
			self.swings[event.instrument].add(event)
		return AG01.execute_event(self, event)

	def allow(self, units, prev, event):
		"""
		Is this leg being placed into a level that faces it?

		The price tested is the order's own - the pair's low for the sell and
		its high for the buy - because that is where the trade would start,
		not where the candles happen to be. "Sitting on" the level rather
		than through it is the close: a pair that closed below a support has
		broken it, and a short there is not a short into support.
		"""
		low = min(prev.mid['l'], event.mid['l'])
		high = max(prev.mid['h'], event.mid['h'])
		close = event.mid['c']
		for level in self.swings[event.instrument].levels():
			price, near = level['price'], level['near']
			if units < 0 and level['kind'] == 'support' \
					and abs(low - price) <= near and close > price:
				self.logger.info("AG01MOD: no sell at %s, support %s"
								 % (low, price))
				return False
			if units > 0 and level['kind'] == 'resistance' \
					and abs(high - price) <= near and close < price:
				self.logger.info("AG01MOD: no buy at %s, resistance %s"
								 % (high, price))
				return False
		return True
