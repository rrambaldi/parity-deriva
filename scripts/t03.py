import copy
from decimal import Decimal, getcontext
import logging
import logging.config
import datetime
import time
import argparse
import queue
import threading
import time

from parity_deriva.etc import settings
from parity_deriva.lib.utils import getLogger
from parity_deriva.strategy.BO01 import BO01
from parity_deriva.strategy.BO02 import BO02
from parity_deriva.data.candles import ForexCandles
from parity_deriva.data.transaction import StreamingForexTransactions
from parity_deriva.execution.execution import OANDAExecutionHandler
from parity_deriva.backtest.oanda import OANDABacktester
from parity_deriva.portfolio.moneymanager import MoneyManager
from parity_deriva.trading.engine import Engine
from parity_deriva.event.saver import EventSaver

today=datetime.date.today().strftime('%Y-%m-%d')
arg_parser = argparse.ArgumentParser(description='Test on dadata')
arg_parser.add_argument('--instrument', required=False, help='Instrument', action='append')
arg_parser.add_argument('--dtfrom', default='2006-01-01', required=False, help='From date')
arg_parser.add_argument('--dtto', default=today, required=False, help='Today')
arg_parser.add_argument('--timeframe', default='M1', required=False, help='timeframe')
arg_parser.add_argument('--timezone', default='Europe/Rome', required=False, help='timezone')
args = arg_parser.parse_args()

# Set up logging
logger = getLogger()

# Set the number of decimal places to 2
getcontext().prec = 2

e = Engine()
e.heartbeat = 0.5  # Time in seconds between polling
equity = settings.EQUITY

# Pairs to include in streaming data set

pairs=args.instrument
# Create the OANDA market price streaming class
# making sure to provide authentication commands

# Create the strategy/signal generator, passing the 
# instrument and the events queue

for p in pairs:
	e.add_handler( BO01(pair=p) )
	e.add_handler( BO02(pair=p) )
#e.add_handler( MoneyManager( pairs=pairs, units=100) )
#e.add_handler( OANDAExecutionHandler() )

#e.add_handler( OANDABacktester( pairs=pairs) )
# Create the portfolio object that will be used to
# compare the OANDA positions with the local, to
# ensure backtesting integrity.
#portfolio = Portfolio( prices, equity=equity, backtest=False)
#e.push(portfolio)

#e.add_handler(EventSaver())



# Create two separate threads: One for the trading loop
# and another for the market price streaming class

e.add_handler( ForexCandles( pairs=pairs
	, granularity=args.timeframe
	, dtfrom=datetime.datetime.strptime(args.dtfrom,"%Y-%m-%d")
	, dtto=datetime.datetime.strptime(args.dtto,"%Y-%m-%d")
))
#e.add_handler(StreamingForexTransactions( pairs=pairs ) )

# Start both threads
logger.info("Starting trading engine")
e.run()

