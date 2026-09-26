"""
A server's roles and the servers it deals with (web/servers.py): what each
role serves over MCP and on the pages, an archive reading its trade servers
and pushing forms to them, a Test pulling the archive's code
(scripts/sync.py pull), the tools that read the simulations saved, and a
set's runs sent to a bucket and back (web/service.py freeze).
"""

import json
import os
import shutil
import tempfile
import time
import types
import unittest
from decimal import Decimal
from unittest import mock

from parity_deriva.data import sources
from parity_deriva.data.sources import SourceError
from parity_deriva.lib import s3
from parity_deriva.scripts import sync
from parity_deriva.web import mcp, servers
from parity_deriva.web.service import Service, ServiceError

SWEEP = '20260926-120000-abcdef'
FIELDS = {'strategy': 'AG01', 'instrument': 'EUR_USD', 'granularity': 'H1',
          'from': '2020-01-01', 'to': '2020-12-31'}
MS = 1590000000000
TRADE = {'n': 1, 'direction': 'long', 'signalTime': MS, 'entryTime': MS, 'entryPrice': 1.1,
         'stopLoss': 1.09, 'takeProfit': 1.12, 'exitTime': MS + 3600000, 'exitPrice': 1.12,
         'outcome': 'tp', 'pl': 5.0, 'balance': 1005.0}


def server(case):
    folder = tempfile.mkdtemp(prefix='parity-deriva-servers-')
    case.addCleanup(shutil.rmtree, folder, True)
    return Service(setup=types.SimpleNamespace(DATA_DIR=folder, EQUITY=Decimal('1000')))


def saveSet(service, sweep=SWEEP):
    """A set of two runs, each with its trades on disk, as a sweep leaves them."""
    service.saveSweep({'id': sweep, 'name': 'a set', 'fields': dict(FIELDS), 'total': 2,
                       'finished': time.time(), 'varied': ['slScale'], 'done': [
                           {'n': n, 'params': {'slScale': str(n)}, 'final': 1005.0, 'balance': 1000.0,
                            'report': {'closedTrades': 1, 'net': 5.0}, 'kpi': {'roi': 0.5, 'score': 1.0},
                            'margin': {'ok': True}, 'curve': [[MS, 1005.0]]} for n in (1, 2)]})
    for n in (1, 2):
        service.saveSweepRun(sweep, n, {'trades': [TRADE], 'instrument': 'EUR_USD'})


