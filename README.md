# parity-deriva

An event-driven lab for finding statistically interesting price patterns and,
once one looks promising, trading it for real - with a simulated twin of the
broker running alongside the live execution handler on the same event stream.
The two are compared step by step; when reality drifts too far from the model,
the divergence itself is the signal, and it can raise an alarm or stop trading.
That is where the name comes from: *parity* is the invariant you want between
the simulated and the real side, *deriva* is what you measure when it breaks.

There are two brokers behind it, OANDA and eToro, chosen with one setting.
Instruments are whatever the chosen one offers - the work so far has been on
DE30_EUR and EUR_USD, so not only forex. Historical candles are warehoused
locally in HDF5 so research runs offline, which on eToro is a shallower
warehouse than on OANDA for reasons set out below.

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
* **Two brokers** - `trading/providers.py` names the handlers each broker
  needs and, more to the point, declares what each broker *cannot* do. A
  wiring asks for the capabilities it needs and fails at startup when they
  are missing, so an unsupported combination is a refusal rather than a run
  that looks fine and is not. `scripts/live.py` is one wiring for either.
* **Audit trail** - every event is written to a JSONL log and can be replayed.
* **Performance** - `performance/analyze.py` reports win/loss statistics,
  consecutive runs and three flavours of optimal *f* over the closed trades
  pulled from the account.
* **Tests** - 865 tests, no network access required.

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

5) At this stage, if you simply wish to carry out practice or live trading then run ```python scripts/live.py --dry-run```, which prints the stack it would build and the chosen broker's capabilities without touching the network. Drop ```--dry-run``` to run it. That wiring takes the broker as an argument and registers the strategy, the money manager, the real execution handler, the simulator shadowing it and the parity monitor comparing the two:

```
python scripts/live.py --dry-run
python scripts/live.py --provider oanda --instrument DE30_EUR --granularity M5
python scripts/live.py --provider etoro --instrument EUR_USD --granularity H1
```

The older wirings are still there: ```python trading/run.py``` wires an ```AG01``` strategy, a ```MoneyManager```, the OANDA execution handler and the price/transaction streams into the ```Engine```, and ```scripts/t01.py``` .. ```t05.py``` and ```onlydata.py``` hold further ready-made OANDA stacks. None of those registers the parity monitor, so the alarm is not watching them. Do not point any of these at a live account until you have read what they do!

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

## Choosing the broker: OANDA or eToro

One setting, and the whole stack follows:

```
PROVIDER = os.environ.get('PARITY_DERIVA_PROVIDER', 'oanda')   # etc/settings.py
```

`DOMAIN` still decides practice against real, for both. On eToro that is not
a different host but a different set of routes (`/trading/execution/demo/orders`
against `/trading/execution/orders`), and `/demo` goes in a different place
per route family, so `lib/etoro.py` spells out both variants of every route
rather than transforming one into the other.

The event bus does not change. Strategies, the money manager, the simulator
and the parity monitor see the same events either way. What changes is what
the broker on the other end can do, and that is declared rather than
discovered:

```
python scripts/live.py --dry-run --provider etoro
```

prints the declaration. `trading/providers.py` holds it, a wiring asks for
what it needs with `providers.require()`, and a missing capability is a
startup error naming it. The alternative is worse than a crash: a strategy
buying the high of a price series it believes is the ask.

### What eToro does not have

| | OANDA | eToro |
| --- | --- | --- |
| candles by date range | `from`/`to`, unlimited | **no** - only the last N, N ≤ 1000 |
| bid/ask candles | three OHLC series | **one series**, no split |
| price stream | pushed | **polled** |
| transaction stream | pushed | **polled** |
| STOP against LIMIT | different orders | **both become `mit`** |
| which leg closed a trade | stated | **inferred from the closing rate** |
| order expiry | `gtdTime` | **none** |
| order result | in the reply | **200 means accepted, then poll** |
| write rate limit | generous | **20 / 60s**, shared across every execution route |

Five of those needed a decision rather than a translation.

