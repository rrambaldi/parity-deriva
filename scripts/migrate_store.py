"""
Bring an HDF5 candle store written under Python 2 forward to pandas 3.

Two things make such a store unusable with a current pandas, and neither
loses data - both are metadata:

1. Python 2 wrote the store's own descriptors ("pandas_type", "table_type",
   "index_kind", ...) as bytes. pandas 2 decoded them; pandas 3 does not, and
   opening the store raises
       TypeError: a bytes-like object is required, not 'str'
   before any row is touched.

2. The timestamp index is stored at nanosecond resolution, while pandas 3
   gives new timestamps microsecond resolution. Reading is fine, but appending
   a freshly downloaded block raises
       TypeError: incompatible kind in col [datetime64[ns] - datetime64[us]]
   which is exactly what data/bulksaver.py does on every run.

So the store is first opened with its descriptors decoded, then rewritten with
the index at the resolution new data will arrive in. The rows themselves are
copied through pandas untouched.

    python scripts/migrate_store.py                 # every store in DATA_DIR
    python scripts/migrate_store.py --dry-run       # report, change nothing
    python scripts/migrate_store.py path/to/X.hd5   # named stores

A .bak copy is kept next to each store unless --no-backup is given. Running it
twice is harmless: a store that needs nothing is reported and skipped.
"""

import argparse
import glob
import os
import shutil
import sys
import tempfile

import numpy as np
import pandas as pd
import tables

from parity_deriva.data import market
from parity_deriva.etc import settings


def target_unit():
    """The datetime resolution pandas gives to newly created timestamps."""
    dtype = pd.DataFrame(index=[pd.Timestamp('2020-01-01')]).index.dtype
    return np.datetime_data(dtype)[0]


def decode_attrs(path):
    """
    Decode the byte-valued HDF5 attributes Python 2 left behind. Returns the
    names decoded. Anything that is not short printable ASCII is left alone:
    pickled attributes are bytes too and must not be touched.
    """
    decoded = []
    handle = tables.open_file(path, mode='a')
    try:
        for node in handle.walk_nodes("/"):
            attrs = node._v_attrs
            for name in attrs._f_list('all'):
                value = attrs[name]
                if not isinstance(value, bytes) or len(value) >= 64:
                    continue
                try:
                    text = value.decode('ascii')
                except UnicodeDecodeError:
                    continue
                attrs[name] = text
                decoded.append("%s:%s" % (node._v_pathname, name))
    finally:
        handle.close()
    return decoded


def inspect(path):
    """
    Report (needs_migration, note) without modifying anything.
    """
    try:
        handle = tables.open_file(path, mode='r')
    except Exception as exc:
        return False, "unreadable: %s" % exc
    byte_attrs = 0
    try:
        for node in handle.walk_nodes("/"):
            attrs = node._v_attrs
            for name in attrs._f_list('all'):
                value = attrs[name]
                if isinstance(value, bytes) and len(value) < 64:
                    try:
                        value.decode('ascii')
                        byte_attrs += 1
                    except UnicodeDecodeError:
                        pass
    finally:
        handle.close()

    if byte_attrs:
        return True, "%d byte-valued descriptors (written under Python 2)" % byte_attrs

    unit = target_unit()
    store = pd.HDFStore(path, mode='r')
    try:
        keys = list(store.keys())
        if not keys:
            return False, "empty: no keys"
        stale = []
        for key in keys:
            index = store.select(key, start=0, stop=1).index
            if isinstance(index, pd.DatetimeIndex):
                have = np.datetime_data(index.dtype)[0]
                if have != unit:
                    stale.append("%s at %s" % (key, have))
        if stale:
            return True, "index resolution %s, new data is %s" % (", ".join(stale), unit)
        return False, "already current (%s)" % ", ".join(keys)
    finally:
        store.close()


def migrate(path, backup=True):
    """
    Migrate one store in place. The original is only replaced once the new
    file has been written and its row counts checked, so an interrupted run
    leaves the store as it was.
    """
    market.guard(path)
    unit = target_unit()
    directory = os.path.dirname(os.path.abspath(path))

    if backup:
        shutil.copy2(path, path + ".bak")

    work = tempfile.mktemp(prefix=".migrate-", suffix=".hd5", dir=directory)
    fresh = tempfile.mktemp(prefix=".migrate-", suffix=".hd5", dir=directory)
    shutil.copy2(path, work)
    try:
        decode_attrs(work)

        counts = {}
        old = pd.HDFStore(work, mode='r')
        new = pd.HDFStore(fresh, mode='w')
        try:
            for key in list(old.keys()):
                frame = old[key]
                if isinstance(frame.index, pd.DatetimeIndex):
                    frame.index = frame.index.as_unit(unit)
                new.append(key, frame)
                counts[key] = len(frame)
        finally:
            new.close()
            old.close()

        check = pd.HDFStore(fresh, mode='r')
        try:
            for key, rows in counts.items():
                got = check.get_storer(key).nrows
                if got != rows:
                    raise RuntimeError(
                        "%s: wrote %d rows, store reports %d" % (key, rows, got))
        finally:
            check.close()

        os.replace(fresh, path)
        return counts
    finally:
        for leftover in (work, fresh):
            if os.path.exists(leftover):
                os.remove(leftover)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument('stores', nargs='*',
                        help='stores to migrate (default: *.hd5 in DATA_DIR)')
    parser.add_argument('--dry-run', action='store_true',
                        help='report what would change and stop')
    parser.add_argument('--no-backup', action='store_true',
                        help='do not leave a .bak copy')
    args = parser.parse_args(argv)

    paths = args.stores or sorted(glob.glob(os.path.join(market.directory(), '*.hd5')))
    if not paths:
        print("no stores found in %s" % market.directory())
        return 1

    print("pandas %s, new timestamps at %s resolution"
          % (pd.__version__, target_unit()))
    migrated = 0
    for path in paths:
        needed, note = inspect(path)
        name = os.path.basename(path)
        if not needed:
            print("  %-20s skipped - %s" % (name, note))
            continue
        if args.dry_run:
            print("  %-20s would migrate - %s" % (name, note))
            continue
        counts = migrate(path, backup=not args.no_backup)
        summary = ", ".join("%s:%d rows" % (k, n) for k, n in sorted(counts.items()))
        print("  %-20s migrated - %s" % (name, summary))
        migrated += 1

    if not args.dry_run and migrated:
        print("%d store(s) migrated%s" % (
            migrated, "" if args.no_backup else "; .bak copies kept"))
    return 0


if __name__ == '__main__':
    sys.exit(main())
