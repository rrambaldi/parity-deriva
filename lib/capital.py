"""
Capital.com: IG's API under other names, and the translation between them.

Capital.com's REST API is IG's shape - a login that answers with CST and
X-SECURITY-TOKEN headers, epics, /prices/{epic} with bid and ask per bar,
/positions, /workingorders, /confirms/{dealReference}, /history/activity -
with a handful of names changed. So this is not a fourth copy of a broker:
data/ig.py and execution/ig.py are driven as they are, through CapitalAPI,
and the differences are all here, each where a request or an answer crosses.

Measured on the demo account on 2026-09-24:

* hosts demo-api-capital.backend-capital.com / api-capital.backend-capital.com,
  every path under /api/v1, the key in X-CAP-API-KEY and no Version header;
* a bracket's target is profitLevel (IG: limitLevel), and an action in the
  activity names its deal as dealId (IG: affectedDealId);
* goodTillDate is ISO, YYYY-MM-DDTHH:MM:SS in UTC (IG: yyyy/MM/dd hh:mm:ss);
* a position is closed by DELETE /positions/{dealId}, with no body;
* EURUSD is dealt in units, minimum and step 100 - see CAPITAL_INSTRUMENTS;
* price rows are IG's row for row, snapshotTimeUTC included, and there is
  no weekly allowance to track.

The settings are CAPITAL_*, read through CapitalSetup under the IG_* names
the IG handlers ask for, so neither side needs to know the other exists.
"""

import datetime

from parity_deriva.etc import settings
from parity_deriva.lib import ig

HOSTS = {'practice': 'demo-api-capital.backend-capital.com',
         'real': 'api-capital.backend-capital.com'}
BASE = '/api/v1'

#: IG's route keys, as Capital.com spells them
ROUTES = {
    'session': '/session', 'session_v3': '/session', 'logout': '/session',
    'switch': '/session', 'accounts': '/accounts', 'search': '/markets',
    'market': '/markets/%s', 'prices': '/prices/%s', 'positions': '/positions',
    'open_position': '/positions', 'close_position': '/positions/%s',
    'workingorders': '/workingorders', 'create_order': '/workingorders',
    'cancel_order': '/workingorders/%s', 'confirm': '/confirms/%s',
    'transactions': '/history/transactions', 'activity': '/history/activity',
}

#: what each order route accepts; the rest of IG's body (expiry,
#: currencyCode, forceOpen, a client dealReference, timeInForce) is IG's
BODY = {
    'open_position': ('epic', 'direction', 'size', 'guaranteedStop',
                      'stopLevel', 'profitLevel'),
    'create_order': ('epic', 'direction', 'size', 'level', 'type', 'goodTillDate',
                     'guaranteedStop', 'stopLevel', 'profitLevel'),
    'switch': ('accountId',),
}

#: the history routes answer for at most a day back
HISTORY_REACH = datetime.timedelta(days=1)


class CapitalSetup(object):
    """The project's settings, with CAPITAL_* standing in for IG_*."""

    NAMES = {'IG_API_KEY': 'CAPITAL_API_KEY', 'IG_IDENTIFIER': 'CAPITAL_IDENTIFIER',
             'IG_PASSWORD': 'CAPITAL_API_PASSWORD', 'IG_ACCOUNT_ID': 'CAPITAL_ACCOUNT_ID',
             'IG_INSTRUMENTS': 'CAPITAL_INSTRUMENTS', 'IG_API_DOMAIN': 'CAPITAL_API_DOMAIN',
             'IG_CURRENCY': 'CAPITAL_CURRENCY'}

    def __init__(self, setup=None):
        self._setup = setup if setup is not None else settings

    def __getattr__(self, name):
        if name == 'IG_SESSION_VERSION':
            return 2
        if name in self.NAMES:
            return getattr(self._setup, self.NAMES[name], None)
        return getattr(self._setup, name)


def setupOf(setup):
    return setup if isinstance(setup, CapitalSetup) else CapitalSetup(setup)


def rename(value, old, new):
    """`old` keys as `new`, all the way down an answer."""
    if isinstance(value, dict):
        return dict((new if k == old else k, rename(v, old, new)) for k, v in value.items())
    if isinstance(value, list):
        return [rename(v, old, new) for v in value]
    return value


class CapitalAPI(ig.IGAPI):

    def __init__(self, **args):
        args['setup'] = setupOf(args.get('setup'))
        ig.IGAPI.__init__(self, **args)
        self.host = (getattr(self.setup, 'IG_API_DOMAIN', '')
                     or HOSTS['practice' if self.demo else 'real'])

    def route(self, key, *parts):
        if key not in ROUTES:
            raise ig.IGError("unknown Capital.com route %r" % key)
        path = ROUTES[key]
        if parts:
            path = path % tuple(str(p) for p in parts)
        return BASE + path, 1

    def headers(self, key, authenticated=True):
        out = ig.IGAPI.headers(self, key, authenticated)
        out['X-CAP-API-KEY'] = out.pop('X-IG-API-KEY')
        out.pop('Version', None)
        return out

    def noteAllowance(self, payload):
        """Capital.com meters no history: nothing to note."""
        return True

    def send(self, method, key, parts=(), params=None, body=None,
             authenticated=True, override=None):
        if key == 'close_position' and body is not None:
            # IG closes with a body on a tunnelled DELETE; this closes the
            # deal the path names
            method, parts, body, override = 'DELETE', (body['dealId'],), None, None
        if body is not None and key in BODY:
            body = rename(body, 'limitLevel', 'profitLevel')
            if body.get('goodTillDate'):
                body['goodTillDate'] = datetime.datetime.strptime(
                    body['goodTillDate'], '%Y/%m/%d %H:%M:%S').strftime('%Y-%m-%dT%H:%M:%S')
            body = dict((k, v) for k, v in body.items() if k in BODY[key])
        if params and key in ('activity', 'transactions'):
            params = dict((k, v) for k, v in params.items() if k in ('from', 'to', 'detailed'))
            if params.get('from'):
                reach = (datetime.datetime.utcnow() - HISTORY_REACH).strftime('%Y-%m-%dT%H:%M:%S')
                params['from'] = max(params['from'], reach)
        status, payload, headers = ig.IGAPI.send(self, method, key, parts, params, body,
                                                 authenticated, override)
        payload = rename(payload, 'profitLevel', 'limitLevel')
        if key == 'activity' and payload:
            for row in payload.get('activities') or []:
                # the account's clock is date; the project's is UTC
                row['date'] = row.get('dateUTC') or row.get('date')
                for action in (row.get('details') or {}).get('actions') or []:
                    action.setdefault('affectedDealId', action.get('dealId'))
        return status, payload, headers

    def call(self, method, key, parts=(), params=None, body=None, override=None):
        """IG's call, with any 401 read as the session gone: it lapses idle."""
        if not self.loggedIn() and not self.login():
            return None, None
        status, payload, _headers = self.send(method, key, parts, params, body,
                                              override=override)
        if status == 401 and self.login():
            status, payload, _headers = self.send(method, key, parts, params, body,
                                                  override=override)
        return status, payload
