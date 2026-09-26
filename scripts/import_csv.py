"""
Import downloaded CSV candles into the HDF5 store the rest of the stack reads.

The filename carries everything needed, so nothing has to be passed on the
command line:

    <instrument>_<timeframe>_<from>_<to>-<ASK|BID>.csv
    eurusd_d1_20160121_20260920-ASK.csv  ->  EUR_USD.hd5, key /D

A side is a CSV with the header timestamp,open,high,low,close,volume - the
timestamp in UTC epoch milliseconds, the candle's open - or the same fields as
JSON, an array of objects or of rows in that order: what dukascopy-node writes
with -f csv, -f json or -f array, and -v for the volume.

    npx dukascopy-node -i eurusd -from 2015-01-01 -to 2026-09-20 -t m5 -p ask -v \
        -f csv -fn eurusd_m5_20150101_20260920-ASK      # and -p bid ... -BID

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
leaves the store exactly as the first run did. It does not even read it
twice: the sha1 of every set imported is kept on the store key it went into,
and a set whose files still hash the same is skipped without being read.
Because the record lives on the key, a store deleted or rebuilt forgets it.
"""

import argparse
import glob
import hashlib
import json
import os
import re
import sys

import pandas as pd

from parity_deriva.data import market
from parity_deriva.etc import settings

LEGS = {'open': 'o', 'high': 'h', 'low': 'l', 'close': 'c'}
#: a side's fields, in the order a CSV header and a JSON row have them
FIELDS = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
#: the files a side can be
EXTENSIONS = ('.csv', '.json')
NAME = re.compile(r'^(?P<instrument>[^_]+)_(?P<timeframe>[^_]+)_'
                  r'(?P<dtfrom>\d+)_(?P<dtto>\d+)-(?P<side>ASK|BID)$',
                  re.IGNORECASE)


#: an exporter's name for a market -> the name the stack trades it under
ALIASES = {'DEUIDXEUR': 'DE30_EUR'}


def instrument_name(raw):
    """eurusd -> EUR_USD. A name that already carries its separator is kept."""
    raw = raw.upper()
    if raw in ALIASES:
        return ALIASES[raw]
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
    stem, extension = os.path.splitext(os.path.basename(path))
    stem = stem if extension.lower() in EXTENSIONS else stem + extension
    match = NAME.match(stem)
    if match is None:
        return None
    return (instrument_name(match.group('instrument')),
            granularity(match.group('timeframe')),
            match.group('side').upper())


def read_side(path, progress=None):
    """
    One side as a frame indexed by its epoch-millisecond timestamp. A CSV is
    read in chunks so that progress(bytes read so far) can be told as it goes.
    """
    if path.lower().endswith('.json'):
        # ponytail: read whole, a few times its size in memory; a long M1 or
        # M5 series goes as CSV, or this becomes a streaming parser
        with open(path) as handle:
            rows = json.load(handle)
        if not isinstance(rows, list):
            raise ValueError("%s is not a JSON array of candles" % os.path.basename(path))
        frame = pd.DataFrame(rows, columns=FIELDS if rows and isinstance(rows[0], list) else None)
        if progress is not None:
            progress(os.path.getsize(path))
        return indexed(frame, os.path.basename(path))
    else:
        parts = []
        with open(path, 'rb') as handle:
            for chunk in pd.read_csv(handle, chunksize=200000):
                parts.append(chunk)
                if progress is not None:
                    progress(handle.tell())
        frame = pd.concat(parts, ignore_index=True)
    return indexed(frame, os.path.basename(path))


def indexed(frame, name):
    """A side's rows by their epoch-millisecond timestamp; `name` is for a refusal."""
    missing = [f for f in FIELDS if f not in frame.columns]
    if missing:
        raise ValueError("%s has no %s (dukascopy-node leaves the volume out without -v)"
                         % (name, ', '.join(missing)))
    frame.index = pd.to_datetime(frame['timestamp'], unit='ms').dt.as_unit('us')
    frame.index.name = None
    return frame


