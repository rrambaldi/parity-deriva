"""
Build a coarser granularity inside an existing HDF5 candle store.

A store is keyed by granularity - "/M1", "/H1", "/D" - which is the layout
data/bulksaver.py writes and data/replay.py reads, so a coarser series belongs
in the same file rather than in a separate one. This aggregates one key into
another:

    python scripts/resample_store.py --source M1 --target H1
    python scripts/resample_store.py --source M1 --target H1 --dry-run
    python scripts/resample_store.py EUR_USD.hd5 --source M1 --target H4

o/h/l/c are aggregated first/max/min/last for each of the ask, bid and mid
triplets, and volume is summed - the same convention data/candles.py uses for
its live aggregation. Partial periods at the edges of a trading session are
kept: a bar built from the four minutes that actually traded is still the truth
about that hour.

Only whole-hour-and-coarser targets are aligned the way OANDA aligns its own
candles. OANDA aligns to alignmentTimezone (Europe/Rome in etc/settings.py),
which is a whole-hour offset from UTC, so H1 and H4 boundaries agree. D and W
would not: use the API for those if the alignment matters.
"""

import argparse
import glob
import os
import sys

import pandas as pd

from parity_deriva.etc import settings
from parity_deriva.lib.utils import granularityToTimedelta

AGGREGATE = {'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}


def aggregation_map(columns):
    """
    Map the flat store columns onto their aggregation. ask_h takes the max,
    bid_o the first, volume the sum.
    """
    how = {}
    for column in columns:
        if column == 'volume':
            how[column] = 'sum'
            continue
        side, _, leg = column.partition('_')
        if side in ('ask', 'bid', 'mid') and leg in AGGREGATE:
            how[column] = AGGREGATE[leg]
        else:
            raise ValueError("unexpected column %r in store" % column)
    return how


def resample(frame, target):
    """Aggregate a flat candle frame up to the target granularity."""
    rule = granularityToTimedelta(target)
    if rule is None:
        raise ValueError("unknown granularity %r" % target)
    out = frame.resample(rule).agg(aggregation_map(frame.columns))
    # A period with no source bars is the market being closed, and there is no
    # candle to report for it. Such a row has NaN prices but volume 0, because
    # summing nothing gives 0 rather than NaN - so the emptiness has to be
    # judged on the prices, not on the whole row.
    prices = [c for c in out.columns if c != 'volume']
    if prices:
        out = out[~out[prices].isna().all(axis=1)]
    if 'volume' in out.columns:
        out['volume'] = out['volume'].fillna(0).astype('int64')
    return out


def process(path, source, target, force=False, dry_run=False):
    src_key = '/' + source.lstrip('/')
    dst_key = '/' + target.lstrip('/')

    store = pd.HDFStore(path, mode='r')
    try:
        keys = list(store.keys())
        if src_key not in keys:
            return None, "no %s key (has %s)" % (src_key, ", ".join(keys) or "nothing")
        if dst_key in keys and not force:
            return None, "%s already present (--force to rebuild)" % dst_key
        frame = store[src_key]
    finally:
        store.close()

    built = resample(frame, target)
    note = "%s: %d bars -> %s: %d bars (%s .. %s)" % (
        src_key, len(frame), dst_key, len(built),
        built.index.min(), built.index.max())
    if dry_run:
        return None, "would build " + note

    store = pd.HDFStore(path, mode='a')
    try:
        if dst_key in store:
            store.remove(dst_key)
        store.append(dst_key, built)
    finally:
        store.close()
    return len(built), "built " + note


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument('stores', nargs='*',
                        help='stores to work on (default: *.hd5 in DATA_DIR)')
    parser.add_argument('--source', default='M1', help='granularity to read')
    parser.add_argument('--target', default='H1', help='granularity to build')
    parser.add_argument('--force', action='store_true',
                        help='rebuild the target even if it is already there')
    parser.add_argument('--dry-run', action='store_true',
                        help='report what would be built and stop')
    args = parser.parse_args(argv)

    paths = args.stores or sorted(glob.glob(os.path.join(settings.DATA_DIR, '*.hd5')))
    if not paths:
        print("no stores found in %s" % settings.DATA_DIR)
        return 1

    for path in paths:
        name = os.path.basename(path)
        try:
            _, note = process(path, args.source, args.target,
                              force=args.force, dry_run=args.dry_run)
        except Exception as exc:
            note = "failed: %s: %s" % (type(exc).__name__, exc)
        print("  %-20s %s" % (name, note))
    return 0


if __name__ == '__main__':
    sys.exit(main())
