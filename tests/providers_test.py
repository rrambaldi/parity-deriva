"""
Tests for trading/providers.py, the broker registry.

The point of the module is that a stack can be pointed at any of the four
brokers and that an unsupported combination is refused at startup rather than
run. So
these tests are mostly about the refusals: an unknown provider name, a
capability a provider does not declare, a capability name that does not
exist. The happy paths are thin by comparison, which is the right shape - a
registry that returns the right class is easy, and a registry that quietly
returns the wrong one is what costs money.
"""

import types
import unittest

from parity_deriva.trading import providers
from parity_deriva.trading.providers import (Capabilities, CapabilityError,
                                             EToroProvider, IBProvider,
                                             IGProvider, OANDAProvider,
                                             Provider, ProviderError,
                                             UnknownProvider)


def settings_stub(**over):
    """
    A settings object whose unset attributes are actually absent.

    Not a MagicMock: getattr() on one of those succeeds for every name, so
    ETORO_SPREAD would come back as a Mock rather than None and every
    'unset means off' assertion here would pass for the wrong reason.
    """
    stub = types.SimpleNamespace(
        PROVIDER='oanda',
        DOMAIN='practice',
        ETORO_SPREAD=None,
        IB_SPREAD=None,
        # enough of the OANDA side for its handlers to be constructed
        ACCOUNT_ID='001-TEST-000',
        ACCESS_TOKEN='TESTTOKEN',
        API_DOMAIN='api-fxpractice.oanda.test',
        # ... and of the IG and IB sides, for the same reason
        IG_API_KEY='TESTKEY',
        IG_IDENTIFIER='someone',
        IG_PASSWORD='secret',
        IG_INSTRUMENTS={'EUR_USD': {'epic': 'CS.D.EURUSD.MINI.IP'}},
        IB_ACCOUNT_ID='DU1234567',
        IB_INSTRUMENTS={'EUR_USD': {'conid': 12345}},
    )
    for key, value in over.items():
        setattr(stub, key, value)
    return stub


class CapabilitiesTest(unittest.TestCase):

    def test_defaults_are_conservative(self):
        """
        Anything undeclared is treated as unsupported.

        A provider that forgets to declare something should have that
        omission surface as a refusal to start, not as a behaviour nobody
        chose.
        """
        caps = Capabilities()
        self.assertFalse(caps.bid_ask_candles)
        self.assertFalse(caps.transaction_stream)
        self.assertFalse(caps.close_reason)
        self.assertEqual(caps.order_types, frozenset())
        self.assertIsNone(caps.max_history_candles)

    def test_unknown_capability_is_rejected(self):
        """A typo in a declaration must not become a silently absent feature."""
        with self.assertRaises(ProviderError):
            Capabilities(bid_ask_candels=True)

    def test_names_lists_every_capability(self):
        names = Capabilities().names()
        self.assertIn('bid_ask_candles', names)
        self.assertIn('order_expiry', names)
        self.assertNotIn('dump', names)
        self.assertEqual(names, sorted(names))

    def test_dump_is_readable(self):
        text = Capabilities(bid_ask_candles=True).dump()
        self.assertIn('bid_ask_candles', text)
        self.assertIn('True', text)


