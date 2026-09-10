"""
One wiring, either broker, with the shadow and the alarm attached.

The scripts/t0*.py files each hard-code a stack against OANDA, and none of
them registers the parity monitor - so the alarm added in trading/parity.py
could be raised in a test and never in a run. This is the wiring that closes
that loop, and it takes the broker as an argument rather than by import:

    python scripts/live.py --dry-run
    python scripts/live.py --provider oanda --instrument DE30_EUR --granularity M5
    python scripts/live.py --provider etoro --instrument EUR_USD --granularity H1

What gets registered, in this order: the strategy, the money manager, the
real execution handler, the simulator shadowing it on the same candles, the
parity monitor comparing the two, the event log, then the candle and
transaction sources. Both execution paths see the same orders, which is the
whole point - the comparison is only meaningful because neither side is
replaying the other's output.

Capabilities are checked before anything is registered, so an unsupported
combination is a startup error naming what is missing. AG01 and AG02 read a
candle's ask and bid; eToro serves one price series, so on eToro they refuse
to start until ETORO_SPREAD says what the spread is. That refusal is the
feature: the alternative is a strategy buying the high of a series it
believes is the ask.

--dry-run prints the plan and the chosen provider's declared capabilities
and exits without touching the network, which is the sane way to read this
before letting it send anything.
"""

import argparse
import sys

from parity_deriva.etc import settings
from parity_deriva.event.saver import EventSaver
from parity_deriva.lib.utils import getLogger
from parity_deriva.portfolio.moneymanager import MoneyManager
from parity_deriva.trading import providers
from parity_deriva.trading.engine import Engine
from parity_deriva.trading.parity import ParityMonitor

#: the strategies this wiring can drive: where the class lives, what it needs
#: of the data, and how it is told which instrument to watch.
#:
#: AG01 and AG02 bracket a reversal using the ask's high and the bid's low, so
#: they cannot run on a single price series; the BO engines only read the
#: direction of a candle, which one series answers.
#:
#: The last field is not a detail. AG01 takes pairs=[...] and handles several;
#: the BO engines take pair='...' and handle one. Handing a BO engine a
#: 'pairs' argument does not fail - _set() simply does not find the key it
#: looks for - so it would quietly watch its default EUR_USD instead of the
#: instrument asked for. One instance per instrument is registered instead.
STRATEGIES = {
    'AG01': ('parity_deriva.strategy.AG01', 'AG01', ('bid_ask_candles',), 'pairs'),
    'AG02': ('parity_deriva.strategy.AG02', 'AG02', ('bid_ask_candles',), 'pairs'),
    'BO01': ('parity_deriva.strategy.BO01', 'BO01', (), 'pair'),
    'BO02': ('parity_deriva.strategy.BO02', 'BO02', (), 'pair'),
    'BO03': ('parity_deriva.strategy.BO03', 'BO03', (), 'pair'),
    'BO04': ('parity_deriva.strategy.BO04', 'BO04', (), 'pair'),
    'BO05': ('parity_deriva.strategy.BO05', 'BO05', (), 'pair'),
}

#: the BO engines measure how long a run of candle directions persists and
#: place no orders at all, so there is nothing for the shadow to fill and
#: nothing for the alarm to compare
RESEARCH_ONLY = frozenset(['BO01', 'BO02', 'BO03', 'BO04', 'BO05'])


def load_strategy(name):
    module, attr, needs, style = STRATEGIES[name]
    __import__(module)
    return getattr(sys.modules[module], attr), needs, style


def add_strategy(engine, strategy_class, style, pairs, granularity):
    """Register the strategy the way its constructor expects to be called."""
    if style == 'pairs':
        engine.add_handler(strategy_class(pairs=pairs, granularity=granularity))
        return
    for pair in pairs:
        engine.add_handler(strategy_class(pair=pair, granularity=granularity))


