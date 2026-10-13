"""
24/7 runner for the Nifty opening-range-breakout strategy on FYERS API & Angel One.

    python main.py

Sleeps outside market hours, logs in each trading morning, streams live ticks,
evaluates 5-minute candles as they close, and places (or simulates) orders as
configured in config.py. Restart-safe: today's state is saved after every event.
"""

import csv
import datetime as dt
import json
import logging
import logging.handlers
import math
import os
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import config
import fyers_login
from broker import FyersLiveBroker, LiveBroker, PaperBroker
from instruments import load_master, resolve
from market_data import CandleBuilder, FyersTickFeed, TickFeed, fetch_candles
from strategy import ORBEngine, Signal, parse_time

try:
    import angel_login
except ImportError:
    angel_login = None

IST = ZoneInfo("Asia/Kolkata")
SESSION_START = dt.time(8, 55)     # log in and prepare
SESSION_END = dt.time(15, 31)      # stop for the day
HOLIDAY_CHECK = dt.time(9, 35)     # no candles by now -> treat as holiday
MARKET_OPEN = dt.time(9, 15)
MARKET_CLOSE = dt.time(15, 30)

log = logging.getLogger("orb")


class Holiday(Exception):
    pass


# ----------------------------------------------------------------- helpers
def now() -> dt.datetime:
    return dt.datetime.now(IST)


def is_trading_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in config.HOLIDAYS


def next_session_start(after: dt.datetime) -> dt.datetime:
    d = after.date()
    if after.time() >= SESSION_START:
        d += dt.timedelta(days=1)
    while not is_trading_day(d):
        d += dt.timedelta(days=1)
    return dt.datetime.combine(d, SESSION_START, IST)


def sleep_until(target: dt.datetime):
    while True:
        remaining = (target - now()).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 60))


def next_fetch_time(t: dt.datetime) -> dt.datetime:
    """The next moment a candle should be fetched: boundary + CANDLE_FETCH_DELAY."""
    cm = config.CANDLE_MINUTES
    floor = t.replace(minute=t.minute - t.minute % cm, second=0, microsecond=0)
    candidate = floor + dt.timedelta(seconds=config.CANDLE_FETCH_DELAY)
    if candidate > t:
        return candidate
    return candidate + dt.timedelta(minutes=cm)


def setup_logging():
    Path(config.LOG_DIR).mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    fh = logging.handlers.TimedRotatingFileHandler(
        Path(config.LOG_DIR) / "orb.log", when="midnight", backupCount=30, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    logging.getLogger("websocket").setLevel(logging.WARNING)


def append_trade(pos, mode: str, symbol: str):
    path = Path(config.TRADES_FILE)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["date", "mode", "symbol", "side", "qty", "signal_time", "entry_spot",
                        "entry_fill", "stop_loss", "target", "exit_time", "exit_reason",
                        "exit_spot", "exit_fill", "points", "pnl"])
        w.writerow([pos.signal_time[:10], mode, symbol, pos.side, pos.qty, pos.signal_time[11:19],
                    f"{pos.entry_spot:.2f}", f"{pos.entry_fill:.2f}", f"{pos.sl:.2f}",
                    f"{pos.target:.2f}", pos.exit_time[11:19], pos.exit_reason,
                    f"{pos.exit_spot:.2f}", f"{pos.exit_fill:.2f}", f"{pos.points:.2f}",
                    f"{pos.pnl:.2f}"])


