"""
Build the shared spread set, spread.json in the market folder.

lib/spread.py reads it for every broker that serves one price a bar - eToro,
IB, Twelve Data - whose own *_SPREAD setting is unset: bid and ask are the
served price minus and plus half of it.

For each instrument and each five minutes of the week, on the instrument's
own clock, the file holds the widest p90 among the sources that quote a real
ask and bid:

  store       the market folder's candles (dukascopy), the last --years
  recorded    every feed the live sessions wrote into candles.db, same window
  --provider  a broker asked now for its last --weeks of M5 (ig, oanda, mt5;
              mt5:<login> picks one of MT5_TERMINALS)

One cautious set for all of them rather than one per broker: a fill on any
one-price broker is priced at least as badly as the worst source quoted, most
of the time. The p90 and not the maximum, or one broker's worst bar in two
years would stand for every bar of its slot.

Each source's slots are kept in the file, so a broker measured once still
counts when the set is rebuilt without it; asking it again replaces them.

The clock matters. The FX day turns at 17:00 in New York, summer and winter,
so the rollover's spread - ten to twenty times the usual - is one slot all
year on New York time and two on UTC. The DAX follows Frankfurt.

    python scripts/spread_profile.py                      # print, write nothing
    python scripts/spread_profile.py --write
    python scripts/spread_profile.py --provider ig --provider mt5:62935868 --write

--write keeps to the market folder's rule, only the writer writes it; --force
writes from a reader too (one file replaced whole, no bars to lose).
"""

import argparse
import datetime
import glob
import json
import os
import sys
import types

import pandas as pd

from parity_deriva.data import candledb, market
from parity_deriva.data import store as stores
from parity_deriva.etc import settings
from parity_deriva.lib import spread
from parity_deriva.lib.utils import granularityToTimedelta, pipSize

FX = 'America/New_York'
#: instruments not on the FX clock
ZONES = {'DE30_EUR': 'Europe/Berlin'}
QUANTILE = 0.9
DAYS = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')


def samples(frame, period):
    """
    The spreads quoted at each bar's open and at its close, by the naive UTC
    time they were quoted. A negative one is a feed fault, not a spread.
    """
    opens = frame['ask_o'] - frame['bid_o']
    closes = frame['ask_c'] - frame['bid_c']
    closes.index = closes.index + period - pd.Timedelta(seconds=1)
    out = pd.concat([opens, closes]).dropna()
    return out[out >= 0]


def slots(spreads, zone):
    """The p90 of each slot of the week, None where nothing was quoted."""
    local = pd.DatetimeIndex(spreads.index).tz_localize('UTC').tz_convert(zone)
    key = (local.weekday * 24 * 60 + local.hour * 60 + local.minute) // spread.SLOT
    q = spreads.groupby(key).quantile(QUANTILE)
    return [round(float(q[i]), 8) if i in q.index else None for i in range(spread.SLOTS)]


def widest(sources):
    """Per slot, the widest of the sources' values."""
    out = []
    for values in zip(*sources):
        known = [v for v in values if v is not None]
        out.append(max(known) if known else None)
    return out


def fromStore(instrument, setup, since):
    where = market.store(instrument, setup)
    if not os.path.exists(where):
        return {}
    frame = stores.load(where, 'M5', dtfrom=since)
    return {'store': samples(frame, pd.Timedelta(minutes=5))}


def fromRecorded(instrument, setup, since):
    """{feed: spreads} for every feed candles.db holds with an ask and a bid."""
    where = getattr(setup, 'CANDLE_DB', None)
    if not where or not os.path.exists(where):
        return {}
    db = candledb.CandleDB(where)
    with db.connect() as connection:
        frame = pd.read_sql_query(
            "SELECT provider, account, granularity, time, ask_o, bid_o, ask_c, bid_c "
            "FROM candles WHERE instrument=? AND ask_c IS NOT NULL AND bid_c IS NOT NULL "
            "AND time>=?", connection, params=(instrument, candledb.iso(since)))
    out = {}
    for (provider, account, granularity), part in frame.groupby(['provider', 'account', 'granularity']):
        period = granularityToTimedelta(granularity)
        if period is None:
            continue
        part = part.set_index(pd.to_datetime(part['time']).dt.tz_localize(None))
        name = '%s:%s' % (provider, account)
        out[name] = pd.concat([out.get(name, pd.Series(dtype=float)), samples(part, period)])
    return out


def withAccount(setup, account):
    """The settings with MT5_ACCOUNT set: which of MT5_TERMINALS answers."""
    copy = types.SimpleNamespace(**dict((k, getattr(setup, k)) for k in dir(setup) if k.isupper()))
    copy.MT5_ACCOUNT = account
    return copy


