"""
Live sessions: a backtest's form trading on an account, one process each.

A session is scripts/live.py started with the form (--form), the account
and a folder of its own, where it writes its event log (--events) and its
console. This module starts it, stops it, and reads that folder back for the
page - it never talks to a broker about the trading itself, so what the page
shows is what the process did, not a second opinion about it.

The sessions are rows of an SQLite database (sessions.db, next to their
folders): what was started, on which account, and whether it was stopped.
That row is the state; the process is only its current incarnation. The
processes are children of the web service in their own process group, so a
SIGHUP restart (scripts/web.py re-execs, same PID) leaves them running;
`systemctl restart` kills the unit's cgroup and them with it, and resume()
- called by scripts/web.py at start - starts again every session that was
never stopped, on the same folder, warming up afresh.
"""

import datetime
import glob
import hashlib
import json
import os
import re
import secrets
import shlex
import signal
import sqlite3
import subprocess
import sys
import time

from zoneinfo import ZoneInfo

from parity_deriva.trading import providers
from parity_deriva.etc import settings
from parity_deriva.lib.utils import granularityToTimedelta
from parity_deriva.web import journal, notify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, 'scripts', 'live.py')

#: the setting each provider reads to choose the account it deals on
ACCOUNT_VARIABLE = {'ig': 'IG_ACCOUNT_ID', 'oanda': 'OANDA_API_ACCOUNT_ID',
                    'ib': 'IB_ACCOUNT_ID', 'capital': 'CAPITAL_ACCOUNT_ID',
                    # the login: lib/mt5.terminal picks the terminal holding it
                    'mt5': 'MT5_ACCOUNT'}

#: seconds a listing of accounts is reused: the page polls, the brokers
#: count requests
ACCOUNTS_TTL = 60

SESSION_ID = re.compile(r'\d{8}-\d{6}-[0-9a-f]{6}')


class LiveError(Exception):
    """A request the sessions refuse: the page shows it as it is."""


def serverAccounts():
    """'demo' or 'real': what this server trades (etc/settings.py ACCOUNTS, from .env)."""
    return 'real' if getattr(settings, 'ACCOUNTS', 'demo') == 'real' else 'demo'


def today():
    """Midnight UTC, in epoch ms: the day a loss limit counts."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)


def dotenv():
    """
    The .env as variables: the older parity_deriva/.env, then DATA_DIR/.env
    over it. The service reads them key by key (etc/settings.dotenv); the
    broker modules read os.environ only, so a session is handed them whole.
    """
    out = {}
    for path in (os.path.join(ROOT, '.env'), os.path.join(settings.DATA_DIR, '.env')):
        try:
            with open(path) as handle:
                for line in handle:
                    words = shlex.split(line, comments=True)
                    if words[:1] == ['export']:
                        words = words[1:]
                    if words and '=' in words[0]:
                        key, _, value = words[0].partition('=')
                        out[key] = value
        except (OSError, ValueError):
            pass
    return out


def _load(path, name):
    """A module from a file, under a name of its own: not in sys.modules."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sessionSettings():
    """
    etc/settings.py as a session sees it, .env included, loaded apart: the
    service's own settings module is not reloaded under it.
    """
    from parity_deriva.etc import settings
    saved = dict(os.environ)
    os.environ.update(dotenv())
    try:
        return _load(settings.__file__, 'parity_deriva_session_settings')
    finally:
        os.environ.clear()
        os.environ.update(saved)


