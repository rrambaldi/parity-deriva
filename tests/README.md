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
| `execution_test.py` | `OANDAExecutionHandler` order wire format, rejects, cancels |
| `backtest_oanda_test.py` | `OANDABacktester`, the local broker simulator |
| `moneymanager_test.py` | `MoneyManager`: sizing, the signal index, OCO, trade close |
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
| `parity_test.py` | `trading/parity.py`, the configurable alarm, and MoneyManager honouring it |

## Class-level mutable state

Several components still declare their dictionaries and lists in the class
body (`Engine.handlers`, `MoneyManager.signals`, `ForexCandles.last`,
`CandleSaver.store`, ...), so instances share them within a process, and the
relevant `setUp` methods reset those attributes. `OANDABacktester` was moved
to per-instance state, because one simulator per instrument is the intended
deployment; the others have not been, and a test in each module pins the
sharing so the constraint stays visible.
