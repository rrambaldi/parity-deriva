"""
Resolve an instrument's IB conid, to be recorded rather than guessed.

This project names instruments the way OANDA does - 'EUR_USD', 'DE30_EUR' -
and Interactive Brokers keys everything by a numeric contract id. Nothing
derives one from the other, and the ambiguity is worse here than at the other
brokers: 'EUR' names a cash pair, several futures and a fund or two, across a
dozen exchanges. So IB_INSTRUMENTS in etc/settings.py has to be filled in by
hand, and this script prints what the gateway answers for you to choose from.

It needs the Client Portal Gateway running and logged in - the same
precondition the trading path has, and for the same reason: nothing in this
project can authenticate an IB session.

    python scripts/ib_instruments.py EUR --sec-type CASH
    python scripts/ib_instruments.py --name DE30_EUR DAX --sec-type FUT
"""

import argparse
import json
import sys

from parity_deriva.etc import settings
from parity_deriva.lib.ib import IBAPI, IBError, IBNotAuthenticated


def describe(row):
    """The fields worth seeing, without dumping the whole record."""
    out = {}
    for key in ('conid', 'symbol', 'companyName', 'companyHeader',
                'description', 'secType', 'exchange', 'listingExchange',
                'currency', 'sections'):
        if key in row:
            out[key] = row[key]
    return out or row


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument('symbols', nargs='+',
                        help="symbols to look up, e.g. EUR DAX")
    parser.add_argument('--sec-type', default=None, dest='sec_type',
                        help="narrow the search, e.g. CASH, STK, FUT, CFD. "
                             "Worth using: without it a currency symbol "
                             "returns the funds named after it too")
    parser.add_argument('--name', default=None,
                        help="the project's name for the instrument, used in "
                             "the printed settings entry (defaults to the "
                             "symbol)")
    parser.add_argument('--raw', action='store_true',
                        help="print the whole record instead of the useful fields")
    args = parser.parse_args(argv)

    try:
        api = IBAPI(setup=settings)
    except IBError as exc:
        print("cannot reach IB: %s" % exc)
        return 2

    print("gateway: %s, account %s" % (api.gateway, api.account))
    try:
        if not api.prepare():
            print("the gateway did not answer /iserver/accounts")
            return 2
    except IBNotAuthenticated as exc:
        print(str(exc))
        return 2
    except IBError as exc:
        print(str(exc))
        return 2

    entries = {}
    for symbol in args.symbols:
        rows = api.resolve(symbol, args.sec_type)
        if not rows:
            print("\n%s: nothing came back" % symbol)
            continue
        print("\n%s: %d match(es)" % (symbol, len(rows)))
        for row in rows:
            print("  " + json.dumps(row if args.raw else describe(row),
                                    sort_keys=True, default=str))
        first = rows[0]
        name = args.name if args.name and len(args.symbols) == 1 else symbol
        entries[name] = {
            'conid': first.get('conid'),
            'symbol': first.get('symbol', symbol),
            'secType': args.sec_type or first.get('secType'),
        }

    if not entries:
        return 1

    print("\nPaste into IB_INSTRUMENTS in etc/settings.py, after checking that")
    print("each conid is the contract you meant. A search returns the most")
    print("heavily traded match first, which is not the same as the right one:\n")
    for name in sorted(entries):
        entry = entries[name]
        print("    %r: {'conid': %s, 'symbol': %r, 'secType': %r},"
              % (name, entry['conid'], entry['symbol'], entry['secType']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
