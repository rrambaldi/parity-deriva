# parity-deriva

An event-driven lab for finding statistically interesting price patterns and,
once one looks promising, trading it for real - with a simulated twin of the
broker running alongside the live execution handler on the same event stream.
The two are compared step by step; when reality drifts too far from the model,
the divergence itself is the signal, and it can raise an alarm or stop trading.
That is where the name comes from: *parity* is the invariant you want between
the simulated and the real side, *deriva* is what you measure when it breaks.

There are four brokers behind it - OANDA, eToro, IG and Interactive Brokers -
chosen with one setting. Instruments are whatever the chosen one offers; the
work so far has been on DE30_EUR and EUR_USD, so not only forex. Historical
candles are warehoused locally in HDF5 so research runs offline, which on
eToro is a shallower warehouse than on the other three for reasons set out
below.

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
* **Four brokers** - `trading/providers.py` names the handlers each broker
  needs and, more to the point, declares what each broker *cannot* do. A
  wiring asks for the capabilities it needs and fails at startup when they
  are missing, so an unsupported combination is a refusal rather than a run
  that looks fine and is not. `scripts/live.py` is one wiring for all of
  them. None of the four is a subset of another, which is why they are
  declared rather than ranked.
* **Backtest viewer** - `scripts/web.py` serves a local page: the candles on
  top, the trades under them in the order they opened, and clicking a trade
  zooms the chart onto it with its entry, exit, stop and target drawn in. It
  runs the offline stack on the local warehouse and contacts no broker.
* **Audit trail** - every event is written to a JSONL log and can be replayed.
* **Performance** - `performance/analyze.py` reports win/loss statistics,
  consecutive runs and three flavours of optimal *f* over the closed trades
  pulled from the account.
* **Tests** - 1081 tests, no network access required.

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

## Choosing the broker: OANDA, eToro, IG or Interactive Brokers

One setting, and the whole stack follows:

```
PROVIDER = os.environ.get('PARITY_DERIVA_PROVIDER', 'oanda')   # etc/settings.py
```

`PROVIDER` is `oanda`, `etoro`, `ig` or `ib`.

`DOMAIN` still decides practice against real - but each broker means something
different by it. OANDA has two hosts. eToro has one host and two sets of
routes (`/trading/execution/demo/orders` against `/trading/execution/orders`),
with `/demo` in a different place per route family, so `lib/etoro.py` spells
out both variants rather than transforming one into the other. IG has two
hosts *and* two API keys, one per host, so a key that does not match `DOMAIN`
fails to authenticate rather than quietly reaching the other account. IB has
neither: its paper account is simply a different account id, `IB_ACCOUNT_ID`
is what chooses, and `--live` is therefore the only thing standing between you
and whichever account the gateway happens to be logged into.

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

### What each broker can do

| | OANDA | eToro | IG | IB |
| --- | --- | --- | --- | --- |
| bid/ask candles | yes | **no** (one series) | yes | **no** (one series) |
| candles by date range | yes | **no**, last N ≤ 1000 | yes | yes |
| price stream | pushed | polled | polled¹ | polled¹ |
| transaction stream | pushed | polled | polled¹ | polled¹ |
| STOP against LIMIT | different | **both `mit`** | different | different |
| which leg closed a trade | stated | **inferred** | **inferred** | stated² |
| order expiry | `gtdTime` | **none** | `GOOD_TILL_DATE` | **none** |
| order result | in the reply | poll after | poll `/confirms` | poll after |
| a bracket is | one order | one order | one order | **three orders** |

¹ Both brokers do push - IG over Lightstreamer, IB over a WebSocket on its
gateway - and neither protocol is one this project carries. A capability says
what *this stack* can do, not what the broker's documentation mentions, so
both are declared polled and the handlers poll.

² Not because IB's API is richer, but because of the row above it. Its
bracket is three orders, so the stop and the target have ids of their own and
the child that filled *names* the leg. See below.

Read the table by columns and no broker is a subset of another. IG is the
closest to OANDA and still cannot say which leg closed a trade; IB can say it
and cannot serve a bid and an ask; eToro is the poorest and is the only one
that cannot even tell a breakout order from a fade. That is why capabilities
are declared rather than ranked, and why `providers_test.py` has a test whose
only job is to fail if a future edit quietly makes one of them a subset of
another.

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
the mismatch rate is measured over. The same applies on IG, which reports a
closing level and no leg either. On OANDA and on IB it does not arise: both
name the leg, IB because its bracket is three orders and the child that filled
is the leg. Counting our own ignorance as the market
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

