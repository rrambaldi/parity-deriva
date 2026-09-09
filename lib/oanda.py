from __future__ import print_function

import json
import logging

class OANDAObject(object):

	def __init__(self, id, dct):
		self.logger = logging.getLogger('qsforex.trading.trading')
		self.add(dct)
		self.id = id

	def add(self, dct):
		for k in dct.keys():
#			if k not in self.__dict__.keys():
#				self.logger.warning("unhandled attribute %s" % k)
			setattr(self, k, dct[k])

	def dump(self):
		txt = ""
		for k in self.__dict__:
			txt += "%s: %s\n" % (k, self.__dict__[k])
		return txt

	def to_dict(self):
		return self.__dict__
'''
{
   "account" : {
      "marginCloseoutNAV" : "94161.0305",
      "marginCloseoutUnrealizedPL" : "0.0000",
      "marginCloseoutMarginUsed" : "0.0000",
      "openPositionCount" : 0,
      "withdrawalLimit" : "94161.0305",
      "hedgingEnabled" : false,
      "createdTime" : "2017-01-06T10:08:52.136226053Z",
      "currency" : "EUR",
      "marginCloseoutPercent" : "0.00000",
      "marginAvailable" : "94161.0305",
      "resettablePL" : "-5826.5350",
      "lastTransactionID" : "2099",
      "marginCallPercent" : "0.00000",
      "unrealizedPL" : "0.0000",
      "id" : "101-000-0000000-000",
      "marginCallMarginUsed" : "0.0000",
      "marginRate" : "0.02",
      "marginUsed" : "0.0000",
      "marginCloseoutPositionValue" : "0.0000",
      "createdByUserID" : 0,
      "pendingOrderCount" : 0,
      "pl" : "-5826.5350",
      "NAV" : "94161.0305",
      "positionValue" : "0.0000",
      "balance" : "94161.0305",
      "openTradeCount" : 0,
      "alias" : "Primary"
   },
   "lastTransactionID" : "2099"
}
'''

'''
 *** STOPLOSSORDER ***
{
    # 
    # The Order's identifier, unique within the Order's Account.
    # 
    id : (OrderID),

    # 
    # The time when the Order was created.
    # 
    createTime : (DateTime),

    # 
    # The current state of the Order.
    # 
    state : (OrderState),

    # 
    # The client extensions of the Order. Do not set, modify, or delete
    # clientExtensions if your account is associated with MT4.
    # 
    clientExtensions : (ClientExtensions),

    # 
    # The type of the Order. Always set to STOP_LOSS for Stop Loss Orders.
    # 
    type : (OrderType, default=STOP_LOSS),

    # 
    # The ID of the Trade to close when the price threshold is breached.
    # 
    tradeID : (TradeID, required),

    # 
    # The client ID of the Trade to be closed when the price threshold is
    # breached.
    # 
    clientTradeID : (ClientID),

    # 
    # The price threshold specified for the StopLoss Order. The associated
    # Trade will be closed by a market price that is equal to or worse than
    # this threshold.
    # 
    price : (PriceValue, required),

    # 
    # The time-in-force requested for the StopLoss Order. Restricted to GTC,
    # GFD and GTD for StopLoss Orders.
    # 
    timeInForce : (TimeInForce, required, default=GTC),

    # 
    # The date/time when the StopLoss Order will be cancelled if its
    # timeInForce is GTD.
    # 
    gtdTime : (DateTime),

    # 
    # ID of the Transaction that filled this Order (only provided when the
    # Order's state is FILLED)
    # 
    fillingTransactionID : (TransactionID),

    # 
    # Date/time when the Order was filled (only provided when the Order's state
    # is FILLED)
    # 
    filledTime : (DateTime),

    # 
    # Trade ID of Trade opened when the Order was filled (only provided when
    # the Order's state is FILLED and a Trade was opened as a result of the
    # fill)
    # 
    tradeOpenedID : (TradeID),

    # 
    # Trade ID of Trade reduced when the Order was filled (only provided when
    # the Order's state is FILLED and a Trade was reduced as a result of the
    # fill)
    # 
    tradeReducedID : (TradeID),

    # 
    # Trade IDs of Trades closed when the Order was filled (only provided when
    # the Order's state is FILLED and one or more Trades were closed as a
    # result of the fill)
    # 
    tradeClosedIDs : (Array[TradeID]),

    # 
    # ID of the Transaction that cancelled the Order (only provided when the
    # Order's state is CANCELLED)
    # 
    cancellingTransactionID : (TransactionID),

    # 
    # Date/time when the Order was cancelled (only provided when the state of
    # the Order is CANCELLED)
    # 
    cancelledTime : (DateTime),

    # 
    # The ID of the Order that was replaced by this Order (only provided if
    # this Order was created as part of a cancel/replace).
    # 
    replacesOrderID : (OrderID),

    # 
    # The ID of the Order that replaced this Order (only provided if this Order
    # was cancelled as part of a cancel/replace).
    # 
    replacedByOrderID : (OrderID)
}
'''