class RolesTest(unittest.TestCase):

    def setUp(self):
        self.service = server(self)
        self.setup = self.service.setup

    def call(self, name, args, client='claude.ai'):
        return mcp.call(self.service, name, args, 'https://here', client)

    def test_a_server_is_all_three_until_it_says_otherwise(self):
        self.assertEqual(servers.roles(self.setup), ['archive', 'test', 'trade'])
        # a setup wizard's keys stay as they are
        servers.keep('auth', {'mode': 'none'}, self.setup)
        self.assertEqual(servers.saveRoles(['trade', 'archive'], self.setup), ['archive', 'trade'])
        self.assertEqual(servers.read(self.setup)['auth'], {'mode': 'none'})
        for wrong in ([], ['demo'], 'trade'):
            with self.assertRaises(ValueError):
                servers.saveRoles(wrong, self.setup)
        self.assertIn('no test role', servers.missing(self.setup, 'test'))
        self.assertIsNone(servers.missing(self.setup, 'test', 'trade'))

    def test_a_trade_server_serves_the_trades_and_takes_code_only_by_push(self):
        servers.saveRoles(['trade'], self.setup)
        listed = mcp.handle(self.service, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
                            'https://here', 'claude.ai')['result']['tools']
        self.assertEqual(sorted(t['name'] for t in listed), sorted([
            'list_strategies', 'submit_strategy', 'submit_indicator', 'push_calendar', 'push_candles',
            'market_status', 'pull_candles', 'pull_calendar', 'push_sweep', 'push_record', 'live_status']))
        with self.assertRaises(mcp.ToolError) as caught:
            self.call('run_backtest', {'strategy': 'AG01'})
        self.assertIn('no run_backtest here: this server has no test role', str(caught.exception))
        for client in ('claude.ai', 'token: cli'):
            with self.assertRaises(mcp.ToolError) as caught:
                self.call('submit_strategy', {'name': 'MY-EMA', 'source': 'x'}, client)
            self.assertIn("only from another server's push", str(caught.exception))
        # the archive's token gets past the role, to the strategy's own checks
        with self.assertRaises(mcp.ToolError) as caught:
            self.call('submit_strategy', {'name': 'MY-EMA', 'source': 'import os\n'}, 'promote: archive')
        self.assertIn('not saved', str(caught.exception))
        told = self.call('live_status', {}, 'promote: archive')
        self.assertEqual((told['server']['accounts'], told['server']['roles'], told['sessions']),
                         ('demo', ['trade'], []))
        # and the pages: no simulation, no strategy file
        with self.assertRaises(ServiceError) as caught:
            self.service.startSweep(dict(FIELDS), {})
        self.assertIn('no test role', str(caught.exception))

    def test_an_archive_without_test_keeps_and_shows_but_does_not_simulate(self):
        servers.saveRoles(['archive'], self.setup)
        saveSet(self.service)
        self.assertEqual(self.call('list_runs', {})['sets'][0]['id'], SWEEP)
        with self.assertRaises(mcp.ToolError):
            self.call('live_status', {}, 'promote: x')
        with self.assertRaises(mcp.ToolError):
            self.call('submit_indicator', {'name': 'KELT', 'source': 'x'})
        with self.assertRaises(ServiceError):
            self.service.need('trade')


class SavedRunsToolsTest(unittest.TestCase):

    def test_an_assistant_reads_the_sets_the_runs_and_one_runs_trades(self):
        service = server(self)
        saveSet(service)
        service.saveRun(dict(FIELDS, slScale='3'), {'trades': [TRADE], 'report': {'net': 5.0},
                                                    'balance': 1000.0, 'from': MS, 'to': MS + 365 * 86400000})
        listed = mcp.call(service, 'list_runs', {}, 'https://here', 'claude.ai')
        self.assertEqual(([s['id'] for s in listed['sets']], len(listed['runs']), listed['mixes']),
                         ([SWEEP], 1, []))
        run = listed['runs'][0]['id']
        one = mcp.call(service, 'get_run', {'run': run}, 'https://here', 'claude.ai')
        self.assertEqual((one['trades'][0]['pl'], one['tradesShown']), (5.0, '1 of 1'))
        self.assertIn('/run?', one['link'])
        whole = mcp.call(service, 'get_run', {'sweep': SWEEP}, 'https://here', 'claude.ai')
        self.assertEqual([(r['n'], r['params'], r['net'], r['score']) for r in whole['runs']],
                         [(1, {'slScale': '1'}, 5.0, 1.0), (2, {'slScale': '2'}, 5.0, 1.0)])
        self.assertTrue(whole['link'].endswith('/?set=' + SWEEP))
        second = mcp.call(service, 'get_run', {'sweep': SWEEP, 'n': 2}, 'https://here', 'claude.ai')
        self.assertEqual((second['fields']['slScale'], second['trades'][0]['outcome'], second['summary']['from']),
                         ('2', 'tp', '2020-01-01 00:00'))
        self.assertIn('sweep=%s' % SWEEP, second['link'])
        with self.assertRaises(mcp.ToolError):
            mcp.call(service, 'get_run', {}, 'https://here', 'claude.ai')