class LiveSessions(object):

    def __init__(self, root, setup=None):
        #: where the journals are (web/journal.py): None writes none
        self.setup = setup
        #: the errors each session had when its last alert went out (alerts)
        # ponytail: in memory - after a restart the count starts again from
        # what the sessions have, so errors made while the service was down
        # are not told
        self._errors = {}
        self.root = root
        self.children = {}
        self._accounts = (0, None)

    # ------------------------------------------------------------ database

    def db(self):
        os.makedirs(self.root, exist_ok=True)
        connection = sqlite3.connect(os.path.join(self.root, 'sessions.db'), timeout=10)
        connection.execute("CREATE TABLE IF NOT EXISTS sessions "
                           "(id TEXT PRIMARY KEY, meta TEXT NOT NULL)")
        return connection

    def writeMeta(self, session, meta):
        with self.db() as connection:
            connection.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?)",
                               (session, json.dumps(meta)))

    def meta(self, session):
        if not SESSION_ID.fullmatch(session or ''):
            raise LiveError("no such session")
        with self.db() as connection:
            row = connection.execute("SELECT meta FROM sessions WHERE id = ?",
                                     (session,)).fetchone()
        if row is None:
            raise LiveError("no such session")
        return json.loads(row[0])

    def ids(self):
        with self.db() as connection:
            return [row[0] for row in connection.execute(
                "SELECT id FROM sessions ORDER BY id DESC")]

    def resume(self):
        """Start again every session never stopped whose process is gone."""
        resumed = []
        for session in self.ids():
            meta = self.meta(session)
            if meta.get('stopped') is not None or self.alive(meta):
                continue
            meta['pid'] = self.launch(session, meta['fields'], meta['provider'],
                                      meta['account'])
            meta.setdefault('resumed', []).append(int(time.time() * 1000))
            self.writeMeta(session, meta)
            resumed.append(session)
        return resumed

    # ------------------------------------------------------------ accounts

    def targets(self, fresh=False):
        """
        Every provider, whether its credentials are there, and the accounts
        they reach: what the page offers to start a session on.
        """
        stamp, cached = self._accounts
        if cached is not None and not fresh and time.time() - stamp < ACCOUNTS_TTL:
            return cached
        settings = sessionSettings()
        out = []
        for name in providers.available():
            provider = providers.get_provider(name, setup=settings)
            row = {'provider': name, 'configured': provider.configured(),
                   'accounts': [], 'error': None,
                   'capabilities': dict((k, getattr(provider.capabilities, k))
                                        for k in ('stop_modify', 'expiring_orders',
                                                  'bid_ask_candles'))}
            if row['configured']:
                try:
                    row['accounts'] = provider.accounts()
                except Exception as exc:
                    row['error'] = "%s: %s" % (type(exc).__name__, exc)
            out.append(row)
        self._accounts = (time.time(), out)
        return out

    # ------------------------------------------------------------ sessions

    def path(self, session, *parts):
        if not SESSION_ID.fullmatch(session or ''):
            raise LiveError("no such session")
        return os.path.join(self.root, session, *parts)

    def check(self, fields, provider):
        """
        The refusals scripts/live.py would give, before a process is started:
        a strategy with no live wiring, a provider without what it needs, a
        rule no live broker acts on.
        """
        script = _load(SCRIPT, 'parity_deriva_live_script')
        try:
            spec = script.fromForm(json.dumps(fields))
        except SystemExit as exc:
            raise LiveError(str(exc))
        except Exception as exc:
            raise LiveError(str(exc))
        name, needs = script.liveStrategy(spec)
        if needs is None:
            raise LiveError("%s has no live wiring; these do: %s"
                            % (name, ", ".join(sorted(script.STRATEGIES))))
        try:
            providers.require(providers.get_provider(provider), *needs)
        except providers.ProviderError as exc:
            raise LiveError("%s on %s: %s" % (spec['strategy'], provider, exc))
        return spec

    def start(self, fields, targets, confirm=None):
        """
        One session per {provider, account}; returns their summaries.

        An account of the other kind than the server's is refused (the paper
        one, the simulator, is both). On a real money server a session also
        needs the form promoted from a demo server (promoted()), the day not
        halted by the loss limit, and `confirm` the capital at risk: the
        page asks for it, so a request that did not see it does not trade.
        """
        if not targets:
            raise LiveError("no account chosen")
        kind = serverAccounts()
        # every account trades the same capital - the form's, else the one
        # the backtest ran on - so the sessions' P&L compare number for
        # number; one in USD takes it 1:1 (scripts/live.quoteBalance)
        fields = dict(fields)
        if fields.get('capital') in (None, ''):
            fields['capital'] = fields.get('balance') or str(settings.EQUITY)
        known = dict(((t['provider'], a['id']), a)
                     for t in self.targets() for a in t['accounts'])
        for target in targets:
            if (target.get('provider'), target.get('account')) not in known:
                raise LiveError("%s account %s is not one these credentials reach"
                                % (target.get('provider'), target.get('account')))
            account = known[(target['provider'], target['account'])]
            if not isPaper(target['provider']) and bool(account.get('demo')) != (kind == 'demo'):
                raise LiveError(
                    "this server trades %s (PARITY_DERIVA_ACCOUNTS=%s in .env): %s account %s is %s"
                    % ('demo accounts only' if kind == 'demo' else 'real money only', kind,
                       target['provider'], target['account'],
                       'a demo one' if account.get('demo') else 'real money'))
            self.check(fields, target['provider'])
        if kind == 'real':
            if self.halted():
                raise LiveError("the day's loss limit stopped every session: no new one before "
                                "tomorrow (UTC)")
            promotion = self.promoted(fields)
            if not promotion or not promotion.get('ok'):
                raise LiveError("this form was not promoted from a demo server with its record "
                                "(%d days, %d trades, net \u2265 %g, no parity alarm): promote it from "
                                "the demo server's live page"
                                % (settings.PROMOTE_DAYS, settings.PROMOTE_TRADES, settings.PROMOTE_MIN_NET))
            if str(confirm or '') != str(fields['capital']):
                raise LiveError("real money: confirm the capital at risk, %s a session on %d "
                                "account%s" % (fields['capital'], len(targets),
                                               '' if len(targets) == 1 else 's'))
        started = []
        for target in targets:
            account = known[(target['provider'], target['account'])]
            started.append(self.spawn(fields, target['provider'], account))
            self.journal(started[-1], 'session-start', {'capital': fields.get('capital')})
        return started

    def journal(self, s, kind, data=None):
        """A session's entry in its strategy's journal (web/journal.py)."""
        if self.setup is None or not isinstance(s, dict):
            return
        fields = s.get('fields') or {}
        journal.record(self.setup, fields.get('strategy'), kind, 'milestone',
                       dict({'id': s.get('id'), 'provider': s.get('provider'), 'account': s.get('account'),
                             'demo': s.get('demo')}, **(data or {})),
                       fields, link={'kind': 'session', 'id': s.get('id')})

    def spawn(self, fields, provider, account):
        session = time.strftime('%Y%m%d-%H%M%S-') + secrets.token_hex(3)
        os.makedirs(self.path(session))
        pid = self.launch(session, fields, provider, account['id'])
        meta = {'id': session, 'fields': fields, 'provider': provider,
                'account': account['id'], 'accountName': account.get('name'),
                'currency': account.get('currency'), 'demo': account.get('demo'),
                'balance': account.get('balance'), 'pid': pid,
                'started': int(time.time() * 1000), 'stopped': None}
        self.writeMeta(session, meta)
        return self.summary(session)

    def launch(self, session, fields, provider, account):
        """scripts/live.py for this session, appending to its folder."""
        folder = self.path(session)
        os.makedirs(folder, exist_ok=True)
        env = dict(os.environ)
        env.update(dotenv())
        if provider in ACCOUNT_VARIABLE:
            env[ACCOUNT_VARIABLE[provider]] = account
        if provider == 'ig':
            # the account on every request (IG-ACCOUNT-ID): a version 2
            # session deals on the login's current account, whichever that is
            env['IG_SESSION_VERSION'] = '3'
        env['PYTHONPATH'] = os.path.dirname(ROOT)
        env['PARITY_DERIVA_HOME'] = ROOT
        command = [sys.executable, SCRIPT, '--provider', provider,
                   '--form', json.dumps(fields), '--account', account,
                   '--events', os.path.join(folder, 'events')]
        # scripts/live.py wants it said on a real DOMAIN; start() checked the rest
        if serverAccounts() == 'real':
            command.append('--live')
        # the shadow simulator fills against a candle's bid and ask: on a
        # provider serving one series it has nothing to fill against. On the
        # paper account the execution handler is the simulator itself, so a
        # shadow would be a second copy of it
        capabilities = providers.get_provider(provider).capabilities
        if capabilities.paper or not capabilities.bid_ask_candles:
            command.append('--no-shadow')
        with open(os.path.join(folder, 'console.log'), 'ab') as console:
            child = subprocess.Popen(command, cwd=os.path.dirname(ROOT), env=env,
                                     stdout=console, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL,
                                     start_new_session=True)
        self.children[session] = child
        return child.pid

    def alive(self, meta):
        """Is the session's process still there - and still this session's?"""
        pid = meta.get('pid')
        if not pid:
            return False
        child = self.children.get(meta['id'])
        if child is not None:
            return child.poll() is None
        try:
            # a child of an earlier life of this service: reaped here, or it
            # stays a zombie that os.kill would call alive
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass
        try:
            with open('/proc/%d/cmdline' % pid, 'rb') as handle:
                return meta['id'].encode() in handle.read()
        except OSError:
            return False

    def stop(self, session):
        """
        Stop and close everything. SIGTERM is taken by the process as "cancel
        this session's resting orders, close its open trades at market, then
        exit" (scripts/live.py stopAndClose), so it keeps running for as long
        as that takes - the page shows it closing - and only this session's
        orders and trades come off the account.
        """
        meta = self.meta(session)
        if self.alive(meta):
            os.killpg(meta['pid'], signal.SIGTERM)
        first = meta.get('stopped') is None
        if first:
            meta['stopped'] = int(time.time() * 1000)
            self.writeMeta(session, meta)
        out = self.summary(session)
        if first:
            self.journal(out, 'session-stop', {'trades': len(out.get('closed') or []), 'net': out.get('net')})
        return out

    def stopAll(self):
        """The kill switch: every session that is running, stopped and closed."""
        stopped = []
        for session in self.ids():
            try:
                if self.alive(self.meta(session)):
                    stopped.append(self.stop(session)['id'])
            except (LiveError, OSError, ValueError):
                continue
        return {'stopped': stopped}

    # ------------------------------------------------ demo, real, promotion

    def record(self, fields):
        """
        What a form did on this (demo) server: every session of it (the same
        groupKey), its closed trades and its parity monitor's findings, and
        the days, trades and alarms added up - what a promotion carries.
        """
        key = groupKey(fields)
        sessions = []
        for session in self.ids():
            try:
                s = self.summary(session)
            except (LiveError, OSError, ValueError):
                continue
            if groupKey(s.get('fields')) != key:
                continue
            sessions.append({'id': s['id'], 'provider': s['provider'], 'account': s['account'],
                             'demo': s.get('demo'), 'started': s['started'], 'stopped': s.get('stopped'),
                             'closed': [{'time': t['time'], 'pl': t['pl']} for t in s['closed']],
                             'parity': {'divergences': s['parity']['divergences'],
                                        'alarms': s['parity']['alarms']}})
        return {'fields': fields, 'sessions': sessions, **judge(sessions)}

    def promotionPath(self, fields):
        name = hashlib.sha1(groupKey(fields).encode()).hexdigest()[:16]
        return os.path.join(self.root, 'promotions', name + '.json')

    def promoted(self, fields):
        """The promotion a real server keeps for a form, or None."""
        try:
            with open(self.promotionPath(fields)) as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return None

    def promote(self, record, origin):
        """
        Keep a demo server's record of a form, judged here and not taken on
        its word: ok once it has the days, the trades, the net and no parity
        alarm this server's settings ask for. Kept either way, as the proof.
        """
        if not isinstance(record, dict) or not isinstance(record.get('fields'), dict) \
                or not isinstance(record.get('sessions'), list):
            raise LiveError("a record is {fields, sessions: [...]}, as record() makes it")
        sessions = record['sessions']
        verdict = judge(sessions)
        need = []
        if verdict['days'] < settings.PROMOTE_DAYS:
            need.append("%d days on demo, it has %.1f" % (settings.PROMOTE_DAYS, verdict['days']))
        slow = verdict['days'] >= settings.PROMOTE_SLOW_DAYS and verdict['trades'] >= settings.PROMOTE_MIN_TRADES
        if verdict['trades'] < settings.PROMOTE_TRADES and not slow:
            need.append("%d closed trades, or %d after %d days: it has %d" % (
                settings.PROMOTE_TRADES, settings.PROMOTE_MIN_TRADES, settings.PROMOTE_SLOW_DAYS,
                verdict['trades']))
        if verdict['net'] < settings.PROMOTE_MIN_NET:
            need.append("net on demo \u2265 %g, it has %.2f" % (settings.PROMOTE_MIN_NET, verdict['net']))
        if verdict['alarms']:
            need.append("no parity alarm, it has %d" % verdict['alarms'])
        if any(s.get('demo') is False for s in sessions):
            need.append("a record of demo accounts only")
        # the version's card, a copy of it kept here: what C1b and the live
        # protections judge it by (web/cards.py)
        card = record.get('card')
        if card is not None and self.setup is not None:
            from parity_deriva.web import cards
            try:
                card = cards.receive(self.setup, card)
            except cards.CardError as exc:
                need.append(str(exc))
        kept = {'fields': record['fields'], 'sessions': sessions, 'origin': origin, 'card': card,
                'received': int(time.time() * 1000), 'ok': not need, 'need': need, **verdict}
        path = self.promotionPath(record['fields'])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + '.part', 'w') as handle:
            json.dump(kept, handle)
        os.replace(path + '.part', path)
        return kept

    def lossToday(self):
        """Today's closed P&L of every session (UTC day) and the capital they trade."""
        since, net, capital = today(), 0.0, 0.0
        for session in self.ids():
            try:
                meta = self.meta(session)
                if meta.get('stopped') is not None and meta['stopped'] < since:
                    continue
                s = self.summary(session)
            except (LiveError, OSError, ValueError):
                continue
            net += sum(t['pl'] for t in s['closed'] if t.get('pl') is not None and (t['time'] or 0) >= since)
            capital += capitalOf(meta) or 0.0
        return net, capital

    def haltPath(self):
        return os.path.join(self.root, 'halted.json')

    def halted(self):
        """Today's halt by the loss limit, or None."""
        try:
            with open(self.haltPath()) as handle:
                held = json.load(handle)
        except (OSError, ValueError):
            return None
        return held if held.get('day') == today() else None

    def guard(self, pct):
        """
        The loss limit, once: a day's loss of `pct` % of the capital traded
        stops every session and keeps new ones off until tomorrow.
        """
        net, capital = self.lossToday()
        if capital <= 0 or net > -capital * pct / 100.0 or self.halted():
            return None
        stopped = self.stopAll()['stopped']
        held = {'day': today(), 'at': int(time.time() * 1000), 'net': net, 'capital': capital,
                'pct': pct, 'stopped': stopped}
        with open(self.haltPath() + '.part', 'w') as handle:
            json.dump(held, handle)
        os.replace(self.haltPath() + '.part', self.haltPath())
        return held

    def watch(self, setup, every=60):
        """
        Every minute, in the background: on a real money server the loss
        limit (guard), on every server the alerts (alerts).
        """
        import logging
        import threading

        log = logging.getLogger('parity_deriva.web')

        def loop():
            while True:
                try:
                    if setup.ACCOUNTS == 'real' and setup.DAILY_LOSS_PCT > 0:
                        self.guard(setup.DAILY_LOSS_PCT)
                except Exception:
                    log.exception("loss limit")
                try:
                    self.alerts(setup)
                except Exception:
                    log.exception("alerts")
                time.sleep(every)
        thread = threading.Thread(target=loop, name='watch')
        thread.daemon = True
        thread.start()

    def alerts(self, setup):
        """
        The minute's round of alerts (web/notify.py), each sent once and
        "resolved" once when it has gone: a session whose process died
        without anybody stopping it, one with no new candle for
        ALERT_STALE_BARS of its bars while the market is open, broker errors
        and rejected orders, a parity alarm, the loss limit.

        Errors are counted rather than a state: an alert says how many came
        since the last one and stays until the session stops or the banner
        is dismissed; more errors after that are a new alert.
        """
        now = time.time()
        opened = notify.read(setup)['open']
        watched = set()
        for sid in self.ids():
            try:
                # a stopped session's log is not read again every minute
                if self.meta(sid).get('stopped') is not None:
                    continue
                s = self.summary(sid)
            except (LiveError, OSError, ValueError):
                continue
            watched.add(sid)
            errors = (s.get('errors') or 0) + (s.get('rejects') or 0)
            seen = self._errors.setdefault(sid, errors)
            problems = {}
            if s.get('exited'):
                problems['exited'] = 'the session stopped by itself: its process is gone'
            elif s.get('running'):
                stale = staleBars(s, now, getattr(setup, 'ALERT_STALE_BARS', 3))
                if stale:
                    problems['stale'] = 'no new candle for %d bars' % stale
            if (s.get('parity') or {}).get('alarms'):
                problems['parity'] = 'parity alarm: ' + '; '.join(s['parity']['alarms'])[:300]
            if errors > seen:
                many = '' if errors - seen == 1 else 's'
                problems['errors'] = '%d broker error%s or rejected order%s' % (errors - seen, many, many)
            for kind in SESSION_ALERTS:
                if kind in problems:
                    sent = notify.notify(setup, 'urgent', kind, sid, '%s · %s' % (label(s), problems[kind]))
                    if sent:
                        self.journal(s, 'alert', {'kind': kind, 'text': problems[kind]})
                    if sent and kind == 'errors':
                        self._errors[sid] = errors
                elif kind != 'errors' and '%s:%s' % (kind, sid) in opened:
                    notify.resolve(setup, kind, sid)
        # a session stopped or deleted with an alert open: its problem has gone with it
        for alert in opened.values():
            if alert['kind'] in SESSION_ALERTS and alert['key'] not in watched:
                notify.resolve(setup, alert['kind'], alert['key'])
        held = self.halted()
        if held:
            notify.notify(setup, 'urgent', 'loss-limit', 'day', "loss limit: %.2f today on a capital of %.2f "
                          "(%g%%): every session stopped until tomorrow (UTC)"
                          % (held['net'], held['capital'], held['pct']))
        elif 'loss-limit:day' in opened:
            notify.resolve(setup, 'loss-limit', 'day')

    def sessions(self):
        out = []
        for session in self.ids():
            try:
                out.append(self.summary(session))
            except (LiveError, OSError, ValueError):
                continue
        return out

    def snapshot(self):
        """
        Every session as a trade server tells the archive (web/mcp.py
        live_status): the form, the account, the trades open and closed with
        their P&L and the parity monitor's findings - not the events nor the
        console, which stay on this server's own page.
        """
        keep = ('id', 'fields', 'provider', 'account', 'accountName', 'currency', 'demo',
                'balance', 'started', 'stopped', 'running', 'exited', 'lastBar', 'signals',
                'orders', 'errors', 'open', 'closed', 'net', 'won', 'lost')
        return [dict([(k, s.get(k)) for k in keep], parity={
            'divergences': s['parity']['divergences'], 'alarms': s['parity']['alarms']})
            for s in self.sessions()]

    def running(self):
        """How many sessions have their process up: the menu's light."""
        count = 0
        for session in self.ids():
            try:
                count += bool(self.alive(self.meta(session)))
            except (LiveError, OSError, ValueError):
                continue
        return count

    def delete(self, session):
        meta = self.meta(session)
        if self.alive(meta):
            raise LiveError("stop the session first")
        import shutil
        shutil.rmtree(self.path(session), ignore_errors=True)
        with self.db() as connection:
            connection.execute("DELETE FROM sessions WHERE id = ?", (session,))
        return {'deleted': session}

    # ------------------------------------------------------------ reading

    def events(self, session):
        """Every event the session logged, in order."""
        out = []
        for name in sorted(glob.glob(self.path(session, 'events-*.log'))):
            with open(name) as handle:
                for line in handle:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
        return out

    def console(self, session, lines=80):
        try:
            with open(self.path(session, 'console.log'), 'rb') as handle:
                handle.seek(0, 2)
                handle.seek(max(0, handle.tell() - 64 * 1024))
                text = handle.read().decode('utf-8', 'replace')
        except OSError:
            return []
        return text.splitlines()[-lines:]

    def summary(self, session, detail=False):
        """
        What the page shows of one session: the process, the last bar, the
        orders, the positions open and closed, and the P&L the broker put on
        each close. With `detail` also the events themselves and the console.
        """
        meta = self.meta(session)
        events = self.events(session)
        state = read(events, paper=isPaper(meta.get('provider')))
        out = dict(meta, running=self.alive(meta), **state)
        if out['running'] is False and meta.get('stopped') is None:
            out['exited'] = True
        start = capitalOf(meta)
        if start is not None:
            out['curve'] = [[meta['started'], start]]
            for trade in state['closed']:
                if trade.get('pl') is not None:
                    out['curve'].append([trade['time'], out['curve'][-1][1] + trade['pl']])
        if detail:
            out['events'] = events[-400:]
            out['console'] = self.console(session)
        return out


