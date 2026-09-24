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
# the aggregation lives with the reader, which derives the same bars on the fly
from parity_deriva.data.store import AGGREGATE, aggregation_map, resample  # noqa: F401

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