## IG

IG is the closest of the three non-OANDA brokers to OANDA, and where it
differs it differs in ways that needed a decision rather than a translation.

**It serves a real bid and a real ask.** Every one of open, high, low and
close comes as `{bid, ask, lastTraded}`, so AG01 buys the high of an ask that
IG quoted rather than of a modelled one. Nothing has to be configured for
this and there is no `IG_SPREAD`. `data/ig.py` computes mid as the average of
the two, because IG serves no mid - a strategy reading `candle.mid` on OANDA
is reading OANDA's own, and the two are not quite the same number.

**It logs in.** There is no long-lived token to paste. `POST /session` issues
tokens and every later request carries them, so `lib/ig.py` opens a session on
its first call and again when the tokens are refused - once, because a token
refused twice is a credential problem and hammering a login endpoint is how an
account gets locked. Two schemes exist and both are implemented: version 2
returns `CST` and `X-SECURITY-TOKEN` as *headers* and they last hours; version
3 returns an OAuth pair whose access token is measured in seconds, and the
reply says how many, so the figure is read rather than assumed. Version 2 is
the default, because a refresh that fails mid-session costs an order.

**The version belongs to the route.** `/prices` is version 3, `/confirms` is
version 1 and `/positions/otc` is version 2. `ROUTES` in `lib/ig.py` carries
the number next to the path so the two cannot drift apart and parse a schema
from another era into the same fields.

**Two endpoints for what this project calls one kind of order.** A market
order opens a position through `/positions/otc`; a resting order is a
*working order* through `/workingorders/otc`, typed `STOP` or `LIMIT`. So
unlike eToro, IG really does tell a breakout from a fade, and the provider
says so.

**It expires an order.** `GOOD_TILL_DATE` with a `goodTillDate`, so the
strategies' end-of-day expiry is the broker's to enforce and
`IG_ENFORCE_EXPIRY` is off by default. This is the one gap in the eToro path
that does not exist here.

**It does not say which leg closed a trade.** The transaction history reports
an open level and a close level and no leg, so `lib/closereason.py` reads the
leg off the closing level with exactly the rule the eToro path uses - the stop
and the target bound the interval the trade lived in - and says `UNKNOWN`
where the level belongs to neither clearly. The close event says it was
inferred, and `trading/parity.py` treats an `UNKNOWN` as undecidable.

**The reply to an order is a reference.** `POST` answers with a
`dealReference` and nothing else; what happened is read from
`GET /confirms/{reference}` afterwards. That confirmation is available only
briefly, which is why `IG_POLL_SECONDS` defaults low and why a deal that is
never confirmed is given up on with an error rather than in silence - it may
well still be live on the account, and the parity monitor will then count it
unpaired, which is the correct reading of what happened.

**History is metered by the week.** The per-minute request limits are the
usual kind and `lib/ig.py` paces against them. The historical price allowance
is not: it counts *data points*, 10,000 a week, shared by everything using the
same API key - including a browser session somebody left open. No local
counter can track that, so what is tracked instead is the figure IG returns in
the metadata of every price response, and a warning is logged when it runs
low. The failure it precedes is silent: the route starts refusing and a
backfill simply stops, hours before anyone looks at why.

### Configuring IG

```
export IG_API_KEY=...          # from My IG > Settings > API keys
export IG_IDENTIFIER=...       # your IG username
export IG_PASSWORD=...
export IG_ACCOUNT_ID=...       # optional; the session's own account if unset
```

A key belongs to **one** host, and this is measured rather than assumed. The
same demo key, on the same route, with nothing but the key on it:

```
demo-api.ig.com   401  {"errorCode":"error.security.client-token-missing"}
api.ig.com        403  {"errorCode":"error.security.api-key-invalid"}
```

The first says the key is fine and only the session is missing; the second
says the key does not exist here. `DOMAIN` picks the host, so a key that does
not match it fails to authenticate rather than reaching the other account -
and the two error codes are how to tell which of the two went wrong.

Worth knowing while debugging that: the read above is **not** a login. IG locks
an account after repeated failed logins, so a probe that answers "is this key
any good" should be a request that needs a session, never a `POST /session`
with a guess in it.

Then the epics, which are not derivable and not pre-filled:

```
python scripts/ig_instruments.py --details EURUSD
```

