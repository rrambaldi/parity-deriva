# parity-deriva

An event-driven lab for finding statistically interesting price patterns and,
once one looks promising, trading it for real - with a simulated twin of the
broker running alongside the live execution handler on the same event stream.
The two are compared step by step; when reality drifts too far from the model,
the divergence itself is the signal, and it can raise an alarm or stop trading.
That is where the name comes from: *parity* is the invariant you want between
the simulated and the real side, *deriva* is what you measure when it breaks.

Instruments are whatever OANDA v3 offers - the work so far has been on
DE30_EUR and EUR_USD, so not only forex. Historical candles are warehoused
locally in HDF5 so research runs offline.

Started as a fork of [QSForex](https://github.com/mhallsmoore/qsforex) by
Michael Halls-Moore, and still MIT licensed (see below). Little of the
original tick-based backtester survives: the engine, the event hierarchy, the
data layer, the strategies and the execution path were rewritten around
candles and the OANDA v3 API, then migrated from Python 2 to Python 3.

# What is in it

* **Event bus** - `trading/engine.py` runs a thread per data source and fans
  every event out to every handler. Strategies, the money manager, execution,
  the simulator and the loggers are all handlers, so the same stack runs live,
  against recorded events, or against the local HDF5 store.
* **Shadow execution** - `backtest/oanda.py` is a local broker simulator that
  mimics the OANDA order lifecycle. Registered on the engine next to the real
  execution handler, it sees the same orders and the same candles, which is
  what makes a step-by-step comparison possible.
* **Pattern research** - the `BO` strategies place no orders. They measure how
  long a run of candle directions persists before it flips, broken down by
  hour of day and weekday, so a pattern can be judged before any money is at
  risk.
* **Trading strategies** - `AG01` and `AG02` bracket a two-bar reversal with a
  pair of opposite pending orders; `AG01` plays the breakout with STOP orders,
  `AG02` fades it with LIMIT orders. The money manager runs one trade at a
  time and cancels the losing leg as soon as the other fills.
* **Data warehouse** - `data/bulksaver.py` downloads years of candles into
  per-instrument HDF5 stores, one process per instrument, resuming where it
  left off; `scripts/check.py` audits those stores for missing bars.
* **Audit trail** - every event is written to a JSONL log and can be replayed.
* **Performance** - `performance/analyze.py` reports win/loss statistics,
  consecutive runs and three flavours of optimal *f* over the closed trades
  pulled from the account.
* **Tests** - 656 tests, no network access required.

# Installation and Usage

1) Visit http://www.oanda.com/ and setup an account to obtain the API authentication credentials, which you will need to carry out live trading. I explain how to carry this out in this article: https://www.quantstart.com/articles/Forex-Trading-Diary-1-Automated-Forex-Trading-with-the-OANDA-API.

2) Clone this git repository into a suitable location on your machine using the following command in your terminal: ```git clone git@github.com:rrambaldi/parity-deriva.git```.

3) Create a set of environment variables for all of the settings found in the ```etc/settings.py``` file. Alternatively, you can "hard code" your specific settings by overwriting the ```os.environ.get(...)``` calls for each setting. The environment variable names are the ones used in that file (```PARITY_DERIVA_CSV_DATA_DIR```, ```OUTPUT_RESULTS_DIR```, ```PARITY_DERIVA_DATA_DIR```, ```PARITY_DERIVA_LOG_DIR```, ```OANDA_API_ACCESS_TOKEN```, ```OANDA_API_ACCOUNT_ID```):

```
# The data directory used to store your backtesting CSV files
CSV_DATA_DIR = "/path/to/your/csv/data/dir"

# The directory where the backtest.csv and equity.csv files 
# will be stored after a backtest is carried out
OUTPUT_RESULTS_DIR = "/path/to/your/output/results/dir"

# Change DOMAIN to "real" if you wish to carry out live trading
DOMAIN = "practice"

# Your OANDA API Access Token (found in your Account Details on their website)
ACCESS_TOKEN = "1234123412341234"

# Your OANDA Account ID (found in your Account Details on their website)
ACCOUNT_ID = "1234123412341234"

# Your base currency (e.g. "GBP", "USD", "EUR" etc.)
BASE_CURRENCY = "GBP"

# Your account equity in the base currency (for backtesting)
EQUITY = Decimal("100000.00")
```

