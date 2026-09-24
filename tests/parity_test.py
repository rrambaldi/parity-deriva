"""
Tests for trading/parity.py.

The monitor is the component that can stop trading, so what it does has to be
a function of configuration and nothing else: every threshold here is set by
the test, and a check left unset must be off rather than defaulted to
something invented.
"""

import unittest
from unittest import mock

from parity_deriva.event.event import (SimulatedFillEvent, StatusEvent,
                                       TransactionEvent)
from parity_deriva.portfolio.moneymanager import MoneyManager
from parity_deriva.trading.parity import (OUTCOME, SLIPPAGE, UNDECIDED,
                                          UNKNOWN_OUTCOMES, ParityMonitor,
                                          policy_for)
from parity_deriva.tests.helpers import Recorder, TempDirCase


def real_fill(key, price=1.5, reason='ORDER_FILL', closed=False, pl=None):
    payload = {'type': 'ORDER_FILL', 'signalNumber': key, 'orderID': 1,
               'price': price, 'reason': reason}
    if closed:
        payload['tradesClosed'] = [{'tradeID': 1}]
        payload['pl'] = pl if pl is not None else 1.0
    return TransactionEvent(payload)


def sim_fill(key, price=1.5, reason='ORDER_FILL', closed=False, pl=None):
    payload = {'signalNumber': key, 'orderID': 1, 'price': price,
               'reason': reason}
    if closed:
        payload['tradesClosed'] = [{'tradeID': 1}]
        payload['pl'] = pl if pl is not None else 1.0
    return SimulatedFillEvent(payload)


class ParityCase(TempDirCase):

    def monitor(self, **policy):
        base = {'window': 100, 'min_sample': 1, 'max_outcome_mismatch': 0.5,
                'max_slippage': None, 'max_unpaired': None, 'action': 'warn'}
        base.update(policy)
        mon = ParityMonitor(setup=self.settings, instrument='EUR_USD',
                            policy=base)
        self.sink = Recorder()
        mon.set_queue(self.sink)
        return mon

    def trade(self, mon, key, real_reason, sim_reason,
              real_price=1.5, sim_price=1.5):
        mon.execute_event(real_fill(key, real_price))
        mon.execute_event(sim_fill(key, sim_price))
        mon.execute_event(real_fill(key, real_price, real_reason, closed=True))
        mon.execute_event(sim_fill(key, sim_price, sim_reason, closed=True))


class TestPolicyIsConfiguration(TempDirCase):
    """Nothing the monitor does is measured; it all comes from settings."""

    def test_the_defaults_come_from_settings(self):
        setup = mock.MagicMock()
        setup.PARITY_ALARM = {'window': 7, 'action': 'halt'}
        setup.PARITY_ALARM_BY_INSTRUMENT = {}
        self.assertEqual(policy_for('EUR_USD', setup),
                         {'window': 7, 'action': 'halt'})

    def test_an_instrument_override_is_merged_key_by_key(self):
        setup = mock.MagicMock()
        setup.PARITY_ALARM = {'window': 7, 'action': 'warn',
                              'max_outcome_mismatch': 0.05}
        setup.PARITY_ALARM_BY_INSTRUMENT = {'DE30_EUR': {'action': 'halt'}}
        got = policy_for('DE30_EUR', setup)
        self.assertEqual(got['action'], 'halt')
        self.assertEqual(got['window'], 7)          # untouched by the override
        self.assertEqual(got['max_outcome_mismatch'], 0.05)

    def test_an_instrument_with_no_override_gets_the_defaults(self):
        setup = mock.MagicMock()
        setup.PARITY_ALARM = {'action': 'warn'}
        setup.PARITY_ALARM_BY_INSTRUMENT = {'DE30_EUR': {'action': 'halt'}}
        self.assertEqual(policy_for('EUR_USD', setup)['action'], 'warn')

    def test_the_shipped_settings_are_a_usable_policy(self):
        from parity_deriva.etc import settings
        policy = policy_for('EUR_USD', settings)
        for field in ('window', 'min_sample', 'max_outcome_mismatch', 'action'):
            self.assertIn(field, policy)
        self.assertIn(policy['action'], ('warn', 'halt'))

    def test_the_shipped_threshold_sits_above_the_measured_floor(self):
        """
        scripts/divergence_band.py reported a 1.23% coin-flip floor for AG01
        on H1. A threshold at or below that would fire on the width of the
        bars, so the shipped default has to clear it.
        """
        from parity_deriva.etc import settings
        self.assertGreater(policy_for('EUR_USD', settings)['max_outcome_mismatch'],
                           0.0123)

    def test_a_policy_can_be_passed_in_instead(self):
        mon = ParityMonitor(setup=self.settings, policy={'window': 3})
        self.assertEqual(mon.policy, {'window': 3})


