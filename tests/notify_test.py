"""
Tests for web/notify.py and the minute's round of alerts
(web/livesessions.py alerts): once per event and once resolved, the banner
always, email and Telegram only when set (both made up here), no account in
the text, and the events of a session - dead, stalled, errors, parity - and
the loss limit.
"""

import datetime
import os
import shutil
import tempfile
import time
import types
import unittest
from unittest import mock

from parity_deriva.web import livesessions, notify

UTC = datetime.timezone.utc


def at(*when):
    return datetime.datetime(*when, tzinfo=UTC).timestamp()


class Mail(object):
    """smtplib.SMTP, made up: what was sent."""
    sent = []

    def __init__(self, host, port, timeout=None):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def ehlo(self):
        pass

    def has_extn(self, name):
        return False

    def login(self, user, password):
        pass

    def send_message(self, message):
        Mail.sent.append(message)


class Case(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix='parity-deriva-notify-')
        self.addCleanup(shutil.rmtree, self.home, True)
        self.setup = types.SimpleNamespace(DATA_DIR=self.home, ACCOUNTS='demo')
        Mail.sent = []
        self.posted = []
        for patch in (mock.patch.object(notify.smtplib, 'SMTP', Mail),
                      mock.patch.object(notify.urllib.request, 'urlopen', self.urlopen),
                      mock.patch.object(notify, 'logger', mock.Mock())):
            patch.start()
            self.addCleanup(patch.stop)

    def urlopen(self, url, data=None, timeout=None):
        self.posted.append((url, data))
        return types.SimpleNamespace(read=lambda: b'{"ok": true}')

    def configure(self):
        self.setup.SMTP_HOST, self.setup.SMTP_TO = 'mail.example', 'me@example.it'
        self.setup.TELEGRAM_TOKEN, self.setup.TELEGRAM_CHAT = 'BOT-SECRET', '42'


class NotifyTest(Case):

    def test_an_alert_goes_once_and_is_resolved_once(self):
        self.configure()
        self.assertIsNotNone(notify.notify(self.setup, 'urgent', 'exited', 'S1', 'AB · session died'))
        self.assertIsNone(notify.notify(self.setup, 'urgent', 'exited', 'S1', 'AB · session died'))
        self.assertEqual((len(Mail.sent), len(self.posted)), (1, 1))
        self.assertIn('exited:S1', notify.read(self.setup)['open'])
        done = notify.resolve(self.setup, 'exited', 'S1')
        self.assertEqual(done['text'], 'resolved: AB · session died')
        self.assertIsNone(notify.resolve(self.setup, 'exited', 'S1'))
        self.assertEqual((len(Mail.sent), len(self.posted)), (2, 2))
        self.assertEqual(notify.read(self.setup)['open'], {})
        self.assertEqual([a.get('resolved', False) for a in notify.read(self.setup)['recent']], [True, False])

    def test_the_banner_always_the_channels_only_when_set(self):
        sent = notify.notify(self.setup, 'urgent', 'stale', 'S1', 'no candles')
        self.assertIsNotNone(sent)
        self.assertEqual((Mail.sent, self.posted), ([], []))
        self.assertEqual(notify.test(self.setup), {'email': 'off', 'telegram': 'off', 'phones': 'off'})
        self.configure()
        self.assertEqual(notify.test(self.setup), {'email': 'sent', 'telegram': 'sent', 'phones': 'off'})
        self.assertEqual(Mail.sent[0]['To'], 'me@example.it')
        self.assertIn('[parity demo]', Mail.sent[0]['Subject'])
        self.assertIn('/botBOT-SECRET/sendMessage', self.posted[0][0])
        # an informative one is a banner and a log line, nothing more
        notify.notify(self.setup, 'info', 'state', 'S1', 'M1502 v3 in DEMO')
        self.assertEqual((len(Mail.sent), len(self.posted)), (1, 1))

    def test_a_channel_that_fails_does_not_stop_the_others(self):
        self.configure()

        def down(*args, **kwargs):
            raise OSError("no route to host")
        with mock.patch.object(notify.urllib.request, 'urlopen', down):
            self.assertEqual(notify.test(self.setup)['telegram'], 'no route to host')
        self.assertEqual(len(Mail.sent), 1)

    def test_a_dismissed_banner_is_closed_without_a_message(self):
        self.configure()
        notify.notify(self.setup, 'urgent', 'errors', 'S1', '2 broker errors')
        notify.dismiss(self.setup, 'errors:S1')
        self.assertEqual(notify.read(self.setup)['open'], {})
        self.assertEqual(len(Mail.sent), 1)


def session(**more):
    base = {'id': 'S1', 'provider': 'ig', 'account': 'ACCOUNT-7731', 'accountName': 'my account',
            'fields': {'strategy': 'AB', 'instrument': 'EUR_USD', 'granularity': 'M15'},
            'started': int(at(2026, 9, 23, 9) * 1000), 'stopped': None, 'running': True,
            'lastBar': {'time': int(at(2026, 9, 23, 10) * 1000)}, 'errors': 0, 'rejects': 0,
            'parity': {'alarms': []}}
    base.update(more)
    return base