`--details` also reads each epic's dealing rules, which is where the minimum
stop distance lives. That number is worth having before the first order: IG
refuses a stop closer to the market than its instrument allows, and nothing in
this project moves a level to fit. A strategy's stop adjusted to satisfy a
broker rule is no longer that strategy's stop, so the order goes as asked and
the refusal is published as a rejection.

Paste the result into `IG_INSTRUMENTS`. Which one you deal is a decision, not
a lookup, and that is why nothing resolves an epic at runtime. On the demo
account `EURUSD` returns two markets and `Germany 40` returns **seven** - cash
at 25, 5 and 1 euro a point, two dated futures, a second 1-euro cash market
and a weekend one that is `EDITS_ONLY`. Picking the first would be picking one
of those at random.

Two things the dealing rules said that the names do not:

* **EUR/USD is denominated in USD** while the account is in EUR. The currency
  is required on every deal, and falling back to `BASE_CURRENCY` would have
  been wrong here - which is why nothing guesses it from the pair's name.
* **the minimum stop distance** is 2.0 points on EUR/USD and 8.0 on the DAX
  market this project uses, against 12.0 on the other one at the same value
  per point. AG01's stop is the low of a bar, so the tighter minimum is the
  one that refuses fewer orders, and that is why `IX.D.DAX.IFMM.IP` is the
  entry rather than `IX.D.DAX.IBE.IP`.

### `scalingFactor` is not a price divisor

Worth its own heading, because the obvious reading of it is wrong and the
cost of acting on it is four decimal places.

EUR/USD on the demo account reports `scalingFactor: 10000` and quotes a bid of
`1.14625`; its price rows come back as `1.14632`. So the prices are **not**
scaled and dividing by the factor would put every level miles from the market.
What the factor relates is *distances in points* to price units: the same
market's minimum stop distance is `2.0 POINTS`, which is `0.0002`. The DAX
reports `scalingFactor: 1` and quotes `25630.8`, which is the same rule seen
from the other side.

`lib/ig.py` therefore does not read that field at all. The manual override
exists, for a market where somebody measures a real discrepancy, and it is
keyed `priceDivisor` precisely so that pasting IG's own `scalingFactor` into
an instrument entry does nothing. `scripts/ig_instruments.py` prints the
factor and says the same thing next to it.

Orders are unaffected either way: this project sends `stopLevel` and
`limitLevel`, which are absolute prices. A version that sent `stopDistance`
would be sending points, and then the factor would matter.

## Interactive Brokers

IB offers three ways in and only one of them fits this stack. The TWS socket
API is an event-driven protocol needing a client library and a running desktop
application; FIX is institutional. The **Client Portal Web API** is JSON over
HTTP, which is what every other broker here speaks. The price of that choice
has to be stated plainly, because it is unlike the other three in one
important way:

> **There is no host to point at.** The Web API is served by a gateway you run
> yourself, normally on localhost, and a human being authenticates it in a
> browser. Nothing in this project can log in. It can only ask whether somebody
> already did, and say so clearly when they did not.

```
1. start the Client Portal Gateway
2. open https://localhost:5000 and log in
3. export IB_ACCOUNT_ID=... and run
```

The session times out on inactivity, so `lib/ib.py` keeps it alive on a timer
of its own rather than only when a poll happens to come round. It will still
need logging into again roughly daily, which is IB's rule and not one this
project can work around. A run that starts against a logged-out gateway raises
`IBNotAuthenticated` with the address in the message, rather than filling the
log with 401s.

Four more things are IB's own shape:

**A bracket is three orders.** IB has no stop-and-limit attached to an entry:
the stop and the target are separate child orders naming the entry as their
`parentId`, in one OCA group so that filling either cancels the other. Without
the group a closed trade leaves its other leg resting, and the next fill opens
a position nobody signalled.

That costs a more involved submission and buys the one thing the other two
polled brokers cannot give. When a child fills, **the broker has named the leg
that closed the trade** - this project placed that particular order as the
stop or as the target and IB is reporting that it filled. So `close_reason` is
true, the close event carries a reason nothing inferred, and it does not claim
otherwise. On IB, unlike eToro and IG, the parity monitor's `max_undecided`
should stay at zero.

