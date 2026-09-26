from decimal import Decimal
import os
import shlex


# Everything that is not code lives under this one folder - Docker's /data:
# the runs, the market data, the .env, the files to import, MetaTrader's Wine.
DATA_DIR = os.environ.get('PARITY_DERIVA_DATA_DIR', os.path.expanduser('~/DATA-dev'))


def dotenv(key, default=None):
    """
    key from the environment, else from DATA_DIR/.env, else from the older
    parity_deriva/.env, else default.

    The .env is written to be sourced by a shell (`export KEY='value'`),
    which systemd's EnvironmentFile ignores, so a service reads it here.
    """
    if key in os.environ:
        return os.environ[key]
    for path in (os.path.join(DATA_DIR, '.env'),
                 os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')):
        try:
            with open(path) as handle:
                for line in handle:
                    words = shlex.split(line, comments=True)
                    if words[:1] == ['export']:
                        words = words[1:]
                    if words and words[0].startswith(key + '='):
                        return words[0][len(key) + 1:]
        except (OSError, ValueError):
            pass
    return default


ENVIRONMENTS = { 
    "streaming": {
        "real": "stream-fxtrade.oanda.com",
        "practice": "stream-fxpractice.oanda.com",
        "sandbox": "stream-sandbox.oanda.com"
    },
    "api": {
        "real": "api-fxtrade.oanda.com",
        "practice": "api-fxpractice.oanda.com",
        "sandbox": "api-sandbox.oanda.com"
    }
}

# --------------------------------------------------------------------------
# Which broker the stack talks to
#
# 'oanda', 'etoro', 'ig', 'ib' or 'mt5'. The event bus is the same whichever it is -
# strategies, the money manager, the simulator and the parity monitor do not
# know which is behind them - but the four brokers are not the same shape, and
# what each can do is declared in trading/providers.py rather than discovered.
# A wiring asks for what it needs with providers.require() and fails at
# startup if the chosen provider does not have it.
#
# DOMAIN below decides practice against real for OANDA, eToro and IG. It does
# not for IB, where the paper account is simply a different account id and
# IB_ACCOUNT_ID is what chooses - so scripts/live.py still refuses a 'real'
# DOMAIN without --live, and on IB that flag is the only thing standing
# between you and whichever account the gateway is logged into.
PROVIDER = os.environ.get('PARITY_DERIVA_PROVIDER', 'oanda')

CSV_DATA_DIR = os.environ.get('PARITY_DERIVA_CSV_DATA_DIR', ".")
OUTPUT_RESULTS_DIR = os.environ.get('OUTPUT_RESULTS_DIR', ".")

# What this server trades: 'demo' accounts or 'real' money, never both - a
# server for each. From .env only, never from a page: a click must not turn a
# server into one that trades real money. It picks DOMAIN below and
# MT5_ALLOW_REAL, web/livesessions.py refuses an account of the other kind,
# and a real one takes only what a demo server promoted (web/mcp.py
# push_record). Anything but 'real' is demo.
ACCOUNTS = 'real' if dotenv('PARITY_DERIVA_ACCOUNTS', 'demo') == 'real' else 'demo'
# on a real server: what a form needs to have done on demo to be promoted -
# days trading, trades closed (or, on a slow timeframe, PROMOTE_MIN_TRADES
# once it has traded PROMOTE_SLOW_DAYS: on D1 30 trades are a year), a net
# P&L of at least PROMOTE_MIN_NET, no parity alarm - and the day's loss, in %
# of the sessions' capital, at which every session is stopped until tomorrow
PROMOTE_DAYS = int(dotenv('PARITY_DERIVA_PROMOTE_DAYS', '20'))
PROMOTE_TRADES = int(dotenv('PARITY_DERIVA_PROMOTE_TRADES', '30'))
PROMOTE_SLOW_DAYS = int(dotenv('PARITY_DERIVA_PROMOTE_SLOW_DAYS', '90'))
PROMOTE_MIN_TRADES = int(dotenv('PARITY_DERIVA_PROMOTE_MIN_TRADES', '10'))
PROMOTE_MIN_NET = float(dotenv('PARITY_DERIVA_PROMOTE_MIN_NET', '0'))
# and the version's card from the gate, whose band the demo curve stays in and
# whose worst losing streak it does not pass (web/livesessions.py versusCard);
# 0 promotes without one, as before the cards
PROMOTE_NEEDS_CARD = dotenv('PARITY_DERIVA_PROMOTE_NEEDS_CARD', '1') != '0'
DAILY_LOSS_PCT = float(dotenv('PARITY_DERIVA_DAILY_LOSS_PCT', '3'))
# The alerts of the live sessions (web/notify.py): a banner on the pages
# always, and an urgent one also by email and by Telegram, each once it is
# set here, and to the phones paired on the settings page (web/phone.py). A
# session with no new candle for ALERT_STALE_BARS of its bars, the market
# open, is one.
SMTP_HOST = dotenv('PARITY_DERIVA_SMTP_HOST')
SMTP_PORT = int(dotenv('PARITY_DERIVA_SMTP_PORT', '587'))
SMTP_USER = dotenv('PARITY_DERIVA_SMTP_USER')
SMTP_PASSWORD = dotenv('PARITY_DERIVA_SMTP_PASSWORD')
SMTP_FROM = dotenv('PARITY_DERIVA_SMTP_FROM')
SMTP_TO = dotenv('PARITY_DERIVA_SMTP_TO')
TELEGRAM_TOKEN = dotenv('PARITY_DERIVA_TELEGRAM_TOKEN')
TELEGRAM_CHAT = dotenv('PARITY_DERIVA_TELEGRAM_CHAT')
ALERT_STALE_BARS = int(dotenv('PARITY_DERIVA_ALERT_STALE_BARS', '3'))
# The holdout and the gate SIM -> DEMO (web/holdout.py, performance/gate.py):
# the last HOLDOUT_SHARE of an instrument's history, and at least
# HOLDOUT_MIN_DAYS of it, is read by no simulation but the gate's, once a
# version. The gate asks the development period for GATE_MIN_TRADES trades, a
# bootstrap profit factor above GATE_PF_LOW, profitable neighbours in the set,
# a PF above it without the 3 best trades, random entries beaten at the
# GATE_BASELINE_PCT percentile; then the holdout for a profit, a PF of at
# least GATE_HOLDOUT_PF_RATIO of the development's and a drawdown of at most
# GATE_HOLDOUT_DD_RATIO of it.
HOLDOUT_SHARE = float(dotenv('PARITY_DERIVA_HOLDOUT_SHARE', '0.25'))
HOLDOUT_MIN_DAYS = int(dotenv('PARITY_DERIVA_HOLDOUT_MIN_DAYS', '365'))
GATE_MIN_TRADES = int(dotenv('PARITY_DERIVA_GATE_MIN_TRADES', '100'))
GATE_PF_LOW = float(dotenv('PARITY_DERIVA_GATE_PF_LOW', '1.0'))
GATE_BASELINE_PCT = float(dotenv('PARITY_DERIVA_GATE_BASELINE_PCT', '95'))
GATE_HOLDOUT_PF_RATIO = float(dotenv('PARITY_DERIVA_GATE_HOLDOUT_PF_RATIO', '0.7'))
GATE_HOLDOUT_DD_RATIO = float(dotenv('PARITY_DERIVA_GATE_HOLDOUT_DD_RATIO', '1.5'))

DOMAIN = "real" if ACCOUNTS == 'real' else "practice"
STREAM_DOMAIN = ENVIRONMENTS["streaming"][DOMAIN]
API_DOMAIN = ENVIRONMENTS["api"][DOMAIN]
# Credentials come from the environment only - never commit them. Set:
#   export OANDA_API_ACCESS_TOKEN=...
#   export OANDA_API_ACCOUNT_ID=...
# An empty token makes OANDA answer 401, which the data handlers report as a
# StatusEvent('ERROR') rather than crashing.
ACCESS_TOKEN = os.environ.get('OANDA_API_ACCESS_TOKEN', '')
ACCOUNT_ID = os.environ.get('OANDA_API_ACCOUNT_ID', '')
API_VERSION = '3'

LOG_DIR = os.environ.get('PARITY_DERIVA_LOG_DIR', DATA_DIR)
# Where candle CSV exports wait to be imported into the stores - the viewer's
# data dialog uploads into it and runs scripts/import_csv.py over it. Set in
# .env (PARITY_DERIVA_IMPORT_DIR) or the environment.
IMPORT_DIR = dotenv('PARITY_DERIVA_IMPORT_DIR', os.path.join(DATA_DIR, 'import'))
BASE_CURRENCY = "EUR"
EQUITY = Decimal("100000.00")
# the leverage a simulation's account trades on, when the form does not say:
# ESMA's 30:1 on a major. Its margin is checked, not enforced (report.margin)
LEVERAGE = int(dotenv('PARITY_DERIVA_LEVERAGE', 30))

DEF_CONFIG = 'logging.conf'
DEF_PAIRS  = [ 'EUR_USD' ]
DEF_GRANULARITY = 'M1'

# Decimal places OANDA accepts for an order price, per instrument - its
# "displayPrecision". Sending more is rejected outright
# (PRICE_PRECISION_EXCEEDED); rounding to fewer silently moves the level,
# which is worse, so a strategy must never guess.
#
# The authoritative list is GET /v3/accounts/{accountID}/instruments, field
# displayPrecision. The two entries below are the instruments this project has
# actually traded, and both are evidenced rather than assumed:
#   EUR_USD  5 - every price in the M1 history carries five decimals
#   DE30_EUR 1 - every accepted price in the captured OANDA responses in
#                execution/execution.py has one decimal, and the single
#                two-decimal one there is the order OANDA rejected with
#                TAKE_PROFIT_ON_FILL_PRICE_PRECISION_EXCEEDED
INSTRUMENT_PRECISION = {
    'EUR_USD': 5,
    'DE30_EUR': 1,
}

# --------------------------------------------------------------------------
# Parity alarm
#
# trading/parity.py joins each live fill to its simulated counterpart on the
# signal key and counts the disagreements. These settings decide when that
# becomes an alarm, and whether an alarm stops trading. They are yours to set:
# the numbers below are defaults, not measurements.
#
# What IS measured is the floor. A simulator reads bars while an account
# trades ticks, so when one bar holds both the stop and the target of an open
# trade it cannot say which came first and has to guess. Run
# scripts/divergence_band.py to size that guess on your own data and
# granularity; a threshold below the figure it reports will fire on the width
# of the bars rather than on the market.
#
# On EUR_USD, AG01 on H1 over two months of M1 history, it reported:
#     fed H1 bars, the simulator's outcome is a coin flip on 1.23% of trades
#     fed M1 bars,                                              0.09%
# so max_outcome_mismatch below is set well clear of the first of those. Your
# instrument, granularity and period will give different numbers - measure,
# do not inherit these.
PARITY_ALARM = {
    # trades the rate is measured over, and the sample below which the
    # monitor declines to judge at all: with a 1% floor, one disagreement in
    # the first three trades means nothing
    'window': 200,
    'min_sample': 50,

    # fraction of reconciled trades whose outcome the two sides disagree on
    'max_outcome_mismatch': 0.05,

    # per-trade fill price difference, in the instrument's own units.
    # None disables the check rather than defaulting to something invented
    'max_slippage': None,

    # trades where only one side ever reported, counted in the window. This
    # is the serious one - the simulator filled and the account did not, or
    # the reverse - so it is deliberately tighter than the rate above
    'max_unpaired': 5,

    # trades the two sides closed but whose outcome could not be judged at
    # all. On OANDA this stays zero, and on IB too: both state which leg
    # closed a trade, IB because its bracket is three separate orders and the
    # child that filled names the leg. On eToro and IG it does not stay zero -
    # neither reports the leg, so lib/closereason.py infers it from the
    # closing level and says UNKNOWN where that level belongs to neither leg
    # clearly. A monitor blind to the outcome is worth knowing about even
    # though it is not itself a divergence.
    # None disables the check rather than defaulting to something invented
    'max_undecided': None,

    # 'warn' logs and keeps going; 'halt' also publishes StatusEvent('HALT'),
    # which MoneyManager honours by refusing further signals until a
    # StatusEvent('RESUME')
    'action': 'warn',
}

# Per-instrument overrides, merged over PARITY_ALARM. Volatility and spread
# differ enough between an index and a currency pair that one band rarely
# fits both.
PARITY_ALARM_BY_INSTRUMENT = {
    # 'DE30_EUR': {'max_outcome_mismatch': 0.10, 'action': 'halt'},
}

# Used when an instrument is not in the table above. Five decimals is the FX
# convention and, more importantly, it errs towards *too many* decimals: the
# broker then rejects the order loudly instead of accepting a level that has
# been silently rounded away.
DEFAULT_PRICE_PRECISION = 5

# --------------------------------------------------------------------------
# eToro
#
# Credentials come from the environment, like OANDA's. Use EITHER a bearer
# token OR the user-key/api-key pair - a request carrying both is rejected
# with 422, so setting both raises here instead.
#
#   export ETORO_API_DOMAIN=...        # the public API host
#   export ETORO_ACCESS_TOKEN=...      # OAuth bearer token
#   # or
#   export ETORO_USER_KEY=...
#   export ETORO_API_KEY=...
#
# The host below is evidenced rather than assumed, which is the only reason
# it has a default at all:
#   - it holds a Google Trust Services certificate issued to
#     public-api.etoro.com, so it is eToro's and not a lookalike
#   - unauthenticated it answers {"errorCode":"Unauthorized",...}, which is
#     the gatewayErrorEnvelope the published OpenAPI documents for exactly
#     that case - a different service would not answer in that shape
#   - GET /api/v1/me on it returns this account's profile and scopes
# It is still an environment variable because the tooling describes the host
# as a property of the deployment, so a partner application may be given a
# different one. Set it to empty and lib/etoro.EToroAPI refuses to be built
# rather than reaching somewhere unintended.
ETORO_API_DOMAIN = os.environ.get('ETORO_API_DOMAIN', 'public-api.etoro.com')
ETORO_ACCESS_TOKEN = os.environ.get('ETORO_ACCESS_TOKEN', '')
ETORO_USER_KEY = os.environ.get('ETORO_USER_KEY', '')
ETORO_API_KEY = os.environ.get('ETORO_API_KEY', '')

# This project names instruments the way OANDA does. eToro keys everything by
# a numeric instrumentId, and the mapping between the two is not derivable -
# 'DE30_EUR' and eToro's German index are the same market under two names and
# an id only eToro knows. So it is configuration, and an unmapped instrument
# raises rather than being resolved at runtime to whatever a search returns.
#
# Fill it in with:
#     python scripts/etoro_instruments.py EUR_USD DE30_EUR
# which prints what the API answers for a symbol, for you to paste here.
# 'precision' is optional and overrides INSTRUMENT_PRECISION for eToro only.
# The two below were resolved against the live API and are evidenced, not
# guessed: symbols=EURUSD returns instrumentId 1, "EUR/USD", type Forex, and
# symbols=GER40 returns instrumentId 32, "GER40 Index (Non Expiry)", type
# Indices. eToro has no GER30 and no DE30 - both 404 - so the counterpart of
# OANDA's DE30_EUR is GER40, a 40-constituent index where DE30_EUR tracks 30.
# They are not the same basket, and a strategy calibrated on one is not
# calibrated on the other.
ETORO_INSTRUMENTS = {
    'EUR_USD': {'symbol': 'EURUSD', 'instrumentId': 1, 'precision': 5},
    'DE30_EUR': {'symbol': 'GER40', 'instrumentId': 32, 'precision': 1},
}

# eToro serves ONE OHLC per candle. OANDA serves three, and the strategies
# read them: AG01 buys the high of the ask and stops out at the low of the
# bid. Those two prices do not exist in eToro's candle data, and no
# measurement recovers them, so bid and ask are a model or they are nothing.
#
# Unset (the default), candles carry mid only, the provider declines
# bid_ask_candles, and a wiring that needs them refuses to start and says
# why. That is the same rule PARITY_ALARM follows: a value nobody measured is
# off, not filled in with something plausible.
#
# Set it to a spread in the instrument's own units, or per instrument:
#     ETORO_SPREAD = 0.00001
#     ETORO_SPREAD = {'EUR_USD': 0.00001, 'DE30_EUR': 2.2}
# Half of it is applied either side of the served price.
#
# What one demo round trip actually measured, on 2026-09-10 in a quiet market
# (EUR_USD, 900 units, market in and market out about thirty seconds apart):
#
#   the rates route, twelve samples, constant to the last digit
#                                        0.00001 on EUR_USD, 2.20 on GER40
#   the fill's openingData               marketSpread 0.01, markup 0.02
#   the realised result                  netProfit -0.04 USD, fees 0.00,
#                                        open 1.16124, close 1.16120
#
# The last line is the only one in units anybody can check: 0.4 pips of
# adverse movement over the whole round trip on a 900-unit position. That is
# consistent with the feed's 0.00001 and rules out a spread of pips being
# charged on top of it.
#
# marketSpread and markup are therefore NOT price differences - 0.01 on a
# 1.16 instrument would be a hundred pips, which the P&L flatly contradicts.
# Read as percentages they come to roughly one and two pips, the right order
# of magnitude for eToro's published forex spread, but the API does not state
# their unit and this has not been established. Do not compute a level from
# them.
#
# So the feed is a defensible starting point after all, at least on the demo
# account. The real account may price differently, and the figure to trust
# there is the one this table's last line is made of: the P&L of a round trip
# you actually did.
ETORO_SPREAD = None

# Leverage on every order. Anything above 1 makes eToro require a stopLossRate,
# which execution/etoro.py then refuses to send an order without.
# Read from .env too, where a demo account can ask for more: a stop a few
# pips away on 1% of the account is several times the account's size.
ETORO_LEVERAGE = int(dotenv('ETORO_LEVERAGE', 1))

# Settlement type: cfd, real, realFutures or marginTrade. Optional on the v2
# create route and left unset here, because the eligible values differ per
# instrument, direction and leverage - POST /api/v2/trading/info/eligibility
# lists the valid combinations for your account.
ETORO_SETTLEMENT_TYPE = None

# eToro does not expire orders. This project's strategies place brackets that
# are meant to die at the end of the day, and a bracket still resting a week
# later is not that trade any more, so data/etoro.EToroTransactions cancels a
# resting order once the gtdTime it was issued with has passed. That is our
# action, not the broker's, and it is logged each time. Set False and a
# resting order rests until it triggers.
ETORO_ENFORCE_EXPIRY = True

# Seconds between polls. Nothing in the eToro public API is pushed, so this
# is the resolution at which candles, prices, fills and closes arrive. The
# published quotas are shared per group - 120/60s across eleven market-data
# endpoints, 20/60s across every execution endpoint, 60/60s for order info -
# and lib/etoro.RateLimiter paces against those, but a poll interval short
# enough to fight the limiter just adds latency.
ETORO_POLL_SECONDS = 5

# --------------------------------------------------------------------------
# Twelve Data
#
# Not a broker: a price feed nobody trades on, which is why the paper
# session runs on it (trading/providers.TwelveDataProvider). Its one
# account, 'paper', is this stack's own simulator filling the orders.
#
#   export TWELVEDATA_API_KEY=...      # in .env, read here through dotenv()
#
# Measured on 2026-09-24 on the basic plan: 8 credits a minute, 800 a day,
# one credit per /time_series call whatever its size, and /api_usage costs
# one too - so the count is kept locally, in candles.db, and the two limits
# below are the plan's figures rather than measurements of anything.
TWELVEDATA_API_KEY = dotenv('TWELVEDATA_API_KEY', '')
TWELVEDATA_MINUTE_LIMIT = 8
TWELVEDATA_DAILY_LIMIT = 800
# credits never spent by the bar poller, kept for the one history call a
# strategy's warm-up makes when a session starts
TWELVEDATA_RESERVE = 40
# seconds after a bar closes before it is asked for. The closed bar is there
# at once; the delay is for the vendor's clock and ours to disagree a little
TWELVEDATA_POLL_DELAY = 20

# This project names instruments the way OANDA does; Twelve Data spells a pair
# 'EUR/USD'. Resolved on 2026-09-24: both below answer 5min bars on the basic
# plan. 'GDAXI' answered 404, so DE30_EUR has no entry - find its ticker with
#     python scripts/twelvedata_instruments.py DAX
# (one credit) and paste it here. An unmapped instrument raises.
TWELVEDATA_INSTRUMENTS = {
    'EUR_USD': 'EUR/USD',
    'GBP_USD': 'GBP/USD',
}

# One OHLC per bar, like eToro: bid and ask are a model or they are nothing.
# Unset, candles carry mid only, the provider declines bid_ask_candles and a
# strategy that reads them refuses to start on it. The live page's skew board
# prints every broker's median spread, in pips, which is the figure to copy:
#     TWELVEDATA_SPREAD = 0.00007
#     TWELVEDATA_SPREAD = {'EUR_USD': 0.00007, 'GBP_USD': 0.00010}
TWELVEDATA_SPREAD = None

# --------------------------------------------------------------------------
# Candle database
#
# Every live session writes the bars it sees here, one row per (provider,
# account, instrument, granularity, bar), next to sessions.db. SQLite in WAL
# mode, because the sessions are separate processes and the HDF5 warehouse
# does not take concurrent appends. See data/candledb.py.
# The MCP endpoint (web/mcp.py). PUBLIC_URL is where an assistant reaches
# the service, path included, when a proxy serves it under one and does not
# send X-Forwarded-Prefix; the sandbox is the memory and the seconds one
# backtest asked over MCP may take (web/sandbox.py).
PUBLIC_URL = dotenv('PARITY_DERIVA_PUBLIC_URL')
MCP_SANDBOX_MB = int(dotenv('PARITY_DERIVA_MCP_SANDBOX_MB', '1024'))
MCP_SANDBOX_SECONDS = int(dotenv('PARITY_DERIVA_MCP_SANDBOX_SECONDS', '240'))
# The public repository of strategies a pull request from the settings page
# goes to (web/mcp.py pullRequest), as owner/name, and the GitHub token that
# opens it: a fine-grained one with contents and pull requests, read and
# write. A token that may not push to the repository forks it first. With
# either missing, the page says so and nothing is sent.
PUBLIC_REPO = dotenv('PARITY_DERIVA_PUBLIC_REPO')
GITHUB_TOKEN = dotenv('PARITY_DERIVA_GITHUB_TOKEN')
# The bucket a set's runs go to off this disk, to come back when one is opened
# (web/service.py freeze, scripts/cold.py): any S3 one - AWS, Hetzner,
# Backblaze, MinIO - by its endpoint, e.g. https://fsn1.your-objectstorage.com,
# the bucket's name and a key that may read, write and delete in it. PREFIX
# goes before every key: two servers may share one bucket. With any missing,
# nothing goes and the page says so.
S3_ENDPOINT = dotenv('PARITY_DERIVA_S3_ENDPOINT')
S3_BUCKET = dotenv('PARITY_DERIVA_S3_BUCKET')
S3_ACCESS_KEY = dotenv('PARITY_DERIVA_S3_ACCESS_KEY')
S3_SECRET_KEY = dotenv('PARITY_DERIVA_S3_SECRET_KEY')
S3_REGION = dotenv('PARITY_DERIVA_S3_REGION', 'us-east-1')
S3_PREFIX = dotenv('PARITY_DERIVA_S3_PREFIX', 'parity-deriva')
# The bucket may also be chosen on the settings page - an S3 one, or a folder
# of a Google Drive or a OneDrive (web/storage.py), the page's choice before
# this one. A Drive goes through rclone: this program, when it is neither
# beside the service's Python (the venv's bin) nor on the PATH.
RCLONE = dotenv('PARITY_DERIVA_RCLONE')

CANDLE_DB = dotenv('PARITY_DERIVA_CANDLE_DB', os.path.join(DATA_DIR, 'live', 'candles.db'))

# --------------------------------------------------------------------------
# MetaTrader 5
#
# The terminal and the MetaTrader5 package run under Wine and are reached
# through scripts/mt5_bridge.sh, which has to be running. See lib/mt5.py.
#
#   export MT5_LOGIN=...  MT5_PASSWORD=...  MT5_SERVER=...
#
# or leave them unset and point MT5_CREDENTIALS at a file holding them.
MT5_LOGIN = dotenv('MT5_LOGIN', '')
MT5_PASSWORD = dotenv('MT5_PASSWORD', '')
MT5_SERVER = dotenv('MT5_SERVER', '')
# MetaTrader and its Wine (scripts/mt5_bridge.sh) are in DATA_DIR/mt5, and
# the credential files beside them
MT5_CREDENTIALS = dotenv('MT5_CREDENTIALS', os.path.join(DATA_DIR, 'mt5', 'mq.txt'))
MT5_BRIDGE = dotenv('MT5_BRIDGE', '127.0.0.1:18812')
MT5_TERMINAL = 'C:\\Program Files\\MetaTrader 5\\terminal64.exe'
# More than one account: one terminal each, each behind its own bridge
# (scripts/mt5_add_account.sh makes the terminal and prints the entry).
# Set, this replaces the single terminal the four settings above describe:
#     MT5_TERMINALS = [
#         {'credentials': DATA_DIR + '/mt5/mq.txt', 'bridge': '127.0.0.1:18812',
#          'terminal': MT5_TERMINAL},
#         {'credentials': DATA_DIR + '/mt5/mq2.txt', 'bridge': '127.0.0.1:18813',
#          'terminal': 'C:\\MT5\\acc2\\terminal64.exe', 'portable': True},
#     ]
# 'suffix' is what that broker adds to every symbol (OANDA TMS: EURUSD.pro).
MT5_TERMINALS = [
    {'credentials': MT5_CREDENTIALS, 'bridge': MT5_BRIDGE, 'terminal': MT5_TERMINAL},
    # OANDA TMS (EU): MT5 only, no v20 REST API. Its clock is UTC+2 in
    # summer, not MetaQuotes' +3: Friday's last EURUSD tick is at 22:59
    # server time and FX shuts at 21:00 UTC (measured 2026-09-26).
    {'credentials': os.path.join(DATA_DIR, 'mt5', 'oanda-mt5.txt'), 'bridge': '127.0.0.1:18813',
     'terminal': 'C:\\MT5\\oanda\\terminal64.exe', 'portable': True, 'suffix': '.pro',
     'utc_offset': 2},
]
# The login a process trades on; web/livesessions sets it per session.
MT5_ACCOUNT = dotenv('MT5_ACCOUNT', '')
# lib/mt5.py reads the account's trade mode after the login and refuses
# anything but a demo while this is False: True on a real money server only.
MT5_ALLOW_REAL = ACCOUNTS == 'real'
# The broker's clock minus UTC, in hours, used only while the market is shut:
# with it open the offset is read off the newest tick. A terminal's own
# 'utc_offset' (MT5_TERMINALS) wins over this one.
MT5_SERVER_UTC_OFFSET = 3
# Marks this stack's orders on the account, and slippage allowed on a market
# order, in points.
MT5_MAGIC = 71210
MT5_DEVIATION = 10
MT5_POLL_SECONDS = 5
# Symbol and precision where the broker's differ from 'EUR_USD' -> 'EURUSD':
#     'DE30_EUR': {'symbol': 'GER40', 'precision': 1},
MT5_INSTRUMENTS = {
}

# --------------------------------------------------------------------------
# Capital.com
#
# IG's API under other names: lib/capital.py drives data/ig.py and
# execution/ig.py through it. A login is the account's email, an API key
# generated in Settings > API integrations, and the key's own password:
#
#   export CAPITAL_API_KEY=...
#   export CAPITAL_IDENTIFIER=...        # the login email
#   export CAPITAL_API_PASSWORD=...      # the key's custom password
#   export CAPITAL_ACCOUNT_ID=...        # optional; the preferred one if unset
#
# DOMAIN chooses demo against real, as for IG.
CAPITAL_API_DOMAIN = os.environ.get('CAPITAL_API_DOMAIN', '')
CAPITAL_API_KEY = os.environ.get('CAPITAL_API_KEY', '')
CAPITAL_IDENTIFIER = os.environ.get('CAPITAL_IDENTIFIER', '')
CAPITAL_API_PASSWORD = os.environ.get('CAPITAL_API_PASSWORD', '')
CAPITAL_ACCOUNT_ID = os.environ.get('CAPITAL_ACCOUNT_ID', '')
# Resolved on the demo account on 2026-09-24 (GET /markets/EURUSD):
# "EUR/USD", quoted in USD, lotSize 1, minDealSize 100, minSizeIncrement 100,
# minStopOrProfitDistance 0.01%. Sizes are units, so contractSize is 1 and
# sizeStep rounds a risk-sized order to what the market accepts.
CAPITAL_INSTRUMENTS = {
    'EUR_USD': {'epic': 'EURUSD', 'expiry': '-', 'currency': 'USD',
                'precision': 5, 'contractSize': 1, 'sizeStep': 100},
}
CAPITAL_CURRENCY = None

# --------------------------------------------------------------------------
# IG
#
# IG issues session tokens from a login rather than accepting a long-lived
# token, so it needs three things from the environment and not one:
#
#   export IG_API_KEY=...          # the application key, from My IG > API keys
#   export IG_IDENTIFIER=...       # your IG username
#   export IG_PASSWORD=...
#   export IG_ACCOUNT_ID=...       # optional; the session's own account if unset
#
# A key belongs to ONE of the two hosts. The demo key works against
# demo-api.ig.com and nowhere else, the live key against api.ig.com, and
# DOMAIN above picks the host - so a key that does not match DOMAIN fails to
# authenticate rather than quietly reaching the other account. IG_API_DOMAIN
# overrides the host, and is there for the case where IG gives a partner
# application one of its own.
IG_API_DOMAIN = os.environ.get('IG_API_DOMAIN', '')
IG_API_KEY = os.environ.get('IG_API_KEY', '')
IG_IDENTIFIER = os.environ.get('IG_IDENTIFIER', '')
IG_PASSWORD = os.environ.get('IG_PASSWORD', '')
IG_ACCOUNT_ID = os.environ.get('IG_ACCOUNT_ID', '')

# 2 or 3. Version 2 returns CST and X-SECURITY-TOKEN headers that last hours;
# version 3 returns an OAuth pair whose access token is measured in seconds
# and has to be refreshed. Two is the default because a refresh that fails
# mid-session costs an order. What version 3 does buy is the account on
# every request (IG-ACCOUNT-ID): a version 2 session deals on the login's
# current account, and two live sessions on two accounts of one login need
# version 3 - web/livesessions.py sets it for them. Measured 2026-09-24: the
# demo's access token lasts 1795 s and lib/ig.py refreshes it.
IG_SESSION_VERSION = int(dotenv('IG_SESSION_VERSION', 2))

# This project names instruments the way OANDA does. IG names them with an
# epic - CS.D.EURUSD.MINI.IP and the like - and the mapping is not derivable:
# the same pair is a different epic on a CFD account, a spread-bet account and
# a demo account, and the difference is a product rather than a spelling. So
# it is configuration, and an unmapped instrument raises rather than being
# resolved at runtime to whatever a search returns first.
#
# Fill it in with:
#     python scripts/ig_instruments.py EURUSD "Germany 40"
# which prints what the API answers, for you to check and paste here.
#
# Per entry:
#   epic           required, and the only field with no sensible default
#   expiry         '-' for a cash CFD or daily funded bet, a contract month
#                  for a dated future. Dealing the wrong month is dealing a
#                  different instrument, so it is explicit
#   currency       what the deal is denominated in; falls back to IG_CURRENCY
#                  and then to BASE_CURRENCY. Not guessed from the pair's
#                  name, and the fallback is wrong more often than it looks:
#                  EUR/USD here is denominated in USD while the account is in
#                  EUR, which GET /markets/{epic} states and the pair's name
#                  does not
#   precision      overrides INSTRUMENT_PRECISION for IG only
#   priceDivisor   almost never. See lib/ig.scale() - it is NOT IG's
#                  scalingFactor, and pasting that field here would move every
#                  level by four decimal places
#
# The two below were resolved against the demo account on 2026-09-22 and are
# evidenced, not guessed. What the search and the dealing rules said:
#
#   EUR_USD   CS.D.EURUSD.CEB.IP  "EUR/USD", lotSize 10
#             CS.D.EURUSD.CEBM.IP "EUR/USD Mini", lotSize 1     <- chosen
#             Both quote in USD, both take a minimum stop of 2.0 points
#             (0.0002) and a minimum size of 0.1. The Mini is a tenth of the
#             value per point, which is the right size for the one-unit
#             trades this project places on a demo account.
#
#   DE30_EUR  "Germany 40" returns seven markets: cash at 25, 5 and 1 euro a
#             point, two dated futures, another 1-euro cash market, and a
#             weekend one that is EDITS_ONLY. Of the two at 1 euro a point,
#             IX.D.DAX.IFMM.IP takes a minimum stop of 8.0 points against
#             IX.D.DAX.IBE.IP's 12.0, and AG01's stop is the low of a bar -
#             so the tighter minimum is the one that refuses fewer orders.
#
#             Note what this pair is NOT: OANDA's DE30_EUR tracks 30
#             constituents and IG's Germany 40 tracks 40. Same market under
#             two names is the eToro GER40 situation again - they are not the
#             same basket, and a strategy calibrated on one is not calibrated
#             on the other.
#
# Resolve others with:
#     python scripts/ig_instruments.py --details EURUSD
# which also prints the dealing rules. The minimum stop distance is the one
# to read: IG refuses a stop closer than that and nothing here moves a level
# to fit, so the order is sent as asked and the refusal is published.
IG_INSTRUMENTS = {
    # contractSize: units in one contract, read off GET /markets/{epic}
    # (2026-09-24: "contractSize": "10000", valueOfOnePip 1.00 USD). The live
    # runner sizes in units on the account's risk and deals size = units /
    # contractSize - see execution/ig.py `sized`
    'EUR_USD': {'epic': 'CS.D.EURUSD.CEBM.IP', 'expiry': '-',
                'currency': 'USD', 'precision': 5, 'contractSize': 10000},
    'DE30_EUR': {'epic': 'IX.D.DAX.IFMM.IP', 'expiry': '-',
                 'currency': 'EUR', 'precision': 1},
}

# The currency a deal is denominated in, where the instrument entry does not
# say. Unset falls through to BASE_CURRENCY.
IG_CURRENCY = None

# A guaranteed stop is honoured at the level asked for even through a gap,
# and IG charges a premium for it. Off by default: it changes what a trade
# costs, which is not something to turn on without meaning to. With it on,
# an order without a stopLoss is refused rather than sent - a guaranteed stop
# is a stop that has to exist.
IG_GUARANTEED_STOP = False

# IG really does expire an order - GOOD_TILL_DATE with a goodTillDate - and
# execution/ig.py sends the strategies' gtdTime as one. So unlike eToro,
# nothing has to be cancelled on our side, and this is off. Turn it on only
# if you place orders without an expiry and want them dropped anyway.
IG_ENFORCE_EXPIRY = False

# Seconds between polls. IG pushes over Lightstreamer, which this project
# does not speak, so this is the resolution at which candles, quotes, fills
# and closes arrive. It matters more here than it looks: a deal confirmation
# is available only briefly after the deal, and a poll slower than that window
# loses the fill. The per-minute limits are 100 trading requests and 30
# non-trading ones per account, and lib/ig.py paces against those.
IG_POLL_SECONDS = 5

# How many times a deal is asked after before it is given up on. A deal whose
# confirmation was never read may still be live on the account, which is why
# giving up is logged as an error rather than passed over - the parity
# monitor will then see it as unpaired, which is the correct reading.
IG_CONFIRM_ATTEMPTS = 20

# TLS verification. On, and there is no reason to turn it off against IG's
# own hosts; it is here for a proxy with a private certificate authority.
IG_VERIFY_TLS = True

# --------------------------------------------------------------------------
# Interactive Brokers
#
# IB is not reached over the internet. The Client Portal Web API is served by
# a gateway you run yourself - normally on this machine - and a human
# authenticates it in a browser. Nothing in this project can log in; it can
# only ask the gateway whether somebody already did, and refuse to trade when
# nobody has. That is the whole shape of the IB integration, and it is why
# there is no password here.
#
#   1. start the Client Portal Gateway
#   2. open https://localhost:5000 and log in
#   3. export IB_ACCOUNT_ID=... and run
#
# The session times out on inactivity, so lib/ib.py keeps it alive; it will
# still need logging in again roughly daily, which is IB's rule and not one
# this project can work around.
IB_GATEWAY = os.environ.get('IB_GATEWAY', 'localhost:5000')

# Which account to deal on. No default: one login can hold a paper account
# and a live one, they are not interchangeable, and picking one for you is
# not something this file is going to do. lib/ib.py refuses to be built
# without it, and refuses again if the gateway's session does not hold it.
IB_ACCOUNT_ID = os.environ.get('IB_ACCOUNT_ID', '')

# The gateway serves HTTPS with a certificate it generated for itself, so
# verification is off. That is only defensible because of where it is
# talking - a loopback address on this machine. Point IB_GATEWAY somewhere
# else and you are trusting whatever answers, so turn this back on with a
# certificate you installed.
IB_VERIFY_TLS = False

# This project names instruments the way OANDA does. IB keys everything by a
# numeric conid, and the mapping is less derivable here than anywhere else:
# 'EUR' names a cash pair, several futures and a fund or two, on a dozen
# exchanges. An unmapped instrument raises rather than being resolved at
# runtime to whatever a search returned first.
#
# Fill it in with:
#     python scripts/ib_instruments.py EUR --sec-type CASH
# which prints what the gateway answers, for you to check and paste here.
# 'precision' is optional and overrides INSTRUMENT_PRECISION for IB only.
IB_INSTRUMENTS = {
    # 'EUR_USD': {'conid': 0, 'symbol': 'EUR', 'secType': 'CASH',
    #             'exchange': 'IDEALPRO', 'precision': 5},
}

# IB's history route serves ONE OHLC per bar, the way eToro's does, so bid and
# ask are a model or they are nothing. Unset (the default), bars carry mid
# only, the provider declines bid_ask_candles, and a wiring that needs them
# refuses to start and says why.
#
# Set it to a spread in the instrument's own units, or per instrument:
#     IB_SPREAD = 0.00002
#     IB_SPREAD = {'EUR_USD': 0.00002, 'DE30_EUR': 1.0}
# Half of it is applied either side of the served price. Measure it first -
# IBRates polls the snapshot route, which is where IB does quote both sides.
IB_SPREAD = None

# Whether orders and bars include trading outside regular hours. True is the
# right default for FX, which barely has regular hours; on an equity index it
# decides whether the gaps in the bars are real.
IB_OUTSIDE_RTH = True

# An order submission can be answered with a question - a message and an id -
# and the order exists only once that id is confirmed. Most orders draw at
# least one, so answering them is the ordinary path and this is on. Every
# question is logged at warning level whether or not it is answered, because
# a question answered silently is a warning IB raised that nobody read.
IB_CONFIRM_ORDER_QUESTIONS = True

# Questions that must never be answered automatically. Any question whose
# text contains one of these fragments stops the submission with the question
# in the log, and no order is placed. Put here whatever you want a person to
# decide - the defaults are the ones about size and about exceeding a limit,
# since those are the questions that precede an order much larger than
# intended.
IB_REFUSE_QUESTIONS = (
    'exceeds',
    'size limit',
)

# Seconds between polls, and seconds between keep-alives. IB pushes over a
# WebSocket on the same gateway, which this project does not speak, so the
# first is the resolution at which bars, quotes and fills arrive. The second
# is what keeps the gateway's session from timing out on inactivity, and is
# separate because a slow poll would otherwise lose the session between two
# candles. The published budget is ten requests a second across the whole Web
# API, and lib/ib.py paces against it.
IB_POLL_SECONDS = 5
IB_TICKLE_SECONDS = 60

# The Web API's time in force is DAY, GTC or an immediate variety - there is
# no expiry instant - so the strategies' end-of-day gtdTime is enforced on our
# side, exactly as on eToro. That is our action, not the broker's, and it is
# logged each time. Set False and a resting order rests until it triggers.
IB_ENFORCE_EXPIRY = True
