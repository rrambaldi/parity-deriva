# parity_deriva characterisation suite

Plain `unittest`, no plugins required. The suite is a *characterisation*
suite: it describes what the code does today, quirks and all, so that the
simulated side can be trusted as a reference against live execution and so
that a later refactor shows up as a failing test rather than as a silent
change in behaviour.

Where a test documents something that looks wrong, the docstring says so and
the assertion still pins current behaviour. Fixing such a bug is *meant* to
break its test - that is the signal.

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
| `execution_test.py` | `OANDAExecutionHandler` order wire format, rejects, cancels |
| `backtest_oanda_test.py` | `OANDABacktester`, the local broker simulator |
| `moneymanager_test.py` | `MoneyManager`: sizing, the signal index, OCO, trade close |
| `strategy_ag_test.py` | `AG01`/`AG02`, the straddle strategies |
| `strategy_bo_test.py` | `BO`..`BO06`, the pattern research engines |
| `data_sources_test.py` | `data/candles.py`, `data/resample.py`, `data/transaction.py`, `data/streaming.py` |
| `persistence_test.py` | `data/bulksaver.py`, `data/datasaver.py`, `data/replay.py`, `event/saver.py`, `event/replay.py` |
| `backtest_path_test.py` | the tick-CSV path: `data/price.py`, `strategy/strategy.py`, `Portfolio`, `Position`, drawdowns, `Backtest` |
| `analyze_test.py` | `performance/analyze.py` trade statistics and optimal f |

## Class-level mutable state

Several components declare their dictionaries and lists in the class body
(`Engine.handlers`, `OANDABacktester.orders`, `MoneyManager.signals`,
`ForexCandles.last`, `CandleSaver.store`, ...), so instances share them within
a process. The relevant `setUp` methods reset those attributes, and a test in
each module pins the sharing itself. Anything that builds more than one of
these objects - one simulator per instrument, say - has to do the same.