# ----------------------------------------------------------------- session
class Session:
    def __init__(self, day: dt.date):
        self.day = day
        self.client = None
        self.feed = None
        self.broker = None
        self.engine = None
        self.signal_inst = None
        self.trade_inst = None
        self.builder = CandleBuilder()
        self.state_path = Path(config.STATE_DIR) / f"{day.isoformat()}.json"
        self.pending_exit = None
        self.square_off = parse_time(config.SQUARE_OFF_TIME)
        self.broker_name = getattr(config, "BROKER", "FYERS").upper()

    # -- setup --
    def prepare(self):
        if self.broker_name == "FYERS":
            self.client, session = fyers_login.login()
            self.signal_inst, self.trade_inst = resolve(None, self.day)
            instruments = [self.signal_inst]
            if self.trade_inst.token != self.signal_inst.token:
                instruments.append(self.trade_inst)

            self.feed = FyersTickFeed(
                client_id=session["app_id"],
                access_token=session["access_token"],
                instruments=instruments,
                builder_token=self.signal_inst.token,
                builder=self.builder,
                tz=IST,
            )
            self.feed.start()

            if config.PAPER_TRADING:
                self.broker = PaperBroker(self.feed, IST)
            else:
                self.broker = FyersLiveBroker(self.client, self.feed, IST)
        else:
            if angel_login is None:
                raise RuntimeError("angel_login module not available. Install smartapi-python or set config.BROKER = 'FYERS'")
            self.client, session = angel_login.login()
            master = load_master()
            self.signal_inst, self.trade_inst = resolve(master, self.day)

            instruments = [self.signal_inst]
            if self.trade_inst.token != self.signal_inst.token:
                instruments.append(self.trade_inst)
            self.feed = TickFeed(
                os.environ["ANGEL_API_KEY"], session["client_code"], session["jwt_token"],
                session["feed_token"], instruments,
                builder_token=self.signal_inst.token, builder=self.builder, tz=IST)
            self.feed.start()

            if config.PAPER_TRADING:
                self.broker = PaperBroker(self.feed, IST)
            else:
                self.broker = LiveBroker(self.client, self.feed, IST)

        self.engine = self._load_state() or ORBEngine()
        self._banner()

    def _banner(self):
        mode = "PAPER (no real money)" if config.PAPER_TRADING else f"LIVE ({self.broker_name} real orders)"
        log.info("=" * 70)
        log.info("ORB strategy | Broker: %s | %s | mode: %s", self.broker_name, self.day, mode)
        log.info("signals on %s, trading %s", self.signal_inst, self.trade_inst)
        if config.INSTRUMENT.upper() == "FUTURES":
            log.info("size: %d lot(s) x %d = %d, expiry %s", config.LOTS, self.trade_inst.lot_size,
                     self.quantity_hint(), self.trade_inst.expiry)
        else:
            log.info("size: capital %d x leverage %d, quantity computed at entry",
                     config.STOCK_CAPITAL, config.STOCK_LEVERAGE)
        log.info("range %s-%s, entries until %s, square off %s, RR 1:%s, max trades %d",
                 config.RANGE_START, config.RANGE_END, config.ENTRY_WINDOW_END,
                 config.SQUARE_OFF_TIME, config.RISK_REWARD, config.MAX_TRADES_PER_DAY)
        if self.engine.candles:
            log.info("restored state: %d candles, range %s-%s, position %s, trades done %d",
                     len(self.engine.candles), self.engine.range_low, self.engine.range_high,
                     self.engine.position.side if self.engine.position else "none",
                     self.engine.trades_done)
        log.info("=" * 70)

    def quantity_hint(self) -> int:
        return config.LOTS * self.trade_inst.lot_size

    def quantity(self) -> int:
        if config.INSTRUMENT.upper() == "FUTURES":
            return self.quantity_hint()
        price = self.feed.ltp(self.trade_inst.token)
        if price is None and self.engine.candles:
            price = self.engine.candles[-1].close
        if not price:
            raise RuntimeError("no price available to size the stock position")
        qty = math.floor(config.STOCK_CAPITAL * config.STOCK_LEVERAGE / price)
        if qty < 1:
            raise RuntimeError(f"capital too small for one share at {price:.2f}")
        return qty

    # -- state --
    def _load_state(self):
        if not self.state_path.exists():
            return None
        try:
            return ORBEngine.from_dict(json.loads(self.state_path.read_text()))
        except Exception as exc:
            log.warning("could not restore state (%s); starting fresh", exc)
            return None

    def _save_state(self):
        self.state_path.parent.mkdir(exist_ok=True)
        self.state_path.write_text(json.dumps(self.engine.to_dict(), indent=1))

    # -- candles --
    def process_candles(self, expected_start=None):
        """Fetch today's candles, feed any new completed ones to the engine, act on signals."""
        t = now()
        candles = None
        for attempt in range(8):
            candles = fetch_candles(self.client, self.signal_inst, self.day)
            if candles is not None:
                have = {c.start for c in candles}
                if expected_start is None or expected_start in have:
                    break
                log.info("candle %s not yet available (attempt %d)", expected_start.strftime("%H:%M"), attempt + 1)
            time.sleep(2)

        if candles is None:
            candles = []
            log.error("historical API unavailable; using tick-built candle if present")
        if expected_start is not None and expected_start not in {c.start for c in candles}:
            built = self.builder.get(expected_start)
            if built is not None:
                log.warning("using tick-built candle for %s", built.label)
                candles.append(built)

        if not candles and not self.engine.candles and t.time() >= HOLIDAY_CHECK:
            raise Holiday()

        last = self.engine.last_candle_start
        fresh = sorted((c for c in candles
                        if (last is None or c.start > last) and c.close_time <= t),
                       key=lambda c: c.start)
        for c in fresh:
            sig = self.engine.on_candle(c)
            log.info("candle %s  O %.2f H %.2f L %.2f C %.2f%s", c.label, c.open, c.high, c.low, c.close,
                     self._range_note(c))
            if sig is not None:
                too_old = (t - c.close_time).total_seconds() > config.CANDLE_MINUTES * 60 + 30
                self.execute(sig, stale=too_old)
        if fresh:
            self._save_state()

    def _range_note(self, c) -> str:
        e = self.engine
        if e.range_done and c.close_time.time() == e.range_end:
            return f"  -> opening range set: high {e.range_high:.2f} low {e.range_low:.2f}"
        return ""

    # -- execution --
    def execute(self, sig: Signal, stale: bool = False):
        e = self.engine
        tag = self.broker.name
        if sig.kind == "ENTER":
            if stale:
                log.warning("MISSED %s breakout at %s (algo was not running); not entering late",
                            sig.side, sig.time.strftime("%H:%M"))
                return
            log.info("SIGNAL %s breakout: close %.2f beyond range, SL %.2f, target %.2f",
                     sig.side, sig.spot, sig.sl, sig.target)
            try:
                qty = self.quantity()
                side = "BUY" if sig.side == "LONG" else "SELL"
                fill = self.broker.market_order(self.trade_inst, side, qty)
            except Exception as exc:
                log.error("[%s] entry failed: %s", tag, exc)
                return
            e.open_position(sig, qty, fill.price)
            log.info("[%s] %s ENTRY %s x%d @ %.2f | spot %.2f | SL %.2f | target %.2f | order %s",
                     tag, sig.side, self.trade_inst.symbol, qty, fill.price, sig.spot, sig.sl,
                     sig.target, fill.order_id)
        else:
            p = e.position
            if p is None:
                return
            side = "SELL" if p.side == "LONG" else "BUY"
            try:
                fill = self.broker.market_order(self.trade_inst, side, p.qty)
            except Exception as exc:
                log.error("[%s] exit failed (%s); will retry: %s", tag, sig.reason, exc)
                self.pending_exit = sig
                return
            self.pending_exit = None
            closed = e.close_position(sig, fill.price)
            log.info("[%s] %s EXIT (%s) %s x%d @ %.2f | spot %.2f | points %+.2f | P&L %+.2f | order %s",
                     tag, closed.side, closed.exit_reason, self.trade_inst.symbol, closed.qty,
                     fill.price, sig.spot, closed.points, closed.pnl, fill.order_id)
            append_trade(closed, tag, self.trade_inst.symbol)
            day_pnl = sum(x.pnl for x in e.closed)
            log.info("day so far: %d trade(s), P&L %+.2f", e.trades_done, day_pnl)
        self._save_state()

    # -- main loop --
    def run(self):
        self.prepare()
        try:
            self.process_candles()                      # catch up on anything already closed
            next_fetch = next_fetch_time(now())
            last_retry = 0.0
            while True:
                t = now()
                if t.time() >= SESSION_END:
                    break
                if self.engine.finished(t.time()):
                    log.info("nothing more to do today")
                    break

                if t >= next_fetch:
                    expected = (next_fetch - dt.timedelta(seconds=config.CANDLE_FETCH_DELAY)
                                - dt.timedelta(minutes=config.CANDLE_MINUTES))
                    if MARKET_OPEN <= expected.time() < MARKET_CLOSE:
                        self.process_candles(expected_start=expected)
                    next_fetch = next_fetch_time(now())

                if self.engine.position is not None:
                    if self.pending_exit is not None:
                        if time.time() - last_retry > 5:
                            last_retry = time.time()
                            self.execute(self.pending_exit)
                    else:
                        ltp = self.feed.ltp(self.signal_inst.token)
                        if ltp is not None:
                            sig = self.engine.on_tick(ltp, t)
                        elif t.time() >= self.square_off and self.engine.candles:
                            sig = self.engine.square_off_signal(t, self.engine.candles[-1].close)
                        else:
                            sig = None
                        if sig is not None:
                            self.execute(sig)

                if MARKET_OPEN <= t.time() < MARKET_CLOSE and self.feed.stale(90):
                    self.feed.restart()
                time.sleep(0.25)
        finally:
            if self.feed is not None:
                self.feed.stop()
            if self.engine is not None:
                self._save_state()
            if self.engine is not None and self.engine.position is not None:
                log.critical("session ended with an OPEN position; check the broker terminal")


def run_day(day: dt.date):
    attempt = 0
    while now().time() < SESSION_END:
        try:
            Session(day).run()
            return
        except Holiday:
            log.info("no market data by %s; treating %s as a holiday", HOLIDAY_CHECK, day)
            return
        except KeyboardInterrupt:
            raise
        except Exception:
            attempt += 1
            wait = min(300, 30 * attempt)
            log.exception("session crashed (attempt %d); restarting in %ds", attempt, wait)
            time.sleep(wait)


def main():
    setup_logging()
    broker = getattr(config, "BROKER", "FYERS").upper()
    log.info("ORB runner started (Broker: %s). Ctrl+C to stop.", broker)
    while True:
        t = now()
        if is_trading_day(t.date()) and t.time() < SESSION_END:
            start = dt.datetime.combine(t.date(), SESSION_START, IST)
            if t < start:
                log.info("waiting for session start at %s", start.strftime("%H:%M"))
                sleep_until(start)
            run_day(t.date())
        nxt = next_session_start(now())
        log.info("sleeping until next session: %s", nxt.strftime("%a %Y-%m-%d %H:%M"))
        sleep_until(nxt)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("stopped by user")