#: the alerts of one session, keyed by its id (LiveSessions.alerts)
SESSION_ALERTS = ('exited', 'stale', 'parity', 'errors')
NEW_YORK = ZoneInfo('America/New_York')


def label(session):
    """A session in an alert: the form and the broker, never the account."""
    f = session.get('fields') or {}
    return '%s · %s %s · %s' % (f.get('strategy'), f.get('instrument'), f.get('granularity'),
                               session.get('provider'))


def marketSeconds(a, b):
    """
    The seconds from `a` to `b` (epoch) with the market open: less the
    forex weekend, Friday 17:00 to Sunday 17:00 New York time.
    """
    # ponytail: the forex week for every instrument, no holidays; a stock's
    # own hours once stocks trade live
    total = max(0.0, b - a)
    day = datetime.datetime.fromtimestamp(a, NEW_YORK).date()
    friday = day - datetime.timedelta(days=(day.weekday() - 4) % 7)
    while True:
        shut = datetime.datetime.combine(friday, datetime.time(17), NEW_YORK).timestamp()
        if shut >= b:
            return total
        opens = datetime.datetime.combine(friday + datetime.timedelta(days=2), datetime.time(17),
                                          NEW_YORK).timestamp()
        total -= max(0.0, min(b, opens) - max(a, shut))
        friday += datetime.timedelta(days=7)


