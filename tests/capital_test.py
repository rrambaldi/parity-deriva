"""
lib/capital.py: IG's requests and answers, translated at the crossing.
"""

import types
import unittest
from unittest import mock

from parity_deriva.lib import capital, ig
from parity_deriva.trading import providers


def setup(**over):
    base = dict(DOMAIN='practice', CAPITAL_API_KEY='KEY', CAPITAL_IDENTIFIER='me@x',
                CAPITAL_API_PASSWORD='pw', CAPITAL_ACCOUNT_ID='', CAPITAL_API_DOMAIN='',
                CAPITAL_INSTRUMENTS={'EUR_USD': {'epic': 'EURUSD', 'expiry': '-',
                                                 'currency': 'USD', 'precision': 5,
                                                 'contractSize': 1, 'sizeStep': 100}},
                CAPITAL_CURRENCY=None, IG_VERIFY_TLS=True)
    base.update(over)
    return types.SimpleNamespace(**base)


class CapitalAPITest(unittest.TestCase):

    def setUp(self):
        self.api = capital.CapitalAPI(setup=setup())
        self.sent = []

        def send(api, method, key, parts=(), params=None, body=None,
                 authenticated=True, override=None):
            self.sent.append((method, key, tuple(parts), params, body, override))
            return 200, self.answer, {}
        self.answer = None
        patcher = mock.patch.object(ig.IGAPI, 'send', send)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_host_the_paths_and_the_key(self):
        self.assertEqual(self.api.host, 'demo-api-capital.backend-capital.com')
        self.assertEqual(self.api.url('prices', 'EURUSD'),
                         'https://demo-api-capital.backend-capital.com/api/v1/prices/EURUSD')
        headers = self.api.headers('positions')
        self.assertEqual(headers['X-CAP-API-KEY'], 'KEY')
        self.assertNotIn('X-IG-API-KEY', headers)
        self.assertNotIn('Version', headers)

    def test_an_order_body_is_capitals(self):
        self.api.send('POST', 'create_order', body={
            'epic': 'EURUSD', 'expiry': '-', 'direction': 'SELL', 'size': 5700,
            'currencyCode': 'USD', 'forceOpen': True, 'guaranteedStop': False,
            'dealReference': 'REF', 'stopLevel': 1.142, 'limitLevel': 1.138,
            'type': 'LIMIT', 'level': 1.14, 'timeInForce': 'GOOD_TILL_DATE',
            'goodTillDate': '2026/09/24 08:00:00'})
        body = self.sent[0][4]
        self.assertEqual(body, {'epic': 'EURUSD', 'direction': 'SELL', 'size': 5700,
                                'guaranteedStop': False, 'stopLevel': 1.142,
                                'profitLevel': 1.138, 'type': 'LIMIT', 'level': 1.14,
                                'goodTillDate': '2026-09-24T08:00:00'})

    def test_a_close_is_a_delete_of_the_deal(self):
        self.api.send('POST', 'close_position', body={'dealId': 'D1', 'size': 100,
                                                      'direction': 'BUY'}, override='DELETE')
        method, key, parts, _params, body, override = self.sent[0]
        self.assertEqual((method, key, parts, body, override),
                         ('DELETE', 'close_position', ('D1',), None, None))

    def test_answers_read_as_igs(self):
        self.answer = {'positions': [{'position': {'dealId': 'D1', 'profitLevel': 1.1}}]}
        _s, payload, _h = self.api.send('GET', 'positions')
        self.assertEqual(payload['positions'][0]['position']['limitLevel'], 1.1)
        self.answer = {'activities': [{'date': '2026-09-24T04:00:00', 'dateUTC': '2026-09-24T02:00:00',
                                       'details': {'actions': [{'actionType': 'POSITION_CLOSED',
                                                                'dealId': 'D1'}]}}]}
        _s, payload, _h = self.api.send('GET', 'activity', params={
            'from': '2020-01-01T00:00:00', 'detailed': 'true', 'pageSize': 50})
        row = payload['activities'][0]
        self.assertEqual(row['date'], '2026-09-24T02:00:00')
        self.assertEqual(row['details']['actions'][0]['affectedDealId'], 'D1')
        params = self.sent[-1][3]
        self.assertNotIn('pageSize', params)
        self.assertGreater(params['from'], '2020-01-01T00:00:00')


class CapitalProviderTest(unittest.TestCase):

    def test_igs_handlers_with_capitals_settings(self):
        provider = providers.get_provider('capital', setup=setup())
        self.assertTrue(provider.configured())
        self.assertTrue(provider.capabilities.expiring_orders)
        handler = provider.execution(sized=True)
        self.assertIsInstance(handler.api, capital.CapitalAPI)
        # units, rounded to the market's step of 100
        self.assertEqual(handler.size('EUR_USD', -5723.4), 5700)
        self.assertEqual(ig.epic('EUR_USD', provider.setup), 'EURUSD')


if __name__ == '__main__':
    unittest.main()