**An order can be answered with a question.** `POST .../orders` may reply with
a message and an id instead of an order id, and the order exists only once
that id has been confirmed at `/iserver/reply/{id}`. This is not an error
path - it is the ordinary one, and most orders draw at least one question.
`lib/ib.IBAPI.place` runs that exchange, logs **every** question at warning
level whether or not it answers it, and refuses to answer any question
matching `IB_REFUSE_QUESTIONS` - which defaults to the ones about size and
about exceeding a limit, since those are the questions that precede an order
much larger than intended. A question that keeps coming back after five
confirmations is given up on: confirming forever is how an order gets placed
by accident.

**One price series per bar.** The history route serves a single OHLC, the way
eToro's does, so `IB_SPREAD` is the same model under the same rule - unset it
stays off, bars carry mid only, the provider declines `bid_ask_candles`, and
AG01 refuses to start and says why. Measure it before setting it: `IBRates`
polls the snapshot route, which is where IB *does* quote both sides.

**No expiry instant.** Time in force is `DAY`, `GTC` or an immediate variety,
so the strategies' end-of-day `gtdTime` is enforced on our side exactly as on
eToro: `data/ib.py` publishes an `OrderCancelEvent` when it passes, the
execution handler turns it into a delete, and the simulator drops the order
too. That is our action, not the broker's, and it is logged each time.

### Configuring IB

```
export IB_GATEWAY=localhost:5000    # where your gateway listens
export IB_ACCOUNT_ID=...            # no default: one login can hold several
```

`IB_ACCOUNT_ID` has no default and `lib/ib.py` refuses to be built without it.
One login can hold a paper account and a live one, they are not
interchangeable, and this is also checked against what the gateway's session
actually holds - discovering a mismatch from a rejection would mean
discovering it after an order went somewhere.

TLS verification is **off** by default, which is only defensible because of
where the client is talking: the gateway serves a certificate it generated for
itself, on a loopback address on your own machine. Point `IB_GATEWAY` at
another host and you are trusting whatever answers, so turn `IB_VERIFY_TLS`
back on with a certificate you installed.

Then the conids:

```
python scripts/ib_instruments.py EUR --sec-type CASH
```

`--sec-type` is worth using. Without it a currency symbol returns the futures
and the funds named after it too, and a search returns the most heavily traded
match first, which is not the same as the right one.

## What has been checked on IG and IB, and what has not

**Interactive Brokers: nothing.** No gateway has been run, no session opened,
no order sent. Everything on that path is read from the documentation and
pinned by tests, and the behaviour under a real account is not evidence yet.

**IG: the read path, on a demo account, on 2026-09-22.** What was exercised,
and what it settled:

* `POST /session` version 2 returns `CST` and `X-SECURITY-TOKEN` as headers,
  and `lib/ig.py` keeps them and authenticates with them. The account id came
  back in `currentAccountId`, which is the fallback the code already had.
* the price route serves a real bid and a real ask on all four of open, high,
  low and close, which is the one thing that lets AG01 run on IG unconfigured.
* **the completeness check is right.** At 22:12 UTC the H1 source emitted the
  19:00, 20:00 and 21:00 bars and withheld the 22:00 one that was still
  forming; the M5 source on the DAX did the same with 22:05 against 22:10.
  This is the exact check that was wrong on eToro until a demo account caught
  it, and IG's rows carry both a local `snapshotTime` and a `snapshotTimeUTC`
  for it to get wrong - `data/ig.py` reads the UTC one.
* the weekly history allowance is reported and counted down: 10000, then
  9993, then 9989. It is metered in data points, not requests.
* `GET /positions`, `GET /workingorders` and `GET /history/transactions` all
  answer 200 on an empty account, so the close-detection path is reachable
  rather than merely written.
* `GET /confirms/{reference}` for a reference that does not exist answers
  **404** `error.service.execution.find`, which is what `data/ig.py` already
  treats as "not yet, or aged out" while it counts attempts.

**IG: the order path, on the same demo account, the same night.** Orders were
sent - small ones, on the smallest contracts these markets deal in - and every
one of them found something. Eleven defects, and not one was reachable
from a test: each was code and test agreeing with each other about a payload
the broker does not send, or about an id that is a number until it is a name.

What the orders established:

* `POST /positions/otc` and `POST /workingorders/otc` both answer with the
  `dealReference` they were given, and a working order accepted at IG shows
  up in `GET /workingorders` with `GOOD_TILL_DATE` and the expiry that was
  sent. `DELETE /workingorders/otc/{dealId}` removes it.