class TradeServersTest(unittest.TestCase):

    def setUp(self):
        self.archive = server(self)
        self.sent = []
        self.answers = {}

        def rpc(upstream, name, args, timeout=180):
            self.sent.append((upstream['name'] if 'name' in upstream else upstream['url'], name, args))
            if name == 'live_status':
                return self.answers[upstream['name']]
            if name == 'push_record':
                return {'ok': True, 'days': 30, 'trades': 40}
            return {'saved': True}
        for patch in (mock.patch.object(sources, 'rpc', rpc), mock.patch.object(sync, 'rpc', rpc)):
            patch.start()
            self.addCleanup(patch.stop)

    def session(self, id, demo=True, days=25, trades=35, fields=None):
        now = int(time.time() * 1000)
        return {'id': id, 'fields': dict(fields or FIELDS), 'provider': 'ig', 'account': 'A', 'demo': demo,
                'started': now - days * 86400000, 'stopped': None, 'running': True,
                'closed': [{'time': now, 'pl': 1.0}] * trades, 'parity': {'divergences': 0, 'alarms': []}}

    def test_the_archive_reads_its_trade_servers_and_the_demo_record_goes_to_the_real_one(self):
        for name in ('demo', 'real'):
            self.archive.saveTradeServer({'name': name, 'url': 'https://%s/mcp' % name, 'token': 't-' + name})
        with self.assertRaises(ServiceError):
            self.archive.saveTradeServer({'name': 'x', 'url': 'ftp://x', 'token': 't'})
        # a token is kept when none is typed
        self.archive.saveTradeServer({'name': 'demo', 'url': 'https://demo/mcp'})
        self.assertEqual(servers.one(self.archive.setup, 'demo')['token'], 't-demo')
        self.answers = {'demo': {'server': {'accounts': 'demo'}, 'sessions': [
                            self.session('S1'), self.session('S2', fields=dict(FIELDS, slScale='9'))]},
                        'real': {'server': {'accounts': 'real'}, 'sessions': [self.session('R1', demo=False)]}}
        polled = self.archive.pollTradeServers()['servers']
        self.assertEqual([(s['name'], s['status']['ok'], len(s['status']['sessions'])) for s in polled],
                         [('demo', True, 2), ('real', True, 1)])
        # the record of a form: the demo servers' sessions of it, not the real one's
        record = servers.record(self.archive, FIELDS)
        self.assertEqual(([s['id'] for s in record['sessions']], record['trades']), (['demo/S1'], 35))
        # pushed to the real server: its record there, judged by it
        self.sent.clear()
        told = self.archive.pushForm({'server': 'real', 'fields': FIELDS})
        self.assertEqual((told['accounts'], told['verdict']['ok']), ('real', True))
        self.assertEqual([name for _, name, _ in self.sent], ['push_record'])
        self.assertEqual([s['id'] for s in self.sent[0][2]['record']['sessions']], ['demo/S1'])
        # a server that stops answering keeps what it said, with the error
        del self.answers['demo']

        def down(upstream, name, args, timeout=180):
            raise SourceError('%s: connection refused' % upstream['url'])
        with mock.patch.object(sources, 'rpc', down):
            polled = self.archive.pollTradeServers()['servers']
        self.assertEqual((polled[0]['status']['ok'], len(polled[0]['status']['sessions'])), (False, 2))
        self.assertIn('connection refused', polled[0]['status']['error'])
        self.archive.saveTradeServer({'name': 'demo', 'drop': True})
        self.assertEqual([s['name'] for s in self.archive.tradeServers()['servers']], ['real'])

    def test_a_starred_run_goes_to_a_demo_server_with_its_set(self):
        saveSet(self.archive)
        self.archive.addFavourite({'kind': 'sweep', 'id': SWEEP, 'n': 1})
        self.archive.saveTradeServer({'name': 'demo', 'url': 'https://demo/mcp', 'token': 't'})
        self.answers = {'demo': {'server': {'accounts': 'demo'}, 'sessions': []}}
        told = self.archive.pushForm({'server': 'demo', 'fields': dict(FIELDS, slScale='1')})
        self.assertIsNone(told['verdict'])
        self.assertEqual([(name, args.get('sweep'), args.get('n')) for _, name, args in self.sent],
                         [('live_status', None, None), ('push_sweep', SWEEP, None), ('push_sweep', SWEEP, 1)])
        # a form nobody starred: the strategy goes, and the answer says the form did not
        told = self.archive.pushForm({'server': 'demo', 'fields': dict(FIELDS, slScale='7')})
        self.assertIn('the form did not', told['lines'][-1])


