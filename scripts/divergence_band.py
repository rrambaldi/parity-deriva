"""
Measure the divergence a candle-driven simulator cannot avoid.

The simulator reads bars; a real account trades ticks. When a bar's range
holds both the stop and the target of an open trade, the bar does not say
which was reached first, and the simulator guesses. That guess is a coin flip,
and it is the floor of any real-versus-simulated comparison: a threshold set
below it fires on the resolution of the data rather than on the market.

This resolves every trade a strategy produces twice - once at the granularity
the strategy signals on, once at M1 - and reports where the two disagree. M1
is the reference, not the truth: a minute holding both levels is still
ambiguous, and that residue is reported separately so the reference's own
limit is visible.

    python scripts/divergence_band.py --instrument EUR_USD
    python scripts/divergence_band.py --instrument EUR_USD --granularity H1 \
        --dtfrom 2018-01-15 --dtto 2018-02-15

What comes out is a band, not a verdict: live-versus-simulated divergence
inside it says nothing about execution, only about bar width. Set the alarm
above it.
"""

import argparse
import collections
import datetime
import logging
import os
import sys

import pandas as pd

from parity_deriva.backtest import resolution
from parity_deriva.backtest.driver import ReplayEngine
from parity_deriva.data import market
from parity_deriva.data.replay import ForexCandles
from parity_deriva.etc import settings
from parity_deriva.event.event import SignalEvent
from parity_deriva.lib.utils import getLogger, granularityToTimedelta
from parity_deriva.trading.handler import ExecutionHandler

STRATEGIES = {}


def load_strategies():
    from parity_deriva.strategy.AG01 import AG01
    from parity_deriva.strategy.AG02 import AG02
    STRATEGIES.update({'AG01': AG01, 'AG02': AG02})
    # Strategies that exit on a moving stop are deliberately not here, and
    # that includes anything strategy/plugins.py may have installed. What this
    # script measures is how often one bar reaches a trade's stop *and* its
    # target, so the two granularities disagree about which came first; a
    # strategy with no target has nothing to disagree about. measure() drops a
    # signal whose takeProfit is None a few lines down, so adding one would
    # buy an option that always reports nothing. What a coarse bar cannot say
    # about a climbing stop is a different measurement from this one.


class SignalTap(ExecutionHandler):
    """Collect the signals a strategy emits, with the bar that produced them."""

    def __init__(self):
        self.logger = logging.getLogger('parity_deriva.trading.trading')
        self.signals = []
        self.last_candle = None

    def execute_event(self, event):
        kind = str(event)
        if kind == 'CANDLE':
            self.last_candle = event
        elif kind == 'SIGNAL':
            self.signals.append((self.last_candle, event))


def collect_signals(strategy_name, instrument, granularity, dtfrom, dtto):
    strategy = STRATEGIES[strategy_name](pairs=[instrument], granularity=granularity)
    tap = SignalTap()
    engine = ReplayEngine()
    engine.add_handler(strategy)
    engine.add_handler(tap)
    engine.run(ForexCandles(pairs=[instrument], granularity=granularity,
                            dtfrom=dtfrom, dtto=dtto))
    return tap.signals


def load_bars(instrument, granularity):
    path = market.store(instrument)
    store = pd.HDFStore(path, mode='r')
    try:
        key = '/' + granularity.lstrip('/')
        if key not in store:
            raise SystemExit("%s has no %s key (has %s)"
                             % (path, key, ", ".join(store.keys())))
        return store[key]
    finally:
        store.close()


def measure(signals, coarse, fine, coarse_span):
    """
    Resolve each trade at both granularities and tally the disagreements.
    """
    tally = collections.Counter()
    unresolved_at_m1 = 0
    disagreements = []

    for candle, signal in signals:
        if signal.stopLoss is None or signal.takeProfit is None:
            continue
        after = coarse[coarse.index > candle.time]
        if after.empty:
            continue

        # where the coarse bars put the entry
        side = 'ask' if signal.units > 0 else 'bid'
        entry_label = resolution.first_touch(after, signal.price, side)
        if entry_label is None:
            tally['never entered'] += 1
            continue

        coarse_exit = coarse[coarse.index > entry_label]
        outcome_coarse, label_coarse = resolution.resolve_exit(
            coarse_exit, signal.stopLoss, signal.takeProfit, signal.units)

        if outcome_coarse == resolution.OPEN:
            tally['still open at the end of the range'] += 1
            continue

        if outcome_coarse != resolution.AMBIGUOUS:
            tally['decided by the coarse bars'] += 1
            continue

        # the coarse bar held both levels: ask the minutes
        refined, label_fine = resolution.refine(
            fine, signal.stopLoss, signal.takeProfit, signal.units,
            label_coarse, coarse_span)
        if refined in (resolution.TARGET, resolution.STOP):
            tally['coin flip, settled by M1'] += 1
            disagreements.append((signal.signalNumber, label_coarse, refined,
                                  label_fine))
        elif refined == resolution.AMBIGUOUS:
            tally['coin flip, unresolved at M1'] += 1
            unresolved_at_m1 += 1
        else:
            tally['coin flip, M1 says neither'] += 1

    return tally, disagreements, unresolved_at_m1


