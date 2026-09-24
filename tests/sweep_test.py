"""
Tests for the sweep: the grid the page types (web/service.gridValues and
expandGrid), the strategy arguments read off a handler's own source
(handlerFields), and the clock that closes a trade held too long
(portfolio/session.TradeTimer).
"""

import datetime
import unittest

from parity_deriva.event.event import CandleEvent, TransactionEvent
from parity_deriva.portfolio.session import MAX_LENGTH, TradeTimer
from parity_deriva.tests.helpers import candle_dict, Recorder, TempDirCase
from parity_deriva.web import service
from parity_deriva.web.service import ServiceError

DAY = datetime.datetime(2024, 1, 2)


class TestGrid(unittest.TestCase):

    def test_lists_ranges_and_none(self):
        self.assertEqual(service.gridValues('x', '10..30/10, none, 5'),
                         ['10', '20', '30', '', '5'])
        self.assertEqual(service.gridValues('x', '0.5..1.5/0.5'), ['0.5', '1.0', '1.5'])
        self.assertEqual(service.gridValues('x', '1..3'), ['1', '2', '3'])

    def test_hours_are_not_ranges(self):
        self.assertEqual(service.gridValues('session', '07:00-16:00, none'),
                         ['07:00-16:00', ''])

    def test_an_empty_box_is_one_run_with_the_field_empty(self):
        self.assertEqual(service.gridValues('x', ''), [''])

    def test_every_combination(self):
        combos = service.expandGrid({'a': '1, 2', 'b': 'none, 3', 'c': ''})
        self.assertEqual(len(combos), 4)
        self.assertIn({'a': '2', 'b': '', 'c': ''}, combos)

    def test_too_many_is_refused(self):
        with self.assertRaises(ServiceError):
            service.expandGrid({'a': '1..100', 'b': '1..100'})

    def test_a_bad_value_is_refused_before_anything_runs(self):
        with self.assertRaises(ServiceError):
            service.backtestArgs(lambda name: {'instrument': 'X', 'granularity': 'H4',
                                               'maxBars': '0'}.get(name))


class TestOrderRules(unittest.TestCase):
    """inverse, trailing and trailProfit, as backtestArgs reads the form."""

    def args(self, **form):
        form = dict({'instrument': 'X', 'granularity': 'H4', 'strategy': 'AG01'}, **form)
        return service.backtestArgs(lambda name: form.get(name))

    def test_empty_is_the_strategy_as_it_is(self):
        got = self.args()
        self.assertEqual((got['inverse'], got['trailing'], got['trailProfit']),
                         (False, None, False))

    def test_set(self):
        got = self.args(inverse='1', trailing='0', trailProfit='1')
        self.assertEqual((got['inverse'], got['trailing'], got['trailProfit']),
                         (True, 0, True))
        self.assertEqual(self.args(trailing='1')['trailing'], 1)

    def test_trailing_is_empty_0_or_1(self):
        with self.assertRaises(ServiceError):
            self.args(trailing='2')

    def test_an_old_inverse_name_is_the_strategy_turned_round(self):
        got = self.args(strategy='AG01-INVERSA')
        self.assertEqual((got['strategy'], got['inverse']), ('AG01', True))
        # a name that only looks like one is left alone
        self.assertEqual(self.args(strategy='NOPE-INVERSA')['strategy'], 'NOPE-INVERSA')


class TestHandlerFields(unittest.TestCase):

    def test_numbers_set_through_set_are_the_form(self):
        fields = dict((f['name'], f['value'])
                      for f in service.handlerFields('H401-PULLBACK-EMA'))
        self.assertEqual(fields['fast'], 50)
        self.assertEqual(fields['reward'], 2.0)
        # wiring is not a parameter
        self.assertNotIn('pipSize', fields)
        self.assertNotIn('granularity', fields)

    def test_the_query_sets_only_what_differs(self):
        get = {'fast': '30', 'reward': '2'}.get
        self.assertEqual(service.handlerArgs('H401-PULLBACK-EMA', get), {'fast': 30})
        with self.assertRaises(ServiceError):
            service.handlerArgs('H401-PULLBACK-EMA', {'fast': '2.5'}.get)


class TestTradeTimer(unittest.TestCase):

    def setUp(self):
        self.timer = TradeTimer(3, granularity='H4', instrument='EUR_USD')
        self.sink = Recorder()
        self.timer.set_queue(self.sink)

    def bar(self, n, granularity='H4'):
        ev = CandleEvent(candle_dict(DAY + datetime.timedelta(hours=4 * n)))
        ev.instrument, ev.granularity = 'EUR_USD', granularity
        self.timer.execute_event(ev)

    def fill(self, closed=False):
        data = {'type': 'ORDER_FILL', 'orderID': 1}
        if closed:
            data['tradesClosed'] = [{'tradeID': 1}]
        self.timer.execute_event(TransactionEvent(data))

    def test_nothing_open_nothing_closed(self):
        for n in range(10):
            self.bar(n)
        self.assertEqual(self.sink.of('CLOSETRADE'), [])

    def test_closed_after_its_bars(self):
        self.fill()
        self.bar(0)
        self.bar(1)
        self.bar(1, granularity='M5')   # the fine stream is not a bar of it
        self.assertEqual(self.sink.of('CLOSETRADE'), [])
        self.bar(2)
        asked = self.sink.of('CLOSETRADE')
        self.assertEqual(len(asked), 1)
        self.assertEqual(asked[0].reason, MAX_LENGTH)

    def test_a_trade_that_closed_on_its_own_stops_the_clock(self):
        self.fill()
        self.bar(0)
        self.fill(closed=True)
        for n in range(1, 6):
            self.bar(n)
        self.assertEqual(self.sink.of('CLOSETRADE'), [])


