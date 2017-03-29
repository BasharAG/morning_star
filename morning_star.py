"""
Author: Ian Doherty - idohertyr
Date: Mar 29, 2017

This Quantopian trading algorithm selects stocks based on Morningstar fundamentals.
Stocks with a profitability and growth rate of 'A' are selected and bought if
total open positions is less than 50. Positions are then sold on a -2% or +5%
fluctuation or if RSI levels for a particular stock rise higher than the 
high_rsi level defined in the initialize function. This algorithm is ready for
integration with Robinhood App, but fails when data is 'NaN'. The number of
open positions, stock list length, and portfolio value are recorded for comparison.

To-do: Prevent 'NaN' values.

"""

# Import libraries
from quantopian.pipeline import Pipeline
from quantopian.pipeline.factors import CustomFactor, SimpleMovingAverage, AverageDollarVolume, Latest, RSI
from quantopian.algorithm import attach_pipeline, pipeline_output
from quantopian.pipeline.data.builtin import USEquityPricing
from quantopian.pipeline.filters.morningstar import Q1500US
from quantopian.pipeline.data import morningstar
from quantopian.pipeline.data.psychsignal import stocktwits
import numpy as np
import talib


def initialize(context):
    """
    Called once at the start of the algorithm.
    """
    ### Robinhood

    # Set a trading guard that will prevent any short positions
    # from being placed. This is to insure that algorithms that
    # depend on short positions are not accidently deployed.
    set_long_only()

    # Keeping track of the last sale that you have.
    context.last_sale = None

    # Just a simple variable to demonstrate `context.last_sale`,
    # `cash_settlement_date`, and `check_last_sale`
    context.trading_days = 0

    # Execute Lock ins every day at market open.
    schedule_function(my_rebalance, date_rules.every_day(), time_rules.market_open())

    # Record variables every day, 1 minutes before market close.
    schedule_function(my_record_vars, date_rules.every_day(), time_rules.market_close(minutes=1))

    # Set commissions and slippage to 0 to determine pure alpha
    set_commission(commission.PerShare(cost=0, min_trade_cost=0))
    set_slippage(slippage.FixedSlippage(spread=0))

    # Define starting portfolio balance
    context.starting_balance = context.portfolio.portfolio_value

    # Define Stock List
    context.stocks = []

    # Define Max Open Positions
    context.max_open_positions = 50

    # Define default weight
    context.default_weight = (1 / float(context.max_open_positions))

    # Define Max Profit or Loss (Percent as Decimal)
    context.profit_lock_in = 0.05
    context.loss_lock_in = -0.02

    # Define RSI levels for weights
    context.low_rsi = 40
    context.high_rsi = 60

    # Define MACD spread
    context.macd_spread = 0.16

    # Define Latest Prices
    context.latest_prices = []

    # Define Portfolio Cost Basis
    context.lock_ins = []

    # Define RSIs
    context.rsis = []

    # Define MACDs
    context.macds = []

    # Define weights
    context.weights = []
    pass


def before_trading_start(context, data):
    """
    Called every day before market open.

    Update Context

    """

    """
    Morningstar - Fundamental Data
    """
    ### Get Fundamentals
    fundamental_df = get_fundamentals(
        # Retrieve data based on PE ratio and economic sector
        query(
            fundamentals.asset_classification.growth_grade,
            fundamentals.asset_classification.profitability_grade,
        )
            # Only take profitability grade that equals A
            .filter(fundamentals.asset_classification.profitability_grade == 'A')
            # Only take growth rate of A
            .filter(fundamentals.asset_classification.growth_grade == 'A')
    )

    context.fundamental_df = fundamental_df

    # Adds new stock, or existing open positions
    context.stocks = update_stock_list(context, data)

    # Determines orders to execute at market open
    context.lock_ins = lock_in_percent(context, data)

    # Gets rsis for stock list
    context.rsis = get_rsis(context, data)

    # Gets the macds for each stock in stock list
    context.macds = get_macd_signals(context, data)

    # Get weights for each stock
    context.weights = my_assign_weights(context, data)

    ## Robinhood
    context.trading_days += 1

    # `check_last_sale` is what `cash_settlement_date` needs in
    # order to work properly. Only for backtesting purposes!
    check_last_sale(context)

    pass


def my_assign_weights(context, data):
    """
    Determine a weight calculate for each stock in stock list.

    Given the current RSI and MACD spread determine a weight.

    """
    weights = {}
    weight = context.default_weight
    rsis = context.rsis
    macds = context.macds

    for stock in context.stocks:
        if ((macds[stock] > context.macd_spread)):
            weights[stock] = weight
        elif ((context.lock_ins[stock] == True) |
                  (rsis[stock] > context.high_rsi)):
            weights[stock] = 0

    return weights