class TestJoining(ParityCase):

    def test_agreeing_trades_produce_nothing(self):
        mon = self.monitor()
        self.trade(mon, 'K1', 'TAKE_PROFIT_ORDER', 'TAKE_PROFIT_ORDER')
        self.assertEqual(mon.divergences, [])
        self.assertEqual(mon.reconciled, 1)

    def test_a_disagreement_on_the_outcome_is_recorded(self):
        mon = self.monitor()
        self.trade(mon, 'K1', 'STOP_LOSS_ORDER', 'TAKE_PROFIT_ORDER')
        self.assertEqual(len(mon.divergences), 1)
        self.assertEqual(mon.divergences[0].kind, OUTCOME)
        self.assertEqual(mon.divergences[0].key, 'K1')

    def test_nothing_is_judged_until_both_sides_close(self):
        mon = self.monitor()
        mon.execute_event(real_fill('K1', 1.5, 'STOP_LOSS_ORDER', closed=True))
        self.assertEqual(mon.reconciled, 0)
        mon.execute_event(sim_fill('K1', 1.5, 'TAKE_PROFIT_ORDER', closed=True))
        self.assertEqual(mon.reconciled, 1)

    def test_the_key_is_what_joins_them(self):
        """Different keys are different trades, however alike they look."""
        mon = self.monitor()
        mon.execute_event(real_fill('K1', 1.5, 'STOP_LOSS_ORDER', closed=True))
        mon.execute_event(sim_fill('K2', 1.5, 'STOP_LOSS_ORDER', closed=True))
        self.assertEqual(mon.reconciled, 0)
        self.assertEqual(sorted(mon.unpaired()), ['K1', 'K2'])

    def test_an_event_without_a_key_is_ignored(self):
        mon = self.monitor()
        mon.execute_event(TransactionEvent({'type': 'ORDER_FILL', 'price': 1.0}))
        self.assertEqual(mon.reconciled, 0)

    def test_other_transactions_are_ignored(self):
        mon = self.monitor()
        mon.execute_event(TransactionEvent(
            {'type': 'STOP_ORDER', 'signalNumber': 'K1'}))
        self.assertEqual(mon.reconciled, 0)

    def test_a_reconciled_key_is_forgotten(self):
        mon = self.monitor()
        self.trade(mon, 'K1', 'TAKE_PROFIT_ORDER', 'TAKE_PROFIT_ORDER')
        self.assertEqual(mon.real, {})
        self.assertEqual(mon.simulated, {})


