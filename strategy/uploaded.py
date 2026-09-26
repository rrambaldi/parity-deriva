"""
Strategies written somewhere else - by an assistant over MCP (web/mcp.py) -
and kept under DATA_DIR rather than in this tree.

Two states, and the directory a file sits in is the state:

	DATA_DIR/strategies/drafts/NAME@N.py   a draft. Backtested only in a child
		process with a ceiling on memory and time (web/sandbox.py); never
		imported by the service, never offered to a live session.
	DATA_DIR/strategies/NAME@N.py          enabled: somebody read it and pressed
		enable on the settings page. From then on it is a strategy like the
		ones beside this file - the page, the sweeps and scripts/live.py
		import it.

N is its version, and its code - the name the page, the runs and the ledger
know it by - is "NAME N". A version is never replaced: submitting NAME again
is N + 1, and the one somebody read and ran stays as it was. Each file ends
with the line stamp() writes, which says which version it is and the server
it was written on, so a copy of it carries both wherever it goes (the public
repository, web/mcp.py pullRequest).

check() is a seatbelt against mistakes, not a sandbox against malice. Whoever
runs this runs it on their own machine, with an assistant of their own
writing the code, so the danger is a strategy that is wrong - one that reads
a file, loops for ever or eats the memory - rather than one written to escape.
The imports are a short list of what a strategy needs and the calls that
reach outside Python are refused; the ceiling in the child process is what
stops the loop and the memory.

Indicators are written and kept the same way, under DATA_DIR/indicators/ with
the same two states. One is a class fed a candle at a time, as
lib/streaming.Series is: add(candle) takes a closed bar, value() gives None
while it warms up, a number, or a tuple of numbers named by LINES. PANEL says
where the chart draws it: False on the candles, a price; True in the strip
under them, a number of its own scale. Fed one bar at a time it cannot read a
bar that has not closed, and the one class is what the strategy reads and what
the chart draws (drawn()), so there is no second copy to keep in step. A
strategy takes one with indicator('NAME N'): the enabled ones are loaded
before the strategies, and one that uses an indicator nobody enabled does not
load - so it cannot be enabled either. examine() is what a submit runs it
through.
"""

import ast
import importlib.util
import inspect
import logging
import math
import numbers
import os
import re
import sys
import time

from parity_deriva.trading.handler import ExecutionHandler

#: upper case, digits and dashes, like the names beside this file. No
#: underscore, so turning the dashes into one for the module name is one to one
NAME = re.compile(r'[A-Z][A-Z0-9-]{1,39}')

#: a version's file: NAME@N.py. Its code has a space for the @
FILE = re.compile(r'(%s)@([1-9][0-9]{0,5})\.py' % NAME.pattern)

#: the module-level constant stamp() writes, and the comment over it
STAMP = 'PARITY_DERIVA'
STAMP_NOTE = '# written by parity-deriva on submit, rewritten on every one: leave it as it is'

#: the largest source accepted; the longest strategy here is 13 kB
MAX_SOURCE = 200 * 1024

#: what a strategy may import: the numbers, and the parts of this project a
#: strategy is made of. Not the brokers in lib/, not etc/settings.py
ALLOWED = frozenset((
	'math', 'statistics', 'collections', 'itertools', 'functools', 'operator',
	'datetime', 'bisect', 'heapq', 'copy', 'dataclasses', 'typing', 'enum',
	'decimal', 'fractions', 'numbers', 'numpy',
	'parity_deriva.lib.streaming', 'parity_deriva.lib.indicators',
	'parity_deriva.lib.utils', 'parity_deriva.event.event',
	'parity_deriva.trading.handler',
))

#: this directory's modules that are not strategies to build on
NOT_BASES = frozenset(('plugins', 'uploaded', 'private', 'strategy'))

#: the builtins that reach outside the strategy's own numbers, or around the
#: attribute check below by spelling a name as a string
FORBIDDEN = frozenset((
	'eval', 'exec', 'compile', 'open', '__import__', 'input', 'breakpoint',
	'globals', 'locals', 'vars', 'getattr', 'setattr', 'delattr', 'memoryview',
	'exit', 'quit',
))

#: the double-underscore names a strategy has a use for
DUNDERS = frozenset(('__init__', '__name__', '__class__'))

HERE = os.path.dirname(os.path.abspath(__file__))

#: what is written here, each in a directory of its own under DATA_DIR
KINDS = ('strategies', 'indicators')