def build(ask_path, bid_path, swap=False, progress=None):
    """
    The store's flat 13-column frame from an ASK/BID file pair. progress, if
    given, is told the bytes of the pair read so far.
    """
    first = os.path.getsize(ask_path)
    ask = read_side(ask_path, progress)
    bid = read_side(bid_path, progress and (lambda n: progress(first + n)))
    if swap:
        ask, bid = bid, ask
    return combine(ask, bid)


def combine(ask, bid):
    """The store's frame from the two sides, and the rows of each with no counterpart."""
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


def merge(path, key, frame, dry_run=False, keep=False):
    """
    Put the frame into the store under key, keeping rows already there that
    the import does not cover. Returns (added, replaced, written). With `keep`
    the rows already there win instead, and only the missing ones go in: a
    push over MCP (web/mcp.py) adds to a store, it does not correct one.

    The store is written as a copy that replaces it, with the market folder
    locked, from the server that writes it only (data/market.py).
    """
    if dry_run:
        return _merge(path, key, frame, True, keep)
    path = os.path.realpath(path)
    market.guard(path)
    with market.lock(os.path.dirname(path)):
        return _merge(path, key, frame, False, keep)


def _merge(path, key, frame, dry_run, keep):
    existing = None
    if os.path.exists(path):
        store = pd.HDFStore(path, mode='r')
        try:
            existing = store[key] if key in store else None
        finally:
            store.close()
    if keep and existing is not None:
        frame = frame[~frame.index.isin(existing.index)]
        if not len(frame.index):
            return 0, 0, False

    if existing is None:
        added, replaced, merged = len(frame.index), 0, frame
    else:
        kept = existing[~existing.index.isin(frame.index)]
        replaced = len(existing.index) - len(kept.index)
        added = len(frame.index) - replaced
        merged = pd.concat([kept, frame]).sort_index()

    if dry_run:
        return added, replaced, False
    # Rewriting a key leaves its old blocks in the file - HDF5 does not give
    # the space back - so a re-run that changes nothing writes nothing, and
    # rows that only extend the series are appended rather than rewritten.
    if existing is not None and merged.equals(existing):
        return added, replaced, False
    with market.rewrite(path) as work:
        store = pd.HDFStore(work, mode='a')
        try:
            if existing is not None and frame.index.min() > existing.index.max():
                store.append(key, frame)
            else:
                # ponytail: a correction inside the series still rewrites the
                # whole key and grows the file; ptrepack reclaims it
                store.put(key, merged, format='table')
        finally:
            store.close()
    return added, replaced, True


def set_name(path):
    """The import set a file belongs to: its name without -ASK/-BID.csv."""
    return re.sub(r'-(ASK|BID)(\.csv|\.json)?$', '', os.path.basename(path),
                  flags=re.IGNORECASE)


def fingerprint(paths):
    """sha1 over the files' bytes, in the order given."""
    digest = hashlib.sha1()
    for path in paths:
        with open(path, 'rb') as handle:
            for block in iter(lambda: handle.read(1 << 20), b''):
                digest.update(block)
    return digest.hexdigest()


def imported(path, key):
    """set name -> sha1 of every set already merged into this key."""
    if not os.path.exists(path):
        return {}
    store = pd.HDFStore(path, mode='r')
    try:
        if key not in store:
            return {}
        return dict(getattr(store.get_storer(key).attrs, 'imported_sets', None) or {})
    finally:
        store.close()


def remember(path, key, name, digest):
    """Record a set as merged into key. put() drops attributes, so after it."""
    path = os.path.realpath(path)
    market.guard(path)
    with market.lock(os.path.dirname(path)), market.rewrite(path) as work:
        sets = imported(work, key)
        sets[name] = digest
        store = pd.HDFStore(work, mode='a')
        try:
            store.get_storer(key).attrs.imported_sets = sets
        finally:
            store.close()


