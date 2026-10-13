"""
Replay the strategy over past days using historical 5-minute candles.

    python replay.py            # last 30 calendar days
    python replay.py 60         # last 60 calendar days

Uses the same ORBEngine as live trading. Ticks are not available historically,
so a target counts as hit when a candle's high/low reaches it, and P&L is in
signal-instrument points times the configured quantity.
"""

import datetime as dt
import logging
import math
import sys
from collections import defaultdict

import config
from instruments import load_master, resolve
from market_data import fetch_candles
from strategy import ORBEngine

try:
    import fyers_login
except ImportError:
    fyers_login = None

try:
    import angel_login
except ImportError:
    angel_login = None


def get_client():
    broker = getattr(config, "BROKER", "FYERS").upper()
    if broker == "FYERS":
        client, _ = fyers_login.login()
        return client, None
    else:
        if angel_login is None:
            raise RuntimeError("angel_login module not available")
        api, _ = angel_login.login()
        master = load_master()
        return api, master


def quantity(trade_inst, price: float) -> int:
    if config.INSTRUMENT.upper() == "FUTURES":
        return config.LOTS * trade_inst.lot_size
    return max(1, math.floor(config.STOCK_CAPITAL * config.STOCK_LEVERAGE / price))


def main():
    logging.basicConfig(level=logging.WARNING)
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    today = dt.date.today()
    client, master = get_client()
    signal_inst, trade_inst = resolve(master, today)

    all_candles = []
    # Fetch day-by-day
    start_date = today - dt.timedelta(days=days)
    cur = start_date
    while cur <= today:
        if cur.weekday() < 5 and cur.isoformat() not in config.HOLIDAYS:
            day_candles = fetch_candles(client, signal_inst, cur)
            if day_candles:
                all_candles.extend(day_candles)
        cur += dt.timedelta(days=1)

    by_day = defaultdict(list)
    for c in all_candles:
        by_day[c.start.date()].append(c)

    broker = getattr(config, "BROKER", "FYERS").upper()
    print(f"\nReplay ({broker} API): {signal_inst.symbol} signals, {trade_inst.symbol} size, "
          f"RR 1:{config.RISK_REWARD}, max {config.MAX_TRADES_PER_DAY} trade/day, "
          f"{len(by_day)} trading days\n")
    print(f"{'date':<11}{'side':<6}{'entry':<7}{'price':>10}{'SL':>10}{'target':>10}"
          f"{'exit':<7}{'reason':<11}{'price':>10}{'points':>9}{'P&L':>11}")
    print("-" * 102)

    all_trades = []
    for day in sorted(by_day):
        engine = ORBEngine(replay=True)
        for c in by_day[day]:
            sig = engine.on_candle(c)
            if sig is None:
                continue
            if sig.kind == "ENTER":
                engine.open_position(sig, quantity(trade_inst, sig.spot), sig.spot)
            else:
                all_trades.append(engine.close_position(sig, sig.spot))
        if engine.position is not None:            # data ended with a position still open
            last = by_day[day][-1]
            sig = engine.square_off_signal(last.close_time, last.close)
            all_trades.append(engine.close_position(sig, last.close))

    for p in all_trades:
        print(f"{p.signal_time[:10]:<11}{p.side:<6}{p.signal_time[11:16]:<7}{p.entry_fill:>10.2f}"
              f"{p.sl:>10.2f}{p.target:>10.2f} {p.exit_time[11:16]:<7}{p.exit_reason:<11}"
              f"{p.exit_fill:>10.2f}{p.points:>+9.2f}{p.pnl:>+11.2f}")

    if not all_trades:
        print("no trades in this period")
        return
    wins = [p for p in all_trades if p.pnl > 0]
    losses = [p for p in all_trades if p.pnl <= 0]
    print("-" * 102)
    print(f"trades {len(all_trades)}  wins {len(wins)}  losses {len(losses)}  "
          f"win rate {100 * len(wins) / len(all_trades):.0f}%")
    print(f"total points {sum(p.points for p in all_trades):+.2f}  "
          f"total P&L {sum(p.pnl for p in all_trades):+.2f}  "
          f"avg win {sum(p.pnl for p in wins) / len(wins) if wins else 0:+.2f}  "
          f"avg loss {sum(p.pnl for p in losses) / len(losses) if losses else 0:+.2f}")
    print(f"days without a signal: {len(by_day) - len({p.signal_time[:10] for p in all_trades})}")


if __name__ == "__main__":
    main()
