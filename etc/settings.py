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

CSV_DATA_DIR = os.environ.get('QSFOREX_CSV_DATA_DIR', ".")
OUTPUT_RESULTS_DIR = os.environ.get('OUTPUT_RESULTS_DIR', ".")

DOMAIN = "practice"
STREAM_DOMAIN = ENVIRONMENTS["streaming"][DOMAIN]
API_DOMAIN = ENVIRONMENTS["api"][DOMAIN]
# Credentials come from the environment only - never commit them. Set:
#   export OANDA_API_ACCESS_TOKEN=...
#   export OANDA_API_ACCOUNT_ID=...
ACCESS_TOKEN = os.environ.get('OANDA_API_ACCESS_TOKEN', '')
ACCOUNT_ID = os.environ.get('OANDA_API_ACCOUNT_ID', '')
API_VERSION = '3'

DATA_DIR = os.environ.get('QSFOREX_DATA_DIR', "/home/rrambaldi/DATA")
LOG_DIR = os.environ.get('QSFOREX_LOG_DIR', "/home/rrambaldi/DATA")
BASE_CURRENCY = "EUR"
EQUITY = Decimal("100000.00")

DEF_CONFIG = 'logging.conf'
DEF_PAIRS  = [ 'EUR_USD' ]
DEF_GRANULARITY = 'M1'