def parse(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument('--provider', default=None,
                        choices=providers.available(),
                        help="broker to trade through (default: settings.PROVIDER, "
                             "currently %r)" % getattr(settings, 'PROVIDER', 'oanda'))
    parser.add_argument('--instrument', action='append', dest='instruments',
                        help="instrument to trade, repeatable (default: "
                             "settings.DEF_PAIRS)")
    parser.add_argument('--granularity', default=None,
                        help="candle granularity the strategy signals on "
                             "(default: settings.DEF_GRANULARITY)")
    parser.add_argument('--strategy', default='AG01', choices=sorted(STRATEGIES),
                        help="strategy to run (default AG01)")
    parser.add_argument('--units', type=int, default=1,
                        help="units per trade, multiplied onto the signal's "
                             "sign (default 1)")
    parser.add_argument('--no-parity', action='store_true',
                        help="leave the parity monitor out. Then the simulator "
                             "runs alongside and nothing compares the two")
    parser.add_argument('--no-shadow', action='store_true',
                        help="leave the simulator out as well, so there is "
                             "nothing to compare against at all")
    parser.add_argument('--heartbeat', type=float, default=0.5,
                        help="seconds the engine waits on an empty queue")
    parser.add_argument('--dry-run', action='store_true',
                        help="print the plan and the provider's capabilities, "
                             "register nothing, touch no network")
    parser.add_argument('--live', action='store_true',
                        help="required when settings.DOMAIN is 'real'. Without "
                             "it a real-money account is refused")
    return parser.parse_args(argv)


def plan(args, provider, pairs, granularity, needs):
    lines = [
        "provider      %s" % provider.name,
        "account       %s" % ("REAL MONEY" if str(
            getattr(settings, 'DOMAIN', 'practice')) == 'real' else "practice"),
        "instruments   %s" % ", ".join(pairs),
        "granularity   %s" % granularity,
        "strategy      %s%s" % (args.strategy,
                                "  (needs %s)" % ", ".join(needs) if needs else ""),
        "units         %d" % args.units,
        "shadow        %s" % ("off" if args.no_shadow else "on"),
        "parity        %s" % ("off" if args.no_parity or args.no_shadow else "on"),
    ]
    return "\n".join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    args = parse(argv)

    provider = providers.get_provider(args.provider)
    pairs = args.instruments or list(getattr(settings, 'DEF_PAIRS', ['EUR_USD']))
    granularity = args.granularity or getattr(settings, 'DEF_GRANULARITY', 'M1')
    strategy_class, needs, style = load_strategy(args.strategy)

    print(plan(args, provider, pairs, granularity, needs))
    if args.strategy in RESEARCH_ONLY:
        print("\nnote: %s places no orders, so the shadow has nothing to fill\n"
              "      and the parity monitor has nothing to compare."
              % args.strategy)

    if args.dry_run:
        print("\ncapabilities of %s:" % provider.name)
        print(provider.capabilities.dump())
        print("\n--dry-run: nothing registered, nothing sent")
        return 0

    if str(getattr(settings, 'DOMAIN', 'practice')) == 'real' and not args.live:
        print("\nsettings.DOMAIN is 'real'. Pass --live to say you mean it.")
        return 2

    # Before anything is registered: a stack missing a capability it needs
    # should fail here, not once a strategy is reading a price that is not
    # the one it thinks it is.
    try:
        providers.require(provider, *needs)
    except providers.CapabilityError as exc:
        print("\n%s" % exc)
        if provider.name == 'etoro' and 'bid_ask_candles' in needs:
            print("eToro serves one price series per candle. Measure the "
                  "spread with\n"
                  "    python scripts/etoro_spread.py --instrument %s\n"
                  "and set ETORO_SPREAD in etc/settings.py, or run a strategy "
                  "that does\nnot read a candle's ask and bid." % pairs[0])
        return 2

    logger = getLogger()
    engine = Engine()
    engine.heartbeat = args.heartbeat

    logger.info("provider %s, %s account" % (
        provider.name, "REAL" if str(getattr(settings, 'DOMAIN', '')) == 'real'
        else "practice"))
    for line in provider.capabilities.dump().split("\n"):
        logger.debug("capability %s" % line)

    add_strategy(engine, strategy_class, style, pairs, granularity)
    engine.add_handler(MoneyManager(pairs=pairs, units=args.units))
    engine.add_handler(provider.execution())

    if not args.no_shadow:
        engine.add_handler(provider.simulator(granularity=granularity))
        if not args.no_parity:
            # One monitor per instrument: the thresholds are per instrument,
            # since one band rarely fits both an index and a currency pair.
            for pair in pairs:
                engine.add_handler(ParityMonitor(instrument=pair))

    engine.add_handler(EventSaver())

    logger.info("registering candles: %s %s" % (", ".join(pairs), granularity))
    engine.add_handler(provider.candles(pairs=pairs, granularity=granularity))

    logger.info("registering transactions")
    engine.add_handler(provider.transactions(pairs=pairs))

    logger.info("starting trading engine")
    engine.run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
