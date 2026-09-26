"""
The service as an MCP server, for an AI assistant of the user's own - claude.ai,
Claude Desktop, ChatGPT, Gemini, or an editor such as Cursor or VS Code - to
read the strategies and the data, write strategies of its own and backtest
them. It cannot trade and it cannot enable a strategy: those stay on the pages.

The protocol is MCP's Streamable HTTP in its plainest form: every message is
a POST to /mcp and every answer is one JSON body, no event stream and no
session. Who may post is web/oauth.py. A strategy it writes is a draft
(strategy/uploaded.py) and every backtest it asks for runs in a child process
with a ceiling on memory and time (web/sandbox.py).

Behind a proxy that serves the service under a path, the addresses the
assistant is sent to must carry that path: set PARITY_DERIVA_PUBLIC_URL, or
have the proxy send X-Forwarded-Prefix. The assistants call from their own
servers, so these routes must be reachable without a client certificate while
every other route keeps its own protection. With nginx and the service at
/parity/:

	location = /parity/mcp { proxy_pass http://127.0.0.1:8731/mcp; ... }
	location ^~ /parity/oauth/ { proxy_pass http://127.0.0.1:8731/oauth/; ... }
	location ^~ /parity/.well-known/ { proxy_pass http://127.0.0.1:8731/.well-known/; ... }
	location = /.well-known/oauth-authorization-server/parity {
		proxy_pass http://127.0.0.1:8731/.well-known/oauth-authorization-server; ... }
	location = /.well-known/oauth-protected-resource/parity/mcp {
		proxy_pass http://127.0.0.1:8731/.well-known/oauth-protected-resource; ... }

each with `proxy_set_header X-Forwarded-Prefix /parity;` and the usual Host
and X-Forwarded-Proto. The connector's address is then https://host/parity/mcp.
"""

import ast
import base64
import datetime
import functools
import gzip
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pandas as pd

from parity_deriva.backtest import ledger
from parity_deriva.data import calendar, market
from parity_deriva.strategy import uploaded
from parity_deriva.web import livesessions, oauth, sandbox, servers

PROTOCOLS = ('2025-11-25', '2025-06-18', '2025-03-26')

GUIDE = """\
parity-deriva backtests forex strategies on stored bid/ask candles. Through this
server you can read the strategies and the data, write strategies and
indicators of your own as drafts, and backtest them. You cannot trade and you
cannot enable a strategy or an indicator: the user does that on the settings
page, after reading your code.

A strategy is one Python file with exactly one class, a subclass of
parity_deriva.strategy.H4.H4 (read it with get_source
"parity_deriva.strategy.H4"; a complete example is get_source
"H401-PULLBACK-EMA"; parity_deriva.strategy.M15.M15 is a richer base for
intraday entries). On the class:
- INSTRUMENT and GRANULARITY: what it is meant for, e.g. 'EUR_USD' and 'H1'.
- DESCRIPTION: its rules in plain words, for the person who reads the code.
- setup(self, args): read every tunable number with
  self._set(args, 'name', default). Those numbers are the parameters
  run_backtest accepts; PARAM_HELP = {'name': 'meaning'} explains them.
- series(self): the reading state of one instrument, usually
  parity_deriva.lib.streaming.Series(ema=(50, 200), sma=(), stdev=(), atr=14,
  rsi=None) - read that module for the rest (Swings, Values, Daily, Hourly,
  Weekly).
- signal(self, state, candle): called on every closed bar, after state has
  taken it. Return None, or (side, stop, target): side 1 long or -1 short,
  stop and target as prices. candle.mid, candle.bid and candle.ask are dicts
  with 'o', 'h', 'l', 'c'; candle.time is a naive UTC datetime. The entry is at
  market on the next bar's open with the stop and target as a bracket, and
  one position is open at a time.
Imports are limited to the numbers and the parts of this project a strategy
is made of (the refusal lists them); files, network, processes, eval/exec,
getattr/setattr and double-underscore attributes are refused. The TAG becomes
the strategy's name. The class's name has to be its own: one another strategy's
class has, built-in or yours, is refused (your versions of one name share it).
A version the same as the one before it is refused too.

An indicator of your own, when list_helpers has none that fits, is one file
with exactly one class, fed a candle at a time as
parity_deriva.lib.streaming.Series is (you may build it on a Series):
- add(self, candle): one closed bar, oldest first - the candle signal() gets.
- value(self): None while it warms up, then a number, or a tuple of numbers
  named by LINES, e.g. LINES = ('upper', 'middle', 'lower').
- PANEL = False for a price, drawn on the candles; PANEL = True for a number
  of its own scale, drawn in the strip under them - an ATR, an RSI, a 0/1.
- __init__'s arguments are its parameters, each with a number for a default;
  the class's docstring says what it computes.
submit_indicator runs it over stored candles before it saves it, and refuses
a value that is not a number, no value at all, two copies of it that disagree
(state kept on the class), a price far off the candles, and more than 1 ms a
candle. A strategy takes it by its code, the version written out:
    from parity_deriva.strategy.uploaded import indicator
    Keltner = indicator('KELTNER 1')
and has the chart draw it by listing it in INDICATORS, the keys but 'label'
its arguments: INDICATORS = ({'indicator': 'KELTNER 1', 'period': 20},). The
user enables an indicator before any strategy that takes it.

Work in this order: get_news first - what changed in this project's code
and asks you to do about it, which comes before anything else you were asked;
list_data, list_strategies and list_helpers to see what exists; submit_indicator for an indicator that is missing; submit_strategy to save a version of a draft (it is imported and built
at once, and an error comes back with its traceback); run_backtest on a year or
two first, then widen. Every backtest is saved, and its link opens it on the
user's page; list_runs and get_run read the saved ones back, the user's sets
of runs too.

Versions: submit_strategy never replaces anything. The first submit of a name
is its version 1, "MY-EMA 1"; every submit of that name after it is the next
version, and the ones before stay as they were. That code is the strategy's
name everywhere - run_backtest, the page, the runs; a bare name means its
newest version. The server ends the file with a PARITY_DERIVA line - the
version, and the version of the server it was written on. Leave it: it is
rewritten on every submit.

The public repository of strategies and indicators: when the user wants one
of yours in it, call propose_public with its code and a note of what it does
and how it tested. Nothing is sent from here - the user reads it and opens the
pull request from the settings page, once that version is enabled. A
strategy's pull request brings the indicators it takes along.

Rules:
1. Build on what exists. Before writing an indicator, a candle pattern, a
   level, a filter or any helper, call list_helpers: what is there you import
   and use, and you do not copy its code into the strategy. Read one with
   get_source when its line is not enough. A built-in strategy close to yours
   is a base to subclass, not a file to copy.
2. A missing indicator is an indicator of its own (submit_indicator), not
   code inside the strategy: so it is read and enabled once, drawn on the
   chart, and taken by the next strategy too. What is not an indicator - a
   function the helpers lack, data the project does not have, a change to how
   the backtest fills - is a feature request: request_feature with what it
   does, what it takes and gives, and the strategy that needs it, and tell the
   user the strategy waits for it; list_helpers shows the requests already
   open, so do not file one twice.
3. Test on years the rules were not tuned on: develop and tune on a first
   stretch (up to the end of 2021, say), then one run on the years after with
   the parameters unchanged. Give both, and say so when the later years are
   worse.
4. Report honestly: always the trades, the net, the max drawdown and the
   profit factor. Under about 100 trades say it is too few to judge, and never
   call a strategy profitable off one window.
5. A grid belongs to the page, not to you: do not call run_backtest in a loop
   over many combinations - the sandbox runs one backtest at a time on a small
   server. Try a few by hand; for a grid, give the user the values to try on
   the simulate page.
6. Pips come from pipSize(instrument) in parity_deriva.lib.utils, never from
   0.0001 written in: a JPY pair's pip is 0.01. A stop or a target is not
   narrower than a few spreads.
7. Every number that sets a rule is a parameter: read with self._set, explained
   in PARAM_HELP, about five at most, and no bare numbers in signal().
8. Talk with the user in the user's language; the code, the names, DESCRIPTION
   and PARAM_HELP are in English."""

#: the modules list_helpers lays out: what a strategy is built from
HELPERS = ('parity_deriva.lib.streaming', 'parity_deriva.lib.indicators',
		   'parity_deriva.lib.utils', 'parity_deriva.strategy.H4', 'parity_deriva.strategy.M15')

#: a feature request's limits: an assistant is not a way to fill the disk
REQUEST_TITLE, REQUEST_TEXT, REQUESTS_OPEN = 120, 4000, 100

#: the news for the assistants, posted with a code update (postNews)
NEWS_TITLE, NEWS_TEXT, NEWS_OPEN = 120, 8000, 50

#: run_backtest's options: the page's form fields for the account's rules
OPTIONS = {
	'balance': ('number', "starting capital; the account's default when absent"),
	'risk': ('number', "percent of the capital lost at the stop, e.g. 1; absent is a fixed size"),
	'units': ('integer', "the fixed size when risk is absent"),
	'maxStop': ('number', "widest stop accepted, in pips"),
	'maxBars': ('integer', "close a trade open this many bars"),
	'slScale': ('number', "multiply the stop's distance"),
	'tpScale': ('number', "multiply the target's distance"),
	'session': ('string', "UTC hours signals are taken in, e.g. 07:00-16:00"),
	'intraday': ('boolean', "close what is open at the end of the day"),
	'inverse': ('boolean', "turn every order round"),
	'trailing': ('integer', "0 a stop that never moves, 1 one that follows"),
	'trailProfit': ('boolean', "the target becomes a floor the stop follows"),
	'trailPips': ('number', "how far behind the trailing stop follows, in pips"),
	'newsBefore': ('integer', "minutes before a calendar event to stand aside"),
	'newsAfter': ('integer', "minutes after it"),
	'newsImpacts': ('string', "which events count: high, medium, low, comma separated"),
	'leverage': ('integer', "n for n:1, for the margin report"),
}

#: the trades a backtest's answer carries; the rest are on the page
TRADES_SHOWN = 200