class OANDAOrder(OANDAObject):
    id = 0
    createTime = None
    state = '' # PENDING | FILLED | TRIGGERED | CANCELLED
    clientExtensions =  {}
    type = 'STOP_LOSS'
    tradeID  = 0
    clientTradeID = 0
    price = 0.0
    units = 0
    timeInForce = None
    gtdTime = None
    fillingTransactionID = 0
    filledTime = None
    tradeOpenedID = 0
    tradeReducedID  = 0
    tradeClosedIDs = 0
    cancellingTransactionID = 0
    cancelledTime = ""
    replacesOrderID  = 0
    replacedByOrderID = 0
    orig = None
    SLOrder = None
    TPOrder = None


'''
{
    # 
    # The Trade's identifier, unique within the Trade's Account.
    # 
    id : (TradeID),

    # 
    # The Trade's Instrument.
    # 
    instrument : (InstrumentName),

    # 
    # The execution price of the Trade.
    # 
    price : (PriceValue),

    # 
    # The date/time when the Trade was opened.
    # 
    openTime : (DateTime),

    # 
    # The current state of the Trade.
    # 
    state : (TradeState),

    # 
    # The initial size of the Trade. Negative values indicate a short Trade,
    # and positive values indicate a long Trade.
    # 
    initialUnits : (DecimalNumber),

    # 
    # The number of units currently open for the Trade. This value is reduced
    # to 0.0 as the Trade is closed.
    # 
    currentUnits : (DecimalNumber),

    # 
    # The total profit/loss realized on the closed portion of the Trade.
    # 
    realizedPL : (AccountUnits),

    # 
    # The unrealized profit/loss on the open portion of the Trade.
    # 
    unrealizedPL : (AccountUnits),

    # 
    # The IDs of the Transactions that have closed portions of this Trade.
    # 
    closingTransactionIDs : (Array[TransactionID]),

    # 
    # The financing paid/collected for this Trade.
    # 
    financing : (AccountUnits),

    # 
    # The date/time when the Trade was fully closed. Only provided for Trades
    # whose state is CLOSED.
    # 
    closeTime : (DateTime),

    # 
    # The client extensions of the Trade.
    # 
    clientExtensions : (ClientExtensions),

    # 
    # Full representation of the Trade's Take Profit Order, only provided if
    # such an Order exists.
    # 
    takeProfitOrder : (TakeProfitOrder),

    # 
    # Full representation of the Trade's Stop Loss Order, only provided if such
    # an Order exists.
    # 
    stopLossOrder : (StopLossOrder),

    # 
    # Full representation of the Trade's Trailing Stop Loss Order, only
    # provided if such an Order exists.
    # 
    trailingStopLossOrder : (TrailingStopLossOrder)
}
'''
class OANDATrade(OANDAObject):
    id = 0
    instrument = ''
    price = 0
    openTime = None
    state = None # OPEN | CLOSED | CLOSE_WHEN_TRADEABLE
    initialUnits = 0
    currentUnits = 0
    realizedPL = 0.0
    unrealizedPL = 0.0
    closingTransactionIDs = 0
    financing = 0.0
    closeTime = None
    clientExtensions = None
    takeProfitOrder = None
    stopLossOrder = None
    trailingStopLossOrder = None

class OANDAPositionSize(OANDAObject):
	units= 0.0
	averagePrice= 0.0
	tradeIDs= []
	pl= 0.0
	unrealizedPL = 0.0
	resettablePL = 0.0

class OANDAPosition(OANDAObject):
	instrument= ""
	pl = 0.0
	unrealizedPL= 0.0
	resettablePL= 0.0
	long= None
	short= None


