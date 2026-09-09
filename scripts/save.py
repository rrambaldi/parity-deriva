
import copy
from decimal import Decimal, getcontext
from dateutil import parser
import logging
import logging.config
import datetime
import time
import argparse

from parity_deriva.execution.execution import OANDAExecutionHandler
#from parity_deriva.portfolio.portfolio import Portfolio
from parity_deriva.etc import settings
from parity_deriva.data.bulksaver import BulkSaver
from parity_deriva.trading.engine import Engine

from parity_deriva.lib.utils import getLogger

# Set up logging
logger = getLogger()

# Set the number of decimal places to 2
getcontext().prec = 2


arg_parser = argparse.ArgumentParser(description='Oanda downloader')
arg_parser.add_argument('--instrument', default='EUR_USD', required=False, help='Instrument')
arg_parser.add_argument('--dtfrom', default='2006-01-01', required=False, help='From date')
arg_parser.add_argument('--dtto', default=datetime.date.today().strftime('%Y-%m-%d'),
	required=False, help='Today')
arg_parser.add_argument('--timeframe', default='M1', required=False, help='timeframe')
arg_parser.add_argument('--timezone', default='Europe/Rome', required=False, help='timezone')
arg_parser.add_argument('--price', default='ABM', required=False, help='Mid/Ask/Bid')
args = arg_parser.parse_args()

logger.info("Starting price streaming thread")
a= BulkSaver( pairs=args.instrument.split(",")
	, granularity=args.timeframe
	, sleep = 0
	, batch_size = 2000
	, dtfrom=parser.parse(args.dtfrom)
	, dtto=parser.parse(args.dtto)
)
a.stream_to_queue()