* **a rejection arrives at the confirmation, not at the POST.** A stop closer
  than the instrument's minimum distance was accepted with a 200 and a deal
  reference, and refused a moment later at `GET /confirms` with
  `ATTACHED_ORDER_LEVEL_ERROR`. So the rejection path that matters is the one
  in `data/ig.py`; the one in `execution/ig.py` catches only what IG refuses
  before the deal exists at all.
* **the confirmation carries two status vocabularies, and the code read the
  wrong one.** The top-level `status` is the position's - `OPEN`, `CLOSED` -
  while `affectedDeals[].status` is the deal's - `OPENED`, `FULLY_CLOSED`.
  `data/ig.py` looked for `OPENED` at the top level, where it never appears,
  so **every market entry was filed as a resting working order** and no
  `ORDER_FILL` was ever published. The test that covered this passed, on an
  invented payload.
* **neither status tells a fill from a resting order.** A market deal that
  filled and a working order sitting on the book both come back `OPEN` /
  `OPENED`. Only the endpoint the order was sent to knows, so that is what
  decides it now.
* **a triggered working order was never reported at all.** Nothing promoted a
  resting order to a filled one - the comment claimed `GET /positions` would
  do it and no code did - so an entry that triggered was invisible, and
  `pollCloses`, which skips a resting position, lost the close as well. Every
  strategy here enters on a STOP, so this was the whole live path.
  `pollFills` now joins on the dealId, which a triggered position keeps from
  its working order, along with the `dealReference` this project chose. Both
  were measured on a DAX stop order that took 85 seconds to trigger.
* **the transaction history quotes levels as strings** - `"1.14646"` - where
  every other route sends numbers, so a close was reaching the ledger with a
  price nothing downstream could subtract.
* **the money manager could not hold an IG order at all.** It put every order
  id through `int()`, because OANDA and eToro number theirs. IG names a deal -
  `PD6e1b03...` - so the first acknowledgement raised inside the handler, and
  `trading/engine.py` answers an exception there with a critical log line and
  `os._exit(1)`: the trading process would have died on its first IG order.
  Ids are now compared as they arrive - a number stays a number, a name stays
  a name.
* **IG's transactions named no order.** The money manager reads `orderID`, the
  name OANDA's transactions use, and `data/ig.py` published `dealId` and
  `dealReference` and no `orderID` - so a fill or a close reached the manager
  as an event about nothing. Every IG transaction now carries the reference
  the acknowledgement established.
* **the losing leg could not be cancelled.** The money manager cancels by the
  id it was given, which here is the deal reference, and IG deletes a working
  order by `dealId`; `GET /workingorders` publishes the dealId and not the
  reference, so the two names for one order never appear together. The order
  is now found on the epic and the level - the same pair the simulator matches
  a cancel on - and a level that matches two orders cancels neither. Verified
  against the account: an order placed and then cancelled by reference alone.
* **rendering a cancel raised.** `OrderCancelEvent.info()` formatted the id
  with `%d`, and a test pinned the TypeError as intended behaviour. The first
  straddle sent to IG died on that line, mid-cancel, with one leg filled and
  the other still live on the account - which is the exact state the cancel
  exists to prevent.
* **a half-size order was logged as an order for nothing.** `SignalEvent` and
  `OrderEvent` rendered the size with `%d` too, so `units=0.5` - a normal size
  on IG and on eToro, and what the money manager produces from `--units 0.5` -
  printed as `0`.
* **the transaction history lags, and the close was read from it.** A
  position closed at 23:12:18 was still absent from `/history/transactions` at
  23:13:25, while an earlier close had appeared there within five seconds. The
  close is noticed within one poll, so the level came back missing and the
  close was published at a price of **0.0** - an event coerces an absent price
  to zero, and the ledger would have believed it. `/history/activity` is
  current, names the `affectedDealId`, and carries the level; it is now the
  source for the level, with the profit still taken from the transaction row
  when that arrives. A close whose deal has not been recorded anywhere yet is
  asked after again rather than published.
* **closing a trade killed the money manager.** `closeTrade` read
  `event.financing` and `event.accountBalance` directly. Those are OANDA's
  fields; on an IG close the attribute is simply absent, so the log line
  raised - again inside a handler, again `os._exit(1)`, this time on the first
  trade the stack completed.