def staleBars(session, now, bars):
    """
    How many of its bars a running session has gone without a new candle,
    with the market open, once that is more than `bars`; else 0. The last
    candle's time is its open, and it arrives when it closes: a bar late.
    """
    try:
        step = granularityToTimedelta((session.get('fields') or {}).get('granularity')).total_seconds()
    except (AttributeError, TypeError, ValueError):
        return 0
    last = (session.get('lastBar') or {}).get('time') or session.get('started')
    if not step or not last:
        return 0
    gone = marketSeconds(last / 1000.0, now) / step - 1
    return int(gone) if gone > bars else 0


def judge(sessions):
    """The days, closed trades and parity alarms of a form's sessions, added up."""
    now = int(time.time() * 1000)
    starts = [s['started'] for s in sessions if s.get('started')]
    ends = [s.get('stopped') or now for s in sessions if s.get('started')]
    return {'days': round((max(ends) - min(starts)) / 86400000.0, 2) if starts else 0.0,
            'trades': sum(len(s.get('closed') or []) for s in sessions),
            'net': sum(t.get('pl') or 0 for s in sessions for t in s.get('closed') or []),
            'alarms': sum(len((s.get('parity') or {}).get('alarms') or []) for s in sessions)}


def millis(text):
    if not text:
        return None
    try:
        when = datetime.datetime.fromisoformat(str(text).replace('Z', ''))
    except ValueError:
        return None
    return int(when.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)