class RegistryTest(unittest.TestCase):

    def test_available(self):
        self.assertEqual(providers.available(),
                         ['capital', 'etoro', 'ib', 'ig', 'mt5', 'oanda', 'twelvedata'])

    def test_default_comes_from_settings(self):
        p = providers.get_provider(setup=settings_stub(PROVIDER='etoro'))
        self.assertEqual(p.name, 'etoro')

    def test_explicit_name_wins_over_settings(self):
        p = providers.get_provider('oanda', setup=settings_stub(PROVIDER='etoro'))
        self.assertEqual(p.name, 'oanda')

    def test_name_is_case_and_space_insensitive(self):
        self.assertEqual(providers.get_provider('  EToro ').name, 'etoro')

    def test_unknown_name_raises_rather_than_defaulting(self):
        """
        Falling back to a default here would trade on a broker nobody named.

        'interactive-brokers' is the example on purpose: IB is registered
        under 'ib', and a near-miss that silently became OANDA is exactly the
        mistake this refusal exists for.
        """
        with self.assertRaises(UnknownProvider) as caught:
            providers.get_provider('interactive-brokers')
        for name in ('etoro', 'ib', 'ig', 'mt5', 'oanda'):
            self.assertIn(name, str(caught.exception))

    def test_missing_provider_setting_falls_back_to_oanda(self):
        """A settings file predating this module still works."""
        self.assertEqual(
            providers.get_provider(setup=types.SimpleNamespace()).name, 'oanda')


class RequireTest(unittest.TestCase):

    def test_passes_when_declared(self):
        p = providers.get_provider('oanda')
        self.assertIs(providers.require(p, 'bid_ask_candles',
                                        'transaction_stream'), p)

    def test_names_what_is_missing(self):
        p = providers.get_provider('etoro', setup=settings_stub())
        with self.assertRaises(CapabilityError) as caught:
            providers.require(p, 'bid_ask_candles', 'dated_history',
                              'transaction_stream')
        message = str(caught.exception)
        self.assertIn('etoro', message)
        self.assertIn('bid_ask_candles', message)
        self.assertIn('dated_history', message)
        self.assertIn('transaction_stream', message)

    def test_unknown_capability_name_raises(self):
        with self.assertRaises(ProviderError):
            providers.require(providers.get_provider('oanda'), 'teleportation')

    def test_no_names_is_a_no_op(self):
        p = providers.get_provider('oanda')
        self.assertIs(providers.require(p), p)


class OANDAProviderTest(unittest.TestCase):

    def setUp(self):
        self.provider = OANDAProvider(setup=settings_stub())

    def test_declares_the_full_shape(self):
        caps = self.provider.capabilities
        self.assertTrue(caps.bid_ask_candles)
        self.assertTrue(caps.dated_history)
        self.assertTrue(caps.transaction_stream)
        self.assertTrue(caps.price_stream)
        self.assertTrue(caps.distinct_stop_limit)
        self.assertTrue(caps.close_reason)
        self.assertTrue(caps.order_expiry)
        self.assertTrue(caps.synchronous_orders)
        self.assertIsNone(caps.max_history_candles)

    def test_declares_that_a_stop_can_be_moved(self):
        """
        Not an amend - OANDA has none. PUT on a trade's orders collection
        cancels the stop it carries and attaches the new one in a single
        batch, which is what the capability is about: the move can be made,
        and the trade is never left without a stop while it is made.
        """
        self.assertTrue(self.provider.capabilities.stop_modify)

    def test_execution_is_the_oanda_handler(self):
        from parity_deriva.execution.execution import OANDAExecutionHandler
        self.assertIsInstance(self.provider.execution(), OANDAExecutionHandler)

    def test_granularity_passes_through(self):
        """The project's vocabulary is OANDA's, so nothing to translate."""
        self.assertEqual(self.provider.granularity('H1'), 'H1')


