"""
One wiring, any broker, with the shadow and the alarm attached.

The scripts/t0*.py files each hard-code a stack against OANDA, and none of
them registers the parity monitor - so the alarm added in trading/parity.py
could be raised in a test and never in a run. This is the wiring that closes
that loop, and it takes the broker as an argument rather than by import:

    python scripts/live.py --dry-run
    python scripts/live.py --provider oanda --instrument DE30_EUR --granularity M5
    python scripts/live.py --provider etoro --instrument EUR_USD --granularity H1
    python scripts/live.py --provider ig    --instrument EUR_USD --granularity H1
    python scripts/live.py --provider ib    --instrument EUR_USD --granularity H1

What gets registered, in this order: the strategy, the money manager, the
real execution handler, the trailer if the strategy exits on a stop that
moves, the simulator shadowing it on the same candles, the parity monitor
comparing the two, the event log, then the candle and transaction sources. Both execution paths see the same orders, which is the
whole point - the comparison is only meaningful because neither side is
replaying the other's output.

Capabilities are checked before anything is registered, so an unsupported
combination is a startup error naming what is missing. AG01 and AG02 read a
candle's ask and bid; eToro and Interactive Brokers each serve one price
series, so there they refuse to start until ETORO_SPREAD or IB_SPREAD says
what the spread is. That refusal is the feature: the alternative is a strategy
buying the high of a series it believes is the ask. OANDA and IG both serve a
real bid and ask, so there is nothing to configure on either.

--dry-run prints the plan and the chosen provider's declared capabilities
and exits without touching the network, which is the sane way to read this
before letting it send anything.
"""

import argparse
import datetime
import json
import os
import signal
import sys
import threading
import time

from parity_deriva.data.candledb import CandleRecorder
from parity_deriva.etc import settings
from parity_deriva.event.event import StatusEvent
from parity_deriva.event.saver import EventSaver
from parity_deriva.lib.utils import getLogger, granularityToTimedelta
from parity_deriva.portfolio.moneymanager import MoneyManager
from parity_deriva.portfolio.trailer import Trailer
from parity_deriva.strategy import plugins, uploaded
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
    'AG01-MOD': ('parity_deriva.strategy.AG01MOD', 'AG01MOD',
                 ('bid_ask_candles',), 'pairs'),
    'H401-PULLBACK-EMA':
        ('parity_deriva.strategy.H401', 'H401', ('bid_ask_candles',), 'pairs'),
    'H402-BREAKOUT':
        ('parity_deriva.strategy.H402', 'H402', ('bid_ask_candles',), 'pairs'),
    'H403-BOLLINGER':
        ('parity_deriva.strategy.H403', 'H403', ('bid_ask_candles',), 'pairs'),
    'H404-LIVELLI-DAILY':
        ('parity_deriva.strategy.H404', 'H404', ('bid_ask_candles',), 'pairs'),
    'H405-MOMENTUM-RSI':
        ('parity_deriva.strategy.H405', 'H405', ('bid_ask_candles',), 'pairs'),
    'M1501-MTP':
        ('parity_deriva.strategy.M1501', 'M1501', ('bid_ask_candles',), 'pairs'),
    'M1502-SBR':
        ('parity_deriva.strategy.M1502', 'M1502', ('bid_ask_candles',), 'pairs'),
    'M1503-SRP':
        ('parity_deriva.strategy.M1503', 'M1503', ('bid_ask_candles',), 'pairs'),
    'M1504-BMR':
        ('parity_deriva.strategy.M1504', 'M1504', ('bid_ask_candles',), 'pairs'),
    'M1505-BBO':
        ('parity_deriva.strategy.M1505', 'M1505', ('bid_ask_candles',), 'pairs'),
    'M1506-BRT':
        ('parity_deriva.strategy.M1506', 'M1506', ('bid_ask_candles',), 'pairs'),
    'BO01': ('parity_deriva.strategy.BO01', 'BO01', (), 'pair'),
    'BO02': ('parity_deriva.strategy.BO02', 'BO02', (), 'pair'),
    'BO03': ('parity_deriva.strategy.BO03', 'BO03', (), 'pair'),
    'BO04': ('parity_deriva.strategy.BO04', 'BO04', (), 'pair'),
    'BO05': ('parity_deriva.strategy.BO05', 'BO05', (), 'pair'),
}
# A strategy that is not published with this repository registers itself
# here. Some of them exit on a stop that climbs rather than on a target, and
# ask for 'stop_modify' to say so: on a provider that cannot replace a trade's
# stop they refuse to start, which is deliberate - a run where the simulator
# walks a ladder the account is not walking is worse than no run at all. The
# ladder itself is portfolio/trailer.py, registered below whenever a strategy
# asks for that capability.
STRATEGIES.update(plugins.live())
# and the ones written over MCP that somebody enabled (strategy/uploaded.py)
STRATEGIES.update(uploaded.live(settings.DATA_DIR))