class TestSlippage(ParityCase):

    def test_it_is_off_when_unset(self):
        """An unconfigured check must not be given a value we invented."""
        mon = self.monitor(max_slippage=None)
        self.trade(mon, 'K1', 'TAKE_PROFIT_ORDER', 'TAKE_PROFIT_ORDER',
                   real_price=1.5, sim_price=9.9)
        self.assertEqual(mon.divergences, [])

    def test_a_fill_within_the_limit_passes(self):
        mon = self.monitor(max_slippage=0.001)
        self.trade(mon, 'K1', 'TAKE_PROFIT_ORDER', 'TAKE_PROFIT_ORDER',
                   real_price=1.5005, sim_price=1.5)
        self.assertEqual(mon.divergences, [])

    def test_a_fill_beyond_it_is_recorded(self):
        mon = self.monitor(max_slippage=0.001)
        self.trade(mon, 'K1', 'TAKE_PROFIT_ORDER', 'TAKE_PROFIT_ORDER',
                   real_price=1.51, sim_price=1.5)
        self.assertEqual([d.kind for d in mon.divergences], [SLIPPAGE])

    def test_the_limit_is_in_the_instrument_own_units(self):
        mon = self.monitor(max_slippage=0.5)
        self.trade(mon, 'K1', 'TAKE_PROFIT_ORDER', 'TAKE_PROFIT_ORDER',
                   real_price=11700.4, sim_price=11700.0)
        self.assertEqual(mon.divergences, [])


class TestTheRateIsJudgedOverAWindow(ParityCase):
    """
    A share of disagreements is structural, not a signal: a bar cannot say
    which of two levels it reached first. So the monitor judges a rate, and
    declines to judge a sample too small to carry one.
    """

    def test_it_refuses_to_judge_below_the_minimum_sample(self):
        mon = self.monitor(min_sample=10, max_outcome_mismatch=0.01)
        self.trade(mon, 'K1', 'STOP_LOSS_ORDER', 'TAKE_PROFIT_ORDER')
        self.assertIsNone(mon.rate())
        self.assertEqual(mon.breaches(), [])

    def test_the_rate_is_the_share_of_the_window(self):
        mon = self.monitor(min_sample=4, max_outcome_mismatch=0.9)
        for i in range(3):
            self.trade(mon, 'A%d' % i, 'TAKE_PROFIT_ORDER', 'TAKE_PROFIT_ORDER')
        self.trade(mon, 'B', 'STOP_LOSS_ORDER', 'TAKE_PROFIT_ORDER')
        self.assertAlmostEqual(mon.rate(), 0.25)

    def test_the_window_forgets_old_trades(self):
        mon = self.monitor(window=4, min_sample=4, max_outcome_mismatch=0.9)
        self.trade(mon, 'bad', 'STOP_LOSS_ORDER', 'TAKE_PROFIT_ORDER')
        for i in range(4):
            self.trade(mon, 'ok%d' % i, 'TAKE_PROFIT_ORDER', 'TAKE_PROFIT_ORDER')
        self.assertEqual(mon.rate(), 0.0)

    def test_a_breach_is_reported_once_the_rate_passes_the_threshold(self):
        mon = self.monitor(min_sample=2, max_outcome_mismatch=0.4)
        self.trade(mon, 'A', 'TAKE_PROFIT_ORDER', 'TAKE_PROFIT_ORDER')
        self.trade(mon, 'B', 'STOP_LOSS_ORDER', 'TAKE_PROFIT_ORDER')
        self.assertAlmostEqual(mon.rate(), 0.5)
        self.assertEqual(len(mon.breaches()), 1)
        self.assertIn("outcome mismatch", mon.breaches()[0])

    def test_no_breach_while_the_rate_stays_under(self):
        mon = self.monitor(min_sample=2, max_outcome_mismatch=0.6)
        self.trade(mon, 'A', 'TAKE_PROFIT_ORDER', 'TAKE_PROFIT_ORDER')
        self.trade(mon, 'B', 'STOP_LOSS_ORDER', 'TAKE_PROFIT_ORDER')
        self.assertEqual(mon.breaches(), [])


