import copy
from decimal import Decimal, getcontext
import logging
import logging.config
try:
	import Queue as queue
except ImportError:
	import queue
import threading
import time

from qsforex.etc import settings
from qsforex.lib.utils import getLogger
from qsforex.strategy.AG02 import AG02
from qsforex.data.candles import ForexCandles
from qsforex.data.transaction import StreamingForexTransactions
from qsforex.execution.execution import OANDAExecutionHandler
from qsforex.backtest.oanda import OANDABacktester
from qsforex.portfolio.moneymanager import MoneyManager
from qsforex.trading.engine import Engine
from qsforex.event.saver import EventSaver

# Set up logging
logger = getLogger()

# Set the number of decimal places to 2
getcontext().prec = 2

e = Engine()
e.heartbeat = 0.5  # Time in seconds between polling
equity = settings.EQUITY

# Pairs to include in streaming data set
pairs = ["DE30_EUR"]

# Create the OANDA market price streaming class
# making sure to provide authentication commands

# Create the strategy/signal generator, passing the 
# instrument and the events queue
e.add_handler( AG02( pairs=pairs) )
e.add_handler( MoneyManager( pairs=pairs, units=100) )
e.add_handler( OANDAExecutionHandler() )

e.add_handler( OANDABacktester( pairs=pairs) )
# Create the portfolio object that will be used to
# compare the OANDA positions with the local, to
# ensure backtesting integrity.
#portfolio = Portfolio( prices, equity=equity, backtest=False)
#e.push(portfolio)

e.add_handler(EventSaver())



# Create two separate threads: One for the trading loop
# and another for the market price streaming class

e.add_handler( ForexCandles( pairs=pairs, granularity='M1') )
e.add_handler(StreamingForexTransactions( pairs=pairs ) )

# Start both threads
logger.info("Starting trading engine")
e.run()

