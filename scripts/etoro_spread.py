"""
Measure the spread before writing it into ETORO_SPREAD.

eToro serves one price per candle. The strategies here read an ask and a bid
- AG01 buys the high of the ask and stops out at the low of the bid - and
those two prices are not in the candle data. ETORO_SPREAD supplies them as a
model, and this script is how the number in it stops being a guess.

The rates route is the one place eToro publishes a real bid and a real ask,
so this samples it and reports the spread's distribution per instrument. What
comes out is a summary of the sampling window, not a constant of the
instrument: the spread widens at the open, around news and overnight, so
sample when you intend to trade, and read the maximum as seriously as the
median.

    python scripts/etoro_spread.py --instrument EUR_USD --samples 60
    python scripts/etoro_spread.py --instrument EUR_USD --instrument DE30_EUR \
        --samples 120 --interval 10

A model set to the median under-prices half the fills by construction; the
difference is what trading/parity.py then measures as slippage.
"""

import argparse
import statistics
import sys
import time

from parity_deriva.etc import settings
from parity_deriva.lib.etoro import (EToroAPI, EToroError, instrumentId,
                                     instrumentName)


def sample(api, ids):
    """One read of the rates route: {instrument name: (bid, ask, quoteType)}."""
    status, payload = api.get('rates',
                              params={'instrumentIds': ",".join(str(i) for i in ids)})
    if status not in (200, 206) or not payload:
        return None
    out = {}
    for row in payload.get('results') or []:
        name = instrumentName(row.get('instrumentId'), settings)
        if name is None or row.get('bid') is None or row.get('ask') is None:
            continue
        out[name] = (float(row['bid']), float(row['ask']), row.get('quoteType'))
    return out


def report(name, spreads, delayed):
    """The distribution, and enough of it to choose a number from."""
    spreads = sorted(spreads)
    n = len(spreads)
    print("\n%s  (%d samples%s)" % (name, n,
                                    ", %d delayed" % delayed if delayed else ""))
    if not n:
        print("  nothing usable came back")
        return
    print("  min     %.6f" % spreads[0])
    print("  median  %.6f" % statistics.median(spreads))
    print("  mean    %.6f" % statistics.fmean(spreads))
    print("  p90     %.6f" % spreads[min(n - 1, int(n * 0.9))])
    print("  max     %.6f" % spreads[-1])
    if delayed:
        print("  note: some quotes were marked delayed, and a delayed quote's")
        print("        spread is not the one an order would have met")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument('--instrument', action='append', required=True,
                        dest='instruments',
                        help="instrument to sample, repeatable; named the way "
                             "this project names it, e.g. EUR_USD")
    parser.add_argument('--samples', type=int, default=60,
                        help="how many reads to take (default 60)")
    parser.add_argument('--interval', type=float, default=5.0,
                        help="seconds between reads (default 5)")
    args = parser.parse_args(argv)

    try:
        api = EToroAPI(setup=settings)
        ids = [instrumentId(name, settings) for name in args.instruments]
    except EToroError as exc:
        print("cannot sample: %s" % exc)
        return 2

    collected = dict((name, []) for name in args.instruments)
    delayed = dict((name, 0) for name in args.instruments)
    taken = 0
    print("sampling %d instrument(s), %d reads every %.1fs"
          % (len(ids), args.samples, args.interval))
    try:
        while taken < args.samples:
            batch = sample(api, ids)
            taken += 1
            if batch is None:
                print("  read %d: the rates route did not answer" % taken)
            else:
                for name, (bid, ask, quote) in batch.items():
                    collected[name].append(ask - bid)
                    if quote == 'delayed':
                        delayed[name] += 1
            if taken < args.samples and args.interval > 0:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\ninterrupted after %d reads" % taken)

    for name in args.instruments:
        report(name, collected[name], delayed[name])

    print("\nETORO_SPREAD takes a number in the instrument's own units, or a")
    print("dict per instrument. Half of whatever you set is applied either")
    print("side of the served price.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