def pairs(paths, report=print):
    """
    Group the given CSV paths into {(instrument, granularity, set): {side:
    path}}. The set is the pair's shared name, so two exports of the same
    series over different ranges are two sets, imported one after the other,
    rather than one silently taking the other's place.
    """
    found = {}
    for path in paths:
        parsed = parse_name(path)
        if parsed is None:
            report("skipping %s: name is not <instrument>_<tf>_<from>_<to>-<SIDE>.csv or .json"
                  % os.path.basename(path))
            continue
        instrument, gran, side = parsed
        found.setdefault((instrument, gran, set_name(path)), {})[side] = path
    return found


def expand(paths):
    """Directories stand for the CSV and JSON files in them."""
    out = []
    for path in paths:
        out.extend(sorted(f for e in EXTENSIONS for f in glob.glob(os.path.join(path, '*' + e)))
                   if os.path.isdir(path) else [path])
    return out


def main(argv=None, report=print, progress=None):
    """
    report(line) is told what happened to each set; progress(done, total,
    text), if given, how far the reading has got, in bytes of CSV.
    """
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[1],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('paths', nargs='*', default=[settings.CSV_DATA_DIR],
                        help='CSV files or directories (default: CSV_DATA_DIR)')
    parser.add_argument('--data-dir', default=market.directory(),
                        help='where the .hd5 stores live (default: the market folder)')
    parser.add_argument('--swap-sides', action='store_true',
                        help='read the -BID file as ask and the -ASK file as '
                             'bid, for an export that labels them backwards')
    parser.add_argument('--dry-run', action='store_true',
                        help='report what would change, write nothing')
    args = parser.parse_args(argv)
    tell = progress or (lambda done, total, text: None)

    status = 0
    todo = []
    for (instrument, gran, name), sides in sorted(pairs(expand(args.paths), report).items()):
        if set(sides) != {'ASK', 'BID'}:
            report("%s %s: only %s present, need both sides for mid - skipped"
                   % (instrument, gran, '/'.join(sorted(sides))))
            status = 1
            continue
        path = os.path.join(args.data_dir, "%s.hd5" % instrument)
        digest = fingerprint([sides['ASK'], sides['BID']])
        if imported(path, '/' + gran).get(name) == digest:
            report("%s %s: %s already imported into %s - skipped"
                   % (instrument, gran, name, os.path.basename(path)))
            continue
        todo.append((instrument, gran, name, sides, path, digest))

    total = sum(os.path.getsize(s['ASK']) + os.path.getsize(s['BID'])
                for _, _, _, s, _, _ in todo)
    done = 0
    for instrument, gran, name, sides, path, digest in todo:
        size = os.path.getsize(sides['ASK']) + os.path.getsize(sides['BID'])
        label = "%s %s" % (instrument, gran)
        tell(done, total, "%s: reading %s" % (label, name))
        try:
            frame, dropped = build(sides['ASK'], sides['BID'], args.swap_sides,
                                   lambda n, at=done: tell(at + n, total,
                                                           "%s: reading %s" % (label, name)))
        except ValueError as problem:
            report("%s: %s" % (label, problem))
            status = 1
            done += size
            continue
        if any(dropped):
            report("%s: %d ask and %d bid rows have no counterpart, dropped"
                   % (label, dropped[0], dropped[1]))

        done += size
        tell(done, total, "%s: writing %s" % (label, os.path.basename(path)))
        added, replaced, written = merge(path, '/' + gran, frame, args.dry_run)
        if not args.dry_run:
            remember(path, '/' + gran, name, digest)
        report("%s %s: %d rows %s %s, %d new, %d replaced (%s..%s)"
               % (label, os.path.basename(path), len(frame.index),
                  'would go to' if args.dry_run else
                  'written to' if written else 'already in', path,
                  added, replaced, frame.index.min(), frame.index.max()))
    tell(total, total, "done")
    return status

if __name__ == '__main__':
    sys.exit(main())