def isPaper(provider):
    """Is this provider's execution handler the simulator (no broker)?"""
    try:
        return bool(providers.get_provider(provider).capabilities.paper)
    except Exception:
        return False


def fills(events, kind='TRANSACTION'):
    """
    Open positions and closed trades out of one side's fill events.

    TRANSACTION ORDER_FILL is the broker's side (or, on the paper account,
    the simulator promoted to it); SIMULATEDFILL is the shadow's. Both carry
    the same fields, so the same reader serves both and the two come out in
    the same shape - which is what lets them be laid side by side.

    The deal key is whatever the side has: a broker's dealId or transaction
    id, the simulator's orderID (the same on the opening fill and on the
    close, backtest/oanda.py reportFill and handleSLTP). A close's exit is
    the closed trade's price where the broker states it and the fill's own
    price otherwise, which is where the simulator puts the level.
    """
    open_ = {}
    closed = []
    for event in events:
        if event.get('_type') != kind:
            continue
        if kind == 'TRANSACTION' and event.get('type') != 'ORDER_FILL':
            continue
        deal = event.get('dealId') or event.get('id') or event.get('orderID')
        if event.get('tradesClosed'):
            opened = open_.pop(deal, {})
            trade = {'deal': deal, 'time': millis(event.get('time')),
                     'units': event.get('units') or opened.get('units'),
                     'entry': opened.get('price') or event.get('openLevel'),
                     'exit': (event['tradesClosed'][0] or {}).get('price')
                     or event.get('price'),
                     'pl': event.get('pl'), 'reason': event.get('reason'),
                     'opened': opened.get('time'),
                     'side': _side(opened.get('units')),
                     'signal': event.get('signalNumber') or opened.get('signal')}
            if trade['pl'] is None and None not in (trade['entry'], trade['exit'],
                                                    trade['units']):
                # a broker whose history carries no P&L for the deal
                # (Capital.com's): the move times the units, in the
                # quote currency, and said to be an estimate
                trade['pl'] = (float(trade['exit']) - float(trade['entry'])) \
                    * float(trade['units'])
                trade['estimated'] = True
            closed.append(trade)
        else:
            open_[deal] = {'deal': deal, 'time': millis(event.get('time')),
                           'units': event.get('units'), 'price': event.get('price'),
                           'signal': event.get('signalNumber')}
    return list(open_.values()), closed


