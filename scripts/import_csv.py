"""
Import downloaded CSV candles into the HDF5 store the rest of the stack reads.

The filename carries everything needed, so nothing has to be passed on the
command line:

    <instrument>_<timeframe>_<from>_<to>-<ASK|BID>.csv
    eurusd_d1_20160121_20260920-ASK.csv  ->  EUR_USD.hd5, key /D

The two sides are one store row: the file pair is read together, mid is the
average of ask and bid leg by leg, and volume is taken from the BID file (the
two differ slightly and the store holds a single figure). A stem with only one
side present is skipped - there is no mid to build.

    python scripts/import_csv.py                 # every pair in CSV_DATA_DIR
    python scripts/import_csv.py ../data         # a directory
    python scripts/import_csv.py a.csv b.csv     # named files
    python scripts/import_csv.py --dry-run       # report, write nothing

An export that labels the two sides backwards is refused rather than stored -
ask below bid would have every backtest buying under its own sell price. Pass
--swap-sides to read such a pair the right way round.

Idempotent: a key is merged, not appended. Rows already in the store keep
their place and the imported values win on a collision, so running it twice
leaves the store exactly as the first run did.
"""

import argparse
import glob
import os
import re
import sys

import pandas as pd

from parity_deriva.etc import settings

LEGS = {'open': 'o', 'high': 'h', 'low': 'l', 'close': 'c'}
NAME = re.compile(r'^(?P<instrument>[^_]+)_(?P<timeframe>[^_]+)_'
                  r'(?P<dtfrom>\d+)_(?P<dtto>\d+)-(?P<side>ASK|BID)$',
                  re.IGNORECASE)


def instrument_name(raw):
    """eurusd -> EUR_USD. A name that already carries its separator is kept."""
    raw = raw.upper()
    if '_' not in raw and len(raw) == 6:
        return raw[:3] + '_' + raw[3:]
    return raw


def granularity(raw):
    """
    d1 -> D, h1 -> H1. OANDA names the daily and weekly candle without a
    count, and the store is keyed the way data/bulksaver.py writes it.
    """
    raw = raw.upper()
    if raw in ('D1', 'W1'):
        return raw[0]
    return raw


def parse_name(path):
    """(instrument, granularity, side) from the filename, or None."""
    stem = os.path.basename(path)
    stem = stem[:-4] if stem.lower().endswith('.csv') else stem
    match = NAME.match(stem)
    if match is None:
        return None
    return (instrument_name(match.group('instrument')),
            granularity(match.group('timeframe')),
            match.group('side').upper())


def read_side(path):
    """One CSV as a frame indexed by its epoch-millisecond timestamp."""
    frame = pd.read_csv(path)
    frame.index = pd.to_datetime(frame['timestamp'], unit='ms').dt.as_unit('us')
    frame.index.name = None
    return frame


def build(ask_path, bid_path, swap=False):
    """The store's flat 13-column frame from an ASK/BID file pair."""
    ask, bid = read_side(ask_path), read_side(bid_path)
    if swap:
        ask, bid = bid, ask
    index = ask.index.intersection(bid.index)
    # ask below bid is not a market that exists, so a file pair that says so
    # is labelled the wrong way round and must not reach the store: every
    # backtest on it would buy under its own sell price.
    inverted = (ask.loc[index, 'close'] < bid.loc[index, 'close']).mean()
    if inverted > 0.5:
        raise ValueError(
            "ask is below bid on %.0f%% of rows - the two files are labelled "
            "the wrong way round. Re-run with --swap-sides to read the -BID "
            "file as ask." % (inverted * 100))
    data = {}
    for column, leg in LEGS.items():
        data['ask_' + leg] = ask.loc[index, column].astype('float64')
        data['bid_' + leg] = bid.loc[index, column].astype('float64')
        data['mid_' + leg] = (data['ask_' + leg] + data['bid_' + leg]) / 2
    data['volume'] = bid.loc[index, 'volume'].round().astype('int64')
    dropped = (len(ask.index) - len(index), len(bid.index) - len(index))
    return pd.DataFrame(data, index=index).sort_index(), dropped


def merge(path, key, frame, dry_run=False):
    """
    Put the frame into the store under key, keeping rows already there that
    the import does not cover. Returns (added, replaced).
    """
    existing = None
    if os.path.exists(path):
        store = pd.HDFStore(path, mode='r')
        try:
            existing = store[key] if key in store else None
        finally:
            store.close()

    if existing is None:
        added, replaced, merged = len(frame.index), 0, frame
    else:
        kept = existing[~existing.index.isin(frame.index)]
        replaced = len(existing.index) - len(kept.index)
        added = len(frame.index) - replaced
        merged = pd.concat([kept, frame]).sort_index()

    if not dry_run:
        # ponytail: the whole key is rewritten rather than appended, so HDF5
        # keeps the old blocks and the file grows. Harmless at D1; repack or
        # switch to an append of only the new rows if M1 imports get heavy.
        store = pd.HDFStore(path, mode='a')
        try:
            store.put(key, merged, format='table')
        finally:
            store.close()
    return added, replaced


def pairs(paths):
    """Group the given CSV paths into {(instrument, granularity): {side: path}}."""
    found = {}
    for path in paths:
        parsed = parse_name(path)
        if parsed is None:
            print("skipping %s: name is not <instrument>_<tf>_<from>_<to>-<SIDE>.csv"
                  % os.path.basename(path))
            continue
        instrument, gran, side = parsed
        found.setdefault((instrument, gran), {})[side] = path
    return found


def expand(paths):
    """Directories stand for the CSV files in them."""
    out = []
    for path in paths:
        out.extend(sorted(glob.glob(os.path.join(path, '*.csv')))
                   if os.path.isdir(path) else [path])
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[1],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('paths', nargs='*', default=[settings.CSV_DATA_DIR],
                        help='CSV files or directories (default: CSV_DATA_DIR)')
    parser.add_argument('--data-dir', default=settings.DATA_DIR,
                        help='where the .hd5 stores live')
    parser.add_argument('--swap-sides', action='store_true',
                        help='read the -BID file as ask and the -ASK file as '
                             'bid, for an export that labels them backwards')
    parser.add_argument('--dry-run', action='store_true',
                        help='report what would change, write nothing')
    args = parser.parse_args(argv)

    status = 0
    for (instrument, gran), sides in sorted(pairs(expand(args.paths)).items()):
        if set(sides) != {'ASK', 'BID'}:
            print("%s %s: only %s present, need both sides for mid - skipped"
                  % (instrument, gran, '/'.join(sorted(sides))))
            status = 1
            continue

        try:
            frame, dropped = build(sides['ASK'], sides['BID'], args.swap_sides)
        except ValueError as problem:
            print("%s %s: %s" % (instrument, gran, problem))
            status = 1
            continue
        if any(dropped):
            print("%s %s: %d ask and %d bid rows have no counterpart, dropped"
                  % (instrument, gran, dropped[0], dropped[1]))

        path = os.path.join(args.data_dir, "%s.hd5" % instrument)
        added, replaced = merge(path, '/' + gran, frame, args.dry_run)
        print("%s %s %s: %d rows %s to %s, %d new, %d replaced (%s..%s)"
              % (instrument, gran, os.path.basename(path), len(frame.index),
                 'would go' if args.dry_run else 'written', path,
                 added, replaced, frame.index.min(), frame.index.max()))
    return status


if __name__ == '__main__':
    sys.exit(main())