#: the BO engines measure how long a run of candle directions persists and
#: place no orders at all, so there is nothing for the shadow to fill and
#: nothing for the alarm to compare
RESEARCH_ONLY = frozenset(['BO01', 'BO02', 'BO03', 'BO04', 'BO05'])


def load_strategy(name):
    module, attr, needs, style = STRATEGIES[name]
    __import__(module)
    return getattr(sys.modules[module], attr), needs, style


def add_strategy(engine, strategy_class, style, pairs, granularity,
                 provider=None, **kwargs):
    """
    Register the strategy the way its constructor expects to be called.

    One that can warm up is given its history first, off the bus: no order
    comes out of bars that printed before the session started.
    """
    if style == 'pairs':
        made = [strategy_class(pairs=pairs, granularity=granularity, **kwargs)]
    else:
        made = [strategy_class(pair=pair, granularity=granularity, **kwargs)
                for pair in pairs]
    for strategy in made:
        if callable(getattr(strategy, 'warmup', None)):
            strategy.warmup(provider)
        engine.add_handler(strategy)


def fromForm(text):
    """
    A backtest page's form as this run: read by web/service.backtestArgs,
    the code the backtest itself was read with, so what goes live is what
    was tested. Returns the service's keyword arguments.
    """
    from parity_deriva.web import service
    fields = json.loads(text)
    spec = service.backtestArgs(lambda key: service._text(fields.get(key)))
    # live only: the capital the risk is a percentage of, in the account's
    # currency. None sizes on the account's balance (quoteBalance)
    spec['capital'] = service.parseAmount(service._text(fields.get('capital')),
                                          'capital', None)
    # a plugin's own engine refuses in the backtest what it does not apply
    # (web/service.Service.backtest); live refuses the same, or the account
    # would trade a rule the backtest never had
    plugin = plugins.viewers().get(spec['strategy'])
    if plugin is not None:
        import inspect
        takes = inspect.signature(plugin['run']).parameters
        for name, label in (('maxStopPips', 'max stop'), ('session', 'hours'),
                            ('news', 'news'), ('filters', 'entry filter')):
            if spec.get(name) and name not in takes:
                raise SystemExit("%s runs its own engine, which has no %s: "
                                 "leave it empty" % (spec['strategy'], label))
        # its engine moves its stops inside the walk (ftw_ab/live.py) and
        # nothing sends those moves to the account
        for name, label in (('trailing', 'trailing stop'),
                            ('trailProfit', 'trailing profit'),
                            ('trailPips', 'trail pips')):
            if spec.get(name):
                raise SystemExit("%s runs its own engine, and live its stop "
                                 "does not move: leave %s empty"
                                 % (spec['strategy'], label))
    return spec


def liveStrategy(spec):
    """
    (the live wiring a form runs on, what it needs of the provider).

    A plugin turns its orders round inside its own engine, so turned round
    it is a different wiring - registered as NAME-INVERSA - and the money
    manager is not told to turn them again (wire). A stop that moves needs a
    provider that can move one.
    """
    name = spec['strategy']
    if spec.get('inverse') and name in plugins.viewers():
        name += '-INVERSA'
    if name not in STRATEGIES:
        return name, None
    needs = tuple(STRATEGIES[name][2])
    follows = spec.get('trailing') == 1 or spec.get('trailProfit') \
        or (spec.get('trailPips') and spec.get('trailing') != 0)
    if follows and 'stop_modify' not in needs:
        needs += ('stop_modify',)
    return name, needs


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
    parser.add_argument('--form', default=None,
                        help="a backtest page's fields, as JSON: strategy, "
                             "instrument, timeframe, parameters, risk and the "
                             "account's rules, read the way the backtest read them")
    parser.add_argument('--account', default=None,
                        help="the account traded, for its balance; the "
                             "provider's own setting decides which one deals")
    parser.add_argument('--events', default=None,
                        help="path prefix of the event log (a file a day), "
                             "for a page to follow")
    parser.add_argument('--candle-db', default=None, dest='candle_db',
                        help="the SQLite file every session writes its candles "
                             "to, one row per provider and bar (default: "
                             "settings.CANDLE_DB)")
    parser.add_argument('--live', action='store_true',
                        help="required when settings.DOMAIN is 'real'. Without "
                             "it a real-money account is refused")
    return parser.parse_args(argv)