* **`goodTillDate` is read in UTC, not in the account's timezone.** The code
  assumed the account's and deliberately sent local wall-clock digits. Three
  orders settled it, on an account whose own clock runs two hours ahead of
  UTC: ninety minutes ahead in UTC - half an hour in the past for the account
  - was accepted and rested; thirty minutes ahead, which is in the past in
  London, was accepted too; ten minutes *behind* UTC was refused outright
  with `GOOD_TILL_DATE_IN_THE_PAST`, which is what makes the other two mean
  something. This machine is on Europe/Berlin, so AG01's end-of-day expiry
  was being sent two hours late.

With those fixed, the whole path ran end to end against the account, with the
money manager, the execution handler and the transaction reader on one bus -
scripts/live.py's own wiring, with two signals placed by hand in place of the
strategy:

```
ORDER  DE30_EUR BUY  STOP 0.5 @25650.1  SL@25640.1 TP@25665.1
ORDER  DE30_EUR SELL STOP 0.5 @25639.0  SL@25649.0 TP@25624.0
ORDER_FILL  orderID=PD08c5f850... price=25639.0 reason=STOP_ORDER
SENT ORDERCANCEL orderID: PDfda95e97...        <- the losing leg, by name
ORDER_FILL  orderID=PD08c5f850... price=25645.3 reason=UNKNOWN pl=-3.15
money manager: onTrade=False orderIssued=False signals={}
working orders at IG: []   positions at IG: []
```

One leg triggered, the other was cancelled by the id the acknowledgement gave
it, the position was closed and reported within one poll, and the signal was
released. The outcome is `UNKNOWN` because that close was sent by hand,
between the two levels - which is exactly what the inference must refuse to
name.

What remains unexercised on IG: a bracket leg firing on its own. The stop and
the target reach IG correctly - they come back on the position, and a stop 30
points away is reported as `stopDistance: 30.0` - but no leg has yet been
seen to fire and close a position by itself, which is the event
`lib/closereason.py` has to read backwards.

So the difference in what backs them is worth spelling out:

* **the routes, fields, versions, order types, limits and enumerations** are
  taken from each broker's published REST documentation. Every one of them is
  in the code next to a comment saying what it is for.
* **the translations** - an order's fields, a candle's sides, a bracket's
  children, a timestamp's format - are pinned test by test, because the
  failure mode there is not a crash but a trade at a price nobody chose.
* **the behaviour under a real account** is evidence on IG now, and nowhere
  else - the section above says exactly how far it goes. Everywhere it does
  not reach, the code is written to fail rather than to assume: a price row
  missing one side is skipped rather than half-filled, an IB history window is
  filtered locally so a misread `startTime` yields fewer bars and never bars
  from the wrong window, and an unconfirmed IG deal is given up on loudly
  because it may still be live. That posture is what made the eleven defects
  above findable in an evening rather than in a drawdown.

Two specific things to check first on an account, before letting either path
size a real position:

1. **IG's `dealReference` is not treated as an idempotency key.** eToro's
   `x-request-id` is one - re-sending under the same derived key was refused
   with a 400 naming the original order - and whether IG refuses a repeated
   reference has not been established. `execution/ig.py` therefore treats a
   resend as a resend.
2. **IB's `priceFactor` is applied as documented** and is 1 for the
   instruments here. If your bars come back off by a power of ten, that is the
   first thing to look at, and it is logged whenever it is not 1.

The path that matters most has now been rehearsed on IG - a straddle left
resting until one leg triggered and the other was cancelled - and not on IB at
all. What is still missing on both is the last step of it: a stop or a target
firing by itself, and the parity monitor judging the pair. That needs a market
that moves through a level, not a test.

## Reading a backtest: the viewer

```
python scripts/web.py
```

and open <http://127.0.0.1:8731>. Pick an instrument and a granularity - the
dates default to what that store actually holds - and press run.

The chart is on top with the whole range on it. The trades are under it in the
order they opened, with the signal that produced each one, where it went in,
where it came out, its stop, its target, and what it made. Click a row and the
chart zooms onto that trade:

* a solid line at the **entry** and another at the **exit**, each with a
  marker on the bar it happened in
* a dashed line at the **stop** and at the **target**, the levels the strategy
  asked for, labelled on the opposite side so that a trade which closed at its
  target does not draw two labels on top of each other
* the bars the trade was open for, shaded
* thin whiskers on each bar at the **ask high and the bid low** - the two
  series the fill rule actually reads, since a long entry is touched on the
  ask and its stop and target on the bid

That last one is the reason the zoom exists at all. It is where you can see
that the bar really did reach the level, and - more interestingly - whether
the same bar also reached the other one, which is the case
`backtest/resolution.py` says a bar cannot settle and which
`scripts/divergence_band.py` counts.

