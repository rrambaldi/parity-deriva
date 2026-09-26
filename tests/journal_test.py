"""
Tests for web/journal.py and the actions that write it: every one leaves its
entry without anybody writing a line, a code change only when the code
changed, a note on an entry, the search, the backfill run twice, an entry
read after its set is gone, and the journal over MCP.
"""

import os
import time
import types
import unittest
from unittest import mock

from parity_deriva.scripts import journal_backfill
from parity_deriva.tests.servers_test import FIELDS, MS, SWEEP, saveSet, server
from parity_deriva.web import cards, journal, livesessions, mcp

PAYLOAD = {'strategy': 'AG01', 'instrument': 'EUR_USD', 'granularity': 'H1', 'from': MS, 'to': MS + 86400000,
           'balance': 1000.0, 'trades': [{'balance': 1005.0, 'pl': 5.0}],
           'report': {'closedTrades': 1, 'net': 5.0, 'profitFactor': None}}


def kinds(setup, strategy='AG01'):
    return [e['kind'] for e in journal.read(setup, strategy)]


class ActionsTest(unittest.TestCase):

    def setUp(self):
        self.service = server(self)
        self.setup = self.service.setup

    def test_every_action_leaves_its_entry(self):
        saveSet(self.service)
        self.service.sweepJournal(self.service.savedSweep(SWEEP))
        self.service.saveRun(dict(FIELDS, slScale='3'), PAYLOAD)
        favourite = self.service.addFavourite({'kind': 'sweep', 'id': SWEEP, 'n': 1})
        self.service.noteFavourite(favourite['id'], 'the steady one')
        self.service.dropFavourite(favourite['id'])
        mix = self.service.saveMix({'name': 'one', 'items': [{'sweep': SWEEP, 'n': 1}, {'sweep': SWEEP, 'n': 2}]})
        self.service.saveMix(dict(mix, name='one again'))
        self.service.dropMix(mix['id'])
        self.service.deleteSweep(SWEEP)
        self.assertEqual(kinds(self.setup), ['idea', 'sweep', 'code', 'run', 'favourite', 'favourite-note',
                                             'unfavourite', 'mix', 'mix-deleted', 'sweep-deleted'])
        entries = journal.read(self.setup, 'AG01')
        sweep = entries[1]
        self.assertEqual((sweep['level'], sweep['instrument'], sweep['granularity'], sweep['by']),
                         ('experiment', 'EUR_USD', 'H1', 'parity-deriva'))
        self.assertEqual((sweep['data']['name'], sweep['data']['runs'], sweep['link']),
                         ('a set', 2, {'kind': 'sweep', 'id': SWEEP}))
        # the set is gone and its entry still says what it was
        self.assertFalse(os.path.exists(self.service.sweepPath(SWEEP, '.meta.json')))
        self.assertEqual(journal.read(self.setup, 'AG01')[1]['data']['bestScore'], 1.0)
        self.assertEqual(entries[-1]['data'], {'id': SWEEP, 'name': 'a set', 'runs': 2})

    def test_a_session_started_and_stopped(self):
        live = livesessions.LiveSessions(os.path.join(self.setup.DATA_DIR, 'live'), self.setup)
        live.targets = lambda fresh=False: [{'provider': 'ig', 'accounts': [{'id': 'DEMO1', 'demo': True}]}]
        live.check = lambda fields, provider: None
        live.spawn = lambda fields, provider, account: {'id': 'S1', 'fields': fields, 'provider': provider,
                                                        'account': account['id'], 'demo': True}
        with mock.patch.object(livesessions.settings, 'ACCOUNTS', 'demo'):
            live.start(dict(FIELDS, capital='1000'), [{'provider': 'ig', 'account': 'DEMO1'}])
        started = journal.read(self.setup, 'AG01')[-1]
        self.assertEqual((started['kind'], started['data']['account'], started['data']['capital']),
                         ('session-start', 'DEMO1', '1000'))

    def test_the_code_only_when_it_changed(self):
        self.assertIsNotNone(cards.codeSeen(self.setup, FIELDS))
        self.assertIsNone(cards.codeSeen(self.setup, FIELDS))
        with mock.patch.object(cards, 'code', lambda strategy, setup=None: ('other', {'AG01': 'x = 2'})):
            changed = cards.codeSeen(self.setup, FIELDS)
        self.assertEqual(changed['data'], {'hash': 'other', 'source': {'AG01': 'x = 2'}})
        self.assertEqual(kinds(self.setup), ['idea', 'code', 'code'])
        # a comment, a blank line or a docstring is no change
        self.assertEqual(cards.shape('x = 1\n'), cards.shape('# why\n\nx = 1  # one\n'))
        self.assertEqual(cards.shape('def f():\n    return 1\n'), cards.shape('def f():\n    "doc"\n    return 1\n'))
        self.assertNotEqual(cards.shape('x = 1\n'), cards.shape('x = 2\n'))
        # a strategy the ledger does not run has no code to see
        self.assertIsNone(cards.codeSeen(self.setup, {'strategy': 'NOT-A-STRATEGY'}))