class EToroProviderTest(unittest.TestCase):

    def setUp(self):
        self.provider = EToroProvider(setup=settings_stub())

    def test_declares_every_gap(self):
        """
        These are the differences the rest of the eToro code is written
        around; a declaration drifting from reality is what would let a
        wiring assume a feature the API does not have.
        """
        caps = self.provider.capabilities
        self.assertFalse(caps.bid_ask_candles)
        self.assertFalse(caps.dated_history)
        self.assertFalse(caps.price_stream)
        self.assertFalse(caps.transaction_stream)
        self.assertFalse(caps.distinct_stop_limit)
        self.assertFalse(caps.close_reason)
        self.assertFalse(caps.order_expiry)
        self.assertFalse(caps.synchronous_orders)
        self.assertFalse(caps.stop_modify)
        self.assertEqual(caps.max_history_candles, 1000)

    def test_accepts_the_projects_order_types(self):
        """
        STOP and LIMIT are accepted even though the broker cannot tell them
        apart - they map onto mit. What is not claimed is that the broker
        distinguishes them, which is distinct_stop_limit.
        """
        self.assertEqual(self.provider.capabilities.order_types,
                         frozenset(['MARKET', 'STOP', 'LIMIT']))
        self.assertFalse(self.provider.capabilities.distinct_stop_limit)

    def test_spread_setting_grants_bid_ask(self):
        p = EToroProvider(setup=settings_stub(ETORO_SPREAD=0.0001))
        self.assertTrue(p.capabilities.bid_ask_candles)

    def test_spread_setting_changes_nothing_else(self):
        """
        The declaration is copied per instance before being changed. Mutating
        the class attribute instead would have one configured session grant
        bid/ask to every other provider object in the process.
        """
        p = EToroProvider(setup=settings_stub(ETORO_SPREAD=0.0001))
        self.assertFalse(p.capabilities.dated_history)
        self.assertFalse(p.capabilities.close_reason)
        self.assertEqual(p.capabilities.max_history_candles, 1000)
        self.assertFalse(EToroProvider.capabilities.bid_ask_candles)
        self.assertFalse(
            EToroProvider(setup=settings_stub()).capabilities.bid_ask_candles)

    def test_granularity_translates(self):
        self.assertEqual(self.provider.granularity('H1'), 'OneHour')

    def test_granularity_refuses_what_etoro_lacks(self):
        from parity_deriva.lib.etoro import EToroError
        with self.assertRaises(EToroError):
            self.provider.granularity('S5')


class IGProviderTest(unittest.TestCase):

    def setUp(self):
        self.provider = IGProvider(setup=settings_stub())

    def test_declares_what_it_has_and_what_it_lacks(self):
        """
        IG is the closest of the three to OANDA, and "closest" is not "the
        same": it serves a real bid and ask and expires an order, and it
        still cannot say which leg closed a trade or answer an order with its
        outcome.
        """
        caps = self.provider.capabilities
        self.assertTrue(caps.bid_ask_candles)
        self.assertTrue(caps.dated_history)
        self.assertTrue(caps.distinct_stop_limit)
        self.assertTrue(caps.order_expiry)
        self.assertFalse(caps.close_reason)
        self.assertFalse(caps.synchronous_orders)

    def test_streams_are_declared_absent(self):
        """
        IG does push, over Lightstreamer, which this project does not speak. A
        capability says what this stack can do, not what the broker's
        documentation mentions.
        """
        self.assertFalse(self.provider.capabilities.price_stream)
        self.assertFalse(self.provider.capabilities.transaction_stream)

    def test_bid_ask_needs_no_configuration(self):
        """Unlike eToro and IB, there is no spread model to set."""
        self.assertTrue(
            IGProvider(setup=settings_stub()).capabilities.bid_ask_candles)

    def test_granularity_translates(self):
        self.assertEqual(self.provider.granularity('H1'), 'HOUR')
        self.assertEqual(self.provider.granularity('M5'), 'MINUTE_5')

    def test_granularity_refuses_what_ig_lacks(self):
        from parity_deriva.lib.ig import IGError
        with self.assertRaises(IGError):
            self.provider.granularity('S5')


