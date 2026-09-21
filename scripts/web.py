"""
Serve the backtest viewer: candles on top, the trades under them.

Reads the local HDF5 warehouse and runs the offline stack - the strategy, the
money manager, the simulator and the adapter that says "the simulator is the
broker here". No broker is contacted, no order is ever sent, and nothing is
written.

    python scripts/web.py
    python scripts/web.py --port 9000 --max-candles 20000

Then open http://127.0.0.1:8731 and press run. Pick an instrument and a
granularity, and the dates default to what that store actually holds.

It binds to 127.0.0.1 because it has no authentication of any kind: anyone
who can reach the port can run backtests on this machine's data and read the
paths in the error messages. --host is there for the case where you know you
want that - a container, say - and it prints a warning when you use it,
because the difference between 127.0.0.1 and 0.0.0.0 is the whole of the
security model here.
"""

import argparse
import sys

from parity_deriva.etc import settings
from parity_deriva.lib.utils import getLogger
from parity_deriva.web.service import MAX_CANDLES, serve


def parse(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument('--host', default='127.0.0.1',
                        help="address to bind (default 127.0.0.1, which is "
                             "the only one that needs no further thought)")
    parser.add_argument('--port', type=int, default=8731,
                        help="port to listen on (default 8731)")
    parser.add_argument('--max-candles', type=int, default=MAX_CANDLES,
                        dest='max_candles',
                        help="most candles one reply may carry (default %d). "
                             "A window wider than this is refused rather than "
                             "truncated" % MAX_CANDLES)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse(argv if argv is not None else sys.argv[1:])
    logger = getLogger()

    server = serve(host=args.host, port=args.port, setup=settings,
                   max_candles=args.max_candles)

    if args.host not in ('127.0.0.1', 'localhost', '::1'):
        logger.warning(
            "bound to %s: this service has no authentication, so anyone who "
            "can reach the port can run backtests on this machine's data"
            % args.host)

    logger.info("reading candles from %s" % settings.DATA_DIR)
    logger.info("serving on http://%s:%d" % (args.host, args.port))
    print("parity-deriva backtest viewer on http://%s:%d  (ctrl-c to stop)"
          % (args.host, args.port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("")
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
