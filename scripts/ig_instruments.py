"""
Resolve an instrument's IG epic, to be recorded rather than guessed.

This project names instruments the way OANDA does - 'EUR_USD', 'DE30_EUR' -
and IG names them with an epic: CS.D.EURUSD.MINI.IP, IX.D.DAX.IFMM.IP. The
two describe the same market and nothing derives one from the other, so
IG_INSTRUMENTS in etc/settings.py has to be filled in by hand.

There is more than naming at stake here than there was on eToro. A search for
'EURUSD' returns the mini, the standard contract and the spread bet, each
with its own epic, its own minimum size and its own currency - so the choice
is a product decision, not a lookup. This script does the looking up and
stops there.

With --details it also reads the dealing rules for each epic, which is where
the minimum stop distance lives. That number is worth knowing before a
strategy places its first order: IG refuses a stop closer to the market than
its instrument allows, and nothing in this project moves a level to fit.

    python scripts/ig_instruments.py EURUSD
    python scripts/ig_instruments.py --name EUR_USD --details EURUSD
"""

import argparse
import json
import sys

from parity_deriva.etc import settings
from parity_deriva.lib.ig import IGAPI, IGError


def describe(row):
    """The fields worth seeing, without dumping the whole record."""
    out = {}
    for key in ('epic', 'instrumentName', 'instrumentType', 'expiry',
                'marketStatus', 'bid', 'offer', 'scalingFactor',
                'streamingPricesAvailable'):
        if key in row:
            out[key] = row[key]
    return out or row


def rules(api, epic):
    """
    The dealing rules for one epic, as far as they matter here.

    Minimum size and minimum stop distance are the two that decide whether a
    strategy can be run at all on this market: a bracket tighter than the
    minimum distance is refused by IG on every single order.
    """
    payload = api.market(epic)
    if payload is None:
        return None
    dealing = payload.get('dealingRules') or {}
    snapshot = payload.get('snapshot') or {}
    instrument = payload.get('instrument') or {}
    out = {
        'currencies': [c.get('code') for c in instrument.get('currencies') or []],
        'lotSize': instrument.get('lotSize'),
        'marketStatus': snapshot.get('marketStatus'),
        'scalingFactor': snapshot.get('scalingFactor'),
    }
    for key in ('minDealSize', 'minNormalStopOrLimitDistance',
                'maxStopOrLimitDistance', 'minControlledRiskStopDistance',
                'minStepDistance'):
        if key in dealing:
            out[key] = dealing[key]
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument('terms', nargs='+',
                        help="what to search for, e.g. EURUSD 'Germany 40'")
    parser.add_argument('--name', default=None,
                        help="the project's name for the instrument, used in "
                             "the printed settings entry (defaults to the "
                             "search term)")
    parser.add_argument('--details', action='store_true',
                        help="also read each epic's dealing rules - minimum "
                             "size and minimum stop distance")
    parser.add_argument('--raw', action='store_true',
                        help="print the whole record instead of the useful fields")
    args = parser.parse_args(argv)

    try:
        api = IGAPI(setup=settings)
    except IGError as exc:
        print("cannot reach IG: %s" % exc)
        return 2

    print("host: %s (%s account)" % (api.host, "demo" if api.demo else "LIVE"))
    if not api.login():
        print("login failed; check IG_API_KEY, IG_IDENTIFIER and IG_PASSWORD, "
              "and that the key belongs to this host")
        return 2

    entries = {}
    for term in args.terms:
        rows = api.resolve(term)
        if not rows:
            print("\n%s: nothing came back" % term)
            continue
        print("\n%s: %d match(es)" % (term, len(rows)))
        for row in rows:
            print("  " + json.dumps(row if args.raw else describe(row),
                                    sort_keys=True))
            if args.details and row.get('epic'):
                detail = rules(api, row['epic'])
                if detail is not None:
                    print("      rules " + json.dumps(detail, sort_keys=True))
        first = rows[0]
        name = args.name if args.name and len(args.terms) == 1 else term
        entries[name] = {
            'epic': first.get('epic'),
            'expiry': first.get('expiry') or '-',
        }

    if not entries:
        return 1

    print("\nNote: IG's scalingFactor above is NOT a price divisor. EUR/USD")
    print("reports 10000 and quotes 1.14625; it relates distances in points to")
    print("price units. Do not paste it into an instrument entry.\n")
    print("Paste into IG_INSTRUMENTS in etc/settings.py, after checking that")
    print("each epic is the contract you meant - the first match is not")
    print("necessarily the one you want to deal:\n")
    for name in sorted(entries):
        entry = entries[name]
        print("    %r: {'epic': %r, 'expiry': %r, 'currency': %r},"
              % (name, entry['epic'], entry['expiry'],
                 getattr(settings, 'BASE_CURRENCY', 'EUR')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