class TestKpis(unittest.TestCase):
    """The comparison table's ratios, on a curve small enough to do by hand."""

    def kpis(self, curve, start=100.0, days=365.25):
        from parity_deriva.performance import report
        begin = datetime.datetime(2023, 1, 2)
        return report.kpis([(begin + datetime.timedelta(days=d), b) for d, b in curve],
                           start, begin, begin + datetime.timedelta(days=days),
                           {'averageWin': 30.0, 'averageLoss': 10.0, 'expectancy': 5.0})

    def test_a_year_that_made_ten_per_cent(self):
        k = self.kpis([(10, 90.0), (20, 110.0)])
        self.assertAlmostEqual(k['roi'], 10.0)
        self.assertAlmostEqual(k['car'], 10.0, places=6)
        self.assertAlmostEqual(k['maxDrawdownPct'], 10.0)
        self.assertAlmostEqual(k['carMdd'], 1.0, places=6)
        self.assertEqual(k['riskReward'], 3.0)
        self.assertGreater(k['ulcer'], 0)
        self.assertIsNotNone(k['sharpe'])

    def test_two_closes_at_one_instant_keep_their_order(self):
        """The last balance is the last one closed, not the larger one."""
        k = self.kpis([(10, 105.0), (10, 95.0)])
        self.assertAlmostEqual(k['roi'], -5.0)

    def test_a_curve_that_never_fell(self):
        k = self.kpis([(10, 105.0), (20, 110.0)])
        self.assertEqual(k['maxDrawdownPct'], 0.0)
        self.assertIsNone(k['carMdd'])
        self.assertEqual(k['ulcer'], 0.0)

    def test_no_trades_is_nothing_and_not_a_crash(self):
        k = self.kpis([])
        self.assertEqual(k['roi'], 0.0)
        self.assertIsNone(k['sharpe'])


class TestSavedSweeps(TempDirCase):
    """A finished set is kept on disk and can be listed, opened, renamed
    and deleted - the registry the sets dialog reads."""

    def setUp(self):
        super(TestSavedSweeps, self).setUp()
        self.settings.RUNS_DIR = self.path('runs')
        self.service = service.Service(setup=self.settings)

    def job(self, id='20260923-120000-abcdef'):
        return {'id': id, 'name': 'first', 'total': 3, 'cancel': True,
                'fields': {'strategy': 'H401-PULLBACK-EMA', 'instrument': 'EUR_USD',
                           'granularity': 'H4', 'from': '2023-01-01', 'to': '2023-12-31'},
                'grid': {'reward': '1.5, 2'}, 'varied': ['reward'],
                'finished': 1790000000.0, 'running': False, 'current': None,
                'done': [{'n': 1, 'params': {'reward': '1.5'}, 'final': 10500.0, 'curve': []},
                         {'n': 2, 'params': {'reward': '2'}, 'final': 11000.0, 'curve': []}]}

    def test_saved_listed_opened(self):
        self.service.saveSweep(self.job())
        listed = self.service.sweeps()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]['best'], 11000.0)
        self.assertEqual(listed[0]['bestParams'], {'reward': '2'})
        self.assertEqual((listed[0]['runs'], listed[0]['total'], listed[0]['stopped']),
                         (2, 3, True))
        opened = self.service.savedSweep('20260923-120000-abcdef')
        self.assertFalse(opened['running'])
        self.assertEqual(opened['grid'], {'reward': '1.5, 2'})
        self.assertEqual(len(opened['done']), 2)

    def test_renamed_and_deleted(self):
        self.service.saveSweep(self.job())
        self.service.renameSweep('20260923-120000-abcdef', ' second ')
        self.assertEqual(self.service.sweeps()[0]['name'], 'second')
        self.service.deleteSweep('20260923-120000-abcdef')
        self.assertEqual(self.service.sweeps(), [])

    def test_a_run_is_kept_whole_and_goes_with_its_set(self):
        sweep = '20260923-120000-abcdef'
        self.service.saveSweep(self.job())
        self.assertEqual(self.service.sweepRun(sweep, 1), {'cached': False})
        self.service.saveSweepRun(sweep, 1, {'trades': [{'n': 1}]})
        self.assertEqual(self.service.sweepRun(sweep, '1'), {'trades': [{'n': 1}]})
        self.assertEqual(len(self.service.sweeps()), 1)
        self.service.deleteSweep(sweep)
        self.assertEqual(self.service.sweepRun(sweep, 1), {'cached': False})

    def test_an_id_that_is_not_one_reaches_no_path(self):
        for bad in ('../../etc/passwd', '', 'x'):
            with self.assertRaises(ServiceError):
                self.service.savedSweep(bad)


if __name__ == '__main__':
    unittest.main()
