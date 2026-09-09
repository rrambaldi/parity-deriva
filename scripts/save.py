
import copy
from decimal import Decimal, getcontext
import logging
import logging.config
import datetime
import time
import argparse

from qsforex.execution.execution import OANDAExecutionHandler
#from qsforex.portfolio.portfolio import Portfolio
from qsforex.etc import settings
from qsforex.data.bulksaver import BulkSaver
from qsforex.trading.engine import Engine

# Set up logging
logging.config.fileConfig('./logging.conf')
logger = logging.getLogger('qsforex.trading.trading')

# Set the number of decimal places to 2
getcontext().prec = 2


arg_parser = argparse.ArgumentParser(description='Oanda downloader')
arg_parser.add_argument('--instrument', default='EUR_USD', required=False, help='Instrument')
arg_parser.add_argument('--dtfrom', default='2006-01-01', required=False, help='From date')
arg_parser.add_argument('--dtto', default='today', required=False, help='Today')
arg_parser.add_argument('--timeframe', default='M1', required=False, help='timeframe')
arg_parser.add_argument('--timezone', default='Europe/Rome', required=False, help='timezone')
arg_parser.add_argument('--price', default='ABM', required=False, help='Mid/Ask/Bid')
args = arg_parser.parse_args()

logger.info("Starting price streaming thread")
a= BulkSaver( pairs=args.instrument
	, granularity=args.timeframe
	, sleep = 0
	, batch_size = 2000
	, dtfrom=args.dtfrom
	, dtto=args.dtto
)