The address bar holds the backtest, so a reload runs the same one and a link
opens on the same trade:

```
http://127.0.0.1:8731/?instrument=EUR_USD&granularity=H1&strategy=AG01&from=2018-01-01&to=2018-03-03&trade=21
```

Arrow keys (or `j` and `k`) walk through the trades, and `escape` goes back to
the whole range.

### What it runs, and what it will not

It runs the offline stack in `backtest/ledger.py`: the strategy, the money
manager, the simulator, and the adapter that says "the simulator is the broker
here", driven by the causally ordered replay engine. No broker is contacted,
no order is ever sent, and nothing is written.

It also runs whatever **viewer plugins** are installed, which are not one of
those. A plugin is a strategy that brings its own bar loop rather than riding
the live stack's - because it opens several positions per signal, say, or
moves a stop on a rule `MoneyManager` is not shaped for - and it is drawn the
same way regardless: the same chart, the same trade table, the same report. A
plugin may declare free parameters, and selecting it then reveals a form built
from what the strategy itself says they are, so the ranges stay where they
belong instead of in a copy in the page; they are part of the cache key and of
the deep link. Anything heavier than one run - a grid search, a walk-forward -
stays on the plugin's own command line, because a few thousand runs behind an
HTTP request is not a page, it is a timeout.

Not every strategy is published with this repository. `strategy/plugins.py` is
the whole bridge: with nothing installed the engine runs the strategies above
and names none of the others, which is the state this repository is checked
out in and the state its tests run in. See that file for what a plugin has to
answer.

It refuses four things rather than doing something nobody asked for:

| asked for | answer |
| --- | --- |
| an instrument with no store in `DATA_DIR` | refused, listing the ones there are |
| a granularity the store does not hold | refused, naming the ones it holds |
| a window wider than `--max-candles` | refused, with the bar count and the limit |
| a `BO` research engine | refused: they place no orders, so there is nothing to write down |

The instrument name reaches a file path, so it is checked against the files
that actually exist rather than sanitised - a rule about what a name may
contain is a rule somebody has to get exactly right, and a list of real files
cannot be talked around.

**There is no authentication of any kind.** It binds to `127.0.0.1`, where
that is fine. `--host` will bind it elsewhere and prints a warning when you
use it, because the difference between `127.0.0.1` and `0.0.0.0` is the whole
of the security model here.

### The capital, and how a trade is sized

The page asks for a starting capital and a **risk per trade**, and draws what
the account did with them: the balance after each close, as a step, because
that is when a balance moves.

The size is not asked for. It follows from the risk and from the distance to
the stop - `capital x risk / |entry - stop|` - so a trade that exits on its
stop costs that percentage of the capital whatever the distance was, and a
wide stop simply buys fewer units. Two consequences worth knowing before
reading a curve:

* **The capital is re-read at the start of each calendar month**, and not
  more often. Re-reading it after every close would make each trade's size
  depend on the one before it, which is compounding by the hour rather than a
  decision anybody takes; re-reading it monthly is one somebody could.
* **A signal carrying no stop cannot be sized and is not sent.** There is no
  distance to divide by, and any size invented for it would be a position
  whose loss nobody chose. A strategy that sets no stop trades nothing this
  way, which is the honest outcome rather than a surprise one.

`ledger.run(risk=None)` is still the old behaviour - a fixed `units` for every
trade - and it is what every caller that has not asked for this gets.

### Two things the numbers are not

**P&L is price times units, not money.** The simulator closes a trade as
(exit - entry) x units, so a one-unit EUR_USD trade that ran 29 pips reports
`0.00292`. Sized off the account instead, the same arithmetic lands in the
instrument's **quote currency** - USD on EUR_USD - and converting that into
the account's own currency needs a rate this project does not model, any more
than it models contract sizes. So the column is labelled `price x units` under
a fixed size and `quote ccy` under a risk, and never with a euro sign: a
currency symbol on a number that is not that currency is exactly the sort of
quiet wrongness the rest of this code is written to avoid.

**A trade still open when the data runs out is not a result.** It is listed,
because it happened, with its outcome as `STILL_OPEN` and no P&L at all rather
than a zero: a trade that has not closed has not made nothing. The report
counts it under `openTrades` and leaves it out of every ratio.

### No framework, no charting library