class SyncCodeTest(unittest.TestCase):

    def setUp(self):
        self.pc, self.cloud = server(self), server(self)

        def rpc(upstream, name, args, timeout=180):
            try:
                found = mcp.call(self.cloud, name, json.loads(json.dumps(args)), 'https://cloud', 'pc: my PC')
            except mcp.ToolError as exc:
                raise SourceError(str(exc))
            return json.loads(json.dumps(found))
        for patch in (mock.patch.object(sync, 'rpc', rpc), mock.patch.object(sync, 'settings', self.pc.setup)):
            patch.start()
            self.addCleanup(patch.stop)

    def write(self, service, kind, code, source, draft=False):
        from parity_deriva.strategy import uploaded
        where = os.path.join(uploaded.root(service.setup.DATA_DIR, kind), *(('drafts',) if draft else ()))
        os.makedirs(where, exist_ok=True)
        with open(os.path.join(where, uploaded.fileName(code)), 'w') as handle:
            handle.write(source)

    def test_a_test_server_pulls_what_the_archive_enabled_as_drafts(self):
        from parity_deriva.strategy import uploaded
        self.write(self.cloud, 'indicators', 'KELT 2', 'class Kelt(object):\n    pass\n')
        self.write(self.cloud, 'strategies', 'MY-EMA 3', 'class MyEma(object):\n    pass\n')
        # a draft on the archive nobody read stays there
        self.write(self.cloud, 'strategies', 'MY-EMA 4', 'class MyEma(object):\n    x = 1\n', draft=True)
        lines = []
        self.assertEqual(sync.main(['pull', '--from', 'https://cloud/mcp', '--token', 't'], report=lines.append), 0)
        self.assertEqual(list(uploaded.drafts(self.pc.setup.DATA_DIR)), ['MY-EMA 3'])
        self.assertEqual(list(uploaded.drafts(self.pc.setup.DATA_DIR, 'indicators')), ['KELT 2'])
        self.assertEqual(lines[-1], '2 pulled')
        # again: nothing new; one changed here is said and left as it is
        with open(uploaded.drafts(self.pc.setup.DATA_DIR)['MY-EMA 3'], 'a') as handle:
            handle.write('# mine\n')
        lines = []
        sync.main(['pull', '--from', 'https://cloud/mcp', '--token', 't'], report=lines.append)
        self.assertEqual(lines, ['MY-EMA 3: this server has another MY-EMA 3 already, left as it is', '0 pulled'])
        # an assistant does not pull the code
        with self.assertRaises(mcp.ToolError):
            mcp.call(self.cloud, 'pull_code', {}, 'https://cloud', 'claude.ai')

    def test_a_strategy_goes_after_its_indicator_under_the_archives_codes(self):
        self.write(self.pc, 'indicators', 'KELT 1', 'class Kelt(object):\n    pass\n', draft=True)
        self.write(self.pc, 'strategies', 'MY-EMA 1',
                   "from parity_deriva.strategy.uploaded import indicator\nKelt = indicator('KELT 1')\n"
                   "class MyEma(object):\n    INDICATORS = ({'indicator': 'KELT 1'},)\n", draft=True)
        calls = []

        def rpc(upstream, name, args, timeout=180):
            calls.append((name, args['name'], args['source']))
            return {'name': {'submit_indicator': 'KELT 3', 'submit_strategy': 'MY-EMA 2'}[name]}
        with mock.patch.object(sync, 'rpc', rpc):
            there = sync.submitted({'url': 'https://cloud/mcp'}, 'MY-EMA 1', self.pc.setup.DATA_DIR, lambda l: None)
        self.assertEqual(there, 'MY-EMA 2')
        self.assertEqual([(name, family) for name, family, _ in calls],
                         [('submit_indicator', 'KELT'), ('submit_strategy', 'MY-EMA')])
        self.assertIn("indicator('KELT 3')", calls[1][2])
        self.assertIn("{'indicator': 'KELT 3'}", calls[1][2])
        self.assertNotIn('KELT 1', calls[1][2])