def band(tally):
    """
    (coin flips, trades that entered and closed).

    Only trades that both entered and reached an outcome carry information
    about ordering inside a bar, so they are the denominator; one that never
    entered, or was still open when the data ran out, says nothing either way.
    """
    flips = (tally['coin flip, settled by M1']
             + tally['coin flip, unresolved at M1']
             + tally['coin flip, M1 says neither'])
    return flips, tally['decided by the coarse bars'] + flips


def main(argv=None):
    load_strategies()
    today = datetime.date.today().strftime('%Y-%m-%d')
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument('--instrument', default='EUR_USD')
    parser.add_argument('--strategy', default='AG01', choices=sorted(STRATEGIES))
    parser.add_argument('--granularity', default='H1',
                        help='the granularity the strategy signals on')
    parser.add_argument('--reference', default='M1',
                        help='the finer granularity used as the reference')
    parser.add_argument('--dtfrom', default='2006-01-01')
    parser.add_argument('--dtto', default=today)
    args = parser.parse_args(argv)

    getLogger()
    logging.getLogger('parity_deriva.trading.trading').setLevel(logging.WARNING)

    dtfrom = datetime.datetime.strptime(args.dtfrom, '%Y-%m-%d')
    dtto = datetime.datetime.strptime(args.dtto, '%Y-%m-%d')
    span = granularityToTimedelta(args.granularity)

    signals = collect_signals(args.strategy, args.instrument,
                              args.granularity, dtfrom, dtto)
    coarse = load_bars(args.instrument, args.granularity)
    fine = load_bars(args.instrument, args.reference)

    tally, disagreements, unresolved = measure(signals, coarse, fine, span)
    seen = sum(tally.values())
    flips, decided = band(tally)

    print("%s %s on %s, reference %s, %s .. %s"
          % (args.strategy, args.instrument, args.granularity, args.reference,
             dtfrom.date(), dtto.date()))
    print("bars: %d %s, %d %s" % (len(coarse), args.granularity,
                                  len(fine), args.reference))
    print("signals: %d, examined: %d" % (len(signals), seen))
    if not seen:
        print("nothing to measure")
        return 1

    print()
    for label, count in sorted(tally.items(), key=lambda kv: -kv[1]):
        print("  %-38s %5d  %5.1f%%" % (label, count, 100.0 * count / seen))

    if not decided:
        print("\nno trade both entered and reached an outcome: nothing to measure")
        return 1

    print()
    print("DIVERGENCE BAND   (over the %d trades that entered and closed)" % decided)
    print("  the %s bars cannot decide             %5d  %5.2f%%"
          % (args.granularity, flips, 100.0 * flips / decided))
    print("  %s cannot decide either              %5d  %5.2f%%"
          % (args.reference, unresolved, 100.0 * unresolved / decided))
    print()
    print("  Read it as: fed %s the simulator's outcome is a coin flip on"
          % args.granularity)
    print("  %.2f%% of closed trades; fed %s that falls to %.2f%%. An alarm on"
          % (100.0 * flips / decided, args.reference,
             100.0 * unresolved / decided))
    print("  real-versus-simulated disagreement must sit above whichever of")
    print("  the two applies, or it will fire on the width of the bars.")
    print("  Trades that never entered or never closed are excluded: they")
    print("  say nothing about ordering inside a bar.")

    if disagreements:
        print()
        print("first coin flips the reference settled:")
        for key, coarse_label, outcome, fine_label in disagreements[:8]:
            print("  %-34s %s -> %-6s at %s" % (key, coarse_label, outcome, fine_label))
    return 0


if __name__ == '__main__':
    sys.exit(main())
