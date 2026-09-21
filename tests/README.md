# parity_deriva test suite

Plain `unittest`, no plugins required.

It began as a characterisation suite: it described what the code did, quirks
included, so that the defects could be found and then fixed without guessing
at the original intent. Those defects have since been fixed and the tests now
describe the intended behaviour. Many docstrings still say what the behaviour
used to be, because that history explains why some assertions look oddly
specific.

A failing test is therefore a real regression, not a deliberate pin.

## Running it

The package must be importable (`PYTHONPATH`, or the `site-packages` symlink
described in the top-level README). Nothing here touches the network, the
OANDA account or `PARITY_DERIVA_DATA_DIR`: HTTP is stubbed and every test that
writes gets its own temporary directory.

    # everything, including the two upstream portfolio test modules
    python -m unittest discover -s parity_deriva -p '*_test.py'

    # just this suite
    python -m unittest discover -s parity_deriva/tests -p '*_test.py'

    # one module, verbose
    python -m unittest -v parity_deriva.tests.backtest_oanda_test

    # under pytest, if preferred (*_test.py matches its default discovery)
    pytest parity_deriva

## Layout

| module | covers |
| --- | --- |
| `helpers.py` | fixtures: candle builders, event recorder, fake `requests`/`HTTPSConnection`, temp-dir base case |
| `lib_test.py` | `lib/utils.py`, `lib/ohlc.py`, `lib/candle.py`, `lib/oanda.py` |
| `event_test.py` | the whole `event/event.py` hierarchy and its coercion rules |
| `trading_test.py` | the handler ABC contract, `_set()`, and `Engine` (including its run loop, out of process) |
| `execution_test.py` | `OANDAExecutionHandler` order wire format, rejects (published, not dropped), cancels |
| `backtest_oanda_test.py` | `OANDABacktester`, the local broker simulator |
| `moneymanager_test.py` | `MoneyManager`: sizing, the signal index, OCO, trade close, and the signals whose orders all died |
| `strategy_ag_test.py` | `AG01`/`AG02`, the straddle strategies |
| `strategy_bo_test.py` | `BO`..`BO06`, the pattern research engines |
| `data_sources_test.py` | `data/candles.py`, `data/resample.py`, `data/transaction.py`, `data/streaming.py` |
| `persistence_test.py` | `data/bulksaver.py`, `data/datasaver.py`, `data/replay.py`, `event/saver.py`, `event/replay.py` |
| `backtest_path_test.py` | the tick-CSV path: `data/price.py`, `strategy/strategy.py`, `Portfolio`, `Position`, drawdowns, `Backtest` |
| `analyze_test.py` | `performance/analyze.py` trade statistics and optimal f |
| `migrate_store_test.py` | `scripts/migrate_store.py`, the Python 2 store migration |
| `resample_store_test.py` | `scripts/resample_store.py`, building a coarser granularity |
| `offline_test.py` | the offline half of the parallel design: the simulator's own events, `backtest/offline.py`, `backtest/driver.py` |
| `resolution_test.py` | `backtest/resolution.py`, reading a trade's outcome off candles |
| `divergence_band_test.py` | `scripts/divergence_band.py`, the threshold calibration |
| `parity_test.py` | `trading/parity.py`, the configurable alarm, MoneyManager honouring it, and the outcomes neither side can judge |
| `providers_test.py` | `trading/providers.py`: the broker registry, and the capability refusals |
| `etoro_test.py` | the eToro provider: `lib/etoro.py`, `data/etoro.py`, `execution/etoro.py` |
| `ig_test.py` | the IG provider: `lib/ig.py`, `data/ig.py`, `execution/ig.py` |
| `ib_test.py` | the Interactive Brokers provider: `lib/ib.py`, `data/ib.py`, `execution/ib.py` |
| `web_test.py` | the backtest viewer: `backtest/ledger.py`, `performance/report.py`, `web/service.py` |

## The viewer's three layers

`web_test.py` tests them as three, because they fail in three different ways.

The **ledger** turns a stream of events into a list of trades, and what is
pinned is the join: which fill belongs to which signal, and which of the
simulator's fills is an entry rather than the stop doing its job. That join is
the one thing no single event carries, and a wrong answer to it produces a
plausible table of nonsense rather than an error. One test there is about a
trap AG01 walks into - its long leg's stop sits at exactly its short leg's
entry price, so an acknowledgement matched on price alone files a child order's
id against a leg that never filled.

The **report** is arithmetic and is tested as arithmetic, one-sided runs
included. A run of nothing but winners is exactly the run somebody wants a
report for, and a profit factor printed as 0.00 there reads as the worst
possible result rather than the best.

The **service** is mostly refusals, and one of them is not a style question:
the instrument name reaches a file path, and the static handler sits next to
the source it must not serve.

## The broker modules' shape

Three of the four providers have a test module of their own, and each is
shaped by what its broker makes hard.

`ig_test.py` is about a broker that is nearly OANDA and is not: a session
rather than a token, a Version header that belongs to the route and not to the
API, two endpoints for what this project calls one kind of order, a reply that
is a reference rather than an outcome, and a close whose leg is inferred and
says so.

`ib_test.py` is about the two things that make Interactive Brokers the odd one
out. There is nothing to log into - the gateway is authenticated by a human in
a browser, so the interesting behaviour is a named refusal that says what to
go and do - and a bracket is three orders, which is more to get wrong on the
way out and, on the way back, the one thing eToro and IG cannot do: the child
that filled *is* the leg. Several tests exist only to pin that such a close
carries a reason the broker stated and does not claim to have inferred it.

## The eToro module's shape

`etoro_test.py` is mostly refusals, which is what the eToro code is mostly
made of: no API host means no client, an unmapped instrument means no order, a
granularity eToro does not serve means no data source, a short without a stop
means nothing sent. Each of those is a place where the alternative would be a
plausible-looking value nobody measured.

The rest is translation, pinned field by field, because the failure mode there
is not a crash. An order with the trigger in the wrong field, or a fill whose
price came from the wrong key, is a trade that happens at a price nobody
chose.

`MoneyManagerCompatibilityTest` is worth its own note. `Engine.run()` calls
`os._exit(1)` when a handler raises, so a synthesised close event missing a
field `MoneyManager.closeTrade` reads would take the process down with the
trade still open. Those tests feed the real money manager the events the
eToro poller builds.

## Class-level mutable state

Several components still declare their dictionaries and lists in the class
body (`Engine.handlers`, `MoneyManager.signals`, `ForexCandles.last`,
`CandleSaver.store`, ...), so instances share them within a process, and the
relevant `setUp` methods reset those attributes. `OANDABacktester` was moved
to per-instance state, because one simulator per instrument is the intended
deployment; the others have not been, and a test in each module pins the
sharing so the constraint stays visible.
