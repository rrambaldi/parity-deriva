"""
A backtest in a process of its own, with a ceiling on its memory.

What web/mcp.py runs its backtests through, a draft nobody has read yet
included: a strategy that loops for ever or eats the memory takes this
process down and not the service, whose live sessions carry on. The parent
kills it at its time limit (run() below); the memory ceiling it sets itself,
before anything of the strategy is imported.

	python -m parity_deriva.web.sandbox < job.json

The job is {"memoryMb", "fields" (the page's form), "strategy": {"name",
"path"} for a draft or null, "check": true to only import the draft and
build it} - or {"memoryMb", "indicator": {"name", "path"}} to run an
indicator's draft over stored candles (uploaded.examine). The answer, on
stdout, is {"payload"} or {"strategy"} or {"indicator"} or {"error",
"trace"}. A print() in the strategy goes to stderr and not into the answer.
The draft indicators are there for the draft strategies that take them.
"""

import json
import os
import resource
import subprocess
import sys
import traceback

#: the package's parent directory, for the child's import path
TOP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class SandboxError(Exception):
	"""A sandboxed run that did not come back with an answer."""


def run(job, dataDir, timeout):
	"""The child's answer to `job`, or SandboxError when it has none."""
	env = dict(os.environ, PARITY_DERIVA_DATA_DIR=dataDir,
			   PYTHONPATH=os.pathsep.join([TOP] + [p for p in [os.environ.get('PYTHONPATH')] if p]))
	try:
		done = subprocess.run([sys.executable, '-m', 'parity_deriva.web.sandbox'],
							  input=json.dumps(job), capture_output=True, text=True,
							  timeout=timeout, env=env)
	except subprocess.TimeoutExpired:
		raise SandboxError("stopped after %d s: shorten the window, use a coarser "
						   "granularity, or look for a loop that never ends" % timeout)
	try:
		return json.loads(done.stdout)
	except ValueError:
		raise SandboxError("the backtest process died (exit %s): %s"
						   % (done.returncode, done.stderr[-2000:].strip()))


def main():
	job = json.load(sys.stdin)
	limit = int(job['memoryMb']) << 20
	resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
	answer = os.fdopen(os.dup(1), 'w')
	os.dup2(2, 1)
	draft, trial = job.get('strategy'), job.get('indicator')
	try:
		from parity_deriva.backtest import ledger
		from parity_deriva.strategy import uploaded
		from parity_deriva.web import service
		dataDir = os.environ['PARITY_DERIVA_DATA_DIR']
		uploaded.loadIndicators(dataDir, withDrafts=True)
		if draft:
			ledger.STRATEGIES[draft['name']] = uploaded.load(draft['name'], draft['path'])
		if trial:
			out = {'indicator': uploaded.examine(uploaded.loadIndicator(trial['name'], trial['path']),
												 candles(dataDir))}
		elif job.get('check'):
			name = draft['name']
			klass = ledger.load_strategy(name)
			klass(pairs=[getattr(klass, 'INSTRUMENT', None) or 'EUR_USD'],
				  granularity=getattr(klass, 'GRANULARITY', None) or 'H1')
			out = {'strategy': {
				'class': klass.__name__,
				'description': ' '.join((getattr(klass, 'DESCRIPTION', None) or '').split()),
				'instrument': getattr(klass, 'INSTRUMENT', None),
				'granularity': getattr(klass, 'GRANULARITY', None),
				'parameters': list(service.handlerFields(name))}}
		else:
			fields = job['fields']
			out = {'payload': service.Service().backtest(
				confirmed=True, **service.backtestArgs(lambda key: fields.get(key)))}
	except MemoryError:
		out = {'error': "the backtest went over its %s MB of memory" % job['memoryMb']}
	except BaseException as exc:
		out = {'error': "%s: %s" % (type(exc).__name__, exc),
			   'trace': traceback.format_exc()[-3000:]}
		# what examine() found wrong says it all: no trace to read through
		if type(exc).__name__ == 'UploadError' and trial:
			del out['trace']
	json.dump(out, answer)
	answer.close()


def candles(dataDir):
	"""
	The last uploaded.TRIAL_BARS H1 candles of EUR_USD, or of the first store
	there is, as a strategy is fed them: what an indicator is tried on.
	"""
	from parity_deriva.data import replay
	from parity_deriva.event.event import CandleEvent
	stores = sorted(n for n in os.listdir(dataDir) if n.endswith('.hd5'))
	if not stores:
		return []
	name = 'EUR_USD.hd5' if 'EUR_USD.hd5' in stores else stores[0]
	from parity_deriva.strategy import uploaded
	frame = replay.frame(os.path.join(dataDir, name), 'H1')[0].iloc[-uploaded.TRIAL_BARS:]
	columns = dict((column, frame[column].tolist()) for _s, _p, column in replay.COLUMNS)
	volume = frame['volume'].tolist()
	out = []
	for i, when in enumerate(frame.index):
		candle = CandleEvent(dict(
			[('time', when.to_pydatetime()), ('volume', int(volume[i])), ('complete', True)]
			+ [(side, dict((part, columns['%s_%s' % (side, part)][i]) for part in 'ohlc'))
			   for side in ('ask', 'bid', 'mid')]))
		candle.instrument, candle.granularity = name[:-4], 'H1'
		out.append(candle)
	return out


if __name__ == '__main__':
	main()
