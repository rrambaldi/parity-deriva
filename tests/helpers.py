"""
Shared fixtures for the parity_deriva test suite.

Was: a characterisation suite. It pinned down what the code did, quirks
     included, so the defects could be found and then fixed without guessing
     at the original intent. A failing test was the intended signal that a
     quirk had been addressed.
Now: those defects are fixed and the assertions describe the intended
     behaviour, so a failing test is a regression. Tests that used to pin a
     defect carry a Was/Now note, which is why a few of them are oddly
     specific - about filling on touch, say, or about which bar a child order
     becomes live on.
"""

import datetime
import json
import logging
import os
import shutil
import tempfile
import unittest
from unittest import mock

from parity_deriva.trading.handler import ExecutionHandler


# Most components fetch 'parity_deriva.trading.trading' by name; a few use
# __name__. Silence the whole tree so the suite output stays readable.
# assertLogs() raises the level on the specific logger it watches, so the
# tests that assert on log output still work.
for _name in ('parity_deriva', 'parity_deriva.trading.trading'):
    _log = logging.getLogger(_name)
    _log.addHandler(logging.NullHandler())
    _log.setLevel(logging.CRITICAL)
    _log.propagate = False


OANDA_TIME = "%Y-%m-%dT%H:%M:%S.%f000Z"
T0 = datetime.datetime(2017, 2, 1, 10, 0, 0)


def oanda_time(dt):
    """Render a datetime the way the OANDA v3 API does."""
    return dt.strftime(OANDA_TIME)


def candle_dict(dt=T0, o=11700.0, h=11706.0, l=11694.0, c=11703.0,
                volume=10, complete=True, spread=0.2):
    """
    One OANDA v3 candle with the ABM price triplet. ask is mid + spread/2*2
    and bid is mid - the same, so ask/bid/mid stay distinguishable in asserts.
    """
    def px(delta):
        return {"o": "%.1f" % (o + delta), "h": "%.1f" % (h + delta),
                "l": "%.1f" % (l + delta), "c": "%.1f" % (c + delta)}
    return {"time": oanda_time(dt), "volume": volume, "complete": complete,
            "ask": px(spread), "bid": px(-spread), "mid": px(0.0)}


def bull_candle(dt=T0, base=11700.0, **kw):
    """Close above open."""
    return candle_dict(dt, o=base, h=base + 6, l=base - 6, c=base + 3, **kw)


def bear_candle(dt=T0, base=11700.0, **kw):
    """Close below open."""
    return candle_dict(dt, o=base + 3, h=base + 6, l=base - 6, c=base, **kw)


def candle_series(directions, start=T0, step=datetime.timedelta(minutes=1),
                  instrument="DE30_EUR", granularity="M1"):
    """
    Build a list of CandleEvent from a sequence of booleans (True = bullish),
    already tagged with instrument/granularity the way a data source does.
    """
    from parity_deriva.event.event import CandleEvent
    out = []
    for i, up in enumerate(directions):
        maker = bull_candle if up else bear_candle
        ev = CandleEvent(maker(start + i * step, base=11700.0 + i))
        ev.instrument = instrument
        ev.granularity = granularity
        out.append(ev)
    return out


def candles_response(n, start=T0, step=datetime.timedelta(minutes=1),
                     instrument="DE30_EUR", granularity="M1", complete=True):
    """The JSON body of GET /v3/instruments/{pair}/candles."""
    return {"instrument": instrument, "granularity": granularity,
            "candles": [candle_dict(start + i * step, o=11700.0 + i,
                                    h=11706.0 + i, l=11694.0 + i,
                                    c=11703.0 + i, volume=10 + i,
                                    complete=complete)
                        for i in range(n)]}


class Recorder(object):
    """Stands in for the Engine as an event sink."""

    def __init__(self):
        self.events = []

    def put(self, event):
        self.events.append(event)

    # convenience accessors used all over the suite
    def kinds(self):
        return [str(e) for e in self.events]

    def of(self, kind):
        return [e for e in self.events if str(e) == kind]

    def statuses(self):
        return [e.status for e in self.of('STATUS')]


class FakeResponse(object):
    """Minimal requests.Response stand-in."""

    def __init__(self, payload=None, status=200, lines=None, text=None):
        self.status_code = status
        self.headers = {}
        if text is not None:
            self.text = text
        else:
            self.text = json.dumps(payload) if payload is not None else ""
        self._lines = lines or []

    def iter_lines(self, *args, **kwargs):
        for line in self._lines:
            yield line.encode("utf-8") if isinstance(line, str) else line


