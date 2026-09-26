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

SIGHUP restarts it in place, on whatever code is on disk now:

    kill -HUP $(systemctl show -p MainPID --value parity-deriva-web)

The process execs itself, so it keeps its PID and systemd sees nothing - and
anyone who can signal the process (its own user) can restart it without
sudo. A backtest running at that moment is lost, as with any restart.
"""

import argparse
import logging
import logging.handlers
import os
import signal
import sys

from parity_deriva.data import market
from parity_deriva.etc import settings
from parity_deriva.lib.utils import getLogger
from parity_deriva.web import servers
from parity_deriva.web.logs import servicePath
from parity_deriva.web.service import LOGGER, MAX_CANDLES, serve


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
    parser.add_argument('--no-resume', action='store_true',
                        help="do not start again the live sessions left "
                             "running: for a second server on the same data, "
                             "a test one, which must not trade twice")
    parser.add_argument('--verbose', action='store_true',
                        help="let the backtest log what it is doing. Off by "
                             "default: a run logs several lines per fill, "
                             "which is right when somebody is watching it and "
                             "is hundreds of journal lines per request when "
                             "nobody is")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse(argv if argv is not None else sys.argv[1:])

    # getLogger() reads logging.conf, which puts every logger at DEBUG - right
    # for a script somebody is reading, wrong for a service. The backtest is
    # quietened to warnings and the service keeps its own logger at info, so
    # the journal holds one line per request instead of one per fill.
    getLogger()
    logger = logging.getLogger(LOGGER)
    logger.setLevel(logging.INFO)
    logging.getLogger('parity_deriva.trading.trading').setLevel(
        logging.DEBUG if args.verbose else logging.WARNING)
    # and in a file too, for the logs page (web/logs.py): the journal keeps
    # it as well, but a page cannot read the journal
    path = servicePath(settings)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=5 * 1024 * 1024,
                                                   backupCount=3)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(handler)

    server = serve(host=args.host, port=args.port, setup=settings,
                   max_candles=args.max_candles)
    if not args.no_resume:
        # the live sessions never stopped: a systemctl restart took their
        # processes with it, and the database says they should be trading
        for session in server.RequestHandlerClass.service.live.resume():
            logger.info("live session %s resumed" % session)
    # the market data's sources on their timers (data/sources.py): nothing
    # on a server that only reads it, or whose sources are all manual
    server.RequestHandlerClass.service.sources.start()
    # an archive reads its trade servers' sessions (web/servers.py poll)
    servers.start(server.RequestHandlerClass.service)
    # every minute: the alerts of the sessions, and a real money server's
    # loss limit (web/livesessions.py watch)
    server.RequestHandlerClass.service.live.watch(settings)

    def restart(signum, frame):
        # the socket is closed first so the new process can bind the port;
        # everything else goes with the exec
        logger.info("SIGHUP: restarting on the code on disk")
        server.server_close()
        os.execv(sys.executable, [sys.executable] + sys.argv)
    signal.signal(signal.SIGHUP, restart)

    if args.host not in ('127.0.0.1', 'localhost', '::1'):
        logger.warning(
            "bound to %s: this service has no authentication, so anyone who "
            "can reach the port can run backtests on this machine's data"
            % args.host)

    logger.info("reading candles from %s" % market.directory())
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