class RoundTest(Case):

    def setUp(self):
        super().setUp()
        self.live = livesessions.LiveSessions(os.path.join(self.home, 'live'))
        self.state = {'S1': session()}
        self.live.ids = lambda: list(self.state)
        self.live.meta = lambda sid: self.state[sid]
        self.live.summary = lambda sid: self.state[sid]
        self.live.halted = lambda: None
        self.now = at(2026, 9, 23, 10, 30)
        clock = mock.patch.object(livesessions.time, 'time', lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)

    def open(self):
        return sorted(notify.read(self.setup)['open'])

    def test_a_session_whose_process_died_is_urgent_and_resolved_by_a_restart(self):
        self.live.alerts(self.setup)
        self.assertEqual(self.open(), [])
        self.state['S1'].update(running=False, exited=True)
        self.live.alerts(self.setup)
        self.live.alerts(self.setup)
        self.assertEqual(self.open(), ['exited:S1'])
        text = notify.read(self.setup)['open']['exited:S1']['text']
        self.assertEqual(text, 'AB · EUR_USD M15 · ig · the session stopped by itself: its process is gone')
        self.assertNotIn('7731', text)
        self.assertNotIn('my account', text)
        self.state['S1'].update(running=True, exited=False)
        self.live.alerts(self.setup)
        self.assertEqual(self.open(), [])

    def test_no_candle_for_three_bars_with_the_market_open(self):
        self.now = at(2026, 9, 23, 11, 1)
        self.live.alerts(self.setup)
        self.assertEqual(self.open(), ['stale:S1'])
        self.assertIn('no new candle for 3 bars', notify.read(self.setup)['open']['stale:S1']['text'])
        self.state['S1']['lastBar'] = {'time': int(at(2026, 9, 23, 10, 45) * 1000)}
        self.live.alerts(self.setup)
        self.assertEqual(self.open(), [])
        # the weekend is no stall: Friday's last bar, Sunday before the open
        self.state['S1']['lastBar'] = {'time': int(at(2026, 9, 25, 20, 45) * 1000)}
        self.now = at(2026, 9, 27, 20, 0)
        self.live.alerts(self.setup)
        self.assertEqual(self.open(), [])
        self.now = at(2026, 9, 27, 22, 0)
        self.live.alerts(self.setup)
        self.assertEqual(self.open(), ['stale:S1'])

    def test_errors_and_rejected_orders_counted_since_the_last_alert(self):
        self.live.alerts(self.setup)
        self.state['S1'].update(errors=1, rejects=1)
        self.live.alerts(self.setup)
        self.assertEqual(notify.read(self.setup)['open']['errors:S1']['text'],
                         'AB · EUR_USD M15 · ig · 2 broker errors or rejected orders')
        # the count is no state: it stays open, and a dismissed one comes back only with new errors
        self.live.alerts(self.setup)
        notify.dismiss(self.setup, 'errors:S1')
        self.live.alerts(self.setup)
        self.assertEqual(self.open(), [])
        self.state['S1']['rejects'] = 2
        self.live.alerts(self.setup)
        self.assertEqual(notify.read(self.setup)['open']['errors:S1']['text'],
                         'AB · EUR_USD M15 · ig · 1 broker error or rejected order')

    def test_a_parity_alarm_and_a_session_stopped_with_its_alert_open(self):
        self.state['S1']['parity'] = {'alarms': ['slippage 3.1 pips > 2']}
        self.live.alerts(self.setup)
        self.assertEqual(self.open(), ['parity:S1'])
        self.state['S1']['stopped'] = int(self.now * 1000)
        self.live.alerts(self.setup)
        self.assertEqual(self.open(), [])

    def test_the_loss_limit(self):
        self.live.halted = lambda: {'day': 1, 'net': -31.5, 'capital': 1000.0, 'pct': 3}
        self.live.alerts(self.setup)
        self.assertEqual(self.open(), ['loss-limit:day'])
        self.live.halted = lambda: None
        self.live.alerts(self.setup)
        self.assertEqual(self.open(), [])


class MarketTest(unittest.TestCase):

    def test_the_forex_weekend_is_left_out(self):
        hours = lambda a, b: livesessions.marketSeconds(at(*a), at(*b)) / 3600
        self.assertEqual(hours((2026, 9, 22, 0), (2026, 9, 23, 0)), 24)
        # Friday 16:00 to Sunday 18:00 in New York (EDT): an hour each side
        self.assertEqual(hours((2026, 9, 25, 20), (2026, 9, 27, 22)), 2)
        self.assertEqual(hours((2026, 9, 26, 12), (2026, 9, 26, 13)), 0)
        # in the winter New York is UTC-5: it shuts at 22:00 UTC
        self.assertEqual(hours((2026, 12, 4, 21), (2026, 12, 4, 23)), 1)

    def test_a_granularity_with_no_length_never_stalls(self):
        self.assertEqual(livesessions.staleBars({'fields': {'granularity': 'M'}, 'started': 1}, time.time(), 3), 0)


if __name__ == '__main__':
    unittest.main()