4) parity-deriva requires **Python 3.11 or newer** (it was migrated off Python 2.7); the pinned requirements were verified on Python 3.12, and the newest NumPy/SciPy releases need 3.12. The code runs on both pandas 2.x and pandas 3.x. Create a virtual environment for the code and utilise pip to install the requirements. For instance in a Unix-based system (Mac or Linux) you might create such a directory as follows by entering the following commands in the terminal:

```
mkdir -p ~/venv/parity-deriva
cd ~/venv/parity-deriva
python3 -m venv .
```

This will create a new virtual environment to install the packages into. Assuming you cloned the repository into an example directory such as ```~/projects/parity_deriva/``` (change this directory below to wherever you installed it), then in order to install the packages you will need to run the following commands:

```
source ~/venv/parity-deriva/bin/activate
pip install -r ~/projects/parity_deriva/requirements.txt
```

That installs what the code imports. The wider research environment
(IPython, SciPy, scikit-learn) is separate and optional:

```
pip install -r ~/projects/parity_deriva/requirements-research.txt
```

This will normally install pre-built wheels for NumPy, SciPy, Pandas, Scikit-Learn, Matplotlib and PyTables. There are many packages required for this to work, so please take a look at these two articles for more information:

* https://www.quantstart.com/articles/Quick-Start-Python-Quantitative-Research-Environment-on-Ubuntu-14-04
* https://www.quantstart.com/articles/Easy-Multi-Platform-Installation-of-a-Scientific-Python-Stack-Using-Anaconda

You will also need to create a symbolic link from your ```site-packages``` directory to your installation directory in order to be able to call ```import parity_deriva``` within the code. To do this you will need a command similar to the following:

```
ln -s ~/projects/parity_deriva/ ~/venv/parity-deriva/lib/python3.12/site-packages/parity_deriva
```

Make sure to change ```~/projects/parity_deriva``` to your installation directory and ```~/venv/parity-deriva/lib/python3.12/site-packages/``` to your virtualenv site packages directory (adjust the version to the Python 3 you created the venv with). Alternatively, put the directory that *contains* ```parity_deriva``` on ```PYTHONPATH```.

You will now be able to run the subsequent commands correctly.

## Practice/Live Trading

5) At this stage, if you simply wish to carry out practice or live trading then you can run ```python trading/run.py```, which wires an ```AG01``` strategy, a ```MoneyManager```, the OANDA execution handler and the price/transaction streams into the ```Engine```. The ```scripts/``` directory holds further ready-made wirings (```t01.py``` .. ```t05.py```, ```onlydata.py```). Do not point any of these at a live account until you have read what they do!

If you wish to create a more useful strategy, then simply create a new class with a descriptive name, e.g. ```MeanReversionMultiPairStrategy```. Strategies driven by the ```Engine``` subclass ```ExecutionHandler``` and implement ```execute_event(event)``` (see ```strategy/AG01.py``` and ```strategy/BO.py```); the backtester's example strategies instead implement ```calculate_signals(event)``` and take the ```pairs``` list plus the ```events``` queue.

Please look at ```strategy/strategy.py``` for details.

## Backtesting

6) In order to carry out any backtesting it is necessary to generate simulated forex data or download historic tick data. If you wish to simply try the software out, the quickest way to generate an example backtest is to generate some simulated data. The current data format is the same as that provided by the DukasCopy Historical Data Feed at https://www.dukascopy.com/swiss/english/marketwatch/historical/.

To generate some historical data, make sure that the ```CSV_DATA_DIR``` setting in ```settings.py``` is to set to a directory where you want the historical data to live. You then need to run ```generate_simulated_pair.py```, which is under the ```scripts/``` directory. It expects a single command line argument, which in this case is the currency pair in ```BBBQQQ``` format. For example:

```
cd ~/projects/parity_deriva
python scripts/generate_simulated_pair.py GBPUSD
```

At this stage the script is hardcoded to create a single month's data for January 2014. That is, you will see individual files, of the format ```BBBQQQ_YYYYMMDD.csv``` (e.g. ```GBPUSD_20140112.csv```) appear in your ```CSV_DATA_DIR``` for all business days in that month. If you wish to change the month/year of the data output, simply modify the file and re-run.

7) Now that the historical data has been generated it is possible to carry out a backtest. The backtest file itself is stored in ```backtest/backtest.py```, but this only contains the ```Backtest``` class. To actually execute a backtest you need to instantiate this class and provide it with the necessary modules. 