TOOLS = [
	{'name': 'get_news',
	 'description': "What changed in this project's code and what the assistants are asked "
					"to do about it - new helpers, strategies to update. Read it first. "
					"For each strategy a news names: its newest version "
					"and whether one was submitted after the news (updated).",
	 'inputSchema': {'type': 'object', 'properties': {}}},
	{'name': 'list_strategies',
	 'description': "Every strategy that can be backtested: built in, enabled by the user, "
					"or a draft of yours, with its description, what it is meant for and "
					"its parameters.",
	 'inputSchema': {'type': 'object', 'properties': {}}},
	{'name': 'get_source',
	 'description': "The Python source of a strategy or an indicator (by its name) or of a module "
					"a strategy may import, e.g. parity_deriva.strategy.H4 or parity_deriva.lib.streaming.",
	 'inputSchema': {'type': 'object', 'required': ['name'],
					 'properties': {'name': {'type': 'string'}}}},
	{'name': 'list_data',
	 'description': "The instruments with stored candles, and for each granularity how many "
					"bars there are and the first and last day.",
	 'inputSchema': {'type': 'object', 'properties': {}}},
	{'name': 'submit_strategy',
	 'description': "Save a strategy as the next version of a draft: the first submit of a "
					"name is version 1, each one after it the next, and none is ever replaced. "
					"It is checked, imported and built in a sandbox first; nothing is saved if "
					"that fails. " + GUIDE,
	 'inputSchema': {'type': 'object', 'required': ['name', 'source'], 'properties': {
		 'name': {'type': 'string', 'pattern': uploaded.NAME.pattern,
				  'description': "upper case, digits and dashes, e.g. MY-EMA-CROSS - "
								 "without a version: the server adds it"},
		 'source': {'type': 'string', 'description': "the whole Python file"}}}},
	{'name': 'submit_indicator',
	 'description': "Save an indicator as the next version of a draft, as submit_strategy does "
					"a strategy: one class fed a candle at a time, add(candle) and value(), with "
					"PANEL and, for several numbers, LINES - the guide in submit_strategy says "
					"how. It is run over stored candles in the sandbox first and nothing is saved "
					"if a check fails; the answer says what it gave - its warm-up, its range, its "
					"time a candle.",
	 'inputSchema': {'type': 'object', 'required': ['name', 'source'], 'properties': {
		 'name': {'type': 'string', 'pattern': uploaded.NAME.pattern,
				  'description': "upper case, digits and dashes, e.g. KELTNER - without a "
								 "version: the server adds it. Not a strategy's name"},
		 'source': {'type': 'string', 'description': "the whole Python file"}}}},
	{'name': 'list_helpers',
	 'description': "What a strategy is built from, to use rather than write again: the "
					"indicators, series, swings, candle patterns and level helpers of this "
					"project, one line each with its arguments, by module; the indicators "
					"written here (submit_indicator), drafts and enabled; and the feature "
					"requests already open.",
	 'inputSchema': {'type': 'object', 'properties': {}}},
	{'name': 'request_feature',
	 'description': "Ask the user for what a strategy needs that list_helpers does not have "
					"and is not an indicator (that one you write with submit_indicator): a "
					"function, data, a change to the backtest. The user reads the requests on "
					"the settings page.",
	 'inputSchema': {'type': 'object', 'required': ['title', 'description'], 'properties': {
		 'title': {'type': 'string', 'maxLength': REQUEST_TITLE,
				   'description': "what it is, e.g. Keltner channel on the streaming Series"},
		 'description': {'type': 'string', 'maxLength': REQUEST_TEXT,
						 'description': "what it computes (formula or reference), its inputs "
										"and outputs, and how the strategy would call it"},
		 'strategy': {'type': 'string', 'description': "the draft that needs it, if any"}}}},
	{'name': 'run_backtest',
	 'description': "Backtest a strategy on the stored candles and return its report and "
					"trades. Runs in a sandbox with a time limit: start with a year or two.",
	 'inputSchema': {'type': 'object',
					 'required': ['strategy', 'instrument', 'granularity', 'from', 'to'],
					 'properties': {
		 'strategy': {'type': 'string'},
		 'instrument': {'type': 'string', 'description': "e.g. EUR_USD"},
		 'granularity': {'type': 'string', 'description': "e.g. M15, H1, H4, D"},
		 'from': {'type': 'string', 'description': "first day, YYYY-MM-DD"},
		 'to': {'type': 'string', 'description': "last day, YYYY-MM-DD"},
		 'parameters': {'type': 'object', 'additionalProperties': {'type': 'number'},
						'description': "the strategy's own parameters (list_strategies)"},
		 'options': {'type': 'object', 'additionalProperties': False,
					 'description': "the account's rules",
					 'properties': dict((k, {'type': t, 'description': d})
										for k, (t, d) in OPTIONS.items())}}}},
	{'name': 'propose_public',
	 'description': "Propose one version of a strategy or an indicator of yours for the public "
					"repository, when the user asks for it. Nothing is sent: the user reads it "
					"on the settings page and opens the pull request from there, once that "
					"version is enabled.",
	 'inputSchema': {'type': 'object', 'required': ['strategy', 'note'], 'properties': {
		 'strategy': {'type': 'string', 'description': "its code, e.g. MY-EMA 3, or KELTNER 1 "
													  "for an indicator"},
		 'note': {'type': 'string', 'maxLength': REQUEST_TEXT,
				  'description': "what it does and how it tested - windows, trades, net, max "
								 "drawdown: the pull request's text"}}}},
	{'name': 'push_calendar',
	 'description': "For the data scripts, not for an assistant: add events to the economic "
					"calendar, or update them - an event is itself by its id and its newest "
					"push wins, so the outcome pushed after the release replaces the empty one. "
					"Each is one of ForexFactory's events as the calendar scraper keeps them "
					"(id, datetime_utc, time, currency, impact, event, actual, forecast, "
					"previous, revision, detail with its 'Usual Effect'). Up to %d a call: a "
					"month at a time." % 5000,
	 'inputSchema': {'type': 'object', 'required': ['events'], 'properties': {
		 'events': {'type': 'array', 'maxItems': 5000, 'items': {'type': 'object'}}}}},
	{'name': 'push_candles',
	 'description': "For the data scripts, not for an assistant: add candles to an "
					"instrument's store. Both sides, each bar [timestamp (epoch ms of its "
					"open, UTC), open, high, low, close, volume]; up to %d bars a side a call. "
					"Bars the store already holds are kept as they are: a push adds, it does "
					"not correct." % 10000,
	 'inputSchema': {'type': 'object', 'required': ['instrument', 'granularity', 'ask', 'bid'],
					 'properties': {
		 'instrument': {'type': 'string', 'description': "e.g. EUR_USD"},
		 'granularity': {'type': 'string', 'enum': ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D', 'W']},
		 'ask': {'type': 'array', 'maxItems': 10000, 'items': {'type': 'array'}},
		 'bid': {'type': 'array', 'maxItems': 10000, 'items': {'type': 'array'}}}}},
	{'name': 'market_status',
	 'description': "For another parity server, not for an assistant: the series each store "
					"keeps, with bars, from and to (epoch ms), and the calendar's events.",
	 'inputSchema': {'type': 'object', 'properties': {}}},
	{'name': 'pull_candles',
	 'description': "For another parity server, not for an assistant: one stored series' bars "
					"after `after` (epoch ms), up to %d, each side as push_candles takes them; "
					"`more` says there are others after these." % 10000,
	 'inputSchema': {'type': 'object', 'required': ['instrument', 'granularity'], 'properties': {
		 'instrument': {'type': 'string'},
		 'granularity': {'type': 'string', 'enum': ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D', 'W']},
		 'after': {'type': 'integer'}, 'limit': {'type': 'integer', 'maximum': 10000}}}},
	{'name': 'pull_calendar',
	 'description': "For another parity server, not for an assistant: the calendar's events "
					"from `since` (YYYY-MM-DD), %d a call from `start`; `next` is the next "
					"call's start, null at the end." % 5000,
	 'inputSchema': {'type': 'object', 'properties': {
		 'since': {'type': 'string'}, 'start': {'type': 'integer'}}}},
	{'name': 'push_sweep',
	 'description': "For the PC's sync (scripts/sync.py), not for an assistant: a simulation "
					"set as runs/sweeps/<id>.json.gz holds it, or with n one of its runs "
					"(<id>/<n>.json.gz); the base64 of the gzip bytes, %d bytes a call, "
					"part from 0 of parts." % (2 << 20),
	 'inputSchema': {'type': 'object', 'required': ['sweep', 'data'], 'properties': {
		 'sweep': {'type': 'string'}, 'n': {'type': 'integer'},
		 'part': {'type': 'integer'}, 'parts': {'type': 'integer'},
		 'data': {'type': 'string'}, 'commit': {'type': 'string'}}}},
	{'name': 'push_record',
	 'description': "For the archive promoting a form to this real money server, not for an "
					"assistant: the form's record on demo (web/servers.py record). Judged here "
					"by this server's minimums and kept: a session of the form starts here "
					"only once it is ok.",
	 'inputSchema': {'type': 'object', 'required': ['record'], 'properties': {
		 'record': {'type': 'object'}}}},
	{'name': 'push_mix',
	 'description': "For the PC's sync, not for an assistant: a mix, once its sets and their "
					"runs are pushed. The runs are then checked again here (verify on the mix page).",
	 'inputSchema': {'type': 'object', 'required': ['mix'], 'properties': {
		 'mix': {'type': 'object'}}}},
	{'name': 'list_runs',
	 'description': "The simulations saved here, newest first: the sets (every combination of a "
					"strategy's parameters, with the best of them), the single backtests and the "
					"mixes. Open one with get_run.",
	 'inputSchema': {'type': 'object', 'properties': {
		 'limit': {'type': 'integer', 'description': "how many of each, 20 when absent"}}}},
	{'name': 'get_run',
	 'description': "One saved simulation, with a link that opens it on the user's page: a "
					"backtest by its run id; a set by its sweep id - each run's parameters and "
					"figures; or one run of a set, sweep and n, with its figures and its trades.",
	 'inputSchema': {'type': 'object', 'properties': {
		 'run': {'type': 'string', 'description': "a backtest's id, from list_runs"},
		 'sweep': {'type': 'string', 'description': "a set's id, from list_runs"},
		 'n': {'type': 'integer', 'description': "one run of that set"}}}},
	{'name': 'pull_code',
	 'description': "For a Test server's sync (scripts/sync.py pull), not for an assistant: "
					"every strategy and indicator enabled here - the ones somebody read - with "
					"its source and what is kept beside it.",
	 'inputSchema': {'type': 'object', 'properties': {}}},
	{'name': 'live_status',
	 'description': "For the archive, not for an assistant: this trade server's sessions - the "
					"form, the account, the trades open and closed with their P&L, the parity "
					"monitor's findings - and whether it trades demo accounts or real money.",
	 'inputSchema': {'type': 'object', 'properties': {}}},
]


class ToolError(Exception):
	"""A tool call refused: the text goes back to the assistant."""


def web():
	# the service imports this module; this one needs its functions only
	# when a tool runs
	from parity_deriva.web import service
	return service


def dataDir(service):
	return service.setup.DATA_DIR


def day(ms):
	return None if ms is None else datetime.datetime.fromtimestamp(
		ms / 1000.0, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M')


def meta(path):
	"""What is kept beside a strategy's file, and its name and version read off the file's."""
	try:
		with open(path[:-3] + '.json') as handle:
			found = json.load(handle)
	except (OSError, ValueError):
		found = {}
	m = uploaded.FILE.fullmatch(os.path.basename(path))
	if m:
		found.update(family=m.group(1), version=int(m.group(2)))
	return found


def keepMeta(path, found):
	with open(path[:-3] + '.json.tmp', 'w') as handle:
		json.dump(dict((k, v) for k, v in found.items() if k not in ('family', 'version')), handle)
	os.replace(path[:-3] + '.json.tmp', path[:-3] + '.json')


@functools.lru_cache(maxsize=1)
def serverVersion():
	"""
	What a strategy is written on: VERSION, raised by hand when what a
	strategy builds on changes, and the commit the service runs - e.g.
	1.0.0+4483392, and .dirty after it for a tree with changes not committed.
	Once per process: a restart (scripts/web.py re-execs) reads it again.
	"""
	top = os.path.join(sandbox.TOP, 'parity_deriva')
	try:
		with open(os.path.join(top, 'VERSION')) as handle:
			version = handle.read().strip()
	except OSError:
		version = '0.0.0'
	try:
		commit = subprocess.run(['git', '-C', top, 'rev-parse', '--short=7', 'HEAD'],
								capture_output=True, text=True, timeout=10).stdout.strip()
		dirty = subprocess.run(['git', '-C', top, 'diff', '--quiet', 'HEAD'],
							   capture_output=True, timeout=10).returncode == 1
	except (OSError, subprocess.SubprocessError):
		commit, dirty = '', False
	return version + ('+%s%s' % (commit, '.dirty' if dirty else '') if commit else '')


def written(service, kind):
	"""code -> path of every version of `kind` written here, drafts and enabled."""
	return dict(uploaded.drafts(dataDir(service), kind), **uploaded.enabled(dataDir(service), kind))


def kindOf(service, code):
	"""What `code` is: 'indicators' for one of the indicators, else 'strategies'."""
	return 'indicators' if code in written(service, 'indicators') else 'strategies'


def takers(service, code):
	"""The strategies written here that take the indicator `code`: (code, state) each."""
	out = []
	for state, held in (('draft', uploaded.drafts(dataDir(service))),
						('enabled', uploaded.enabled(dataDir(service)))):
		for name, path in held.items():
			with open(path) as handle:
				if code in uploaded.used(handle.read()):
					out.append((name, state))
	return out


def resolve(service, name):
	"""A strategy's or an indicator's code: `name` itself, or the newest version of a name of yours."""
	held = dict(uploaded.drafts(dataDir(service)), **uploaded.enabled(dataDir(service)))
	if name in held or name in web().strategies() or name in written(service, 'indicators'):
		return name
	return uploaded.latest(dataDir(service), name) \
		or uploaded.latest(dataDir(service), name, 'indicators') or name


# ---------------------------------------------------------------- the tools

def listStrategies(service, args):
	w = web()
	enabled = uploaded.enabled(dataDir(service))
	descriptions, defaults = w.descriptions(), w.defaults()
	out = [dict({'name': name, 'state': 'enabled' if name in enabled else 'built in',
				 'description': descriptions.get(name),
				 'parameters': list(w.handlerFields(name))}, **defaults.get(name, {}),
				**(meta(enabled[name]) if name in enabled else {}))
		   for name in w.strategies()]
	for name, path in uploaded.drafts(dataDir(service)).items():
		out.append(dict(meta(path), name=name, state='draft'))
	return {'strategies': out}


def getSource(service, args):
	name = resolve(service, str(args.get('name') or ''))
	for kind in uploaded.KINDS:
		held = written(service, kind)
		if name in held:
			with open(held[name]) as handle:
				return {'name': name, 'source': handle.read()}
	module = ledger.STRATEGIES.get(name, (name,))[0]
	if not module.startswith('parity_deriva.') or not uploaded.allowedModule(module):
		raise ToolError("%s: no source to show. Strategies are named by list_strategies; "
						"modules are the parity_deriva ones a strategy may import" % name)
	path = os.path.join(sandbox.TOP, *module.split('.')) + '.py'
	with open(path) as handle:
		return {'name': name, 'module': module, 'source': handle.read()}


def listData(service, args):
	return {'instruments': [
		{'instrument': row['instrument'], 'granularities': [
			dict(g, **{'from': day(g['from'])[:10], 'to': day(g['to'])[:10]})
			for g in row['granularities']]}
		for row in service.instruments()]}


#: the most a push carries: a call stays under nginx's 4 MB for /parity/mcp
PUSH_EVENTS = 5000
PUSH_BARS = 10000
GRANULARITIES = ('M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D', 'W')


def role(client):
	"""
	'token' for the secret, a program's role ('pc', 'mirror', 'market') for
	its token, None for an assistant connected through OAuth.
	"""
	head, sep, _ = str(client).partition(': ')
	return head if sep and head in ('token',) + oauth.Authority.ROLES else None


def pusher(service, client):
	"""
	The data scripts hold the token itself, or one of the market role; an
	assistant connected through OAuth reads and backtests, it does not write
	the data every run reads. And only to the server that writes the market
	data (data/market.py).
	"""
	if role(client) not in ('token', 'market'):
		raise ToolError("pushing data is for a script with the token, not for an assistant")
	try:
		market.guard(market.directory(service.setup), service.setup)
	except market.MarketError as exc:
		raise ToolError(str(exc))


def pushCalendar(service, args, client):
	pusher(service, client)
	events = args.get('events')
	if not isinstance(events, list) or not events or len(events) > PUSH_EVENTS:
		raise ToolError("events: 1 to %d of ForexFactory's events" % PUSH_EVENTS)
	try:
		rows = [calendar.fromForexFactory(e) for e in events]
	except (ValueError, TypeError, AttributeError) as exc:
		raise ToolError("not saved: %s" % exc)
	where = calendar.path(service.setup)
	with calendar.writing(where, service.setup):
		before = calendar.load(where)
		after = calendar.merge(before, rows)
		calendar.save(after, where)
	return {'received': len(rows), 'added': len(after) - len(before), 'events': len(after)}


def series(args):
	"""The instrument and granularity a push or a pull names, checked."""
	from parity_deriva.web.service import importer
	csv = importer()
	instrument = csv.instrument_name(str(args.get('instrument') or '').replace('/', '_'))
	# the store's file name: nothing that could reach outside the data directory
	if not re.fullmatch(r'[A-Z0-9]{2,12}(_[A-Z0-9]{2,12})?', instrument):
		raise ToolError("instrument: letters and digits, e.g. EUR_USD")
	granularity = csv.granularity(str(args.get('granularity') or ''))
	if granularity not in GRANULARITIES:
		raise ToolError("granularity: one of %s" % ', '.join(GRANULARITIES))
	return csv, instrument, granularity


def pushCandles(service, args, client):
	pusher(service, client)
	csv, instrument, granularity = series(args)
	sides = []
	for side in ('ask', 'bid'):
		rows = args.get(side)
		if not isinstance(rows, list) or not rows or len(rows) > PUSH_BARS:
			raise ToolError("%s: 1 to %d bars, each [timestamp ms, open, high, low, close, "
							"volume]" % (side, PUSH_BARS))
		try:
			sides.append(csv.indexed(pd.DataFrame(
				rows, columns=csv.FIELDS if isinstance(rows[0], list) else None), side))
		except (ValueError, TypeError) as exc:
			raise ToolError("%s: %s" % (side, exc))
	try:
		frame, unpaired = csv.combine(*sides)
	except (ValueError, TypeError) as exc:
		raise ToolError("not saved: %s" % exc)
	added = service.mergeBars(instrument, granularity, frame, keep=True)
	return {'instrument': instrument, 'granularity': granularity, 'received': len(frame.index),
			'added': added, 'kept': len(frame.index) - added, 'unpaired': list(unpaired)}


def puller(client):
	"""Another parity server copying the market data: the token, a mirror or a pc one."""
	if role(client) not in ('token', 'mirror', 'pc'):
		raise ToolError("pulling the market data is for a server with the token, not for an assistant")


def marketStatus(service, args, client):
	"""The series each store keeps (not the ones built from them) and the calendar's span."""
	puller(client)
	where = calendar.path(service.setup)
	held = calendar.load(where)
	return {'instruments': [
		{'instrument': row['instrument'],
		 'granularities': [g for g in row['granularities'] if not g.get('derivedFrom')]}
		for row in service.instruments()],
		'calendar': {'events': len(held),
					 'to': held['time'].max().strftime('%Y-%m-%d %H:%M:%S') if len(held) else None,
					 'changed': int(os.path.getmtime(where) * 1000) if os.path.exists(where) else None}}


def pullCandles(service, args, client):
	"""
	The bars of one stored series after `after` (epoch ms, the last one the
	caller holds), `limit` of them at most, each side as push_candles takes
	them: the answer to a pull is a push the other way.
	"""
	puller(client)
	from parity_deriva.data import store
	_, instrument, granularity = series(args)
	path = market.store(instrument, service.setup)
	key = '/' + granularity
	try:
		limit = max(1, min(int(args.get('limit') or PUSH_BARS), PUSH_BARS))
		after = args.get('after')
		after = pd.Timestamp(int(after), unit='ms') if after not in (None, '') else None
	except (TypeError, ValueError):
		raise ToolError("after: epoch milliseconds; limit: 1 to %d" % PUSH_BARS)
	if not os.path.exists(path):
		raise ToolError("no store for %s" % instrument)
	with pd.HDFStore(path, mode='r') as held:
		if key not in held:
			raise ToolError("%s keeps no %s series (%s)" % (instrument, granularity,
															', '.join(held.keys())))
		index = pd.DatetimeIndex(held.select_column(key, 'index')).unique().sort_values()
	todo = index[index > after] if after is not None else index
	if not len(todo):
		return {'instrument': instrument, 'granularity': granularity, 'ask': [], 'bid': [], 'more': False}
	upto = todo[min(limit, len(todo)) - 1]
	frame = store.load(path, granularity, todo[0], upto)
	frame = frame[~frame.index.duplicated(keep='last')]
	stamps = pd.DatetimeIndex(frame.index).as_unit('ms').asi8.tolist()
	volume = frame['volume'].astype('int64').tolist()
	def side(name):
		legs = [frame['%s_%s' % (name, leg)].tolist() for leg in 'ohlc']
		return [[stamps[i]] + [leg[i] for leg in legs] + [volume[i]] for i in range(len(stamps))]
	return {'instrument': instrument, 'granularity': granularity, 'ask': side('ask'),
			'bid': side('bid'), 'more': bool(upto < index[-1])}


def pullCalendar(service, args, client):
	"""
	The calendar's events from `since` (a day, YYYY-MM-DD; all of them
	without), PUSH_EVENTS at a time from `start`: `next` is the next call's
	start, None at the end.
	"""
	puller(client)
	held = calendar.load(calendar.path(service.setup))
	try:
		since = pd.Timestamp(str(args['since'])) if args.get('since') else None
		start = max(0, int(args.get('start') or 0))
	except (TypeError, ValueError):
		raise ToolError("since: a day, YYYY-MM-DD; start: the `next` of the call before")
	if since is not None:
		held = held[held['time'] >= since]
	page = held.iloc[start:start + PUSH_EVENTS]
	events = [dict((column, str(value)) for column, value in row.items())
			  for row in page.assign(time=page['time'].dt.strftime('%Y-%m-%d %H:%M:%S'))
			  .to_dict('records')]
	end = start + len(page)
	return {'events': events, 'next': end if end < len(held) else None}


#: a pushed file comes a chunk a call, the base64 of up to this many of its
#: gzip bytes: under nginx's 4 MB for /parity/mcp
PUSH_CHUNK = 2 << 20
#: the most a pushed file may weigh, gzipped
PUSH_FILE = 256 << 20


def pcPusher(client):
	"""The PC's sync (scripts/sync.py) holds a pc token, the archive a promote one."""
	if role(client) not in ('token', 'pc', 'promote'):
		raise ToolError("pushing a set or a mix is for the PC's token, not for an assistant")


def received(args, where):
	"""
	A file pushed a chunk a call - `data` the base64 of its gzip bytes, `part`
	of `parts` - kept beside `where` until the last part is in: then its
	bytes and their JSON, None before.
	"""
	try:
		part, parts = int(args.get('part') or 0), int(args.get('parts') or 1)
		data = base64.b64decode(str(args.get('data') or ''), validate=True)
	except (TypeError, ValueError):
		data = part = parts = None
	if data is None or not data or len(data) > PUSH_CHUNK or not 0 <= part < parts:
		raise ToolError("part (from 0), parts, and data: the base64 of the gzipped file, "
						"%d bytes of it a call at most" % PUSH_CHUNK)
	os.makedirs(os.path.dirname(where), exist_ok=True)
	chunks = ['%s.push%d' % (where, i) for i in range(parts)]
	with open(chunks[part] + '.part', 'wb') as handle:
		handle.write(data)
	os.replace(chunks[part] + '.part', chunks[part])
	if part < parts - 1:
		return None
	missing = [i for i, chunk in enumerate(chunks) if not os.path.exists(chunk)]
	if missing:
		raise ToolError("parts %s did not arrive: push them again" % missing[:10])
	try:
		if sum(os.path.getsize(chunk) for chunk in chunks) > PUSH_FILE:
			raise ToolError("a pushed file is %d MB at most" % (PUSH_FILE >> 20))
		blob = b''
		for chunk in chunks:
			with open(chunk, 'rb') as handle:
				blob += handle.read()
	finally:
		for chunk in chunks:
			os.remove(chunk)
	try:
		return blob, json.loads(gzip.decompress(blob))
	except (OSError, EOFError, ValueError):
		raise ToolError("the file is not gzipped JSON")


def pushSweep(service, args, client):
	"""
	A set simulated on the PC, as runs/sweeps/<id>.json.gz holds it, or with
	`n` one of its runs as <id>/<n>.json.gz does: the set first. Marked with
	who pushed it, and with `commit`, the code it ran on there.
	"""
	pcPusher(client)
	sweep, n = str(args.get('sweep') or ''), args.get('n')
	try:
		if n in (None, ''):
			where = service.sweepPath(sweep, '.json.gz')
		else:
			held = service.savedSweep(sweep)
			where = service.sweepRunPath(sweep, int(n))
	except (web().ServiceError, TypeError, ValueError) as exc:
		raise ToolError("%s: push the set before its runs" % exc)
	got = received(args, where)
	if got is None:
		return {'sweep': sweep, 'n': n, 'part': int(args.get('part') or 0), 'saved': False}
	blob, found = got
	if n not in (None, ''):
		if not isinstance(found, dict) or not isinstance(found.get('trades'), list):
			raise ToolError("not a run: the payload the run page draws, with its trades")
		if not any(row.get('n') == int(n) for row in held['done']):
			raise ToolError("set %s has no run %s" % (sweep, n))
		service._write(where, blob)
		return {'sweep': sweep, 'n': int(n), 'trades': len(found['trades']), 'saved': True}
	if not isinstance(found, dict) or found.get('id') != sweep \
			or not isinstance(found.get('fields'), dict) or not isinstance(found.get('done'), list):
		raise ToolError("not the set %s: {id, fields, done, ...} as runs/sweeps/<id>.json.gz holds it"
						% sweep)
	if os.path.exists(where) and not service.savedSweep(sweep).get('origin'):
		raise ToolError("a set of this server's own is called %s" % sweep)
	found['origin'] = {'client': client, 'pushed': int(time.time() * 1000),
					   'commit': str(args.get('commit') or '')[:40]}
	service.saveSweep(found)
	return {'sweep': sweep, 'runs': len(found['done']), 'saved': True}


def pushMix(service, args, client):
	"""A mix made on the PC, once its sets and their runs are here (push_sweep)."""
	pcPusher(client)
	mix = args.get('mix')
	if not isinstance(mix, dict) or not isinstance(mix.get('items'), list) or not mix['items']:
		raise ToolError("mix: {id, name, leverage, items: [{sweep, n}]}")
	held = next((m for m in service.mixes() if m.get('id') == mix.get('id')), None)
	if held and not held.get('origin'):
		raise ToolError("a mix of this server's own has the id %s" % mix.get('id'))
	for item in mix['items']:
		try:
			there = service.sweepPayload(str((item or {}).get('sweep') or ''), int(item.get('n')))
		except (web().ServiceError, TypeError, ValueError, AttributeError):
			raise ToolError("an item of a mix is {sweep, n}")
		if there is None:
			raise ToolError("run %s of set %s is not here: push_sweep it first" % (item['n'], item['sweep']))
	entry = service.saveMix(mix, origin={'client': client, 'pushed': int(time.time() * 1000)})
	return {'mix': entry['id'], 'items': len(entry['items']), 'saved': True}


def pushRecord(service, args, client):
	"""
	A form's record on demo - its sessions, trades and parity, on the archive
	and its demo servers - for this real money server to judge by its own
	minimums and keep as proof.
	"""
	if role(client) != 'promote':
		raise ToolError("a promotion comes from the archive, with a promote token")
	if livesessions.serverAccounts() != 'real':
		raise ToolError("this server trades demo accounts: a promotion goes to a real money one")
	try:
		kept = service.live.promote(args.get('record'), client)
	except livesessions.LiveError as exc:
		raise ToolError(str(exc))
	return dict((k, kept[k]) for k in ('ok', 'need', 'days', 'trades', 'alarms', 'net'))


# the next version is taken and written by one submit at a time
_submitLock = threading.Lock()


def classOwner(service, name, klass):
	"""
	The strategy, not a version of `name`, whose class is also called `klass`;
	None when there is none. Each file imports as a module of its own, but AG02
	signs its signals with its class's name: two classes of one name would
	take each other's trades for their own on a live account.
	"""
	for other, (module, attr) in ledger.STRATEGIES.items():
		if attr == klass and not module.startswith('parity_deriva.strategy.uploaded_'):
			return other
	held = dict(uploaded.drafts(dataDir(service)), **uploaded.enabled(dataDir(service)))
	for code, path in held.items():
		if uploaded.split(code)[0] != name:
			with open(path) as handle:
				tree = ast.parse(handle.read())
			if any(isinstance(node, ast.ClassDef) and node.name == klass for node in tree.body):
				return code
	return None


def submit(service, args, client, kind='strategies'):
	"""A strategy's or an indicator's next version, checked in the sandbox and kept as a draft."""
	name, source = str(args.get('name') or ''), args.get('source')
	if not uploaded.NAME.fullmatch(name):
		raise ToolError("name: upper case, digits and dashes, 2 to 40, e.g. MY-EMA-CROSS - "
						"without a version: the server adds it")
	if not isinstance(source, str) or not source.strip():
		raise ToolError("source: the whole Python file")
	if name in web().strategies():
		raise ToolError("%s is a built-in strategy: choose another name" % name)
	# one name is one thing: a code alone says whether it is a strategy or an indicator
	other = [k for k in uploaded.KINDS if k != kind][0]
	if uploaded.latest(dataDir(service), name, other):
		raise ToolError("%s is the name of one of the %s already: choose another" % (name, other))
	problems = uploaded.check(source)
	if kind == 'indicators' and uploaded.used(source):
		problems.append("an indicator does not take another uploaded indicator: build it on "
						"parity_deriva.lib.streaming instead")
	if problems:
		raise ToolError("not saved:\n" + "\n".join(problems))
	where = os.path.join(uploaded.root(dataDir(service), kind), 'drafts')
	os.makedirs(where, exist_ok=True)
	# Was: a draft of the same name was replaced. Now: never - each submit is
	# the name's next version, and the one run and read before stays
	with _submitLock:
		# the newest version sent again, as it is: no version to make of it
		newest = uploaded.latest(dataDir(service), name, kind)
		if newest:
			with open(written(service, kind)[newest]) as handle:
				if uploaded.stamp(handle.read(), name, 0, '') == uploaded.stamp(source, name, 0, ''):
					raise ToolError("not saved: the same code as %s, no new version made" % newest)
		version, server = uploaded.nextVersion(dataDir(service), name, kind), serverVersion()
		code = uploaded.code(name, version)
		path = os.path.join(where, uploaded.fileName(code))
		trial = os.path.join(where, '_' + uploaded.fileName(code))
		with open(trial, 'w') as handle:
			handle.write(uploaded.stamp(source, name, version, server))
		try:
			if kind == 'indicators':
				found = sandboxed(service, {'indicator': {'name': code, 'path': trial}},
								  timeout=60)['indicator']
			else:
				found = sandboxed(service, {'check': True, 'strategy': {'name': code, 'path': trial}},
								  timeout=60)['strategy']
				owner = classOwner(service, name, found['class'])
				if owner:
					raise ToolError("not saved: class %s is already the class of %s - rename the class"
									% (found['class'], owner))
			os.replace(trial, path)
		finally:
			if os.path.exists(trial):
				os.remove(trial)
		found = dict(found, server=server, submitted=int(time.time() * 1000), client=client)
		keepMeta(path, found)
	return dict(found, name=code, family=name, version=version, state='draft',
				next="run_backtest with strategy %s" % code if kind == 'strategies' else
				"take it in a strategy: from parity_deriva.strategy.uploaded import indicator, "
				"then %s = indicator(%r); the user enables it before the strategy"
				% (found['class'], code))


def runBacktest(service, args, base):
	name = resolve(service, str(args.get('strategy') or ''))
	draft = uploaded.drafts(dataDir(service)).get(name)
	if draft:
		known = [f['name'] for f in meta(draft).get('parameters', ())]
	elif name in web().strategies():
		known = [f['name'] for f in web().handlerFields(name)]
	else:
		raise ToolError("no strategy %r: list_strategies names them" % name)
	fields = {'strategy': name}
	for key in ('instrument', 'granularity', 'from', 'to'):
		fields[key] = str(args.get(key) or '')
	for key, value in (args.get('parameters') or {}).items():
		if key not in known:
			raise ToolError("%s has no parameter %r; it has %s"
							% (name, key, ', '.join(known) or 'none'))
		fields[key] = str(value)
	for key, value in (args.get('options') or {}).items():
		if key not in OPTIONS:
			raise ToolError("no option %r; the options are %s" % (key, ', '.join(OPTIONS)))
		if value is True or value is False:
			value = '1' if value else ''
		if value not in (None, ''):
			fields[key] = str(value)
	answer = sandboxed(service, {'fields': fields, 'strategy': draft and {
		'name': name, 'path': draft}})
	payload = answer['payload']
	service.saveRun(fields, payload)
	run = service.runId(fields)
	_, summary, _ = service.simulated({'kind': 'run', 'id': run})
	summary.update({'from': day(summary['from']), 'to': day(summary['to']),
					'fine': payload.get('fine'), 'counts': payload.get('counts')})
	return dict({'strategy': name, 'run': run, 'link': base + '/run?' + urllib.parse.urlencode(fields),
				 'summary': summary}, **tradeRows(payload.get('trades') or []))


def tradeRows(trades):
	"""The first TRADES_SHOWN of a run's trades, as an answer carries them."""
	return {'trades': [{'n': t['n'], 'direction': t['direction'], 'signal': day(t['signalTime']),
						'entry': day(t['entryTime']), 'entryPrice': t['entryPrice'],
						'stopLoss': t['stopLoss'], 'takeProfit': t['takeProfit'],
						'exit': day(t['exitTime']), 'exitPrice': t['exitPrice'],
						'outcome': t['outcome'], 'pl': t['pl'], 'balance': t['balance']}
					   for t in trades[:TRADES_SHOWN]],
			'tradesShown': '%d of %d' % (min(len(trades), TRADES_SHOWN), len(trades))}


def summed(summary):
	"""A simulation's summary with its window as days, not epoch ms."""
	return dict(summary, **{'from': day(summary.get('from')), 'to': day(summary.get('to'))})


def listRuns(service, args):
	try:
		limit = max(1, min(int(args.get('limit') or 20), 200))
	except (TypeError, ValueError):
		raise ToolError("limit: a number of each, 1 to 200")
	return {'sets': [dict((k, s.get(k)) for k in ('id', 'name', 'saved', 'strategy', 'instrument',
												   'granularity', 'from', 'to', 'runs', 'total', 'varied',
												   'best', 'bestParams', 'bestScore', 'origin', 'cold'))
					 for s in service.sweeps()[:limit]],
			'runs': [dict([(k, r.get(k)) for k in ('id', 'saved', 'strategy', 'instrument', 'granularity',
												   'trades', 'balance')], **{'from': day(r.get('from')),
																			 'to': day(r.get('to'))})
					 for r in service.runs()[:limit]],
			'mixes': [{'id': m['id'], 'name': m.get('name'), 'items': m.get('items'),
					   'origin': (m.get('origin') or {}).get('client')} for m in service.mixes()[:limit]]}


def getRun(service, args, base):
	run, sweep, n = str(args.get('run') or ''), str(args.get('sweep') or ''), args.get('n')
	if run:
		fields, summary, _ = service.simulated({'kind': 'run', 'id': run})
		return dict({'run': run, 'link': base + '/run?' + urllib.parse.urlencode(fields),
					 'fields': fields, 'summary': summed(summary)},
					**tradeRows(((service.savedRun(run) or {}).get('payload') or {}).get('trades') or []))
	if not sweep:
		raise ToolError("run, or sweep (and n for one of its runs): list_runs names them")
	job = service.savedSweep(sweep)
	if n in (None, ''):
		return {'sweep': sweep, 'name': job.get('name'), 'link': base + '/?' + urllib.parse.urlencode({'set': sweep}),
				'fields': job.get('fields'), 'varied': job.get('varied'), 'total': job.get('total'),
				'runs': [dict([('n', r['n']), ('params', r.get('params')), ('error', r.get('error')),
							   ('final', r.get('final'))]
							  + [(k, (r.get('report') or {}).get(k)) for k in (
								  'closedTrades', 'net', 'winRate', 'profitFactor', 'maxDrawdown')]
							  + [(k, (r.get('kpi') or {}).get(k)) for k in (
								  'roi', 'car', 'maxDrawdownPct', 'sharpe', 'score')])
						 for r in job['done']]}
	try:
		n = int(n)
	except (TypeError, ValueError):
		raise ToolError("n: the number of one run of the set")
	fields, summary, name = service.simulated({'kind': 'sweep', 'id': sweep, 'n': n}, job)
	payload = service.sweepPayload(sweep, n)
	out = {'sweep': sweep, 'n': n, 'name': name, 'fields': fields, 'summary': summed(summary),
		   'link': base + '/run?' + urllib.parse.urlencode(dict({'sweep': sweep, 'run': n}, **fields))}
	if payload is None:
		return dict(out, trades=None, tradesShown="its trades are not saved: the link runs it again")
	return dict(out, **tradeRows(payload.get('trades') or []))


def pullCode(service, args, client):
	"""The enabled strategies and indicators, for a Test's sync to take as drafts."""
	if role(client) not in ('token', 'pc'):
		raise ToolError("pulling the code is for a Test's sync with a pc token, not for an assistant")
	out = {}
	for kind in uploaded.KINDS:
		out[kind] = []
		for code, path in uploaded.enabled(dataDir(service), kind).items():
			with open(path) as handle:
				out[kind].append({'code': code, 'source': handle.read(), 'meta': meta(path)})
	return out


def liveStatus(service, args, client):
	"""This trade server's sessions, for the archive that reads them (web/servers.py poll)."""
	if role(client) not in ('token', 'promote'):
		raise ToolError("a trade server's sessions are read by the archive, with a promote token")
	return {'server': {'accounts': livesessions.serverAccounts(), 'roles': servers.roles(service.setup),
					   'version': serverVersion()},
			'halted': service.live.halted(), 'sessions': service.live.snapshot()}


def reference():
	"""
	HELPERS read off their source, not imported: each module's docstring,
	and every public function, class and method with its arguments and its
	docstring, and the upper-case constants with their value and the #:
	comment over them - in the order they are written. The docs page shows
	it whole; list_helpers a line each. Read off the code, so what is added
	there is documented at once.
	"""
	def comment(lines, node):
		"""The #: comment right over a line, without its hashes."""
		above, i = [], node.lineno - 2
		while i >= 0 and lines[i].strip().startswith('#'):
			above.insert(0, lines[i].strip().lstrip('#:').strip())
			i -= 1
		return ' '.join(above)

	def constants(lines, body):
		out = []
		for node in body:
			targets = node.targets if isinstance(node, ast.Assign) else []
			for target in targets:
				if isinstance(target, ast.Name) and target.id.isupper():
					value = ast.unparse(node.value)
					out.append({'name': target.id, 'doc': comment(lines, node),
								'value': value if len(value) <= 60 else value[:57] + '...'})
		return out

	def entry(node):
		if isinstance(node, ast.ClassDef):
			args = ', '.join(ast.unparse(b) for b in node.bases)
		else:
			args = ast.unparse(node.args)
		return {'name': node.name, 'kind': 'class' if isinstance(node, ast.ClassDef) else 'function',
				'args': args, 'doc': ast.get_docstring(node) or ''}

	out = []
	for module in HELPERS:
		with open(os.path.join(sandbox.TOP, *module.split('.')) + '.py') as handle:
			source = handle.read()
		tree, lines = ast.parse(source), source.splitlines()
		items = []
		for node in tree.body:
			if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and not node.name.startswith('_'):
				item = entry(node)
				if isinstance(node, ast.ClassDef):
					item['constants'] = constants(lines, node.body)
					item['methods'] = [entry(m) for m in node.body
									   if isinstance(m, ast.FunctionDef) and not m.name.startswith('_')]
				items.append(item)
		out.append({'module': module, 'doc': ast.get_docstring(tree) or '',
					'constants': constants(lines, tree.body), 'items': items})
	return out


def helpers():
	"""reference() a line each: the arguments and the docstring's first paragraph."""
	def line(item, owner=''):
		doc = ' '.join(item['doc'].split('\n\n')[0].split())
		if len(doc) > 200:
			doc = doc[:197] + '...'
		return '%s%s(%s)%s' % (owner, item['name'], item['args'], ' - ' + doc if doc else '')
	out = []
	for module in reference():
		lines = []
		for item in module['items']:
			lines.append(line(item))
			lines += [line(m, '    %s.' % item['name']) for m in item.get('methods', ())]
		out.append({'module': module['module'], 'helpers': lines})
	return out


def listHelpers(service, args):
	mine = [dict(dict((k, m.get(k)) for k in ('class', 'description', 'panel', 'lines', 'parameters')),
				 code=code, state=state)
			for state, held in (('draft', uploaded.drafts(dataDir(service), 'indicators')),
								('enabled', uploaded.enabled(dataDir(service), 'indicators')))
			for code, m in ((c, meta(p)) for c, p in held.items())]
	return {'modules': helpers(), 'indicators': mine, 'requested': [
		dict((k, r.get(k)) for k in ('id', 'title', 'strategy')) for r in requests(service)]}


# the requests and the news are a small file each, written whole: one writer
# at a time
_requestsLock = threading.Lock()


def board(service, name):
	"""The rows of requests.json or news.json, oldest first."""
	try:
		with open(os.path.join(uploaded.root(dataDir(service)), name + '.json')) as handle:
			return json.load(handle)
	except (OSError, ValueError):
		return []


def keepBoard(service, name, rows):
	path = os.path.join(uploaded.root(dataDir(service)), name + '.json')
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path + '.tmp', 'w') as handle:
		json.dump(rows, handle, indent=1)
	os.replace(path + '.tmp', path)


def requests(service):
	"""The feature requests still open, oldest first."""
	return board(service, 'requests')


def keepRequests(service, rows):
	keepBoard(service, 'requests', rows)


def news(service):
	"""
	The news the user posted, oldest first, each strategy it names with its
	newest version and whether that was submitted after the news: how far
	the assistants got with what it asks.
	"""
	held = dict(uploaded.drafts(dataDir(service)), **uploaded.enabled(dataDir(service)))
	out = []
	for row in board(service, 'news'):
		named = []
		for name in row.get('strategies') or ():
			code = uploaded.latest(dataDir(service), name)
			path = held.get(code)
			submitted = (meta(path).get('submitted') or 0) if path else 0
			named.append({'name': name, 'latest': code, 'updated': submitted > row['posted']})
		out.append(dict(row, strategies=named))
	return out


def number(row):
	"""N12 -> 12: the order the news were posted in."""
	return int(row['id'][1:])


def clients(service):
	"""
	client -> the server version it last connected to and the last news it
	read: how a client that comes back is told what changed while it was
	away. A client is the name it connects by (the OAuth client's, or the
	token's User-Agent), so two copies of one program are one client.
	"""
	found = board(service, 'clients')
	return found if isinstance(found, dict) else {}


def remember(service, client, **fields):
	with _requestsLock:
		held = clients(service)
		held[client] = dict(held.get(client) or {}, **fields)
		keepBoard(service, 'clients', held)


def unread(service, client):
	"""The news open now that this client has not read with get_news."""
	read = (clients(service).get(client) or {}).get('read', 0)
	return [r for r in board(service, 'news') if number(r) > read]


def notice(service, client):
	"""
	What a tool's answer carries on top while news wait: MCP has no way for a
	server to speak first here (every request is a POST of its own, and a
	client reads the instructions once, when it connects), so the news ride
	on every answer until get_news is called.
	"""
	rows = unread(service, client)
	if not rows:
		return None
	return ("News from the user you have not read yet - call get_news before going on: "
			+ "; ".join("%s: %s" % (r['id'], r['title']) for r in rows))


def getNews(service, args, client=None):
	rows = news(service)
	if client and rows:
		remember(service, client, read=max(number(r) for r in rows))
	return {'news': rows, 'next': (
		"do what each one asks, then tell the user what you did. A strategy it names "
		"is done once you submit a new version of it (updated: true)") if rows else "no news"}


def postNews(service, asked):
	"""A news for the assistants, posted with the code update it is about - not
	from the page, which only lists them:
	curl -H 'X-Parity-Deriva: 1' -d '{"title": ..., "text": ..., "strategies": "A, B"}'
	localhost:<port>/api/mcp/news, on dev and on prod (each has its DATA)."""
	title = ' '.join(str(asked.get('title') or '').split())
	text = str(asked.get('text') or '').strip()
	names = asked.get('strategies') or []
	if isinstance(names, str):
		# by commas or lines: a version's code has a space in it
		names = names.replace('\n', ',').split(',')
	# a version named is its name: the news is about what comes after it
	names = list(dict.fromkeys(uploaded.split(' '.join(str(n).split()))[0]
							   for n in names if str(n).strip()))
	if not title or len(title) > NEWS_TITLE:
		raise ToolError("title: 1 to %d characters" % NEWS_TITLE)
	if not text or len(text) > NEWS_TEXT:
		raise ToolError("text: what changed and what to do, 1 to %d characters" % NEWS_TEXT)
	wrong = [n for n in names if not uploaded.NAME.fullmatch(n)]
	if wrong:
		raise ToolError("not a strategy name: %s" % ', '.join(wrong))
	with _requestsLock:
		rows = board(service, 'news')
		if len(rows) >= NEWS_OPEN:
			raise ToolError("%d news are posted already: remove some first" % len(rows))
		rows.append({'id': 'N%d' % (max([int(r['id'][1:]) for r in rows] or [0]) + 1),
					 'title': title, 'text': text, 'strategies': names,
					 'posted': int(time.time() * 1000)})
		keepBoard(service, 'news', rows)
	return status(service)


def instructions(service, client=None):
	"""
	GUIDE, and what changed: what an assistant is told when it connects. A
	client that last connected to another version of this server is told so,
	and the news open now are listed - the ones it has not read marked new.
	The version it connects to now is remembered for the next time.
	"""
	now = serverVersion()
	before = (clients(service).get(client) or {}) if client else {}
	if client:
		remember(service, client, version=now, connected=int(time.time() * 1000))
	text = GUIDE
	if before.get('version') and before['version'] != now:
		text += ("\n\nThis server was updated since you last connected: you knew %s, it is "
				 "now %s. What changed and what to do about it is in get_news."
				 % (before['version'], now))
	rows = board(service, 'news')
	if rows:
		text += "\n\nNews from the user, open now - call get_news before anything else:\n" \
			+ "\n".join("- %s: %s%s" % (r['id'], r['title'],
										  ' (new)' if number(r) > before.get('read', 0) else '')
						 for r in rows)
	return text


def requestFeature(service, args):
	title = ' '.join(str(args.get('title') or '').split())
	text = str(args.get('description') or '').strip()
	strategy = str(args.get('strategy') or '').strip()[:40] or None
	if not title or len(title) > REQUEST_TITLE:
		raise ToolError("title: what is asked for, 1 to %d characters" % REQUEST_TITLE)
	if not text or len(text) > REQUEST_TEXT:
		raise ToolError("description: what it computes, its inputs and outputs, 1 to %d "
						"characters" % REQUEST_TEXT)
	with _requestsLock:
		rows = requests(service)
		same = [r for r in rows if r['title'].lower() == title.lower()]
		if same:
			raise ToolError("already requested as %s: %s. Add to it by telling the user"
							% (same[0]['id'], same[0]['title']))
		if len(rows) >= REQUESTS_OPEN:
			raise ToolError("%d requests are open already: the user has to answer some "
							"on the settings page first" % len(rows))
		row = {'id': 'R%d' % (max([int(r['id'][1:]) for r in rows] or [0]) + 1), 'title': title,
			   'description': text, 'strategy': strategy, 'submitted': int(time.time() * 1000)}
		keepRequests(service, rows + [row])
	return dict(row, next="tell the user that %s waits for it: the request is on the "
				"settings page" % (strategy or 'the strategy'))


def dropRequest(service, id, name='requests'):
	"""A request answered or a news done, from the settings page: it leaves the list."""
	with _requestsLock:
		rows = board(service, name)
		if not any(r['id'] == id for r in rows):
			raise ToolError("no %s %r" % ('request' if name == 'requests' else 'news', id))
		keepBoard(service, name, [r for r in rows if r['id'] != id])
	return status(service)


def proposePublic(service, args):
	code = resolve(service, str(args.get('strategy') or ''))
	note = str(args.get('note') or '').strip()
	kind = kindOf(service, code)
	enabled = uploaded.enabled(dataDir(service), kind)
	path = written(service, kind).get(code)
	if not path:
		raise ToolError("%s is not one of yours: only a strategy or an indicator written here "
						"goes to the public repository (list_strategies, list_helpers)" % code)
	if not note or len(note) > REQUEST_TEXT:
		raise ToolError("note: what it does and how it tested, 1 to %d characters" % REQUEST_TEXT)
	found = meta(path)
	if found.get('pull'):
		raise ToolError("%s has its pull request already: %s" % (code, found['pull']['url']))
	found['proposed'] = {'note': note, 'at': int(time.time() * 1000)}
	keepMeta(path, found)
	return {'strategy': code, 'proposed': True, 'next': "tell the user it waits on the settings "
			"page: " + ("the pull request is opened there" if code in enabled else
						"enable %s there, then open the pull request" % code)}


def github(token, method, path, body=None):
	"""One call to GitHub's REST API: its answer, or ToolError in GitHub's words."""
	request = urllib.request.Request(
		'https://api.github.com' + path, method=method,
		data=None if body is None else json.dumps(body).encode(),
		headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
				 'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'parity-deriva'})
	try:
		with urllib.request.urlopen(request, timeout=30) as response:
			raw = response.read()
	except urllib.error.HTTPError as error:
		try:
			said = json.loads(error.read() or b'{}').get('message') or ''
		except ValueError:
			said = ''
		raise ToolError("GitHub said %s to %s %s: %s" % (error.code, method, path, said))
	except (urllib.error.URLError, OSError) as error:
		raise ToolError("GitHub cannot be reached: %s" % error)
	return json.loads(raw) if raw else {}


def pullRequest(service, code):
	"""
	The pull request of a version the assistant proposed and the user enabled,
	opened on PUBLIC_REPO with GITHUB_TOKEN from the settings page: its file as
	it is, stamp included, and a card of what the page knows of it beside it,
	under strategies/NAME/ or indicators/NAME/, on a branch of its own - of the
	repository when the token may push to it, else of the token's fork of it.
	A strategy's brings the indicators it takes that the repository lacks.
	"""
	setup = service.setup
	repo, token = getattr(setup, 'PUBLIC_REPO', None), getattr(setup, 'GITHUB_TOKEN', None)
	if not repo or not token:
		raise ToolError("no public repository: set PARITY_DERIVA_PUBLIC_REPO (owner/name) and "
						"PARITY_DERIVA_GITHUB_TOKEN in parity_deriva/.env, then restart")
	kind = kindOf(service, code)
	path = uploaded.enabled(dataDir(service), kind).get(code)
	if not path:
		raise ToolError("%s is not enabled: a pull request carries a version somebody read" % code)
	found = meta(path)
	if not found.get('proposed'):
		raise ToolError("%s was not proposed: the assistant proposes it with a note "
						"(propose_public)" % code)
	if found.get('pull'):
		raise ToolError("%s has its pull request already: %s" % (code, found['pull']['url']))
	with open(path) as handle:
		source = handle.read()
	upstream = github(token, 'GET', '/repos/' + repo)
	base, head = upstream['default_branch'], repo
	if not (upstream.get('permissions') or {}).get('push'):
		head = github(token, 'POST', '/repos/%s/forks' % repo, {'default_branch_only': True})['full_name']
		# GitHub makes a fork in the background: wait for it, then bring it level
		for _ in range(15):
			try:
				github(token, 'GET', '/repos/' + head)
				break
			except ToolError:
				time.sleep(2)
		github(token, 'POST', '/repos/%s/merge-upstream' % head, {'branch': base})
	files = pullFiles(kind, code, source, found, found['proposed']['note'])
	taken = uploaded.used(source) if kind == 'strategies' else []
	for used in taken:
		held = uploaded.enabled(dataDir(service), 'indicators').get(used)
		if not held:
			raise ToolError("%s takes %s, which is not enabled" % (code, used))
		try:
			github(token, 'GET', '/repos/%s/contents/%s?ref=%s' % (repo, urllib.parse.quote(
				'indicators/%s/%s' % (uploaded.split(used)[0], uploaded.fileName(used))), base))
			continue
		except ToolError as exc:
			if not str(exc).startswith('GitHub said 404'):
				raise
		with open(held) as handle:
			files += pullFiles('indicators', used, handle.read(), meta(held),
							   (meta(held).get('proposed') or {}).get('note') or 'taken by %s' % code)
	sha = github(token, 'GET', '/repos/%s/git/ref/heads/%s' % (repo, base))['object']['sha']
	# the time in it: a try that failed half way leaves its branch, and the next is another
	branch = '%s/%s-v%d-%d' % ({'strategies': 'strategy', 'indicators': 'indicator'}[kind],
							   found['family'], found['version'], time.time())
	github(token, 'POST', '/repos/%s/git/refs' % head, {'ref': 'refs/heads/' + branch, 'sha': sha})
	for name, text in files:
		github(token, 'PUT', '/repos/%s/contents/%s' % (head, urllib.parse.quote(name)),
			   {'message': 'Add %s' % code, 'branch': branch,
				'content': base64.b64encode(text.encode()).decode()})
	meant = ('meant for %s %s' % (found.get('instrument') or 'any instrument', found.get('granularity') or '')
			 if kind == 'strategies' else 'drawn %s' % ('in a strip of its own' if found.get('panel')
														else 'on the candles'))
	pull = github(token, 'POST', '/repos/%s/pulls' % repo, {
		'title': 'Add %s' % code, 'base': base,
		'head': branch if head == repo else '%s:%s' % (head.split('/')[0], branch),
		'body': '**%s**: %s\n\n- written on parity-deriva %s\n- %s\n'
				'- parameters: %s\n%s\n%s\n' % (
					code, found.get('description') or '', found.get('server') or 'a server before versions',
					meant, ', '.join(p.get('name', '') for p in found.get('parameters') or ()) or 'none',
					'- takes the indicators %s\n' % ', '.join(taken) if taken else '',
					found['proposed']['note'])})
	found['pull'] = {'url': pull['html_url'], 'number': pull['number'], 'at': int(time.time() * 1000)}
	keepMeta(path, found)
	return status(service)


def pullFiles(kind, code, source, found, note):
	"""(path, text) of the two files a version is in the public repository: its source and its card."""
	keys = ('family', 'version', 'server', 'description') + (
		('instrument', 'granularity') if kind == 'strategies' else ('panel', 'lines')) + ('parameters', 'submitted')
	card = dict((k, found.get(k)) for k in keys)
	card.update(code=code, note=note)
	folder = '%s/%s/' % (kind, uploaded.split(code)[0])
	return [(folder + uploaded.fileName(code), source),
			(folder + uploaded.fileName(code)[:-3] + '.json', json.dumps(card, indent=1) + '\n')]


def sandboxed(service, job, timeout=None):
	"""The sandbox's answer, one at a time: a second call waits for the first."""
	setup = service.setup
	job = dict(job, memoryMb=getattr(setup, 'MCP_SANDBOX_MB', 1024))
	# ponytail: one sandbox at a time for the whole service; a queue per
	# assistant if several ever share one machine
	with service._sandbox:
		answer = sandbox.run(job, dataDir(service),
							 timeout or getattr(setup, 'MCP_SANDBOX_SECONDS', 240))
	if 'error' in answer:
		raise ToolError(answer['error'] + ('\n' + answer['trace'] if answer.get('trace') else ''))
	return answer


#: another parity server copying the market data
PULLS = ('market_status', 'pull_candles', 'pull_calendar')
#: the market data coming in
PUSHES = ('push_calendar', 'push_candles')
#: what a program's token may call, by its role: the PC everything but the
#: market data (it takes that from here, never the other way)
#: on a real money server, what only a promotion does, and no assistant
WRITES = ('submit_strategy', 'submit_indicator', 'run_backtest', 'propose_public',
		  'request_feature', 'push_sweep', 'push_mix', 'push_record')
#: what the archive does on a trade server with its promote token: push a
#: form's code and set, a record to a real money one, and read the sessions
PROMOTES = ('submit_strategy', 'submit_indicator', 'push_sweep', 'push_record')
ROLES = {'mirror': lambda name: name in PULLS,
		 'market': lambda name: name in PUSHES + ('market_status',),
		 'pc': lambda name: name not in PUSHES,
		 'promote': lambda name: name in PROMOTES + ('list_strategies', 'market_status', 'live_status')}

#: the server roles (web/servers.py) a tool is served on; one not here, the
#: market data's, is served wherever the market folder says (data/market.py)
SERVED = {'get_news': ('test',), 'list_helpers': ('test',), 'request_feature': ('test',),
		  'run_backtest': ('test',), 'propose_public': ('test',),
		  'list_strategies': servers.ROLES, 'submit_strategy': servers.ROLES,
		  'submit_indicator': servers.ROLES,
		  'get_source': ('test', 'archive'), 'list_data': ('test', 'archive'),
		  'list_runs': ('test', 'archive'), 'get_run': ('test', 'archive'),
		  'pull_code': ('archive',), 'push_mix': ('archive',), 'push_sweep': ('archive', 'trade'),
		  'push_record': ('trade',), 'live_status': ('trade',)}


def served(service, name):
	"""None when this server serves the tool `name`, else why not."""
	return servers.missing(service.setup, *SERVED.get(name, servers.ROLES))


def call(service, name, args, base, client):
	held = role(client)
	if held in ROLES and not ROLES[held](name):
		raise ToolError("a %s token may not call %s" % (held, name))
	refused = served(service, name)
	if refused:
		raise ToolError("no %s here: %s" % (name, refused))
	# a server that does not test takes code and results only from another
	# server's push: the Test's (pc) on an archive, the archive's (promote) on
	# a trade server - no assistant writes a draft on it
	if name in WRITES and held not in ('pc', 'promote') and servers.missing(service.setup, 'test'):
		raise ToolError("this server has no test role: it takes %s only from another server's "
						"push" % name)
	if livesessions.serverAccounts() == 'real' and name in WRITES and held != 'promote':
		raise ToolError("a real money server takes only what the archive promotes to it: no %s here"
						% name)
	if name == 'get_news':
		return getNews(service, args, client)
	if name == 'list_strategies':
		return listStrategies(service, args)
	if name == 'get_source':
		return getSource(service, args)
	if name == 'list_data':
		return listData(service, args)
	if name == 'list_helpers':
		return listHelpers(service, args)
	if name == 'request_feature':
		return requestFeature(service, args)
	if name == 'submit_strategy':
		return submit(service, args, client)
	if name == 'submit_indicator':
		return submit(service, args, client, 'indicators')
	if name == 'run_backtest':
		return runBacktest(service, args, base)
	if name == 'propose_public':
		return proposePublic(service, args)
	if name == 'push_calendar':
		return pushCalendar(service, args, client)
	if name == 'push_candles':
		return pushCandles(service, args, client)
	if name == 'market_status':
		return marketStatus(service, args, client)
	if name == 'pull_candles':
		return pullCandles(service, args, client)
	if name == 'pull_calendar':
		return pullCalendar(service, args, client)
	if name == 'push_sweep':
		return pushSweep(service, args, client)
	if name == 'push_mix':
		return pushMix(service, args, client)
	if name == 'push_record':
		return pushRecord(service, args, client)
	if name == 'list_runs':
		return listRuns(service, args)
	if name == 'get_run':
		return getRun(service, args, base)
	if name == 'pull_code':
		return pullCode(service, args, client)
	if name == 'live_status':
		return liveStatus(service, args, client)
	raise ToolError("no tool %r" % name)


# ------------------------------------------------------------- the protocol

def handle(service, message, base, client):
	"""One JSON-RPC message: its answer, or None for a notification. `client` is
	who sent it, kept with the strategies it submits."""
	# a notification, or an answer to a request this server never makes
	if not isinstance(message, dict) or 'id' not in message or 'method' not in message:
		return None
	method, params = message.get('method'), message.get('params') or {}
	answer = {'jsonrpc': '2.0', 'id': message['id']}
	if method == 'initialize':
		asked = params.get('protocolVersion')
		answer['result'] = {'protocolVersion': asked if asked in PROTOCOLS else PROTOCOLS[0],
							'capabilities': {'tools': {}},
							'serverInfo': {'name': 'parity-deriva', 'version': serverVersion()},
							'instructions': instructions(service, client)}
	elif method == 'ping':
		answer['result'] = {}
	elif method == 'tools/list':
		# what this server's roles serve: a trade server's assistant sees no backtest
		answer['result'] = {'tools': [t for t in TOOLS if not served(service, t['name'])]}
	elif method == 'tools/call':
		try:
			found = call(service, params.get('name'), params.get('arguments') or {}, base, client)
			answer['result'] = {'content': [{'type': 'text', 'text': json.dumps(found, indent=1)}]}
		except (ToolError, uploaded.UploadError, sandbox.SandboxError, ledger.LedgerError,
				web().ServiceError, OSError) as exc:
			answer['result'] = {'content': [{'type': 'text', 'text': str(exc)}], 'isError': True}
		# the news not read yet, after the answer: its first block stays the answer
		told = notice(service, client) if params.get('name') != 'get_news' else None
		if told:
			answer['result']['content'].append({'type': 'text', 'text': told})
	else:
		answer['error'] = {'code': -32601, 'message': "no method %s" % method}
	return answer


# ------------------------------------------------- the settings page's side

def status(service):
	"""The settings page's box: the secret, the clients, the strategies and the indicators."""
	def rows(kind):
		out = []
		for state, held in (('draft', uploaded.drafts(dataDir(service), kind)),
							('enabled', uploaded.enabled(dataDir(service), kind))):
			for name, path in held.items():
				out.append(dict(meta(path), name=name, state=state))
		return sorted(out, key=lambda r: r.get('submitted') or 0, reverse=True)
	return dict(service.oauth.status(), keys=service.oauth.keys(),
				strategies=rows('strategies'), indicators=rows('indicators'),
				requests=requests(service)[::-1], news=news(service)[::-1])


def source(service, name):
	for kind in uploaded.KINDS:
		held = written(service, kind)
		if name in held:
			with open(held[name]) as handle:
				text = handle.read()
			return {'name': name, 'source': text, 'problems': uploaded.check(text)}
	raise ToolError("no uploaded strategy or indicator %r" % name)


def act(service, name, action):
	"""
	enable, disable or delete an uploaded strategy or indicator, from the
	settings page. A strategy that takes an indicator nobody enabled does not
	load, so it is not enabled; an indicator an enabled strategy takes is not
	disabled.
	"""
	kind = kindOf(service, name)
	draft = uploaded.drafts(dataDir(service), kind).get(name)
	live = uploaded.enabled(dataDir(service), kind).get(name)
	if action == 'enable':
		if not draft:
			raise ToolError("no draft %r" % name)
		if name in (uploaded.LOADED if kind == 'indicators' else ledger.STRATEGIES):
			raise ToolError("something is already called %s" % name)
		if kind == 'strategies':
			with open(draft) as handle:
				missing = [c for c in uploaded.used(handle.read()) if c not in uploaded.LOADED]
			if missing:
				raise ToolError("%s takes %s: enable %s first" % (
					name, ', '.join(missing), 'it' if len(missing) == 1 else 'them'))
		target = os.path.join(uploaded.root(dataDir(service), kind), uploaded.fileName(name))
		moves = [(draft, target), (draft[:-3] + '.json', target[:-3] + '.json')]
		for a, b in moves:
			if os.path.exists(a):
				os.replace(a, b)
		try:
			if kind == 'indicators':
				uploaded.loadIndicator(name, target)
			else:
				ledger.STRATEGIES[name] = uploaded.load(name, target)
		except BaseException as exc:
			for a, b in moves:
				if os.path.exists(b):
					os.replace(b, a)
			raise ToolError("%s does not load, so it stays a draft: %s: %s"
							% (name, type(exc).__name__, exc))
	elif action == 'disable':
		if not live:
			raise ToolError("%s is not enabled" % name)
		if kind == 'indicators':
			users = [code for code, state in takers(service, name) if state == 'enabled']
			if users:
				raise ToolError("%s is taken by %s, enabled: disable %s first"
								% (name, ', '.join(users), 'it' if len(users) == 1 else 'them'))
			uploaded.unloadIndicator(name)
		else:
			ledger.STRATEGIES.pop(name, None)
			sys.modules.pop(uploaded.moduleName(name), None)
		where = os.path.join(uploaded.root(dataDir(service), kind), 'drafts')
		os.makedirs(where, exist_ok=True)
		for a in (live, live[:-3] + '.json'):
			if os.path.exists(a):
				os.replace(a, os.path.join(where, os.path.basename(a)))
	elif action == 'delete':
		# an enabled one is disabled first: what the page asked about (uses)
		if live and not draft:
			act(service, name, 'disable')
			draft = uploaded.drafts(dataDir(service), kind).get(name)
		if not draft:
			raise ToolError("no uploaded strategy or indicator %r" % name)
		for a in (draft, draft[:-3] + '.json'):
			if os.path.exists(a):
				os.remove(a)
	else:
		raise ToolError("the action is enable, disable or delete")
	return status(service)


def uses(service, name):
	"""
	What runs `name` now, a line each, for the page to say before it is
	disabled or deleted: the live sessions trading it, with their open trades,
	and the simulations. Nothing here is stopped - that stays the user's. For
	an indicator: the strategies that take it.
	"""
	if kindOf(service, name) == 'indicators':
		return ["%s strategy %s takes it%s" % (
			state, code, ": disable that first" if state == 'enabled' else ": its backtests will fail")
			for code, state in takers(service, name)]
	out = []
	live = service.live
	for session in live.ids():
		try:
			meta = live.meta(session)
			if (meta.get('fields') or {}).get('strategy') != name or not live.alive(meta):
				continue
			held = len(live.summary(session)['open'])
		except (web().livesessions.LiveError, OSError, ValueError):
			continue
		out.append("live session %s on %s %s: %s - it keeps trading it until stopped on the "
				   "live page" % (session, meta.get('provider'), meta.get('accountName') or meta.get('account'),
								  "%d open trade%s" % (held, '' if held == 1 else 's') if held else "no open trade"))
	sweep = getattr(service, '_sweep', None) or {}
	grid = sweep.get('grid') or {}
	swept = sweep.get('running') and name in (
		web().gridValues('strategy', grid['strategy']) if 'strategy' in grid
		else [(sweep.get('fields') or {}).get('strategy')])
	if swept:
		out.append("a sweep with it, %d of %d runs done: the runs left will fail"
				   % (len(sweep.get('done') or ()), sweep.get('total') or 0))
	# the backtest running now, when it is not that sweep's: a single run, or
	# the mix simulated together. ponytail: a mix's queued runs are not seen,
	# that would take reading each run's fields
	progress = service._progress
	if not swept and progress.get('running') and progress.get('strategy') == name:
		out.append("a backtest of it is running now: it goes on to its end")
	return out


# --------------------------------------------------------------- the routes

def reply(handler, status, body, kind='application/json', headers=()):
	data = body if isinstance(body, bytes) else (
		body.encode('utf-8') if isinstance(body, str) else json.dumps(body).encode('utf-8'))
	handler.send_response(status)
	handler.send_header('Content-Type', kind + '; charset=utf-8')
	handler.send_header('Content-Length', str(len(data)))
	handler.send_header('Cache-Control', 'no-store')
	for key, value in headers:
		handler.send_header(key, value)
	handler.end_headers()
	handler.wfile.write(data)
	return True


def publicBase(handler):
	"""Where the assistant reaches this service: see the module docstring."""
	configured = getattr(handler.service.setup, 'PUBLIC_URL', None)
	if configured:
		return configured.rstrip('/')
	return '%s://%s%s' % (handler.headers.get('X-Forwarded-Proto') or 'http',
						  handler.headers.get('X-Forwarded-Host') or handler.headers.get('Host')
						  or 'localhost', (handler.headers.get('X-Forwarded-Prefix') or '').rstrip('/'))


def body(handler):
	try:
		length = int(handler.headers.get('Content-Length') or 0)
	except ValueError:
		length = 0
	return handler.rfile.read(min(length, 4 << 20)) if length > 0 else b''


def form(handler):
	return dict((k, v[0]) for k, v in urllib.parse.parse_qs(
		body(handler).decode('utf-8', 'replace'), keep_blank_values=True).items())


def route(handler, method, path, query):
	"""Serve an MCP, OAuth or settings route; False for any other."""
	authority = handler.service.oauth
	if path == '/mcp':
		if method != 'POST':
			return reply(handler, 405, {'error': "POST only"}, headers=[('Allow', 'POST')])
		base = publicBase(handler)
		auth = handler.headers.get('Authorization') or ''
		bearer = auth[7:].strip() if auth[:7].lower() == 'bearer ' else None
		if not authority.allowed(bearer):
			return reply(handler, 401, {'error': 'invalid_token'}, headers=[(
				'WWW-Authenticate', 'Bearer resource_metadata="%s/.well-known/oauth-protected-resource"'
				% base)])
		try:
			message = json.loads(body(handler) or b'null')
		except ValueError:
			return reply(handler, 400, {'jsonrpc': '2.0', 'id': None,
										'error': {'code': -32700, 'message': "not JSON"}})
		# the OAuth client's own name; with the secret in the header, the only
		# name there is is the program's User-Agent
		# a program's token is its role and name ("pc: my PC"), the secret is
		# "token: <User-Agent>", and an OAuth client its own name, which may
		# not pass for either
		key = authority.keyOf(bearer)
		client = authority.holder(bearer)
		if client is not None and role(client):
			client = 'oauth %s' % client
		client = client or ('%s: %s' % (key['role'], key['name']) if key else 'token: %s' % (
			handler.headers.get('User-Agent') or 'no user agent')[:60])
		if isinstance(message, list):
			answers = [a for a in (handle(handler.service, m, base, client) for m in message) if a]
		else:
			answers = handle(handler.service, message, base, client)
		return reply(handler, 200, answers) if answers else reply(handler, 202, b'')
	if path.startswith('/.well-known/oauth-protected-resource'):
		return reply(handler, 200, authority.resourceMetadata(publicBase(handler)))
	if path.startswith(('/.well-known/oauth-authorization-server',
						'/.well-known/openid-configuration')):
		return reply(handler, 200, authority.serverMetadata(publicBase(handler)))
	try:
		if path == '/oauth/register' and method == 'POST':
			try:
				asked = json.loads(body(handler) or b'{}')
			except ValueError:
				asked = None
			return reply(handler, 201, authority.register(asked))
		if path == '/oauth/authorize':
			params = form(handler) if method == 'POST' else dict((k, v[0]) for k, v in query.items())
			client, _ = authority.request(params)
			if method == 'POST':
				back = authority.decide(params, publicBase(handler))
				if back:
					return reply(handler, 302, b'', headers=[('Location', back)])
			return reply(handler, 403 if method == 'POST' else 200,
						 authority.consentPage(client, params, wrong=method == 'POST'), 'text/html')
		if path == '/oauth/token' and method == 'POST':
			return reply(handler, 200, authority.token(form(handler)))
	except oauth.OAuthError as exc:
		if path == '/oauth/authorize':
			return reply(handler, exc.status, '<!doctype html><title>parity-deriva</title><p>%s</p>'
						 % oauth.html.escape(str(exc)), 'text/html')
		return reply(handler, exc.status, {'error': exc.code, 'error_description': str(exc)})
	if not path.startswith('/api/mcp'):
		return False
	# the settings page's own routes, behind whatever guards the pages
	try:
		if method == 'GET' and path == '/api/mcp':
			return reply(handler, 200, status(handler.service))
		if method == 'GET' and path == '/api/mcp/docs':
			return reply(handler, 200, {'guide': GUIDE, 'reference': reference(),
										'allowed': sorted(uploaded.ALLOWED),
										'refused': sorted(uploaded.FORBIDDEN)})
		if method == 'GET' and path == '/api/mcp/uses':
			return reply(handler, 200, {'uses': uses(handler.service, handler.one(query, 'name') or '')})
		if method == 'GET' and path == '/api/mcp/source':
			return reply(handler, 200, source(handler.service, handler.one(query, 'name') or ''))
		if method != 'POST' or handler.headers.get('X-Parity-Deriva') != '1':
			return reply(handler, 403, {'error': "missing X-Parity-Deriva header"})
		if path == '/api/mcp/secret':
			return reply(handler, 200, {'secret': authority.newSecret()})
		if path == '/api/mcp/secret/show':
			return reply(handler, 200, {'secret': authority.shownSecret()})
		if path == '/api/mcp/disconnect':
			authority.disconnect()
			return reply(handler, 200, status(handler.service))
		if path in ('/api/mcp/keys', '/api/mcp/keys/drop'):
			# {"name", "role"} makes a program's token, {"name"} on drop removes it
			try:
				asked = json.loads(body(handler) or b'{}')
				if path == '/api/mcp/keys':
					authority.newKey(asked.get('name'), asked.get('role'))
				else:
					authority.dropKey(str(asked.get('name') or ''))
			except (ValueError, AttributeError) as exc:
				raise ToolError(str(exc) or 'the body is {"name", "role"}')
			return reply(handler, 200, status(handler.service))
		if path == '/api/mcp/strategy':
			try:
				asked = json.loads(body(handler) or b'{}')
			except ValueError:
				asked = {}
			return reply(handler, 200, act(handler.service, str(asked.get('name') or ''),
										   asked.get('action')))
		if path == '/api/mcp/import':
			# a file saved from the code's dialog, back as a draft: the same
			# checks as an assistant's submit_strategy. A trade server takes
			# code only from the archive's push
			refused = servers.missing(handler.service.setup, 'test', 'archive')
			if refused:
				raise ToolError(refused)
			try:
				asked = json.loads(body(handler) or b'{}')
			except ValueError:
				asked = {}
			submit(handler.service, asked, 'imported from a file',
				   'indicators' if asked.get('kind') == 'indicators' else 'strategies')
			return reply(handler, 200, status(handler.service))
		if path in ('/api/mcp/news', '/api/mcp/news/drop'):
			try:
				asked = json.loads(body(handler) or b'{}')
			except ValueError:
				asked = {}
			if path == '/api/mcp/news':
				return reply(handler, 200, postNews(handler.service, asked))
			return reply(handler, 200, dropRequest(handler.service, str(asked.get('id') or ''), 'news'))
		if path == '/api/mcp/request':
			try:
				asked = json.loads(body(handler) or b'{}')
			except ValueError:
				asked = {}
			return reply(handler, 200, dropRequest(handler.service, str(asked.get('id') or '')))
		if path == '/api/mcp/pull':
			try:
				asked = json.loads(body(handler) or b'{}')
			except ValueError:
				asked = {}
			return reply(handler, 200, pullRequest(handler.service, str(asked.get('name') or '')))
	except (ToolError, uploaded.UploadError, sandbox.SandboxError) as exc:
		return reply(handler, 400, {'error': str(exc)})
	return reply(handler, 404, {'error': "no route %s" % path})