def fromProvider(name, instrument, setup, since):
    from parity_deriva.trading import providers
    provider_name, _, account = name.partition(':')
    provider = providers.get_provider(provider_name, withAccount(setup, account) if account else setup)
    providers.require(provider, 'bid_ask_candles')
    bars = [e for e in providers.history(provider, instrument, 'M5', since=since) if e.complete]
    if not bars:
        return {}
    index = pd.DatetimeIndex([pd.Timestamp(e.time).tz_convert(None) if pd.Timestamp(e.time).tzinfo
                              else pd.Timestamp(e.time) for e in bars])
    frame = pd.DataFrame([{'ask_o': float(e.ask['o']), 'bid_o': float(e.bid['o']),
                           'ask_c': float(e.ask['c']), 'bid_c': float(e.bid['c'])} for e in bars],
                         index=index)
    return {name: samples(frame, pd.Timedelta(minutes=5))}


def build(instrument, measured, kept, today):
    """
    One instrument's entry: the sources measured now over the ones the file
    already had, and the widest of them per slot.
    """
    zone = ZONES.get(instrument, FX)
    sources = dict((kept or {}).get('sources') or {})
    for name, spreads in measured.items():
        if len(spreads):
            sources[name] = {'measured': today, 'samples': int(len(spreads)),
                             'slots': slots(spreads, zone)}
    if not sources:
        return None
    combined = widest([s['slots'] for s in sources.values()])
    known = [v for v in combined if v is not None]
    return {'zone': zone, 'widest': max(known), 'slots': combined, 'sources': sources}


def when(i):
    return '%s %02d:%02d' % (DAYS[i // 288], i % 288 // 12, i % 12 * spread.SLOT)


def report(instrument, entry, setup, out=print):
    pip = pipSize(instrument, setup)
    combined = entry['slots']
    known = sorted(v for v in combined if v is not None)
    top = max(range(len(combined)), key=lambda i: combined[i] if combined[i] is not None else -1)
    out("%s  (%s clock, pips)" % (instrument, entry['zone']))
    out("  %-22s %10s %8s %6s %6s %6s  %s" % ('source', 'measured', 'samples', 'p50', 'p90', 'max',
                                             'widest in'))
    for name, source in sorted(entry['sources'].items()):
        values = sorted(v for v in source['slots'] if v is not None)
        wins = sum(1 for a, b in zip(source['slots'], combined) if a is not None and a == b)
        out("  %-22s %10s %8d %6.2f %6.2f %6.2f  %d of %d slots" % (
            name, source['measured'], source['samples'], values[len(values) // 2] / pip,
            values[int(len(values) * 0.9)] / pip, values[-1] / pip, wins, len(known)))
    out("  %-22s %10s %8s %6.2f %6.2f %6.2f  at %s, %d slots empty" % (
        'the set', '', '', known[len(known) // 2] / pip, known[int(len(known) * 0.9)] / pip,
        known[-1] / pip, when(top), spread.SLOTS - len(known)))


def main(argv=None, setup=None, out=print):
    setup = setup if setup is not None else settings
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[1],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--instrument', action='append',
                        help="default: every store in the market folder")
    parser.add_argument('--years', type=float, default=2,
                        help="how far back the store and candles.db are read (default 2)")
    parser.add_argument('--provider', action='append', default=[],
                        help="a broker to ask now; mt5:<login> for one MT5 terminal")
    parser.add_argument('--weeks', type=float, default=4,
                        help="how far back a --provider is asked (default 4)")
    parser.add_argument('--write', action='store_true', help="write spread.json")
    parser.add_argument('--force', action='store_true', help="write it from a reader too")
    args = parser.parse_args(argv)

    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    since = now - datetime.timedelta(days=365 * args.years)
    asked = now - datetime.timedelta(weeks=args.weeks)
    where = spread.path(setup)
    try:
        with open(where) as handle:
            before = json.load(handle).get('instruments') or {}
    except (OSError, ValueError):
        before = {}
    instruments = args.instrument or sorted(
        os.path.basename(p)[:-4] for p in glob.glob(os.path.join(market.directory(setup), '*.hd5')))

    kept = {}
    for instrument in instruments:
        measured = {}
        measured.update(fromStore(instrument, setup, since))
        measured.update(fromRecorded(instrument, setup, since))
        for name in args.provider:
            try:
                measured.update(fromProvider(name, instrument, setup, asked))
            except Exception as exc:
                out("%s %s: %s: %s" % (instrument, name, type(exc).__name__, exc))
        entry = build(instrument, measured, before.get(instrument), now.strftime('%Y-%m-%d'))
        if entry is None:
            out("%s: no source quotes an ask and a bid" % instrument)
            continue
        kept[instrument] = entry
        report(instrument, entry, setup, out)

    if not args.write:
        out("\nnothing written: --write puts it in %s" % where)
        return 0
    if not args.force:
        market.guard(where, setup)
    # instruments not rebuilt this time stay as they were
    everything = dict(before, **kept)
    temporary = where + '.tmp'
    with open(temporary, 'w') as handle:
        json.dump({'built': now.isoformat(timespec='seconds'), 'quantile': QUANTILE,
                   'slot': spread.SLOT, 'instruments': everything}, handle)
    os.replace(temporary, where)
    out("\nwritten: %s (%d instruments)" % (where, len(everything)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
