"""
Strategies written somewhere else - by an assistant over MCP (web/mcp.py) -
and kept under DATA_DIR rather than in this tree.

Two states, and the directory a file sits in is the state:

	DATA_DIR/strategies/drafts/NAME.py   a draft. Backtested only in a child
		process with a ceiling on memory and time (web/sandbox.py); never
		imported by the service, never offered to a live session.
	DATA_DIR/strategies/NAME.py          enabled: somebody read it and pressed
		enable on the settings page. From then on it is a strategy like the
		ones beside this file - the page, the sweeps and scripts/live.py
		import it.

check() is a seatbelt against mistakes, not a sandbox against malice. Whoever
runs this runs it on their own machine, with an assistant of their own
writing the code, so the danger is a strategy that is wrong - one that reads
a file, loops for ever or eats the memory - rather than one written to escape.
The imports are a short list of what a strategy needs and the calls that
reach outside Python are refused; the ceiling in the child process is what
stops the loop and the memory.
"""

import ast
import importlib.util
import logging
import os
import re
import sys

from parity_deriva.trading.handler import ExecutionHandler

#: upper case, digits and dashes, like the names beside this file. No
#: underscore, so turning the dashes into one for the module name is one to one
NAME = re.compile(r'[A-Z][A-Z0-9-]{1,39}')

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


class UploadError(Exception):
	"""A strategy that cannot be kept, enabled or loaded as asked."""


def root(dataDir):
	return os.path.join(dataDir, 'strategies')


def drafts(dataDir):
	"""name -> path of every draft."""
	return _listed(os.path.join(root(dataDir), 'drafts'))


def enabled(dataDir):
	"""name -> path of every enabled strategy."""
	return _listed(root(dataDir))


def _listed(where):
	try:
		names = os.listdir(where)
	except OSError:
		return {}
	return dict((name[:-3], os.path.join(where, name)) for name in sorted(names)
				if name.endswith('.py') and NAME.fullmatch(name[:-3]))


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
	return problems


def moduleName(name):
	# under parity_deriva.strategy, which is what web/service.handlerFields
	# reads a strategy's parameters from
	return 'parity_deriva.strategy.uploaded_' + name.replace('-', '_')


def load(name, path):
	"""
	Import the strategy in `path` as `name`: (module, class), the entry
	backtest/ledger.STRATEGIES keeps. Its TAG becomes its name, so its
	orders cannot pass for another strategy's.
	"""
	module = moduleName(name)
	spec = importlib.util.spec_from_file_location(module, path)
	loaded = importlib.util.module_from_spec(spec)
	sys.modules[module] = loaded
	try:
		spec.loader.exec_module(loaded)
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
	found[0].TAG = name
	return module, found[0].__name__


def backtest(dataDir):
	"""name -> (module, class) of every enabled strategy that imports."""
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
