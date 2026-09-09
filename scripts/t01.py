import copy
from decimal import Decimal, getcontext
import logging
import logging.config
import queue
import threading
import time

from parity_deriva.etc import settings
from parity_deriva.lib.utils import getLogger
from parity_deriva.strategy.AG01 import AG01
from parity_deriva.data.candles import ForexCandles
from parity_deriva.data.transaction import StreamingForexTransactions
from parity_deriva.execution.execution import OANDAExecutionHandler
from parity_deriva.backtest.oanda import OANDABacktester
from parity_deriva.portfolio.moneymanager import MoneyManager
from parity_deriva.trading.engine import Engine
from parity_deriva.event.saver import EventSaver

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
e.add_handler( AG01( pairs=pairs) )
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