class TestUnpaired(ParityCase):
    """
    The serious case: one side closed a trade the other never had. That is
    not bar width, it is a rejected order, a missed fill or a dropped stream.
    """

    def test_they_are_listed(self):
        mon = self.monitor()
        mon.execute_event(sim_fill('K1', 1.5, 'STOP_LOSS_ORDER', closed=True))
        self.assertEqual(mon.unpaired(), ['K1'])

    def test_the_check_is_off_when_unset(self):
        mon = self.monitor(max_unpaired=None)
        for i in range(20):
            mon.execute_event(sim_fill('K%d' % i, 1.5, 'STOP_LOSS_ORDER', closed=True))
        self.assertEqual(mon.breaches(), [])

    def test_beyond_the_limit_it_is_a_breach(self):
        mon = self.monitor(max_unpaired=2)
        for i in range(2):
            mon.execute_event(sim_fill('K%d' % i, 1.5, 'STOP_LOSS_ORDER', closed=True))
        self.assertEqual(mon.breaches(), [])
        mon.execute_event(sim_fill('K2', 1.5, 'STOP_LOSS_ORDER', closed=True))
        breaches = mon.breaches()
        self.assertEqual(len(breaches), 1)
        self.assertIn("one side only", breaches[0])

    def test_one_side_going_silent_halts_without_any_reconcile(self):
        """
        Was: the alarm was only weighed after a successful reconcile, so a
             dead transaction stream - nothing to reconcile, ever - kept it
             quiet through exactly the failure it exists to catch.
        Now: intake is judged too.
        """
        mon = self.monitor(max_unpaired=2, action='halt')
        for i in range(3):
            mon.execute_event(sim_fill('K%d' % i, 1.5, 'STOP_LOSS_ORDER', closed=True))
        self.assertEqual(mon.reconciled, 0)
        self.assertTrue(mon.halted)
        self.assertIn('HALT', self.sink.statuses())

    def test_a_trade_that_pairs_up_later_is_not_stranded(self):
        mon = self.monitor(max_unpaired=0)
        mon.execute_event(sim_fill('K1', 1.5, 'STOP_LOSS_ORDER', closed=True))
        self.assertEqual(mon.unpaired(), ['K1'])
        mon.execute_event(real_fill('K1', 1.5, 'STOP_LOSS_ORDER', closed=True))
        self.assertEqual(mon.unpaired(), [])


class TestAction(ParityCase):

    def test_warn_does_not_halt(self):
        mon = self.monitor(min_sample=1, max_outcome_mismatch=0.0,
                           action='warn')
        self.trade(mon, 'K1', 'STOP_LOSS_ORDER', 'TAKE_PROFIT_ORDER')
        self.assertTrue(mon.breaches())
        self.assertFalse(mon.halted)
        self.assertNotIn('HALT', self.sink.statuses())

    def test_halt_publishes_a_status_event(self):
        mon = self.monitor(min_sample=1, max_outcome_mismatch=0.0,
                           action='halt')
        self.trade(mon, 'K1', 'STOP_LOSS_ORDER', 'TAKE_PROFIT_ORDER')
        self.assertTrue(mon.halted)
        self.assertEqual(set(self.sink.kinds()), {'STATUS'})
        self.assertIn('HALT', self.sink.statuses())

    def test_it_halts_once_and_does_not_repeat(self):
        mon = self.monitor(min_sample=1, max_outcome_mismatch=0.0,
                           action='halt')
        for i in range(4):
            self.trade(mon, 'K%d' % i, 'STOP_LOSS_ORDER', 'TAKE_PROFIT_ORDER')
        self.assertEqual(self.sink.statuses().count('HALT'), 1)

    def test_resume_clears_it(self):
        mon = self.monitor(min_sample=1, max_outcome_mismatch=0.0,
                           action='halt')
        self.trade(mon, 'K1', 'STOP_LOSS_ORDER', 'TAKE_PROFIT_ORDER')
        mon.execute_event(StatusEvent('RESUME'))
        self.assertFalse(mon.halted)