#: this module, from which a strategy imports indicator() and nothing else
SELF = 'parity_deriva.strategy.uploaded'

#: code -> class of every indicator loaded: the enabled ones, and in the
#: sandbox the drafts. What indicator() hands a strategy
LOADED = {}

#: the slowest an indicator may be, in microseconds a candle: ten years of M5
#: are 750,000 candles, so 1000 is some twelve minutes more on a backtest
MAX_US_PER_BAR = 1000

#: the candles examine() runs an indicator over, at most
TRIAL_BARS = 5000


class UploadError(Exception):
	"""A strategy that cannot be kept, enabled or loaded as asked."""


def root(dataDir, kind='strategies'):
	return os.path.join(dataDir, kind)


def drafts(dataDir, kind='strategies'):
	"""name -> path of every draft."""
	return _listed(os.path.join(root(dataDir, kind), 'drafts'))


def enabled(dataDir, kind='strategies'):
	"""name -> path of every enabled strategy, or indicator."""
	return _listed(root(dataDir, kind))


def code(name, version):
	"""The name a version is known by: "MY-EMA 3"."""
	return '%s %d' % (name, version)


def split(code):
	"""(name, version) of a code, or (code, None) for one without a version."""
	name, _, version = code.rpartition(' ')
	return (name, int(version)) if NAME.fullmatch(name) and version.isdigit() else (code, None)


def fileName(code):
	name, version = split(code)
	return '%s@%d.py' % (name, version)


def _listed(where):
	"""code -> path of every version in `where`, oldest version first."""
	try:
		names = os.listdir(where)
	except OSError:
		return {}
	found = [(m.group(1), int(m.group(2)), name) for m, name in
			 ((FILE.fullmatch(name), name) for name in names) if m]
	# a file from before versions is version 1, renamed the first time it is seen
	for name in names:
		if name.endswith('.py') and NAME.fullmatch(name[:-3]) \
				and not any(n == name[:-3] for n, _, _ in found):
			for a, b in ((name, name[:-3] + '@1.py'), (name[:-3] + '.json', name[:-3] + '@1.json')):
				if os.path.exists(os.path.join(where, a)):
					os.replace(os.path.join(where, a), os.path.join(where, b))
			found.append((name[:-3], 1, name[:-3] + '@1.py'))
	return dict((code(n, v), os.path.join(where, f)) for n, v, f in sorted(found))


def latest(dataDir, name, kind='strategies'):
	"""The code of a name's newest version, draft or enabled, or None."""
	versions = [split(c) for c in list(drafts(dataDir, kind)) + list(enabled(dataDir, kind))]
	mine = [v for n, v in versions if n == name]
	return code(name, max(mine)) if mine else None


def nextVersion(dataDir, name, kind='strategies'):
	found = latest(dataDir, name, kind)
	return split(found)[1] + 1 if found else 1


def stamp(source, name, version, server):
	"""
	`source` with the line that says which version it is and the server it
	was written on at its end, in place of any it had: an assistant that
	edits the source it read back sends the old one along.
	"""
	try:
		tree = ast.parse(source)
	except SyntaxError:
		tree = None
	lines = source.splitlines()
	drop = set()
	for node in (tree.body if tree else ()):
		if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == STAMP
												for t in node.targets):
			drop.update(range(node.lineno - 1, node.end_lineno))
	kept = [l for i, l in enumerate(lines) if i not in drop and l.strip() != STAMP_NOTE]
	while kept and not kept[-1].strip():
		kept.pop()
	return '\n'.join(kept + ['', '', STAMP_NOTE, '%s = %r' % (
		STAMP, {'name': name, 'version': version, 'server': server})]) + '\n'


def allowedModule(module):
	"""May a strategy import `module`, and so may an assistant read it?"""
	if module in ALLOWED:
		return True
	prefix = 'parity_deriva.strategy.'
	if module.startswith(prefix):
		name = module[len(prefix):]
		return '.' not in name and name not in NOT_BASES \
			and os.path.isfile(os.path.join(HERE, name + '.py'))
	return False