class IBProviderTest(unittest.TestCase):

    def setUp(self):
        self.provider = IBProvider(setup=settings_stub())

    def test_declares_the_one_thing_it_does_better(self):
        """
        IB states which leg closed a trade - not because its API is richer,
        but because a bracket there is three orders and the child that filled
        names the leg.
        """
        self.assertTrue(self.provider.capabilities.close_reason)

    def test_declares_its_gaps(self):
        caps = self.provider.capabilities
        self.assertFalse(caps.bid_ask_candles)
        self.assertFalse(caps.price_stream)
        self.assertFalse(caps.transaction_stream)
        self.assertFalse(caps.order_expiry)
        self.assertFalse(caps.synchronous_orders)
        self.assertTrue(caps.dated_history)
        self.assertTrue(caps.distinct_stop_limit)

    def test_spread_setting_grants_bid_ask(self):
        p = IBProvider(setup=settings_stub(IB_SPREAD=0.0002))
        self.assertTrue(p.capabilities.bid_ask_candles)

    def test_spread_setting_changes_nothing_else(self):
        """
        The declaration is copied per instance before being changed. Mutating
        the class attribute instead would have one configured session grant
        bid/ask to every other provider object in the process.
        """
        p = IBProvider(setup=settings_stub(IB_SPREAD=0.0002))
        self.assertTrue(p.capabilities.close_reason)
        self.assertFalse(p.capabilities.order_expiry)
        self.assertFalse(IBProvider.capabilities.bid_ask_candles)
        self.assertFalse(
            IBProvider(setup=settings_stub()).capabilities.bid_ask_candles)

    def test_granularity_translates(self):
        self.assertEqual(self.provider.granularity('H1'), '1h')
        self.assertEqual(self.provider.granularity('M5'), '5min')

    def test_granularity_refuses_what_the_route_lacks(self):
        from parity_deriva.lib.ib import IBError
        with self.assertRaises(IBError):
            self.provider.granularity('S5')


class NoProviderIsASubsetTest(unittest.TestCase):
    """
    The point of declaring capabilities rather than ranking brokers.

    If one provider were strictly poorer than another, a wiring could be
    written against the poorest and run anywhere. None of them is, so the
    declaration is the only thing that can be reasoned about - and this test
    is here so that a future edit which quietly makes one provider a subset of
    another has to say so.
    """

    def test_each_of_the_three_has_something_the_others_do_not(self):
        setup = settings_stub()
        etoro = providers.get_provider('etoro', setup=setup).capabilities
        ig = providers.get_provider('ig', setup=setup).capabilities
        ib = providers.get_provider('ib', setup=setup).capabilities

        # IG serves two sides of a candle; IB does not
        self.assertTrue(ig.bid_ask_candles)
        self.assertFalse(ib.bid_ask_candles)
        # IB names the leg that closed a trade; IG does not
        self.assertTrue(ib.close_reason)
        self.assertFalse(ig.close_reason)
        # IG expires an order at the broker; neither of the others does
        self.assertTrue(ig.order_expiry)
        self.assertFalse(ib.order_expiry)
        self.assertFalse(etoro.order_expiry)
        # eToro cannot tell a STOP from a LIMIT; both of the others can
        self.assertFalse(etoro.distinct_stop_limit)
        self.assertTrue(ig.distinct_stop_limit)
        self.assertTrue(ib.distinct_stop_limit)


class BaseProviderTest(unittest.TestCase):

    def test_missing_factories_raise_instead_of_returning_none(self):
        """
        A wiring that registered None on the engine would run with a leg
        silently missing.
        """
        bare = Provider(setup=settings_stub())
        for factory in ('candles', 'prices', 'transactions', 'execution'):
            with self.assertRaises(CapabilityError):
                getattr(bare, factory)()

    def test_simulator_is_shared_by_both_providers(self):
        """
        The shadow reads candles and orders off the bus and knows nothing
        about any of the APIs, so it is the same class whichever broker is
        behind it.
        """
        from parity_deriva.backtest.oanda import OANDABacktester
        for name in ('oanda', 'etoro', 'ig', 'ib'):
            sim = providers.get_provider(name, setup=settings_stub()).simulator()
            self.assertIsInstance(sim, OANDABacktester)


if __name__ == '__main__':
    unittest.main()