class TestTheMoneyManagerHonoursTheAlarm(TempDirCase):
    """
    Was: nothing stopped the money manager. The alarm could be raised and
         orders kept going out, which makes the alarm decorative.
    Now: HALT refuses further signals until RESUME.
    """

    def manager(self):
        mm = MoneyManager(setup=self.settings, units=1)
        mm.signals, mm.processed = {}, []
        mm.onTrade = mm.orderIssued = False
        mm.halted = False
        self.sink = Recorder()
        mm.set_queue(self.sink)
        return mm

    def signal(self, key="K1"):
        from parity_deriva.event.event import SignalEvent
        return SignalEvent({"instrument": "EUR_USD", "units": 1,
                            "orderType": "STOP", "price": 1.5,
                            "stopLoss": 1.4, "takeProfit": 1.6,
                            "signalNumber": key, "gtdTime": None})

    def test_it_starts_unhalted(self):
        self.assertFalse(self.manager().halted)

    def test_halt_stops_new_orders(self):
        mm = self.manager()
        mm.execute_event(StatusEvent('HALT'))
        mm.execute_event(self.signal())
        self.assertEqual(self.sink.events, [])

    def test_it_traded_before_the_halt(self):
        mm = self.manager()
        mm.execute_event(self.signal('before'))
        self.assertEqual(len(self.sink.of('ORDER')), 1)

    def test_resume_lets_it_trade_again(self):
        mm = self.manager()
        mm.execute_event(StatusEvent('HALT'))
        mm.execute_event(self.signal('blocked'))
        mm.execute_event(StatusEvent('RESUME'))
        mm.execute_event(self.signal('allowed'))
        self.assertEqual(len(self.sink.of('ORDER')), 1)

    def test_other_statuses_do_not_halt_it(self):
        mm = self.manager()
        for status in ('STARTED', 'DONE', 'ERROR'):
            mm.execute_event(StatusEvent(status))
        self.assertFalse(mm.halted)

    def test_a_halt_the_monitor_publishes_reaches_it(self):
        """End to end: the monitor trips, the money manager stops."""
        mon = ParityMonitor(setup=self.settings, instrument='EUR_USD',
                            policy={'window': 10, 'min_sample': 1,
                                    'max_outcome_mismatch': 0.0,
                                    'max_slippage': None,
                                    'max_unpaired': None, 'action': 'halt'})
        mm = self.manager()
        bus = Recorder()
        mon.set_queue(bus)
        mon.execute_event(real_fill('K1', 1.5, 'STOP_LOSS_ORDER', closed=True))
        mon.execute_event(sim_fill('K1', 1.5, 'TAKE_PROFIT_ORDER', closed=True))
        for event in bus.events:
            mm.execute_event(event)
        self.assertTrue(mm.halted)
        mm.execute_event(self.signal())
        self.assertEqual(self.sink.events, [])


if __name__ == "__main__":
    unittest.main()