def check(source):
	"""The problems with a strategy's source, one line each; [] is none."""
	if len(source.encode('utf-8')) > MAX_SOURCE:
		return ["the source is over %d kB" % (MAX_SOURCE // 1024)]
	try:
		tree = ast.parse(source)
	except SyntaxError as exc:
		return ["line %s: %s" % (exc.lineno, exc.msg)]
	problems = []
	for node in ast.walk(tree):
		line = getattr(node, 'lineno', '?')
		if isinstance(node, ast.Import):
			modules = [alias.name for alias in node.names]
		elif isinstance(node, ast.ImportFrom) and node.module == SELF and not node.level:
			# the one name there is to take from here: what used() reads
			modules = ()
			if [(alias.name, alias.asname) for alias in node.names] != [('indicator', None)]:
				problems.append("line %s: from %s import indicator, and nothing else" % (line, SELF))
		elif isinstance(node, ast.ImportFrom):
			modules = ['.' * node.level + (node.module or '')]
		else:
			modules = ()
		for module in modules:
			if not allowedModule(module):
				problems.append("line %s: import of %s; allowed are %s and "
								"parity_deriva.strategy.<a strategy module>"
								% (line, module, ', '.join(sorted(ALLOWED))))
		if isinstance(node, ast.Name) and (node.id in FORBIDDEN or (
				node.id.startswith('__') and node.id not in DUNDERS)):
			problems.append("line %s: %s is not allowed in a strategy" % (line, node.id))
		if isinstance(node, ast.Attribute) and node.attr.startswith('__') \
				and node.attr not in DUNDERS:
			problems.append("line %s: .%s is not allowed in a strategy" % (line, node.attr))
		if isinstance(node, (ast.Global, ast.Nonlocal)):
			problems.append("line %s: global state is not allowed in a strategy" % line)
		if _taken(node) is False:
			problems.append("line %s: an uploaded indicator is named by its whole code written "
							"out, indicator('NAME N') or {'indicator': 'NAME N'} - the version "
							"the strategy was tested with" % line)
	return problems


def _taken(node):
	"""
	The code of an uploaded indicator a node names - indicator('NAME N'), or
	an INDICATORS entry {'indicator': 'NAME N'} for the chart; False for one
	that names none as it should; None for any other node.
	"""
	if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'indicator':
		given = node.args[0] if len(node.args) == 1 and not node.keywords else None
	elif isinstance(node, ast.Dict):
		named = [v for k, v in zip(node.keys, node.values)
				 if isinstance(k, ast.Constant) and k.value == 'indicator']
		if not named:
			return None
		given = named[0]
	else:
		return None
	given = given.value if isinstance(given, ast.Constant) else None
	return given if isinstance(given, str) and split(given)[1] else False


def used(source):
	"""The codes of the uploaded indicators a source takes or draws, in order."""
	try:
		tree = ast.parse(source)
	except SyntaxError:
		return []
	found = []
	for node in ast.walk(tree):
		taken = _taken(node)
		if taken and taken not in found:
			found.append(taken)
	return found


def moduleName(name):
	# under parity_deriva.strategy, which is what web/service.handlerFields
	# reads a strategy's parameters from. A code's space is _v: a name has no
	# lower case, so it stays one to one
	return 'parity_deriva.strategy.uploaded_' + name.replace('-', '_').replace(' ', '_v')


def _run(loaded, path):
	# from the source every time, never from __pycache__: a trial file is
	# written again under one name, and a cache kept by the second and the
	# size of the file would run the one written before it
	with open(path) as handle:
		exec(compile(handle.read(), path, 'exec'), vars(loaded))


def load(name, path):
	"""
	Import the strategy in `path` as `name`: (module, class), the entry
	backtest/ledger.STRATEGIES keeps. Its TAG becomes its name, so its
	orders cannot pass for another strategy's; its VERSION and SERVER_VERSION
	are its stamp's (None where it has none).
	"""
	module = moduleName(name)
	spec = importlib.util.spec_from_file_location(module, path)
	loaded = importlib.util.module_from_spec(spec)
	sys.modules[module] = loaded
	try:
		_run(loaded, path)
		found = [value for value in vars(loaded).values()
				 if isinstance(value, type) and value.__module__ == module
				 and issubclass(value, ExecutionHandler)]
		if len(found) != 1:
			raise UploadError("%s defines %d strategy classes; it needs exactly one "
							  "subclass of H4 (or another ExecutionHandler)"
							  % (name, len(found)))
	except BaseException:
		sys.modules.pop(module, None)
		raise
	stamped = vars(loaded).get(STAMP)
	stamped = stamped if isinstance(stamped, dict) else {}
	found[0].TAG = name
	found[0].VERSION = stamped.get('version') or split(name)[1]
	found[0].SERVER_VERSION = stamped.get('server')
	return module, found[0].__name__


def indicatorModule(code):
	# a module of its own, like a strategy's, under lib where the indicators are
	return 'parity_deriva.lib.uploaded_' + code.replace('-', '_').replace(' ', '_v')


def loadIndicator(code, path):
	"""Import the indicator in `path` as `code` and keep its class in LOADED: the class."""
	module = indicatorModule(code)
	spec = importlib.util.spec_from_file_location(module, path)
	loaded = importlib.util.module_from_spec(spec)
	sys.modules[module] = loaded
	try:
		_run(loaded, path)
		found = [value for value in vars(loaded).values()
				 if isinstance(value, type) and value.__module__ == module
				 and callable(getattr(value, 'add', None)) and callable(getattr(value, 'value', None))]
		if len(found) != 1:
			raise UploadError("%s defines %d indicator classes; it needs exactly one, with "
							  "add(candle) and value()" % (code, len(found)))
	except BaseException:
		sys.modules.pop(module, None)
		raise
	found[0].CODE = code
	LOADED[code] = found[0]
	return found[0]


def unloadIndicator(code):
	LOADED.pop(code, None)
	sys.modules.pop(indicatorModule(code), None)


def loadIndicators(dataDir, withDrafts=False):
	"""Every enabled indicator into LOADED, and the drafts as well when asked - in the sandbox only."""
	held = dict(drafts(dataDir, 'indicators')) if withDrafts else {}
	held.update(enabled(dataDir, 'indicators'))
	for name, path in held.items():
		try:
			loadIndicator(name, path)
		except (Exception, SystemExit) as exc:
			# as a broken strategy: said and left out. The strategies that
			# use it do not load in their turn, and say why
			logging.getLogger('parity_deriva.trading.trading').warning(
				"uploaded indicator %s does not load: %s", name, exc)


def indicator(code):
	"""
	The class of the uploaded indicator `code`, for a strategy to build its
	own from: Keltner = indicator('KELTNER 2'). The version is part of the
	code, so the strategy reads the one it was tested with and not whatever
	comes after it. An enabled one - in the sandbox, where a draft strategy
	runs, a draft as well.
	"""
	try:
		return LOADED[code]
	except KeyError:
		raise UploadError("no indicator %s: it has to be enabled on the settings page first "
						  "(a draft one is only there in the sandbox)" % code) from None


def examine(klass, candles):
	"""
	An indicator run over `candles` the way a strategy runs it, one at a time:
	what it gave, or UploadError with what is wrong. The checks are what the
	chart or a live strategy would otherwise find out later: a value that is
	not a number, no value at all, two copies that disagree (state kept on the
	class, or something other than the candles deciding), a "price" off the
	candles' scale that would squash them on the chart, and the time it takes.
	"""
	problems = []
	doc = ' '.join((klass.__doc__ or '').split('\n\n')[0].split())
	if not doc:
		problems.append("a docstring on the class: what it computes, for whoever reads it")
	panel = getattr(klass, 'PANEL', None)
	if not isinstance(panel, bool):
		problems.append("PANEL = True for the strip under the candles (a number of its own, "
						"as an ATR or an RSI), False for a price drawn on them")
	lines = getattr(klass, 'LINES', None)
	if lines is not None and not (isinstance(lines, tuple) and len(lines) > 1
								  and all(isinstance(l, str) and l for l in lines)):
		problems.append("LINES: a tuple of two or more names, one for each number value() gives")
	parameters = []
	try:
		signature = inspect.signature(klass).parameters.items()
	except (TypeError, ValueError):
		signature = ()
	for name, p in signature:
		if isinstance(p.default, bool) or not isinstance(p.default, (int, float)):
			problems.append("%s(%s): every argument of __init__ needs a number for its default"
							% (klass.__name__, name))
		else:
			parameters.append({'name': name, 'default': p.default})
	if not candles:
		problems.append("no candles to try it on: the data directory holds none")
	if problems:
		raise UploadError("\n".join(problems))

	def run():
		made, out = klass(), []
		started = time.perf_counter()
		for candle in candles:
			made.add(candle)
			out.append(made.value())
		return out, time.perf_counter() - started

	first, spent = run()
	width = None
	for i, value in enumerate(first):
		if value is None:
			continue
		many = isinstance(value, (tuple, list))
		row = value if many else (value,)
		if not row or not all(isinstance(x, numbers.Real) and math.isfinite(x) for x in row):
			raise UploadError("value() gave %r at bar %d: None while it warms up, a number, or a "
							  "tuple of numbers - finite ones" % (value, i))
		if width is None:
			width = len(row) if many else 0
		elif (len(row) if many else 0) != width:
			raise UploadError("value() gave %r at bar %d, and %s before: always the same shape"
							  % (value, i, "a number" if not width else "%d numbers" % width))
	if width is None:
		raise UploadError("value() gave no number in %d candles: a warm-up longer than that, "
						  "or no value ever" % len(candles))
	if width and not lines:
		raise UploadError("value() gives %d numbers: name them, LINES = ('upper', ...)" % width)
	if lines and len(lines) != (width or 1):
		raise UploadError("LINES names %d lines, value() gives %s"
						  % (len(lines), "one number" if not width else "%d numbers" % width))
	second, _ = run()
	for i, (a, b) in enumerate(zip(first, second)):
		if a != b:
			raise UploadError("a second %s made afresh gave %r at bar %d, where the first gave %r: "
							  "it keeps state on the class, or reads something other than the "
							  "candles" % (klass.__name__, b, i, a))
	rows = [[float(x) for x in (value if width else (value,))] for value in first if value is not None]
	if not panel:
		low = min(c.mid['l'] for c in candles)
		high = max(c.mid['h'] for c in candles)
		off = [x for row in rows for x in row if not low - (high - low) <= x <= high + (high - low)]
		if off:
			raise UploadError("PANEL = False draws it on the candles, but %d of its values are far "
							  "off them (the candles go from %g to %g; it gave %g): a number of its "
							  "own scale - PANEL = True" % (len(off), low, high, off[0]))
	perBar = spent / len(candles) * 1e6
	if perBar > MAX_US_PER_BAR:
		raise UploadError("%.0f microseconds a candle, and %d is the most: ten years of M5 would be "
						  "%.0f minutes more on every backtest" % (perBar, MAX_US_PER_BAR,
																 perBar * 750000 / 60e6))
	return {'class': klass.__name__, 'description': doc, 'panel': panel,
			'lines': list(lines) if lines else None, 'parameters': parameters,
			'bars': len(candles), 'warmup': next(i for i, v in enumerate(first) if v is not None),
			'usPerBar': round(perBar, 1),
			'values': [{'line': line, 'min': min(column), 'max': max(column), 'last': column[-1]}
					   for line, column in zip(lines or (klass.__name__,), zip(*rows))]}


def drawn(spec, candles):
	"""
	What the chart draws of an uploaded indicator a strategy lists in its
	INDICATORS - {'indicator': 'KELTNER 2', 'period': 20}, the rest of the
	keys but 'label' its arguments: a fresh one fed the backtest's candles,
	one curve a line, in lib/indicators.curve()'s shape.
	"""
	klass = indicator(spec['indicator'])
	args = dict((k, v) for k, v in spec.items() if k not in ('indicator', 'label'))
	made, values = klass(**args), []
	for candle in candles:
		made.add(candle)
		values.append(made.value())
	label = spec.get('label') or ' '.join([klass.__name__] + ['%g' % v for v in args.values()])
	lines = getattr(klass, 'LINES', None)
	return [{'kind': 'indicator', 'panel': bool(getattr(klass, 'PANEL', False)),
			 'label': label + (' ' + line if lines else ''),
			 'values': [None if v is None else float(v[i] if lines else v) for v in values]}
			for i, line in enumerate(lines or (None,))]


def backtest(dataDir):
	"""name -> (module, class) of every enabled strategy that imports, the indicators they take loaded first."""
	loadIndicators(dataDir)
	out = {}
	for name, path in enabled(dataDir).items():
		try:
			out[name] = load(name, path)
		except (Exception, SystemExit) as exc:
			# enabled and broken: said, and left out, rather than taking the
			# service or the live session down with it
			logging.getLogger('parity_deriva.trading.trading').warning(
				"uploaded strategy %s does not load: %s", name, exc)
	return out


def live(dataDir):
	"""The same, in scripts/live.py's shape: a handler on bid/ask candles."""
	return dict((name, entry + (('bid_ask_candles',), 'pairs'))
				for name, entry in backtest(dataDir).items())
