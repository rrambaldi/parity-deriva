from decimal import Decimal
import os


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

CSV_DATA_DIR = os.environ.get('PARITY_DERIVA_CSV_DATA_DIR', ".")
OUTPUT_RESULTS_DIR = os.environ.get('OUTPUT_RESULTS_DIR', ".")

DOMAIN = "practice"
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

DATA_DIR = os.environ.get('PARITY_DERIVA_DATA_DIR', "/home/rrambaldi/DATA")
LOG_DIR = os.environ.get('PARITY_DERIVA_LOG_DIR', "/home/rrambaldi/DATA")
BASE_CURRENCY = "EUR"
EQUITY = Decimal("100000.00")

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

# Used when an instrument is not in the table above. Five decimals is the FX
# convention and, more importantly, it errs towards *too many* decimals: the
# broker then rejects the order loudly instead of accepting a level that has
# been silently rounded away.
DEFAULT_PRICE_PRECISION = 5