class FakeBucket(object):

    def __init__(self):
        self.held, self.down = {}, False

    def put(self, key, data):
        self.held[key] = bytes(data)

    def get(self, key):
        if key not in self.held:
            raise s3.S3Error('404 NoSuchKey', status=404)
        return self.held[key]

    def delete(self, key):
        if self.down:
            raise s3.S3Error('503 SlowDown', status=503)
        self.held.pop(key, None)


class ColdStorageTest(unittest.TestCase):

    def setUp(self):
        self.service = server(self)
        self.bucket = FakeBucket()

    def test_without_a_bucket_nothing_goes(self):
        saveSet(self.service)
        with self.assertRaises(ServiceError) as caught:
            self.service.freeze(SWEEP)
        self.assertIn('choose one on the settings page', str(caught.exception))
        self.assertIsNotNone(self.service.sweepPayload(SWEEP, 1))

    def test_a_sets_runs_go_to_the_bucket_and_come_back_when_opened(self):
        saveSet(self.service)
        folder = self.service.sweepPath(SWEEP, '')
        with mock.patch.object(s3, 'fromSettings', lambda setup: self.bucket):
            told = self.service.freeze(SWEEP)
            self.assertEqual((told['files'], told['freed'] > 0), (2, True))
            self.assertFalse(os.path.exists(folder))
            self.assertEqual(sorted(self.bucket.held), ['sweeps/%s/1.json.gz' % SWEEP, 'sweeps/%s/2.json.gz' % SWEEP])
            # the set's table stays: the list says where its runs are
            listed = self.service.sweeps()[0]
            self.assertEqual((listed['id'], listed['cold']['bytes']), (SWEEP, told['bytes']))
            self.assertEqual(self.service.savedSweep(SWEEP)['total'], 2)
            # a run opened comes back by itself
            self.assertEqual(self.service.sweepPayload(SWEEP, 2)['trades'][0]['pl'], 5.0)
            self.assertTrue(os.path.exists(os.path.join(folder, '2.json.gz')))
            self.assertEqual(self.service.sweepTrades(SWEEP, 1) is not None, True)
            # all of them back, the bucket's copies kept until the set goes
            self.service.freeze(SWEEP)
            self.assertEqual(self.service.thaw(SWEEP)['files'], 2)
            self.assertNotIn('cold', self.service.sweeps()[0])
            self.service.freeze(SWEEP)
            # a bucket that does not answer keeps the set
            self.bucket.down = True
            with self.assertRaises(ServiceError) as caught:
                self.service.deleteSweep(SWEEP)
            self.assertIn('it is kept', str(caught.exception))
            self.assertEqual(len(self.service.sweeps()), 1)
            self.bucket.down = False
            self.service.deleteSweep(SWEEP)
            self.assertEqual((self.bucket.held, self.service.sweeps()), ({}, []))

    def test_the_cron_rule_sends_the_old_sets_nobody_uses(self):
        from parity_deriva.scripts import cold
        starred = '20260926-120000-000001'
        saveSet(self.service)
        saveSet(self.service, starred)
        self.service.addFavourite({'kind': 'sweep', 'id': starred, 'n': 1})
        lines = []
        with mock.patch.object(cold, 'settings', self.service.setup):
            self.assertEqual(cold.main(['--older-than', '0'], report=lines.append), 2)
            self.assertIn('no bucket', lines[0])
            with mock.patch.object(s3, 'fromSettings', lambda setup: self.bucket):
                cold.main(['--older-than', '1'], report=lines.append)
                self.assertEqual(self.bucket.held, {})
                cold.main(['--older-than', '0'], report=lines.append)
        self.assertEqual(sorted(set(k.split('/')[1] for k in self.bucket.held)), [SWEEP])
        self.assertTrue(os.path.isdir(self.service.sweepPath(starred, '')))


if __name__ == '__main__':
    unittest.main()