def read(events, paper=False):
    """
    The trading of a session out of its event log.

    TRANSACTION events are the broker's side of things: an ORDER_FILL with
    tradesClosed is a close carrying its P&L, one without opens a position.
    SIMULATEDFILL events are the shadow's, read into simOpen/simClosed the
    same way - except on the paper account, where the log holds the
    simulator's fills twice (its own and the promoted TRANSACTION) and the
    shadow side would be a copy of the real one.

    STATUS PARITY events are the parity monitor's findings, one per
    divergence, and PARITY_ALARM its breaches (trading/parity.py).
    """
    last = None
    signals = orders = cancels = rejects = 0
    errors = []
    parity = {'divergences': 0, 'byKind': {}, 'alarms': [], 'last': None}
    for event in events:
        kind = event.get('_type')
        if kind == 'CANDLE':
            mid = event.get('mid') or {}
            last = {'time': millis(event.get('time')), 'close': mid.get('c'),
                    'instrument': event.get('instrument')}
        elif kind == 'SIGNAL':
            signals += 1
        elif kind == 'ORDER':
            orders += 1
        elif kind == 'ORDERCANCEL':
            cancels += 1
        elif kind == 'STATUS' and event.get('status') == 'ERROR':
            errors.append(millis(event.get('_created')))
        elif kind == 'TRANSACTION' and str(event.get('type') or '').endswith('ORDER_REJECT'):
            rejects += 1
        elif kind == 'STATUS' and event.get('status') == 'PARITY':
            parity['divergences'] += 1
            name = event.get('kind') or '?'
            parity['byKind'][name] = parity['byKind'].get(name, 0) + 1
            parity['last'] = {'kind': name, 'key': event.get('key'),
                              'detail': event.get('detail'),
                              'time': millis(event.get('_created'))}
        elif kind == 'STATUS' and event.get('status') == 'PARITY_ALARM':
            parity['alarms'] = list(event.get('breaches') or [])
    open_, closed = fills(events, 'TRANSACTION')
    simOpen, simClosed = ([], []) if paper else fills(events, 'SIMULATEDFILL')
    net = sum(t['pl'] for t in closed if t.get('pl') is not None)
    return {'lastBar': last, 'signals': signals, 'orders': orders,
            'cancels': cancels, 'errors': len(errors), 'rejects': rejects,
            'open': open_, 'closed': closed, 'net': net,
            'won': sum(1 for t in closed if (t.get('pl') or 0) > 0),
            'lost': sum(1 for t in closed if (t.get('pl') or 0) < 0),
            'simOpen': simOpen, 'simClosed': simClosed, 'parity': parity}