**One price series.** AG01 and AG02 read a candle's ask and bid - they buy
the high of the ask and stop out at the low of the bid - and eToro serves one
OHLC. No measurement recovers two numbers from one, so bid and ask are a
model or they are nothing. `ETORO_SPREAD` is unset by default, which leaves
candles carrying mid only, makes the provider decline `bid_ask_candles`, and
makes those strategies refuse to start with a message saying why. Measure
your own before setting it:

```
python scripts/etoro_spread.py --instrument EUR_USD --samples 60
```

Whatever you set is a constant standing in for something that varies by the
hour, and the difference is what the parity alarm will be measuring. That is
the right place for it: the model belongs under the alarm, not above it.

**No history to speak of.** The candle route answers "the last N candles",
N at most 1000, with no `from` or `to`. So `data/bulksaver.py` cannot be
pointed at eToro, nothing can be backfilled, and an offline run makes one
pass and stops - asking again would return the same window. Keep the research
and the warehouse on OANDA.

**One resting order type.** eToro has `mkt`, `mit` (market-if-touched) and
`limitIOC`. A resting entry is `mit` with a trigger rate, and that single type
covers AG01's breakout STOP above the market and AG02's fade LIMIT below it.
The strategies and the simulator tell the two apart; the broker cannot. The
provider declares `distinct_stop_limit` false rather than pretending, and
where it matters the disagreement is exactly what the parity monitor exists
to surface.

**No expiry.** The strategies bracket one reversal with orders meant to die
at the end of the day, and eToro leaves a resting order resting. So
`data/etoro.py` cancels it once the `gtdTime` the order was issued with has
passed, publishing an `OrderCancelEvent` so the real book and the simulator's
drop it together. That is our action, not the broker's, it is logged each
time, and `ETORO_ENFORCE_EXPIRY = False` turns it off.

**No closing reason.** OANDA's transaction says which leg took the trade.
eToro reports the rate it closed at, and `data/etoro.closeReason()` reads the
leg off that: the stop and the target bound the interval the trade lived in,
so a close at or beyond a level reached it and a close strictly between them
reached neither and was something else - a manual close, a margin call, a
gap. Stated that way the rule needs no invented tolerance. Where it errs it
declines to judge: a target that slipped and filled just inside its level is
reported `UNKNOWN` rather than claimed as a target.

`trading/parity.py` therefore treats an `UNKNOWN` on either side as
**undecidable** rather than as a divergence, and keeps it out of the window
the mismatch rate is measured over. Counting our own ignorance as the market
disagreeing with the simulator would have the alarm fire loudest on the
broker that explains itself least, and counting it as agreement would dilute
the rate - nineteen unjudgeable trades would hide one real mismatch under a
5% threshold. `max_undecided` in `PARITY_ALARM` can alarm on being blind
instead, and like every other threshold there it is off unless you set it.

### Configuring eToro

Credentials come from the environment. Use **either** a bearer token **or**
the key pair - a request carrying both is rejected with 422, so setting both
raises rather than being sent:

```
export ETORO_ACCESS_TOKEN=...      # OAuth bearer token
# or
export ETORO_USER_KEY=...          # the user key ("secret")
export ETORO_API_KEY=...           # the application key ("public")
```

`ETORO_API_DOMAIN` defaults to `public-api.etoro.com`, and the default is
there because that host was checked rather than guessed: it holds a
certificate issued to that name, unauthenticated it answers with the
`gatewayErrorEnvelope` shape the published OpenAPI documents for a missing
key - which a different service would not - and `GET /api/v1/me` on it
returns the account. It stays an environment variable because the tooling
describes the host as a property of the deployment, so a partner application
may be given another one; set it empty and the client refuses to be built
rather than reaching somewhere unintended.

Then the instruments. This project names them the way OANDA does and eToro
keys everything by a numeric id, and nothing derives one from the other, so
`ETORO_INSTRUMENTS` has to be filled in:

```
python scripts/etoro_instruments.py EURUSD GER40
```

prints what the API answers, for you to check and paste. It deliberately
stops there rather than resolving ids at runtime, where a search result would
be quietly deciding which market the money goes into.

