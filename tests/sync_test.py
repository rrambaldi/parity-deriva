"""
Tests for scripts/sync.py and what it pushes into (web/mcp.py push_sweep,
push_mix; the service's verify): the PC's mixes on the cloud, and checked
again there on the cloud's code and candles.
"""

import datetime
import json
import os
import shutil
import tempfile
import time
import types
import unittest
from decimal import Decimal
from unittest import mock

from parity_deriva.data.sources import SourceError
from parity_deriva.scripts import sync
from parity_deriva.tests.helpers import T0
from parity_deriva.tests.web_test import StoreCase
from parity_deriva.web import mcp
from parity_deriva.web.service import Service


class SyncTest(unittest.TestCase):

    def setUp(self):
        self.pc, self.cloud = self.server(), self.server()
        self.calls = []

        def rpc(upstream, name, args, timeout=180):
            # as the cloud's endpoint answers the PC's token: JSON both ways
            self.calls.append((name, args.get('sweep'), args.get('n'), args.get('part')))
            try:
                found = mcp.call(self.cloud, name, json.loads(json.dumps(args)), 'https://cloud', 'pc: my PC')
            except mcp.ToolError as exc:
                raise SourceError(str(exc))
            return json.loads(json.dumps(found))
        for patch in (mock.patch.object(sync, 'rpc', rpc),
                      mock.patch.object(sync, 'settings', self.pc.setup),
                      # a set in several chunks, as a long one goes
                      mock.patch.object(sync, 'PUSH_CHUNK', 500),
                      mock.patch.object(mcp, 'PUSH_CHUNK', 500)):
            patch.start()
            self.addCleanup(patch.stop)

    def server(self):
        folder = tempfile.mkdtemp(prefix='parity-deriva-sync-')
        self.addCleanup(shutil.rmtree, folder, True)
        StoreCase.store(os.path.join(folder, 'EUR_USD.hd5'))
        return Service(setup=types.SimpleNamespace(DATA_DIR=folder, EQUITY=Decimal('100000.00')),
                       max_candles=1000)

    def wait(self, status):
        for _ in range(600):
            state = status()
            if not state.get('running'):
                return state
            time.sleep(0.05)
        self.fail('still running')

    def push(self, *extra):
        lines = []
        self.assertEqual(sync.main(['push', '--to', 'https://cloud/mcp', '--token', 'the pc token']
                                   + list(extra), report=lines.append), 0, lines)
        return lines

    def test_a_mix_goes_to_the_cloud_with_its_sets_and_runs_and_is_checked_there(self):
        fields = {'instrument': 'EUR_USD', 'granularity': 'H1', 'strategy': 'AG01',
                  'from': T0.strftime('%Y-%m-%d'),
                  'to': (T0 + datetime.timedelta(hours=199)).strftime('%Y-%m-%d')}
        self.pc.startSweep(fields, {'risk': '1, 2'})
        sweep = self.wait(self.pc.sweepStatus)['id']
        # saved just after it says it has stopped
        self.wait(lambda: {'running': not any(s['id'] == sweep for s in self.pc.sweeps())})
        mix = self.pc.saveMix({'name': 'mine', 'items': [{'sweep': sweep, 'n': 1}, {'sweep': sweep, 'n': 2}]})

        lines = self.push()
        self.assertIn('mix mine: pushed with 2 runs', lines[-1])
        self.assertGreater(len([c for c in self.calls if c[:3] == ('push_sweep', sweep, None)]), 1)
        held = next(s for s in self.cloud.sweeps() if s['id'] == sweep)
        self.assertEqual(held['origin'], 'pc: my PC')
        there = next(m for m in self.cloud.mixes() if m['id'] == mix['id'])
        self.assertEqual((there['name'], there['origin']['client']), ('mine', 'pc: my PC'))

        # the same code and the same candles make the same trades
        self.cloud.startVerify(mix['id'])
        results = self.wait(self.cloud.verifyStatus)['results']
        self.assertEqual([r['ok'] for r in results], [True, True], results)
        self.assertGreater(results[0]['here']['trades'], 0)
        self.assertTrue(all(r['verified']['ok'] for r in self.cloud.mix(mix['id'])['runs']))

        # nothing changed: only the mix goes again
        del self.calls[:]
        self.push()
        self.assertEqual([c[0] for c in self.calls], ['push_mix'])

        # a run that did not trade here as it did there says where it parts
        payload = self.cloud.sweepPayload(sweep, 2)
        payload['trades'] = payload['trades'][1:]
        self.cloud.saveSweepRun(sweep, 2, payload)
        self.cloud.startVerify(mix['id'])
        results = self.wait(self.cloud.verifyStatus)['results']
        self.assertEqual([r['ok'] for r in results], [True, False])
        self.assertEqual(results[1]['first']['trade'], 1)

    def test_a_run_goes_without_a_mix_and_is_checked_on_its_own(self):
        fields = {'instrument': 'EUR_USD', 'granularity': 'H1', 'strategy': 'AG01',
                  'from': T0.strftime('%Y-%m-%d'),
                  'to': (T0 + datetime.timedelta(hours=199)).strftime('%Y-%m-%d')}
        self.pc.startSweep(fields, {'risk': '1, 2'})
        sweep = self.wait(self.pc.sweepStatus)['id']
        self.wait(lambda: {'running': not any(s['id'] == sweep for s in self.pc.sweeps())})
        lines = self.push('--run', '%s/2' % sweep)
        self.assertIn('1 run pushed', lines[-1])
        self.assertEqual(self.cloud.mixes(), [])
        self.assertIsNone(self.cloud.sweepPayload(sweep, 1))
        told = self.cloud.verify(sweep, 2)
        self.assertTrue(told['ok'], told)
        self.assertGreater(told['here']['trades'], 0)
        # the starred runs, the ones already there not again
        self.pc.addFavourite({'kind': 'sweep', 'id': sweep, 'n': 1})
        self.pc.addFavourite({'kind': 'sweep', 'id': sweep, 'n': 2})
        del self.calls[:]
        self.push('--favourites')
        self.assertEqual(sorted(set(c[2] for c in self.calls if c[0] == 'push_sweep' and c[2] is not None)), [1])
        self.assertIsNotNone(self.cloud.sweepPayload(sweep, 1))
        lines = []
        self.assertEqual(sync.main(['push', '--to', 'https://cloud/mcp', '--token', 't', '--run', sweep],
                                   report=lines.append), 2)
        self.assertIn('a run is SET/N', lines[-1])

    def test_an_uploaded_strategy_is_the_version_the_cloud_gave_it(self):
        from parity_deriva.strategy import uploaded
        where = os.path.join(uploaded.root(self.pc.setup.DATA_DIR, 'strategies'), 'drafts')
        os.makedirs(where)
        with open(os.path.join(where, uploaded.fileName('MY-EMA 3')), 'w') as handle:
            handle.write('class MyEma(object):\n    pass\n')
        told = []
        answers = [{'name': 'MY-EMA 1'}, SourceError('not saved: the same code as MY-EMA 1, no new version made'),
                   SourceError('not saved: a problem')]
        def rpc(upstream, name, args, timeout=180):
            told.append((name, args['name']))
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        with mock.patch.object(sync, 'rpc', rpc):
            for _ in range(2):
                self.assertEqual(sync.submitted({}, 'MY-EMA 3', self.pc.setup.DATA_DIR, lambda line: None),
                                 'MY-EMA 1')
            with self.assertRaises(SourceError):
                sync.submitted({}, 'MY-EMA 3', self.pc.setup.DATA_DIR, lambda line: None)
        self.assertEqual(told, [('submit_strategy', 'MY-EMA')] * 3)
        # a built-in one is the same everywhere, through git
        self.assertEqual(sync.submitted({}, 'AG01', self.pc.setup.DATA_DIR, lambda line: None), 'AG01')

    def test_each_token_does_its_own_work_only(self):
        bar = [[1757592000000, 1.17, 1.171, 1.169, 1.17, 5]]
        candles = {'instrument': 'EUR_USD', 'granularity': 'M5', 'ask': bar, 'bid': bar}
        # the PC takes the market data from the cloud, never the other way
        with self.assertRaises(mcp.ToolError) as caught:
            mcp.call(self.cloud, 'push_candles', candles, 'https://cloud', 'pc: my PC')
        self.assertIn('a pc token may not call push_candles', str(caught.exception))
        for client in ('mirror: backup', 'market: scraper', 'claude.ai', 'oauth token: x'):
            with self.assertRaises(mcp.ToolError):
                mcp.call(self.cloud, 'push_sweep', {'sweep': '20260926-120000-abcdef', 'data': 'eA=='},
                         'https://cloud', client)
        self.assertEqual(mcp.call(self.cloud, 'push_candles', candles, 'https://cloud',
                                  'market: scraper')['added'], 1)
        with self.assertRaises(mcp.ToolError):
            mcp.call(self.cloud, 'list_strategies', {}, 'https://cloud', 'market: scraper')
        # a mix whose runs are not there is refused, and names what to push first
        with self.assertRaises(mcp.ToolError) as caught:
            mcp.call(self.cloud, 'push_mix', {'mix': {'items': [{'sweep': '20260926-120000-abcdef', 'n': 1}]}},
                     'https://cloud', 'pc: my PC')
        self.assertIn('push_sweep it first', str(caught.exception))
        # and the authority hands out one token a program, by role
        key = self.cloud.oauth.newKey('my PC', 'pc')
        self.assertEqual(self.cloud.oauth.keyOf(key)['role'], 'pc')
        self.assertTrue(self.cloud.oauth.allowed(key))
        with self.assertRaises(ValueError):
            self.cloud.oauth.newKey('x', 'admin')
        self.cloud.oauth.dropKey('my PC')
        self.assertFalse(self.cloud.oauth.allowed(key))


if __name__ == '__main__':
    unittest.main()
