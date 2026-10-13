=====================================================================
 NIFTY OPENING RANGE BREAKOUT (ORB) ALGO  -  FYERS API & Angel One
=====================================================================

This package is a complete, ready-to-run trading algorithm. Follow the
steps in order. Nothing here needs code changes; all settings live in
config.py and all credentials live in your computer's environment
variables (never inside the code).

CONTENTS
  1. What the strategy does
  2. What you need before starting
  3. Install Python and the libraries
  4. FYERS API credentials setup
  5. Add your credentials to the computer
  6. Test the login (fyers_login.py)
  7. Configure the algo (config.py)
  8. Replay on past data (replay.py)
  9. Run the algo (main.py)
 10. Understanding the screen output
 11. Files in this package
 12. Going live: checklist and warnings
 13. Troubleshooting


---------------------------------------------------------------------
 1. WHAT THE STRATEGY DOES
---------------------------------------------------------------------
Instrument : Nifty 50 index on 5-minute candles (or a single stock).
Timeframe  : Intraday only. Everything is closed by 15:10.

Rules
  a) Opening range = the HIGH and LOW of the six 5-minute candles from
     09:15 to 09:45.
  b) From the 09:45 candle until the candle that closes at 12:10, the
     first candle that CLOSES above the range high gives a LONG entry.
     The first candle that CLOSES below the range low gives a SHORT.
     Entry is a market order the moment that candle closes.
  c) Stop loss = the LOW of the candle just before the breakout candle
     (for a long) or its HIGH (for a short).
  d) Target = entry + 4 x (entry - stop) for a long, mirrored for a
     short. Target is checked on every live tick and exits immediately.
  e) The stop loss is only honoured if a candle CLOSES beyond it.
     A wick through the stop is ignored. This avoids fake stop-outs.
  f) No signal by 12:10 = no trade that day.
  g) Any position still open at 15:10 is closed at market.
  h) One completed trade per day by default (MAX_TRADES_PER_DAY).

Two modes
  FUTURES : signals on the Nifty 50 index, orders on current-month
            Nifty futures contract. Size = LOTS.
  STOCK   : signals and orders on one stock, e.g. RELIANCE. Size =
            STOCK_CAPITAL x STOCK_LEVERAGE / price.

Paper vs live
  PAPER_TRADING = True  : every step runs, but orders are simulated at
                          live prices. No real money.
  PAPER_TRADING = False : real orders are sent to FYERS API (or Angel One).


---------------------------------------------------------------------
 2. WHAT YOU NEED BEFORE STARTING
---------------------------------------------------------------------
  * A FYERS trading account (or Angel One account) with F&O segment activated.
  * Python 3.10+ installed.
  * Internet connection and always-on PC / Cloud VM during market hours.


---------------------------------------------------------------------
 3. INSTALL PYTHON AND THE LIBRARIES
---------------------------------------------------------------------
  1. Make sure Python 3.10+ is installed with PATH enabled.
  2. In terminal / PowerShell, navigate to this folder:
        pip install -r requirements.txt


---------------------------------------------------------------------
 4. FYERS API CREDENTIALS SETUP
---------------------------------------------------------------------
Your default FYERS API credentials (from api-dashboard.fyers.in):
  FYERS_APP_ID       : KNPUOI0K79-100
  FYERS_SECRET_ID    : NB74EL61EH
  FYERS_REDIRECT_URI : http://127.0.0.1


---------------------------------------------------------------------
 5. ADD YOUR CREDENTIALS TO THE COMPUTER
---------------------------------------------------------------------
Run the credential setup helper:

        python setup_credentials.py

Select FYERS API setup. Then CLOSE PowerShell and open a NEW PowerShell window.


---------------------------------------------------------------------
 6. TEST THE LOGIN
---------------------------------------------------------------------
In the NEW PowerShell window:

        python fyers_login.py

On first run, it will output an authorization URL link. Open the link in your browser,
log in to FYERS, authorize the app, and paste the redirected URL or auth code back into
the terminal prompt. Once verified, today's session token is cached in .fyers_session.json.


---------------------------------------------------------------------
 7. CONFIGURE THE ALGO  (config.py)
---------------------------------------------------------------------
Open config.py to adjust parameters:
  BROKER = "FYERS"          ("FYERS" or "ANGEL")
  PAPER_TRADING = True      (Set False for live trading)
  INSTRUMENT = "FUTURES"    (or "STOCK")
  STOCK_SYMBOL = "RELIANCE"
  LOTS = 1
  RISK_REWARD = 4
  MAX_TRADES_PER_DAY = 1


---------------------------------------------------------------------
 8. REPLAY ON PAST DATA
---------------------------------------------------------------------
        python replay.py 30

Runs the strategy on the last 30 calendar days of historical 5-minute candles.


---------------------------------------------------------------------
 9. RUN THE ALGO
---------------------------------------------------------------------
        python main.py

or double-click run.bat.

The algo logs in, streams live ticks via FYERS WebSocket, evaluates 5-minute
candles as they close, executes paper or live orders, and closes positions at 15:10 IST.


---------------------------------------------------------------------
 11. FILES IN THIS PACKAGE
---------------------------------------------------------------------
  README.txt            This guide.
  CHANGELOG.txt         What changed in each version.
  VERSION.txt           Version number of this package.
  requirements.txt      Library list for pip.
  run.bat               Launcher script.
  setup_credentials.py  Credential setup helper for FYERS and Angel One.
  config.py             All strategy settings and broker selection.
  main.py               24/7 runner engine.
  replay.py             Historical backtester.
  fyers_login.py        FYERS API v3 session manager & authenticator.
  angel_login.py        Angel One SmartAPI authenticator.
  strategy.py           Opening Range Breakout (ORB) logic.
  market_data.py        FYERS & Angel candle fetchers & WebSocket feeds.
  instruments.py        FYERS & Angel instrument resolvers.
  broker.py             PaperBroker, FyersLiveBroker, and LiveBroker.
=====================================================================