def plan(args, provider, pairs, granularity, needs):
    lines = [
        "provider      %s" % provider.name,
        "account       %s" % ("PAPER (the simulator fills the orders)"
                              if provider.capabilities.paper else "REAL MONEY" if str(
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
    if provider.capabilities.paper:
        # the execution handler IS the simulator: a shadow beside it would be
        # a second simulator filling the same orders, and the parity monitor
        # would be comparing the two copies
        args.no_shadow = True
    spec = fromForm(args.form) if args.form else None
    if spec is not None:
        args.strategy, wanted = liveStrategy(spec)
        args.instruments = [spec['instrument']]
        args.granularity = spec['granularity']
        if wanted is None:
            print("%s cannot run on a live account: %s"
                  % (args.strategy, ", ".join(sorted(STRATEGIES))))
            return 2
    pairs = args.instruments or list(getattr(settings, 'DEF_PAIRS', ['EUR_USD']))
    granularity = args.granularity or getattr(settings, 'DEF_GRANULARITY', 'M1')
    strategy_class, needs, style = load_strategy(args.strategy)
    if spec is not None:
        needs = wanted

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
        if 'bid_ask_candles' in needs and provider.name == 'etoro':
            print("eToro serves one price series per candle. Measure the "
                  "spread with\n"
                  "    python scripts/etoro_spread.py --instrument %s\n"
                  "and set ETORO_SPREAD in etc/settings.py, or run a strategy "
                  "that does\nnot read a candle's ask and bid." % pairs[0])
        if 'stop_modify' in needs:
            print("%s exits on a stop that moves while the trade is open, and "
                  "%s\ncannot move one. Run it on a provider that can - "
                  "oanda replaces a\ntrade's stop in one call - or offline "
                  "through backtest/ledger.py."
                  % (args.strategy, provider.name))
        if 'bid_ask_candles' in needs and provider.name == 'ib':
            print("IB's history route serves one price series per bar. Its "
                  "snapshot route\ndoes quote both sides, so measure the "
                  "spread there and set IB_SPREAD in\netc/settings.py, or run "
                  "a strategy that does not read a candle's ask\nand bid.")
        if 'bid_ask_candles' in needs and provider.name == 'twelvedata':
            print("Twelve Data serves one price series per bar. The live "
                  "page's skew board\nprints each broker's median spread in "
                  "pips: set TWELVEDATA_SPREAD in\netc/settings.py from it, "
                  "or run a strategy that does not read a candle's\nask and "
                  "bid.")
        if 'bid_ask_candles' in needs and provider.name in ('etoro', 'ib', 'twelvedata'):
            print("Or build the shared set every one-price broker reads, "
                  "into the market\nfolder: python scripts/spread_profile.py")
        return 2

    logger = getLogger()
    engine = Engine()
    engine.heartbeat = args.heartbeat

    logger.info("provider %s, %s account" % (
        provider.name, "REAL" if str(getattr(settings, 'DOMAIN', '')) == 'real'
        else "practice"))
    for line in provider.capabilities.dump().split("\n"):
        logger.debug("capability %s" % line)

    # the first bar a signal may come from is the one still forming now: it
    # closes after the session started, as every bar of a backtest closes
    # before its order is placed. See MoneyManager.notBefore.
    notBefore = datetime.datetime.utcnow() - granularityToTimedelta(granularity)
    if spec is None:
        add_strategy(engine, strategy_class, style, pairs, granularity)
        engine.add_handler(MoneyManager(pairs=pairs, units=args.units,
                                        notBefore=notBefore))
        engine.add_handler(provider.execution())
    else:
        wire(engine, provider, spec, args, strategy_class, style, pairs, granularity,
             notBefore)
    stopAndClose(engine, logger)

    if 'stop_modify' in needs:
        # A climbing stop is a rule, not a level: an order states its stop once
        # and never speaks again, so something has to read each closed candle
        # and say where the stop belongs now. Registered before the shadow, so
        # a stop moved on this bar applies from the next one - which is what
        # backtest/ledger.py does offline, and the two have to agree.
        engine.add_handler(Trailer(granularity=granularity))

    if not args.no_shadow:
        engine.add_handler(provider.simulator(granularity=granularity))
        if not args.no_parity:
            # One monitor per instrument: the thresholds are per instrument,
            # since one band rarely fits both an index and a currency pair.
            for pair in pairs:
                engine.add_handler(ParityMonitor(instrument=pair))

    engine.add_handler(EventSaver(logname=args.events) if args.events
                       else EventSaver())
    # every bar this session sees, next to every other session's, so the
    # brokers can be laid side by side afterwards (data/candledb.py). The
    # session id is the folder the event log lives in
    engine.add_handler(CandleRecorder(
        path=args.candle_db or getattr(settings, 'CANDLE_DB', None),
        provider=provider.name, account=args.account or '',
        session=os.path.basename(os.path.dirname(args.events)) if args.events else None))

    logger.info("registering candles: %s %s" % (", ".join(pairs), granularity))
    engine.add_handler(provider.candles(pairs=pairs, granularity=granularity))

    logger.info("registering transactions")
    engine.add_handler(provider.transactions(pairs=pairs))

    logger.info("starting trading engine")
    engine.run()
    return 0


def wire(engine, provider, spec, args, strategy_class, style, pairs, granularity,
         notBefore=None):
    """
    The strategy, the money manager and the execution handler of a run read
    from a backtest's form.

    Sized on the account: the risk is the form's, of what the account holds
    now - not of the backtest's capital. A plugin's own engine applies the
    stop and target scales itself, as it did in the backtest, so the money
    manager must not apply them a second time.
    """
    from parity_deriva.backtest.ledger import _calendar
    from parity_deriva.portfolio.session import SessionCloser, TradeTimer
    plugin = plugins.viewers().get(spec['strategy'])
    risk = spec['risk']
    rules = []
    if plugin is not None:
        # the plugin's engine closes on the clock itself, as it did in the
        # backtest, and sends the CloseTradeEvent: see ftw_ab/live.py
        kwargs = {'params': spec['params'], 'slScale': spec['slScale'],
                  'tpScale': spec['tpScale'], 'maxBars': spec['maxBars'],
                  'intraday': spec['intraday'], 'setup': settings}
        scales = {}
        if risk is None:
            from parity_deriva.strategy.private.ftw_ab import config as ab_config
            risk = ab_config.RISK_PER_TRADE
    else:
        kwargs = spec['strategyArgs'] or {}
        scales = {'slScale': spec['slScale'], 'tpScale': spec['tpScale'],
                  'inverse': spec['inverse'], 'trailing': spec['trailing'],
                  'trailProfit': spec['trailProfit'], 'trailPips': spec['trailPips']}
        # the same two handlers backtest/ledger.py registers
        if spec['intraday']:
            rules.append(SessionCloser(
                at=spec['session'][1] if spec['session'] else '23:59',
                granularity=granularity, instrument=pairs[0]))
        if spec['maxBars']:
            rules.append(TradeTimer(spec['maxBars'], granularity=granularity,
                                    instrument=pairs[0]))
    # the entry filter reads the strategy's bars before the strategy does, as
    # in the backtest (backtest/ledger.py run): the bar a signal comes on is
    # the last one it has seen
    entry = None
    if spec.get('filters'):
        from parity_deriva.portfolio.filters import EntryFilter
        entry = EntryFilter(spec['filters'], granularity=granularity, instrument=pairs[0])
        engine.add_handler(entry)
    add_strategy(engine, strategy_class, style, pairs, granularity,
                 provider=provider, **kwargs)
    balance = None
    if risk is not None:
        balance = quoteBalance(provider, args.account, pairs[0], granularity,
                               getattr(engine.handlers[-1], 'rows', None),
                               amount=spec.get('capital'))
    reference = spec.get('capital') is not None and balance is not None
    getLogger().info("sizing: risk %s of %s (%s)%s" % (
        risk, balance, pairs[0][-3:],
        ", a reference capital moved by this session's closes, not the account's"
        if reference else ""))
    engine.add_handler(MoneyManager(
        pairs=pairs, units=args.units, risk=risk, balance=balance, notBefore=notBefore,
        # the rate the reference was converted at: a EUR account's P&L in
        # EUR is added to a capital kept in USD at the same rate
        reference=reference,
        plRate=balance / spec['capital'] if reference else 1.0,
        maxStopPips=spec['maxStopPips'], session=spec['session'],
        calendar=_calendar(pairs[0], spec['news'], spec['newsImpacts'], settings),
        filters=entry, **scales))
    engine.add_handler(provider.execution(sized=risk is not None))
    for rule in rules:
        engine.add_handler(rule)


#: seconds a stopped session waits for its closes to be reported before it
#: exits anyway: the polled brokers answer within a few of their polls
LIQUIDATE_SECONDS = 90


def stopAndClose(engine, logger):
    """
    SIGTERM - the live page's stop - is "stop and close everything": the
    money manager cancels this session's resting orders and closes its open
    trades (MoneyManager.liquidate), and the process exits once the account
    says nothing of this session's is left, or after LIQUIDATE_SECONDS.

    The engine loop keeps running meanwhile, since it is what carries the
    cancels and the closes to the broker and the fills back.
    """
    manager = next(h for h in engine.handlers if isinstance(h, MoneyManager))

    def wait():
        deadline = time.time() + LIQUIDATE_SECONDS
        time.sleep(2)   # the cancels reach the money manager through the bus
        while time.time() < deadline and not manager.settled():
            time.sleep(1)
        if manager.settled():
            logger.info("STOP: nothing left open; exiting")
        else:
            logger.error("STOP: still open after %ds; exiting anyway - check the "
                         "account" % LIQUIDATE_SECONDS)
        engine.quit()
        os._exit(0)

    def handler(signum, frame):
        if manager.liquidating:
            return
        logger.warning("STOP: stop and close everything")
        engine.put(StatusEvent('LIQUIDATE'))
        threading.Thread(target=wait, daemon=True).start()

    signal.signal(signal.SIGTERM, handler)


def quoteBalance(provider, account, instrument, granularity, rows=None, amount=None):
    """
    The account's balance in the instrument's quote currency - or `amount`,
    a reference capital held in the account's currency, converted the same
    way: 1000 EUR on a EUR account trading EUR_USD is 1000 x the last close.

    The money manager sizes in price units: a stop 0.0020 away on EUR_USD
    loses units x 0.0020 US dollars, so the capital it takes the risk of has
    to be in dollars too - as the backtest's capital is. An account held in
    the base currency (EUR for EUR_USD) is converted at the last close; one
    in a third currency is refused rather than guessed at - its balance, that
    is: a reference capital is one number every account trades, taken 1:1.
    """
    row = next((a for a in provider.accounts() if account in (None, a['id'])), None)
    if row is None:
        raise SystemExit("%s has no account %r" % (provider.name, account))
    balance, held = row['balance'], (row.get('currency') or '').upper()
    if amount is not None:
        balance = float(amount)
    base, quote = instrument.split('_')[0], instrument.split('_')[-1]
    if held in ('', quote):
        return balance
    if held != base:
        if amount is not None:
            # a reference capital is one number for every account: one in a
            # third currency (eToro's USD on DE30_EUR) takes it 1:1.
            # ponytail: its P&L comes back converted at the real rate, off
            # by that rate against the others; a EURUSD feed would fix it
            return balance
        raise SystemExit("account in %s, %s quoted in %s: no rate to size with"
                         % (held, instrument, quote))
    if rows:
        price = rows[max(rows)][3]
    else:
        candles = providers.history(provider, instrument, granularity, bars=10)
        if not candles:
            raise SystemExit("no %s price to convert the balance at" % instrument)
        price = candles[-1].mid['c']
    return round(balance * float(price), 2)


if __name__ == '__main__':
    sys.exit(main())