# ------------------------------------------------------------------- skew

#: the form fields that make two sessions the same run: not the window it
#: was backtested on, not the capital, not the confirmation tick
GROUP_SKIP = frozenset(['from', 'to', 'balance', 'capital', 'confirmed'])


def groupKey(fields):
    fields = fields or {}
    head = [str(fields.get(k, '')) for k in ('strategy', 'instrument', 'granularity')]
    rest = sorted("%s=%s" % (k, v) for k, v in fields.items()
                  if k not in GROUP_SKIP and k not in ('strategy', 'instrument', 'granularity')
                  and v not in ('', None))
    return "|".join(head + rest)


def _side(units):
    """+1 long, -1 short, None when the opening fill is not in the log."""
    try:
        units = float(units)
    except (TypeError, ValueError):
        return None
    return 1 if units > 0 else -1 if units < 0 else None


def capitalOf(session):
    """The capital a session sizes on: its reference, else the account's balance."""
    for value in ((session.get('fields') or {}).get('capital'), session.get('balance')):
        try:
            if value not in (None, '') and float(value) > 0:
                return float(value)
        except (TypeError, ValueError):
            pass
    return None


def _pips(a, b, pip):
    if a is None or b is None:
        return None
    return round((float(a) - float(b)) / pip, 3)