`http.server` and a canvas the page draws on itself. The requirements file
lists what the code imports with a note per line saying why, and neither a web
framework for three JSON routes nor a CDN script for a page that reads a local
file earns a line in it. A vendored minified library would be worse: code
nobody here can review, sitting next to code that is commented line by line.

What it costs is the chart drawing in `web/static/app.js`, which is about a
hundred lines of turning prices into pixels.

### What the viewer found on its first run

Worth recording, because it changes every backtest number this project
produced before it and because it is exactly the kind of thing a list of
trades makes obvious and a log does not.

The first ledger of EUR_USD H1 over January and February 2018 had trades that
closed twice - a target taken in the morning and a stop taken days later, on
the same entry, with the balance moved both times. 73 of 103 trades were like
that.

The cause was in `backtest/oanda.py`. When a trade closed, the simulator meant
to cancel the other half of its bracket and did it by assigning the string
`'CANCELED'` over `o.orig.SLOrder` - a reference to the `Event` the child was
built from, not to the `OANDAOrder` the book actually holds. Nothing consults
that attribute, so the loser stayed `PENDING` for the rest of the run and
filled whenever price later reached it.

It is fixed: the sibling is now retired the way a cancel retires an order, one
close per trade, and the balance moves once. `backtest_oanda_test.py` pins the
simulator's side of it and `web_test.py` pins it again where it was noticed.
Two tests that had pinned the old behaviour were rewritten with a Was/Now note
rather than deleted, which is this suite's convention.

If you have backtest results from before this, they are wrong by however many
of their trades ran into their other leg afterwards.

### Behind a reverse proxy

The service has no authentication of any kind, so putting it anywhere but
`127.0.0.1` means putting something in front of it. The page is written to be
prefix-agnostic for exactly this: its assets and its API are referenced
relatively, so it works at the root of a local server and under whatever path
a proxy puts it at, with nothing told to either end. The one thing it needs is
the **trailing slash** - without it a browser resolves `static/app.js` against
`/`, which is the proxy's site rather than the app.

nginx, with TLS and the password where they belong:

```nginx
location = /parity {
        return 301 https://$host/parity/;
}

location /parity/ {
        auth_basic "parity-deriva";
        auth_basic_user_file /etc/nginx/parity-deriva.htpasswd;

        proxy_pass http://127.0.0.1:8731/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # a backtest is seconds inside one blocking call, answered as a single
        # JSON body of a few hundred kB
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
}
```

and systemd, so it survives a reboot:

```ini
[Unit]
Description=parity-deriva backtest viewer
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
# the repository's parent: the package is imported as parity_deriva
WorkingDirectory=/path/to/parity-deriva
Environment=PYTHONPATH=/path/to/parity-deriva
# getLogger() looks for etc/logging.conf under this and calls os._exit(-1)
# without it, with no traceback to read
Environment=PARITY_DERIVA_HOME=/path/to/parity-deriva/parity_deriva
ExecStart=/path/to/venv/bin/python /path/to/parity-deriva/parity_deriva/scripts/web.py \
    --host 127.0.0.1 --port 8731
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Run as a service it keeps quiet: `getLogger()` reads `logging.conf`, which puts
everything at DEBUG, and a backtest logs several lines per fill - right for a
run somebody is watching, and several hundred journal lines per request when
nobody is. So the trading logger is held at warnings and the service keeps its
own at info. `--verbose` puts it back.

What still comes through is the simulator's own warnings, which is what a
warning is for. One of them is noisy and predates all of this: `cancelOrder`
dumps a whole order when it walks past a filled one at the price it is
cancelling, which happens seven times over two months of EUR_USD H1 - it then
carries on and cancels the right order, so it is a warning about nothing in
particular.

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

A strategy installed under `strategy/private/` brings its own tests, and
discovery picks them up where they sit. They can be a large share of the run:
a bar loop with its own engine is usually checked against synthetic series
swept bar by bar, which is slow on purpose.

# License Terms

Copyright (c) 2015 Michael Halls-Moore

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

# Forex Trading Disclaimer

Trading foreign exchange on margin carries a high level of risk, and may not be suitable for all investors. Past performance is not indicative of future results. The high degree of leverage can work against you as well as for you. Before deciding to invest in foreign exchange you should carefully consider your investment objectives, level of experience, and risk appetite. The possibility exists that you could sustain a loss of some or all of your initial investment and therefore you should not invest money that you cannot afford to lose. You should be aware of all the risks associated with foreign exchange trading, and seek advice from an independent financial advisor if you have any doubts.