Two entries are already in, resolved against the live API: `EUR_USD` is
eToro's `EURUSD`, instrument 1, and `DE30_EUR` is `GER40`, instrument 32.
That second pairing deserves a look before you trade it - eToro has no GER30
and no DE30, and GER40 is a forty-constituent index where OANDA's DE30_EUR
tracks thirty. The two are not the same basket, so a strategy calibrated on
one is not calibrated on the other, and the parity monitor comparing a GER40
account against a simulator fed DE30_EUR candles would be measuring the
index change as if it were execution.

The rest, all documented in `etc/settings.py`: `ETORO_LEVERAGE` (anything
above 1 makes eToro require a stop loss, which the execution handler then
refuses to send an order without - as it does for every short),
`ETORO_SETTLEMENT_TYPE` (optional, and left unset because the eligible values
differ per instrument, direction and leverage), and `ETORO_POLL_SECONDS`
(nothing is pushed, so this is the resolution at which candles, prices, fills
and closes arrive).

### What has been run against the live API

On 2026-09-10, against a demo account whose granted scopes are `trade.demo`
read and write with no `trade.real` of any kind - so these credentials cannot
place a real-money order at all:

* the client, the candle source and the rate source work, and the candle
  source's completeness check was **wrong** until this run caught it: it
  compared a UTC candle time against a local clock, so on a CEST host the bar
  still forming was published as complete. No fixture could have shown that.
* a resting `mit` order was accepted, reported `PendingTriggeredRate` with
  every field echoed back as sent, and cancelled to `Canceled`.
* re-sending an order under the same derived key was **refused** with a 400
  naming the original order, which is the idempotency property the uuid5 key
  exists for, enforced by eToro rather than merely intended.
* a market order filled, the poller assembled the fill from the lookup, the
  position was closed, and `closeReason()` read the manual close as `UNKNOWN`
  - correctly, since it reached neither the stop nor the target.

Two things the API does not do that the OpenAPI suggests it might.
`orders:lookup?referenceId=` answered 404 for an order the same route
returned by `orderId`, so the derived key is an idempotency key and not a
handle; nothing here relies on it. And a fill's `marketSpread` and `markup`
come back in a unit the API never states, which cannot be price - 0.01 on a
1.16 instrument would be a hundred pips, and the round trip's realised P&L
says otherwise.

What remains unexercised is the path that matters most and cannot be
rehearsed: a bracket left resting until one leg triggers and the other is
cancelled, its stop or target actually taken, and the parity monitor judging
the pair. That needs a market, not a test.

### One thing the eToro path cannot see

`data/etoro.EToroTransactions` learns which orders exist by listening to the
bus for the acknowledgements the execution handler publishes, then polls after
those. An order nobody acknowledged is invisible to it - a position opened
from eToro's own app, say. The parity monitor counts unpaired trades
separately for exactly this sort of reason, so such a position surfaces
there rather than silently.

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

## Orders that never fill

The money manager holds the account to one trade at a time, which it does by
refusing a new signal number while orders from the last one are outstanding.
That only works if it is told when orders stop being outstanding *without* a
trade, and there are three ways that happens:

* the broker expires them - OANDA's orders are GTD, so an untriggered
  bracket dies nightly and the transaction stream reports it
* the broker refuses them - a price precision error, a halted market
* nothing expires them, because the broker has no expiry, and
  `data/etoro.py` cancels them on our side once their `gtdTime` has passed

All three end up at `MoneyManager.orderDied`, and a signal group whose every
order is dead is released: dropped from the live index, kept in `processed`
for the audit trail, and the block on new signals lifted. A group still
holding a fill is not released, which is what keeps the one-trade-at-a-time
rule intact while a trade is open.

Both execution handlers publish a rejection as a `TransactionEvent` of type
`ORDER_REJECT`, so there is one thing to handle rather than one per broker -
OANDA's arrives in the reply to the order, eToro's is found later by polling,
and downstream neither is distinguishable from the other.

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