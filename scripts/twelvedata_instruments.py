"""
Resolve an instrument's Twelve Data ticker, to be recorded rather than guessed.

This project names instruments the way OANDA does - 'EUR_USD', 'DE30_EUR' -
and Twelve Data spells a pair 'EUR/USD' and an index by a ticker of its own.
Nothing derives one from the other, so TWELVEDATA_INSTRUMENTS in
etc/settings.py is filled in by hand, and this prints what to paste.

Each search costs one credit of the day's 800.

    python scripts/twelvedata_instruments.py DAX
    python scripts/twelvedata_instruments.py --name DE30_EUR "Germany 40"
"""

import argparse
import json
import sys

from parity_deriva.etc import settings
from parity_deriva.lib.twelvedata import TwelveDataAPI, TwelveDataError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument('symbols', nargs='+', help="text to search for")
    parser.add_argument('--name', default=None,
                        help="this project's name for it, for the line to paste")
    args = parser.parse_args(argv)
    try:
        api = TwelveDataAPI(setup=settings)
    except TwelveDataError as exc:
        print(exc)
        return 2
    for text in args.symbols:
        status, rows = api.search(text)
        print("%s: status %s, %d results" % (text, status, len(rows)))
        for row in rows:
            print("  " + json.dumps(dict((k, row.get(k)) for k in
                                         ('symbol', 'instrument_name', 'exchange',
                                          'instrument_type', 'currency', 'country')
                                         if k in row)))
        if args.name and rows:
            print("\n    '%s': '%s',   # %s" % (args.name, rows[0].get('symbol'),
                                              rows[0].get('instrument_name')))
    print("\ncredits today: %s" % api.budget())
    return 0


if __name__ == '__main__':
    sys.exit(main())