class TestUndecidableOutcomes(ParityCase):
    """
    A side that cannot say how a trade ended is not a side that disagrees.

    OANDA states which leg closed a trade. eToro reports only the rate it
    closed at, so data/etoro.closeReason() reads the leg off the rate and
    says UNKNOWN where the rate reached neither level - a manual close, a
    margin call, a gap. Scoring that as a mismatch would put our own
    ignorance into the rate the alarm fires on, and the alarm would go off
    loudest on the broker that explains itself least.
    """

    def test_what_counts_as_unable_to_say(self):
        self.assertIn(None, UNKNOWN_OUTCOMES)
        self.assertIn('UNKNOWN', UNKNOWN_OUTCOMES)
        self.assertIn('', UNKNOWN_OUTCOMES)

    def test_an_unknown_real_outcome_is_not_a_divergence(self):
        mon = self.monitor()
        self.trade(mon, 'k1', 'UNKNOWN', 'TAKE_PROFIT_ORDER')
        self.assertEqual(mon.divergences, [])
        self.assertEqual(mon.undecided, 1)

    def test_an_unknown_simulated_outcome_is_not_a_divergence_either(self):
        mon = self.monitor()
        self.trade(mon, 'k1', 'TAKE_PROFIT_ORDER', 'UNKNOWN')
        self.assertEqual(mon.divergences, [])
        self.assertEqual(mon.undecided, 1)

    def test_both_unknown_is_not_agreement_either(self):
        """
        Two sides that both shrug have not agreed on anything, so the trade
        must not be counted as one that matched.
        """
        mon = self.monitor()
        self.trade(mon, 'k1', 'UNKNOWN', 'UNKNOWN')
        self.assertEqual(mon.divergences, [])
        self.assertEqual(mon.undecided, 1)
        self.assertEqual(len(mon.window), 0)

    def test_an_undecidable_trade_is_still_reconciled(self):
        """It was paired, so it must not linger in the unpaired count."""
        mon = self.monitor()
        self.trade(mon, 'k1', 'UNKNOWN', 'TAKE_PROFIT_ORDER')
        self.assertEqual(mon.reconciled, 1)
        self.assertEqual(mon.unpaired(), [])

    def test_it_stays_out_of_the_window(self):
        """
        Counting it as agreement would dilute the rate: nineteen trades
        nobody could judge would hide one real mismatch under a 5% threshold.
        """
        mon = self.monitor()
        for i in range(19):
            self.trade(mon, 'u%d' % i, 'UNKNOWN', 'TAKE_PROFIT_ORDER')
        self.trade(mon, 'real', 'STOP_LOSS_ORDER', 'TAKE_PROFIT_ORDER')
        self.assertEqual(len(mon.window), 1)
        self.assertEqual(mon.rate(), 1.0)

    def test_slippage_is_still_judged_on_an_undecidable_trade(self):
        """
        The outcome is what could not be read; the fill prices were both
        reported, and comparing them needs no outcome at all.
        """
        mon = self.monitor(max_slippage=0.001)
        self.trade(mon, 'k1', 'UNKNOWN', 'TAKE_PROFIT_ORDER',
                   real_price=1.5, sim_price=1.6)
        self.assertEqual([d.kind for d in mon.divergences], [SLIPPAGE])
        self.assertEqual(mon.undecided, 1)

    def test_a_trade_that_slipped_does_enter_the_window(self):
        mon = self.monitor(max_slippage=0.001)
        self.trade(mon, 'k1', 'UNKNOWN', 'TAKE_PROFIT_ORDER',
                   real_price=1.5, sim_price=1.6)
        self.assertEqual(list(mon.window), [True])

    def test_a_decidable_mismatch_still_diverges(self):
        """The new branch must not have swallowed the old one."""
        mon = self.monitor()
        self.trade(mon, 'k1', 'STOP_LOSS_ORDER', 'TAKE_PROFIT_ORDER')
        self.assertEqual([d.kind for d in mon.divergences], [OUTCOME])
        self.assertEqual(mon.undecided, 0)


class TestUndecidedThreshold(ParityCase):
    """
    A monitor that can never read the outcome is blind, and being blind is
    worth an alarm of its own - but only if the operator asked for one.
    """

    def test_unset_means_off(self):
        mon = self.monitor(max_undecided=None)
        for i in range(50):
            self.trade(mon, 'u%d' % i, 'UNKNOWN', 'TAKE_PROFIT_ORDER')
        self.assertEqual(mon.undecided, 50)
        self.assertEqual(mon.breaches(), [])

    def test_over_the_limit_is_a_breach(self):
        mon = self.monitor(max_undecided=2)
        for i in range(3):
            self.trade(mon, 'u%d' % i, 'UNKNOWN', 'TAKE_PROFIT_ORDER')
        breaches = mon.breaches()
        self.assertEqual(len(breaches), 1)
        self.assertIn('could not be judged', breaches[0])

    def test_at_the_limit_is_not_a_breach(self):
        mon = self.monitor(max_undecided=2)
        for i in range(2):
            self.trade(mon, 'u%d' % i, 'UNKNOWN', 'TAKE_PROFIT_ORDER')
        self.assertEqual(mon.breaches(), [])

    def test_it_can_halt_like_any_other_breach(self):
        mon = self.monitor(max_undecided=0, action='halt')
        self.trade(mon, 'u1', 'UNKNOWN', 'TAKE_PROFIT_ORDER')
        self.assertEqual(self.sink.statuses().count('HALT'), 1)

    def test_the_shipped_setting_is_off(self):
        """
        Consistent with every other threshold in PARITY_ALARM: a check the
        operator has not set is off, not given a number we invented.
        """
        from parity_deriva.etc import settings
        self.assertIsNone(policy_for('EUR_USD', settings).get('max_undecided'))