def compareTrades(broker, sim, pip, capitals=(None, None)):
    """
    A broker session's closed trades against the reference's, joined on the
    signal key.

    The key is a function of the candle that produced the signal
    (lib/utils.signalNumber), so the same strategy on the same bars gives
    the same key on every account - and a key one side has and the other
    has not is the finding this exists for: one feed's candles made the
    strategy signal and the other's did not, or one account never filled.
    Differences are in pips of the instrument, positive when the broker
    paid more than the simulator. The P&L difference three ways: in the
    accounts' money (the same capital on every account, a USD one taken
    1:1), in pips - the price the broker gave, whatever the size - and in
    percent of each side's `capitals` (broker, sim).
    """
    mine = dict((t['signal'], t) for t in broker if t.get('signal'))
    theirs = dict((t['signal'], t) for t in sim if t.get('signal'))
    rows = []
    for key in sorted(set(mine) | set(theirs), key=lambda k: (
            (mine.get(k) or theirs.get(k) or {}).get('opened') or 0, k)):
        b, s = mine.get(key), theirs.get(key)
        row = {'signal': key, 'broker': b, 'sim': s, 'entryDiff': None,
               'exitDiff': None, 'plDiff': None, 'plDiffPips': None,
               'plDiffPct': None, 'outcomeMatch': None,
               'entryLagMs': None, 'unpaired': None}
        if b is None:
            row['unpaired'] = 'broker'
        elif s is None:
            row['unpaired'] = 'sim'
        else:
            row['entryDiff'] = _pips(b.get('entry'), s.get('entry'), pip)
            row['exitDiff'] = _pips(b.get('exit'), s.get('exit'), pip)
            if b.get('pl') is not None and s.get('pl') is not None:
                row['plDiff'] = round(float(b['pl']) - float(s['pl']), 4)
                if all(capitals):
                    row['plDiffPct'] = round(float(b['pl']) / capitals[0] * 100
                                             - float(s['pl']) / capitals[1] * 100, 4)
            side = s.get('side') or b.get('side')
            if side and None not in (row['entryDiff'], row['exitDiff']):
                row['plDiffPips'] = round(side * (row['exitDiff'] - row['entryDiff']), 3)
            if b.get('reason') and s.get('reason'):
                row['outcomeMatch'] = (b['reason'] == s['reason'])
            if b.get('opened') and s.get('opened'):
                row['entryLagMs'] = b['opened'] - s['opened']
        rows.append(row)
    paired = [r for r in rows if r['unpaired'] is None]
    entries = [abs(r['entryDiff']) for r in paired if r['entryDiff'] is not None]
    exits = [r['exitDiff'] for r in paired if r['exitDiff'] is not None]
    summary = {
        'paired': len(paired),
        'unpairedBroker': sum(1 for r in rows if r['unpaired'] == 'broker'),
        'unpairedSim': sum(1 for r in rows if r['unpaired'] == 'sim'),
        'meanEntryDiff': round(sum(entries) / len(entries), 3) if entries else None,
        'maxEntryDiff': round(max(entries), 3) if entries else None,
        'meanExitDiff': round(sum(exits) / len(exits), 3) if exits else None,
        'outcomeMismatch': sum(1 for r in paired if r['outcomeMatch'] is False),
        'plDiff': round(sum(r['plDiff'] for r in paired if r['plDiff'] is not None), 4),
        'plDiffPips': round(sum(r['plDiffPips'] for r in paired if r['plDiffPips'] is not None), 3),
        'plDiffPct': round(sum(r['plDiffPct'] for r in paired if r['plDiffPct'] is not None), 4),
    }
    return rows, summary


def tradeSkew(sessions, setup=None):
    """
    Every group of sessions running one form, each broker session against
    the group's paper session. Without a paper session in the group there
    is no reference and the rows are empty, said as `reference: null`.
    """
    from parity_deriva.lib.utils import pipSize
    groups = {}
    for s in sessions:
        groups.setdefault(groupKey(s.get('fields')), []).append(s)
    out = []
    for key in sorted(groups):
        members = groups[key]
        fields = members[0].get('fields') or {}
        instrument = fields.get('instrument') or 'EUR_USD'
        pip = pipSize(instrument, setup)
        reference = next((s for s in members if isPaper(s.get('provider'))), None)
        group = {'key': key, 'strategy': fields.get('strategy'),
                 'instrument': instrument, 'granularity': fields.get('granularity'),
                 'pip': pip, 'sessions': [],
                 'reference': None if reference is None else {
                     'id': reference['id'], 'provider': reference['provider'],
                     'account': reference['account']}}
        for s in members:
            if reference is not None and s is reference:
                continue
            rows, summary = ([], None)
            if reference is not None:
                rows, summary = compareTrades(s.get('closed') or [],
                                              reference.get('closed') or [], pip,
                                              (capitalOf(s), capitalOf(reference)))
            group['sessions'].append({
                'id': s['id'], 'provider': s.get('provider'), 'account': s.get('account'),
                'running': s.get('running'), 'summary': summary, 'rows': rows,
                'parity': s.get('parity')})
        out.append(group)
    return {'groups': out}