def my_rebalance(context, data):
    """
    Adjust the weights of each stock in the stock list. Orders are then exeucted
    to meet the order_target_percent() function. 
    """
    for stock in context.stocks:
        if cash_settlement_date(context):
            log.info("Unsettled Cash Simulated")
        elif (data.can_trade(stock)):
            order_target_percent(stock, context.default_weight)
    pass


def update_stock_list(context, data):
    """
    Adds open positions to stock list and any additionally selected stocks
    from the pipeline. The stock list is then used to check parameters to
    adjust weights. This function is run in before_trading_start()
    """
    pos_list = {}
    updated_list = []
    count = 0
    sample = context.fundamental_df
    # context.security_list_sorted
    for stock in context.stocks:
        if (context.portfolio.positions[stock].amount != 0):
            pos_list[count] = stock
            count = count + 1
    for stock in sample:
        if ((context.portfolio.positions[stock].amount == 0) & (count < context.max_open_positions)):
            pos_list[count] = stock
            count = count + 1
    for key, value in pos_list.iteritems():
        updated_list.append(value)
    return updated_list


def my_record_vars(context, data):
    """
    Plot variables at the end of each day.
    """
    record(open_positions=len(context.portfolio.positions),
           stock_list=len(context.stocks),
           portfolio_value=(context.portfolio.portfolio_value / context.starting_balance))
    pass


def handle_data(context, data):
    """
    Called every minute.
    """
    pass


"""
Get Functions
"""


def get_latest_prices(context, data):
    """
    Retreives the latest prices for each stock in stock list.

    """
    prices = {}
    for stock in context.stocks:
        price = data.current(stock, 'price')
        prices[stock] = price
    return prices


def lock_in_percent(context, data):
    """
    Determines if stock in stock_list should be sold according the position value.

    """
    # Local Variable
    lock_in = {}
    # Get Latest Prices
    context.prices = get_latest_prices(context, data)
    for stock in context.stocks:
        cost_basis = context.portfolio.positions[stock].cost_basis
        shares = context.portfolio.positions[stock].amount
        if ((cost_basis == 0) | (shares == 0)):
            lock_in[stock] = False
        else:
            bought = cost_basis * shares
            current = context.prices[stock] * shares
            percent_change = (current - bought) / bought
            if ((percent_change > context.profit_lock_in) | (percent_change < context.loss_lock_in)):
                lock_in[stock] = True
            else:
                lock_in[stock] = False
    return lock_in


def get_rsis(context, data):
    """
    Calculates the rsis for context.stocks

    """
    rsis = {}
    for stock in context.stocks:
        prices = data.history(stock, 'close', 50, '1d')
        prices = prices.dropna()
        rsi = talib.RSI(prices, timeperiod=25)[-1]
        rsis[stock] = rsi
    return rsis


def get_macd_signals(context, data):
    macds = {}
    for stock in context.stocks:
        prices = data.history(stock, 'price', 40, '1d')
        macd_raw, signal, hist = talib.MACD(prices, fastperiod=12, slowperiod=26, signalperiod=9)
        macds[stock] = abs(macd_raw[-1] - signal[-1])
    return macds


"""
Helper Functions

"""


def printStockList(stocks):
    for stock in stocks:
        log.info('\n' + str(stock.symbol) + '\n')
    pass


def checkValues(prices):
    for price in prices:
        if (price == 'nan'):
            print 'nan'


"""
Robinhood
Functions
"""


def cash_settlement_date(context):
    """
    This will simulate Robinhood's T+3 cash settlement. If the 
    most recent sale is less than 3 trading days from the current
    day, assume we have unsettled funds and exit

    To only be used for backtesting!
    """
    if context.last_sale and (get_datetime() - context.last_sale).days < 3:
        return True


def do_unsettled_funds_exist(context):
    """
    For Robinhood users. In order to prevent you from attempting
    to trade on unsettled cash (settlement dates are T+3) from
    sale of proceeds. You can use this snippet of code which
    checks for whether or not you currently have unsettled funds

    To only be used for live trading!
    """
    if context.portfolio.cash != context.account.settled_cash:
        return True


def check_last_sale(context):
    """
    To be used at the end of each bar. This checks if there were
    any sales made and sets that to `context.last_sale`.
    `context.last_sale` is then used in `cash_settlement_date` to
    simulate a T+3 Cash Settlement date

    To only be used for backtesting!
    """
    open_orders = get_open_orders()
    most_recent_trade = []
    # If there are open orders check for the most recent sale
    if open_orders:
        for sec, order in open_orders.iteritems():
            for oo in order:
                if oo.amount < 0:
                    most_recent_trade.append(oo.created)
    if len(most_recent_trade) > 0:
        context.last_sale = max(most_recent_trade)
    pass