class TestDivergencesArePublished(ParityCase):
    """
    Was: a divergence was a log line and nothing else. The web page reads the
         JSONL event log, not the logger, so it saw the HALT and never what
         led up to it.
    Now: each finding is a STATUS event as well - status PARITY for one
         comparison, PARITY_ALARM for a breach - so it lands in the log the
         page reads. Nothing else on the bus reads STATUS for anything but
         HALT, RESUME, LIQUIDATE, ERROR and DONE, so these pass everyone by.
    """

    def parity(self):
        return [e for e in self.sink.of('STATUS') if e.status == 'PARITY']

    def alarms(self):
        return [e for e in self.sink.of('STATUS') if e.status == 'PARITY_ALARM']

    def test_an_outcome_divergence_is_a_status_event(self):
        mon = self.monitor()
        self.trade(mon, 'K1', 'STOP_LOSS_ORDER', 'TAKE_PROFIT_ORDER')
        found = self.parity()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].kind, OUTCOME)
        self.assertEqual(found[0].key, 'K1')
        self.assertEqual(found[0].instrument, 'EUR_USD')
        self.assertEqual(found[0].detail, mon.divergences[0].detail)

    def test_an_undecided_pair_is_said_too(self):
        mon = self.monitor()
        self.trade(mon, 'K1', 'UNKNOWN', 'TAKE_PROFIT_ORDER')
        found = self.parity()
        self.assertEqual([e.kind for e in found], [UNDECIDED])
        self.assertEqual(found[0].key, 'K1')
        self.assertIn('UNKNOWN', found[0].detail)

    def test_an_agreeing_trade_says_nothing(self):
        mon = self.monitor()
        self.trade(mon, 'K1', 'TAKE_PROFIT_ORDER', 'TAKE_PROFIT_ORDER')
        self.assertEqual(self.sink.events, [])

    def test_a_breach_is_an_alarm_event_carrying_the_breaches(self):
        mon = self.monitor(min_sample=1, max_outcome_mismatch=0.0,
                           action='warn')
        self.trade(mon, 'K1', 'STOP_LOSS_ORDER', 'TAKE_PROFIT_ORDER')
        alarms = self.alarms()
        self.assertTrue(alarms)
        self.assertEqual(alarms[0].breaches, mon.breaches())
        self.assertIn('outcome mismatch', alarms[0].breaches[0])
        self.assertEqual(alarms[0].instrument, 'EUR_USD')
        # 'warn' is still warn
        self.assertFalse(mon.halted)

    def test_the_alarm_comes_before_the_halt(self):
        """The page should read what tripped it, then that it tripped."""
        mon = self.monitor(min_sample=1, max_outcome_mismatch=0.0,
                           action='halt')
        self.trade(mon, 'K1', 'STOP_LOSS_ORDER', 'TAKE_PROFIT_ORDER')
        statuses = self.sink.statuses()
        self.assertLess(statuses.index('PARITY_ALARM'), statuses.index('HALT'))

    def test_the_money_manager_is_not_moved_by_them(self):
        mm = MoneyManager(setup=self.settings, units=1)
        mm.signals, mm.processed = {}, []
        mm.onTrade = mm.orderIssued = mm.halted = False
        mm.set_queue(Recorder())
        mm.execute_event(StatusEvent({'status': 'PARITY', 'kind': OUTCOME}))
        mm.execute_event(StatusEvent({'status': 'PARITY_ALARM', 'breaches': ['x']}))
        self.assertFalse(mm.halted)