The best way to see how this is done is to look at the example Moving Average Crossover implementation in the ```examples/mac.py``` file and use this as a template. This makes use of the ```MovingAverageCrossStrategy``` which is found in ```strategy/strategy.py```. This defaults to trading both GBP/USD and EUR/USD to demonstrate multiple currency pair usage. It uses data found in ```CSV_DATA_DIR```.

To execute the example backtest, simply run the following:

```
python examples/mac.py
```

**This will take some time.** On my Ubuntu desktop system at home, with the historical data generated via ```generate_simulated_pair.py```, it takes around 5-10 mins to run. A large part of this calculation occurs at the end of the actual backtest, when the drawdown is being calculated, so please remember that the code has not hung up! Please leave it until completion.

8) If you wish to view the performance of the backtest you can simply use ```output.py``` to view an equity curve, period returns (i.e. tick-to-tick returns) and a drawdown curve:

```
python backtest/output.py
```

And that's it! At this stage you are ready to begin creating your own backtests by modifying or appending strategies in ```strategy/strategy.py``` and using real data downloaded from DukasCopy (https://www.dukascopy.com/swiss/english/marketwatch/historical/).

If you have any questions about the installation then please feel free to email me at mike@quantstart.com.

If you have any bugs or other issues that you think may be due to the codebase specifically, they may well be inherited from upstream: https://github.com/mhallsmoore/qsforex/issues

## The parity alarm

`trading/parity.py` joins each live fill to its simulated counterpart on the
signal key and counts the disagreements. **Every threshold is yours to set**,
in `PARITY_ALARM` in ```etc/settings.py```: the window the rate is measured
over, the minimum sample below which it declines to judge, the tolerated
outcome-mismatch rate, the per-trade slippage, how many one-sided trades are
acceptable, and whether an alarm merely warns or publishes a
```StatusEvent('HALT')``` - which the money manager honours by refusing
further signals until a ```RESUME```. A check left unset is off, not
defaulted. `PARITY_ALARM_BY_INSTRUMENT` overrides any of it per instrument.

What is *not* a matter of taste is the floor. A simulator reads bars while an
account trades ticks, so when one bar holds both the stop and the target of an
open trade it cannot say which came first and has to guess. Size that guess on
your own data before choosing a threshold:

```
python scripts/divergence_band.py --instrument EUR_USD --granularity H1
```

On EUR_USD with AG01 on H1 over two months it reported a 1.23% coin-flip floor
when the simulator is fed H1 bars, and 0.09% when it is fed M1 - the concrete
reason to keep minute history for a strategy that signals on hours. A
threshold below the figure that applies to you will fire on the width of the
bars rather than on the market.

## Migrating a store written under Python 2

An HDF5 candle store created by the Python 2 version of this code cannot be
used by pandas 3. Nothing is wrong with the rows - both problems are metadata:

* the store's own descriptors were written as bytes, and opening it raises
  ```TypeError: a bytes-like object is required, not 'str'``` before any row
  is read
* its timestamp index is at nanosecond resolution while pandas 3 gives new
  timestamps microsecond resolution, so appending a freshly downloaded block
  raises ```TypeError: incompatible kind in col [datetime64[ns] -
  datetime64[us]]``` - which is what ```data/bulksaver.py``` does on every run

One pass fixes both, in place, keeping a ```.bak``` copy:

```
python scripts/migrate_store.py --dry-run    # report, change nothing
python scripts/migrate_store.py              # every store in DATA_DIR
```

It is idempotent, so a store that needs nothing is reported and skipped.

# Tests

The project ships a test suite under ```parity_deriva/tests/``` (plain
```unittest```, no network access, no OANDA account needed). Run it with:

```
python -m unittest discover -s parity_deriva -p '*_test.py'
```

That also picks up the two original ```portfolio/*_test.py``` modules. See
```parity_deriva/tests/README.md``` for what each module covers.

# License Terms

Copyright (c) 2015 Michael Halls-Moore

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

# Forex Trading Disclaimer

Trading foreign exchange on margin carries a high level of risk, and may not be suitable for all investors. Past performance is not indicative of future results. The high degree of leverage can work against you as well as for you. Before deciding to invest in foreign exchange you should carefully consider your investment objectives, level of experience, and risk appetite. The possibility exists that you could sustain a loss of some or all of your initial investment and therefore you should not invest money that you cannot afford to lose. You should be aware of all the risks associated with foreign exchange trading, and seek advice from an independent financial advisor if you have any doubts.