class NotesTest(unittest.TestCase):

    def setUp(self):
        self.setup = types.SimpleNamespace(DATA_DIR=server(self).setup.DATA_DIR)
        self.run = journal.add(self.setup, 'AG01', 'run', 'experiment', {'trades': 40, 'net': 12.5}, FIELDS)

    def test_a_note_on_an_entry_and_a_free_one(self):
        on = journal.note(self.setup, 'AG01', 'slScale 1.5 is steadier', about=self.run['id'], mark='up', by='rr')
        self.assertEqual((on['data'], on['instrument'], on['by']),
                         ({'about': self.run['id'], 'mark': 'up', 'text': 'slScale 1.5 is steadier'}, 'EUR_USD', 'rr'))
        free = journal.note(self.setup, 'AG01', 'try it on H4 next')
        self.assertIsNone(free['data']['about'])
        for bad in ({'about': 'nope'}, {'mark': 'love'}):
            with self.assertRaises(ValueError):
                journal.note(self.setup, 'AG01', 'x', **bad)
        with self.assertRaises(ValueError):
            journal.note(self.setup, 'NO-JOURNAL', 'x')
        with self.assertRaises(ValueError):
            journal.note(self.setup, 'AG01', '  ')

    def test_the_search_finds_entries_and_notes(self):
        journal.note(self.setup, 'AG01', 'the London session is where it earns', about=self.run['id'])
        journal.add(self.setup, 'MY-TRY', 'sweep', 'experiment', {'name': 'london only'}, at=journal.now() + 1000)
        found = journal.search(self.setup, 'LONDON')
        self.assertEqual(sorted((e['strategy'], e['kind']) for e in found), [('AG01', 'note'), ('MY-TRY', 'sweep')])
        self.assertEqual(journal.search(self.setup, ''), [])
        self.assertEqual([j['strategy'] for j in journal.journals(self.setup)], ['MY-TRY', 'AG01'])

    def test_the_versions_of_an_uploaded_strategy_share_one_journal(self):
        journal.add(self.setup, 'WK10-BOLLINGER 1', 'run', 'experiment', {})
        journal.add(self.setup, 'WK10-BOLLINGER 2', 'run', 'experiment', {})
        journal.add(self.setup, '../escape', 'run', 'experiment', {})
        self.assertEqual(sorted(os.listdir(journal.folder(self.setup))),
                         ['..%2Fescape.jsonl', 'AG01.jsonl', 'WK10-BOLLINGER.jsonl'])
        entries = journal.read(self.setup, 'WK10-BOLLINGER 2')
        self.assertEqual([(e['kind'], e['strategy']) for e in entries],
                         [('idea', 'WK10-BOLLINGER 1'), ('run', 'WK10-BOLLINGER 1'), ('run', 'WK10-BOLLINGER 2')])


class BackfillTest(unittest.TestCase):

    def test_run_twice_it_adds_nothing_the_second_time(self):
        service = server(self)
        saveSet(service)
        service.addFavourite({'kind': 'sweep', 'id': SWEEP, 'n': 1})
        service.saveMix({'name': 'one', 'items': [{'sweep': SWEEP, 'n': 1}]})
        # the journal written so far is not the backfill's: start from none
        for name in os.listdir(journal.folder(service.setup)):
            os.remove(os.path.join(journal.folder(service.setup), name))
        said = []
        journal_backfill.main([], report=said.append, setup=service.setup)
        journal_backfill.main([], report=said.append, setup=service.setup)
        self.assertEqual(said, ['wrote 3 entries in 1 journals', 'wrote 0 entries in 1 journals'])
        entries = journal.read(service.setup, 'AG01')
        self.assertEqual([e['kind'] for e in entries], ['idea', 'sweep', 'favourite', 'mix'])
        self.assertTrue(entries[0]['at'] < entries[1]['at'])


class ToolsTest(unittest.TestCase):

    def setUp(self):
        self.service = server(self)
        self.setup = self.service.setup
        cards.codeSeen(self.setup, FIELDS)

    def call(self, name, **args):
        return mcp.call(self.service, name, args, 'https://here', 'claude.ai')

    def test_an_assistant_reads_the_journals_and_writes_a_note(self):
        self.assertEqual([j['strategy'] for j in self.call('list_journals')['journals']], ['AG01'])
        held = self.call('get_journal', strategy='AG01')
        code = held['entries'][-1]
        self.assertEqual((code['kind'], code['data']['parts']), ('code', ['AG01']))
        self.assertNotIn('source', code['data'])
        noted = self.call('add_note', strategy='AG01', text='worth a sweep of slScale', mark='up')
        self.assertEqual(noted['by'], 'assistant claude.ai')
        self.assertEqual(len(self.call('search_journals', text='slScale')['entries']), 1)
        with self.assertRaises(mcp.ToolError):
            self.call('get_journal', strategy='NOPE')
        with mock.patch.object(livesessions.settings, 'ACCOUNTS', 'real'):
            with self.assertRaises(mcp.ToolError):
                self.call('add_note', strategy='AG01', text='from afar')


if __name__ == '__main__':
    unittest.main()
