# QuantORB-Framework
QuantORB is an algorithmic trading engine built in Python. Designed to seamlessly connect with the Fyers API to process historical data and execute dynamic Opening Range Breakout strategies on the Nifty index with strict risk control

**QuantORB Framework** is an automated quantitative trading engine built in Python, designed to execute Opening Range Breakout (ORB) strategies on index derivatives (e.g., Nifty 50). It features modular broker integration, robust risk management protocols, and market data processing.

> **Disclaimer:** This repository is for educational and showcase purposes only. The strategy logic provided here is abstracted. This does not constitute financial advice. Always forward-test and paper-trade before deploying real capital.

## 🚀 Features

*   **Algorithmic Execution:** Automated order placement based on time-bound breakout parameters.
*   **Modular Broker Architecture:** Designed to interface seamlessly with modern Indian broker APIs (e.g., Angel One, FYERS).
*   **Risk Management:** Dynamic position sizing and stop-loss mechanisms built into the execution flow.
*   **Market Data Processing:** Asynchronous fetching and parsing of historical and live ticker data.
*   **Historical Replay Mode:** Backtest and validate execution logic using past market data.

## 📂 Repository Structure

*   `main.py` - The central entry point for the trading engine.
*   `strategy.py` - Contains the abstracted ORB signal generation logic.
*   `market_data.py` - Handles live tick data and candlestick aggregation.
*   `broker.py` & `angel_login.py` - Broker API authentication and order routing modules.
*   `instruments.py` - Token and symbol mapping for index options/futures.
*   `config.py` - Global configuration settings and trading parameters.
*   `setup_credentials.py` - Utility script for initial API key configuration.
*   `replay.py` - Utility for testing the engine against historical data.

---

## ⚙️ Installation & Setup

### 1. Clone the Repository
Open your terminal or Visual Studio Code and clone the project:
```bash
git clone [https://github.com/YourUsername/QuantORB-Framework.git](https://github.com/YourUsername/QuantORB-Framework.git)
cd QuantORB-Framework
