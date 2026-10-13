"""
All strategy settings live here. Edit values; no code changes are needed.

Times are IST in 24-hour "HH:MM" format. Charts name a 5-minute candle by its
START time (the 11:10 candle runs 11:10 to 11:15). Each setting below says
whether it refers to a candle's start or close time.
"""

# ---- Broker selection ------------------------------------------------------
BROKER = "FYERS"              # "FYERS": FYERS API v3 (App ID: KNPUOI0K79-100)
                              # "ANGEL": Angel One SmartAPI

# ---- Trading mode ----------------------------------------------------------
PAPER_TRADING = True          # True: simulate fills, no real money. False: send live orders.

INSTRUMENT = "FUTURES"        # "FUTURES": trade Nifty futures, signals on the Nifty 50 index
                              # "STOCK":   trade the stock below, signals on that stock's price
STOCK_SYMBOL = "RELIANCE"     # Symbol in CAPITALS. Used only when INSTRUMENT = "STOCK".

# ---- Position size ---------------------------------------------------------
LOTS = 1                      # futures lots per trade
STOCK_CAPITAL = 100000        # rupees of your own capital per stock trade
STOCK_LEVERAGE = 5            # intraday leverage. quantity = STOCK_CAPITAL * STOCK_LEVERAGE / price

# ---- Strategy rules --------------------------------------------------------
RISK_REWARD = 4               # target = entry +/- RISK_REWARD * (entry - stop)
MAX_TRADES_PER_DAY = 1        # after this many completed trades, no more entries that day
CANDLE_MINUTES = 5

RANGE_START = "09:15"         # START time of the first opening-range candle
RANGE_END = "09:45"           # CLOSE time of the last opening-range candle (the 9:40 candle)
ENTRY_WINDOW_END = "12:10"    # last candle CLOSE time that may trigger an entry
SQUARE_OFF_TIME = "15:10"     # any open position is closed at market at this time

# ---- Execution -------------------------------------------------------------
PRODUCT_TYPE = "INTRADAY"     # FYERS: INTRADAY or MARGIN. Angel: INTRADAY (MIS) or CARRYFORWARD (NRML)
FUT_ROLLOVER_DAYS = 2         # use next-month future when current expiry is within this many days
ORDER_FILL_TIMEOUT = 20       # seconds to wait for a live market order to fill
CANDLE_FETCH_DELAY = 3        # seconds after a candle closes before asking the API for it

# ---- Calendar --------------------------------------------------------------
# NSE trading holidays as "YYYY-MM-DD". Weekends are skipped automatically.
# If the exchange returns no candles by 09:35 the day is treated as a holiday anyway.
HOLIDAYS = [
]

# ---- Files -----------------------------------------------------------------
LOG_DIR = "logs"
STATE_DIR = "state"
TRADES_FILE = "trades.csv"
