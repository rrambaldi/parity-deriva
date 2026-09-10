"""
Resolve an instrument's eToro identity, to be recorded rather than guessed.

This project names instruments the way OANDA does - 'EUR_USD', 'DE30_EUR' -
and eToro keys everything by a numeric instrumentId. The two names describe
the same market and nothing derives one from the other, so
ETORO_INSTRUMENTS in etc/settings.py has to be filled in by hand.

This script does the looking up and prints the result as the dict entry to
paste. It deliberately stops there. Resolving an id at runtime instead would
mean a search result quietly deciding which market the money goes into, and
a symbol that resolved to something else next month would move the trading
with it.

    python scripts/etoro_instruments.py EURUSD GER40
    python scripts/etoro_instruments.py --name EUR_USD EURUSD
"""

import argparse
import json
import sys

from parity_deriva.etc import settings
from parity_deriva.lib.etoro import EToroAPI, EToroError


def rows_for(api, symbol):
    """Whatever the instruments route says about one ticker."""
    return api.resolve(symbol)


def describe(row):
    """The fields worth seeing, without dumping the whole record."""
    out = {}
    for key in ('symbol', 'symbolFull', 'instrumentId', 'instrumentDisplayName',
                'name', 'displayName', 'instrumentTypeId', 'exchangeId',
                'precision', 'decimals', 'priceDecimals'):
        if key in row:
            out[key] = row[key]
    return out or row


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument('symbols', nargs='+',
                        help="eToro tickers to look up, e.g. EURUSD GER40")
    parser.add_argument('--name', default=None,
                        help="the project's name for the instrument, used in "
                             "the printed settings entry (defaults to the "
                             "symbol)")
    parser.add_argument('--raw', action='store_true',
                        help="print the whole record instead of the useful fields")
    args = parser.parse_args(argv)

    try:
        api = EToroAPI(setup=settings)
    except EToroError as exc:
        print("cannot reach eToro: %s" % exc)
        return 2

    print("account: %s" % ("demo" if api.demo else "REAL"))
    entries = {}
    for symbol in args.symbols:
        rows = rows_for(api, symbol)
        if not rows:
            print("\n%s: nothing came back" % symbol)
            continue
        print("\n%s: %d match(es)" % (symbol, len(rows)))
        for row in rows:
            print("  " + json.dumps(row if args.raw else describe(row),
                                    sort_keys=True))
        first = rows[0]
        name = args.name if args.name and len(args.symbols) == 1 else symbol
        entries[name] = {
            'symbol': first.get('symbol', symbol),
            'instrumentId': first.get('instrumentId'),
        }

    if not entries:
        return 1

    print("\nPaste into ETORO_INSTRUMENTS in etc/settings.py, after checking")
    print("that each id is the market you meant:\n")
    for name in sorted(entries):
        entry = entries[name]
        print("    %r: {'symbol': %r, 'instrumentId': %r},"
              % (name, entry['symbol'], entry['instrumentId']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
