"""
The logs page's service side (web/logs.py): which files it lists, and that
it reads those and nothing else.
"""

import os
import tempfile
import types
import unittest
from unittest import mock

from parity_deriva.web import livesessions, logs

CRONTAB = """\
MAILTO=someone
# a comment
0 6 * * 0    cd /srv/calendar && flock calendar.lock python3 ff.py --week >> logs/calendar.log 2>&1
*/2 * * * *  cd /srv/calendar && flock -n calendar.lock python3 ff.py --tick >> logs/calendar.log 2>&1
17 7 * * 6   X=1 /srv/spread_cron.sh --force >> %(home)s/spread.log 2>&1
@reboot      /srv/no_log.sh
"""


class Handler(object):
    def __init__(self, service):
        self.service = service
        self.sent = None

    def sendJSON(self, payload, status=200):
        self.sent = (status, payload)

    def sendError(self, message, status=400):
        self.sent = (status, {'error': message})

    def sendFile(self, name):
        self.sent = (200, name)


class LogsTest(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.service = types.SimpleNamespace(
            setup=types.SimpleNamespace(DATA_DIR=self.home),
            live=livesessions.LiveSessions(os.path.join(self.home, 'live')))
        run = types.SimpleNamespace(stdout=CRONTAB % {'home': self.home})
        patcher = mock.patch.object(logs.subprocess, 'run', return_value=run)
        patcher.start()
        self.addCleanup(patcher.stop)
        logs._read['at'] = 0.0

    def get(self, route, **query):
        handler = Handler(self.service)
        self.assertTrue(logs.route(handler, "GET", route, dict((k, [v]) for k, v in query.items())))
        return handler.sent

    def test_the_files_the_crontab_writes_to_once_each(self):
        listed = logs.logs(self.service)
        cron = [l for l in listed if l['group'] == 'cron']
        self.assertEqual([l['path'] for l in cron],
                         ['/srv/calendar/logs/calendar.log', os.path.join(self.home, 'spread.log'), None])
        # the two jobs writing calendar.log are both said
        self.assertIn('--week', cron[0]['about'])
        self.assertIn('--tick', cron[0]['about'])
        self.assertIn('no log file', cron[2]['about'])
        self.assertIn(logs.servicePath(self.service.setup), [l['path'] for l in listed])

    def test_each_live_session_s_console(self):
        session = '20260926-120000-abcdef'
        self.service.live.writeMeta(session, {'id': session, 'provider': 'ig', 'account': 'Z1',
                                              'fields': {'strategy': 'AG01'}})
        path = self.service.live.path(session, 'console.log')
        os.makedirs(os.path.dirname(path))
        with open(path, 'w') as handle:
            handle.write('started\n')
        live = [l for l in logs.logs(self.service) if l['group'] == 'live']
        self.assertEqual([(l['name'], l['path']) for l in live], [(session, path)])
        self.assertEqual(live[0]['about'], 'ig Z1 AG01')
        self.assertEqual(self.get('/api/logs/tail', path=path)[1]['lines'], ['started'])

    def test_the_redirects_that_are_files(self):
        self.assertEqual(logs.targets('cd /a && run > out.log 2>&1'), ['/a/out.log'])
        self.assertEqual(logs.targets('run 2>&1 >>/b/x.log'), ['/b/x.log'])
        self.assertEqual(logs.targets('run'), [])

    def test_a_listed_file_is_read_from_its_end(self):
        path = os.path.join(self.home, 'spread.log')
        with open(path, 'w') as handle:
            handle.write(''.join('line %d\n' % i for i in range(10)))
        status, got = self.get('/api/logs/tail', path=path, lines='3')
        self.assertEqual(status, 200)
        self.assertEqual(got['lines'], ['line 7', 'line 8', 'line 9'])

    def test_a_long_file_loses_the_cut_line_not_the_last(self):
        path = os.path.join(self.home, 'spread.log')
        with open(path, 'w') as handle:
            handle.write('x' * 50 + '\nlast\n')
        with mock.patch.object(logs, 'WINDOW', 20):
            self.assertEqual(logs.tail(path, 10), ['last'])

    def test_a_path_not_on_the_list_is_refused(self):
        secret = os.path.join(self.home, 'secret.txt')
        with open(secret, 'w') as handle:
            handle.write('no\n')
        for path in (secret, '/etc/passwd', os.path.join(self.home, 'spread.log/../secret.txt'), ''):
            status, got = self.get('/api/logs/tail', path=path)
            self.assertEqual(status, 404)
            self.assertNotIn('lines', got)

    def test_other_routes_are_not_this_modules(self):
        self.assertFalse(logs.route(Handler(self.service), 'GET', '/api/live', {}))
        self.assertFalse(logs.route(Handler(self.service), 'POST', '/api/logs', {}))
        self.assertEqual(self.get('/logs'), (200, 'logs.html'))


class ServiceLoggerTest(unittest.TestCase):

    def test_reading_logging_conf_leaves_a_module_logger_on(self):
        """
        data/sources.py makes the service's logger at import, before
        scripts/web.py reads logging.conf. fileConfig's default turned it off,
        and the service wrote nothing, not even to the journal. In a process
        of its own: fileConfig rewires the logging of the whole process.
        """
        import subprocess
        import sys
        home = os.path.dirname(os.path.dirname(os.path.abspath(logs.__file__)))
        code = ("import logging; from parity_deriva.data import sources; "
                "from parity_deriva.lib.utils import getLogger; "
                "getLogger(%r); print(logging.getLogger('parity_deriva.web').disabled)"
                % os.path.join(home, 'etc', 'logging.conf'))
        out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                             env=dict(os.environ, PARITY_DERIVA_HOME=home), timeout=120)
        self.assertEqual(out.stdout.strip().splitlines()[-1:], ['False'], out.stderr[-500:])


if __name__ == '__main__':
    unittest.main()