class FakeRequests(object):
    """
    Replacement for the `requests` module as the data handlers use it:
    requests.packages.urllib3.disable_warnings(), requests.Request(...),
    requests.Session().send(...). Records every prepared request so tests can
    assert on URL, headers and query parameters.
    """

    def __init__(self, *responses, **kwargs):
        self.responses = list(responses)
        self.raise_on_send = kwargs.get('raise_on_send', False)
        self.sent = []
        self.packages = mock.MagicMock()
        self.packages.urllib3.disable_warnings = lambda: None
        self.exceptions = mock.MagicMock()

    # -- requests.Request ------------------------------------------------
    def Request(self, method, url, headers=None, params=None, **kwargs):
        # data/json are recorded too: the eToro client sends its order bodies
        # this way, and a test that could only see the URL would be unable to
        # assert on what was actually ordered.
        self.sent.append({"method": method, "url": url,
                          "headers": dict(headers or {}),
                          "params": dict(params or {}),
                          "data": kwargs.get("data"),
                          "json": kwargs.get("json")})
        req = mock.MagicMock()
        req.prepare.return_value = "PREPARED"
        return req

    def body(self, index=-1):
        """The JSON body of a recorded request, parsed."""
        raw = self.sent[index].get("data")
        return json.loads(raw) if raw else None

    # -- requests.Session ------------------------------------------------
    def Session(self):
        outer = self

        class _Session(object):
            closed = False

            def send(self, prepared, **kwargs):
                if outer.raise_on_send:
                    raise IOError("network disabled in tests")
                if not outer.responses:
                    raise AssertionError("more requests than canned responses")
                if len(outer.responses) == 1:
                    return outer.responses[0]
                return outer.responses.pop(0)

            def close(self):
                self.closed = True

        return _Session()

    @property
    def last(self):
        return self.sent[-1]


class FakeHTTPSConnection(object):
    """
    Replacement for http.client.HTTPSConnection, used by the OANDA execution
    handler. `calls` is class level so a test can read what was sent without
    holding a reference to the instance the handler created.
    """

    calls = []
    response_body = b"{}"

    def __init__(self, host, *args, **kwargs):
        FakeHTTPSConnection.calls.append({"host": host})

    def request(self, method, url, body=None, headers=None):
        FakeHTTPSConnection.calls[-1].update(
            method=method, url=url, body=body, headers=dict(headers or {}))

    def getresponse(self):
        resp = mock.MagicMock()
        resp.read.return_value = FakeHTTPSConnection.response_body
        return resp

    @classmethod
    def reset(cls, body=b"{}"):
        cls.calls = []
        cls.response_body = body

    @classmethod
    def last(cls):
        return cls.calls[-1]


class TempDirCase(unittest.TestCase):
    """Base case giving each test its own DATA_DIR / LOG_DIR."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="parity_deriva-test-")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        from parity_deriva.etc import settings
        self.settings = mock.MagicMock()
        self.settings.DATA_DIR = self.tmpdir
        self.settings.LOG_DIR = self.tmpdir
        self.settings.API_DOMAIN = settings.API_DOMAIN
        self.settings.STREAM_DOMAIN = settings.STREAM_DOMAIN
        self.settings.ACCESS_TOKEN = "TESTTOKEN"
        self.settings.ACCOUNT_ID = "001-TEST-000"
        self.settings.API_VERSION = '3'
        self.settings.EQUITY = settings.EQUITY

    def path(self, *parts):
        return os.path.join(self.tmpdir, *parts)


class MovingStopStrategy(ExecutionHandler):
    """
    A stand-in for a strategy whose exit is a stop that has to be moved.

    tests/live_test.py asks what the runner registers for one of those, and
    the answer has to hold for any such strategy rather than for a particular
    one - the strategies that exit this way are not all shipped with this
    repository, and a test naming one would pass or fail depending on which
    checkout it ran in. It signals nothing: what is under test is the wiring
    around it, not what it would trade.
    """

    def __init__(self, **args):
        self.logger = logging.getLogger('parity_deriva.trading.trading')
        self._set(args, 'pairs', [])
        self._set(args, 'granularity', 'D')

    def execute_event(self, event):
        return None
