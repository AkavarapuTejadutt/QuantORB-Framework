"""
Opening-range-breakout engine. Pure logic, no broker or network calls.

Feed it completed candles with on_candle() and live prices with on_tick().
Each call returns a Signal when the strategy wants to enter or exit, and the
caller confirms the fill with open_position() / close_position().

Rules implemented:
  * Opening range = high/low of candles from RANGE_START until the candle closing at RANGE_END.
  * Entry: first candle (closing after RANGE_END and no later than ENTRY_WINDOW_END) that
    closes above the range high -> LONG, or below the range low -> SHORT. Entry at that close.
  * Stop loss: low of the candle before the breakout candle (long), or its high (short).
  * Target: entry +/- RISK_REWARD * stop distance. Checked on every tick.
  * Stop loss is only honoured when a candle CLOSES beyond it; a wick through it is ignored.
  * Any open position is squared off at SQUARE_OFF_TIME.
  * At most MAX_TRADES_PER_DAY completed trades per day.
"""

import datetime as dt
from dataclasses import dataclass, asdict
from typing import List, Optional

import config
from market_data import Candle


def parse_time(s: str) -> dt.time:
    h, m = s.split(":")
    return dt.time(int(h), int(m))


@dataclass
class Signal:
    kind: str          # "ENTER" or "EXIT"
    side: str          # "LONG" or "SHORT"
    reason: str        # "BREAKOUT", "STOP_LOSS", "TARGET", "SQUARE_OFF"
    time: dt.datetime
    spot: float        # signal-instrument price at the time of the signal
    sl: float = 0.0
    target: float = 0.0


@dataclass
class Position:
    side: str
    qty: int
    signal_time: str
    entry_spot: float
    entry_fill: float
    sl: float
    target: float
    exit_time: str = ""
    exit_reason: str = ""
    exit_spot: float = 0.0
    exit_fill: float = 0.0

    @property
    def direction(self) -> int:
        return 1 if self.side == "LONG" else -1

    @property
    def points(self) -> float:
        return (self.exit_fill - self.entry_fill) * self.direction

    @property
    def pnl(self) -> float:
        return self.points * self.qty


class ORBEngine:
    def __init__(self, replay: bool = False):
        # replay=True: no ticks available, so targets are checked against candle high/low.
        self.replay = replay
        self.range_start = parse_time(config.RANGE_START)
        self.range_end = parse_time(config.RANGE_END)
        self.entry_end = parse_time(config.ENTRY_WINDOW_END)
        self.square_off = parse_time(config.SQUARE_OFF_TIME)

        self.range_high: Optional[float] = None
        self.range_low: Optional[float] = None
        self.range_done = False
        self.candles: List[Candle] = []
        self.position: Optional[Position] = None
        self.closed: List[Position] = []
        self.last_candle_start: Optional[dt.datetime] = None

    # ------------------------------------------------------------------ state
    @property
    def trades_done(self) -> int:
        return len(self.closed)

    @property
    def entries_allowed(self) -> bool:
        return (self.range_done and self.position is None
                and self.trades_done < config.MAX_TRADES_PER_DAY)

    def finished(self, now_time: dt.time) -> bool:
        """True when nothing more can happen today."""
        if self.position is not None:
            return False
        if self.trades_done >= config.MAX_TRADES_PER_DAY:
            return True
        # The last eligible candle closes at entry_end and is only fetched a few
        # seconds later, so wait until it has been processed (or is long overdue).
        if self.last_candle_start is not None:
            last_close = (self.last_candle_start + dt.timedelta(minutes=config.CANDLE_MINUTES)).time()
            if last_close >= self.entry_end:
                return True
        overdue = (dt.datetime.combine(dt.date.today(), self.entry_end)
                   + dt.timedelta(minutes=2 * config.CANDLE_MINUTES)).time()
        return now_time >= overdue

    # --------------------------------------------------------------- candles
    def on_candle(self, c: Candle) -> Optional[Signal]:
        if self.last_candle_start is not None and c.start <= self.last_candle_start:
            return None                      # already seen
        self.candles.append(c)
        self.last_candle_start = c.start
        close_t = c.close_time.time()

        # Opening range.
        if c.start.time() >= self.range_start and close_t <= self.range_end:
            self.range_high = c.high if self.range_high is None else max(self.range_high, c.high)
            self.range_low = c.low if self.range_low is None else min(self.range_low, c.low)
            if close_t == self.range_end:
                self.range_done = True
            return None
        if not self.range_done and close_t > self.range_end and self.range_high is not None:
            self.range_done = True           # last range candle was missing; use what we have

        # Manage an open position.
        if self.position is not None:
            p = self.position
            if close_t >= self.square_off:
                return Signal("EXIT", p.side, "SQUARE_OFF", c.close_time, c.close)
            if p.side == "LONG":
                if c.close < p.sl:
                    return Signal("EXIT", p.side, "STOP_LOSS", c.close_time, c.close)
                if (self.replay and c.high >= p.target) or c.close >= p.target:
                    spot = p.target if self.replay else c.close
                    return Signal("EXIT", p.side, "TARGET", c.close_time, spot)
            else:
                if c.close > p.sl:
                    return Signal("EXIT", p.side, "STOP_LOSS", c.close_time, c.close)
                if (self.replay and c.low <= p.target) or c.close <= p.target:
                    spot = p.target if self.replay else c.close
                    return Signal("EXIT", p.side, "TARGET", c.close_time, spot)
            return None

        # Look for a breakout entry.
        if not self.entries_allowed or len(self.candles) < 2:
            return None
        if not (self.range_end < close_t <= self.entry_end):
            return None
        prev = self.candles[-2]
        rr = config.RISK_REWARD
        if c.close > self.range_high:
            sl = prev.low
            risk = c.close - sl
            if risk > 0:
                return Signal("ENTER", "LONG", "BREAKOUT", c.close_time, c.close, sl, c.close + rr * risk)
        elif c.close < self.range_low:
            sl = prev.high
            risk = sl - c.close
            if risk > 0:
                return Signal("ENTER", "SHORT", "BREAKOUT", c.close_time, c.close, sl, c.close - rr * risk)
        return None

    # ----------------------------------------------------------------- ticks
    def on_tick(self, ltp: float, ts: dt.datetime) -> Optional[Signal]:
        p = self.position
        if p is None:
            return None
        if ts.time() >= self.square_off:
            return Signal("EXIT", p.side, "SQUARE_OFF", ts, ltp)
        if p.side == "LONG" and ltp >= p.target:
            return Signal("EXIT", p.side, "TARGET", ts, ltp)
        if p.side == "SHORT" and ltp <= p.target:
            return Signal("EXIT", p.side, "TARGET", ts, ltp)
        return None

    def square_off_signal(self, ts: dt.datetime, spot: float) -> Optional[Signal]:
        if self.position is None:
            return None
        return Signal("EXIT", self.position.side, "SQUARE_OFF", ts, spot)

    # ------------------------------------------------------------ fills
    def open_position(self, sig: Signal, qty: int, fill_price: float):
        self.position = Position(
            side=sig.side, qty=qty, signal_time=sig.time.isoformat(),
            entry_spot=sig.spot, entry_fill=fill_price, sl=sig.sl, target=sig.target,
        )
        return self.position

    def close_position(self, sig: Signal, fill_price: float) -> Position:
        p = self.position
        p.exit_time = sig.time.isoformat()
        p.exit_reason = sig.reason
        p.exit_spot = sig.spot
        p.exit_fill = fill_price
        self.closed.append(p)
        self.position = None
        return p

    # ------------------------------------------------------ persistence
    def to_dict(self) -> dict:
        return {
            "range_high": self.range_high,
            "range_low": self.range_low,
            "range_done": self.range_done,
            "candles": [c.to_list() for c in self.candles],
            "position": asdict(self.position) if self.position else None,
            "closed": [asdict(p) for p in self.closed],
        }

    @classmethod
    def from_dict(cls, d: dict, replay: bool = False) -> "ORBEngine":
        e = cls(replay=replay)
        e.range_high = d.get("range_high")
        e.range_low = d.get("range_low")
        e.range_done = bool(d.get("range_done"))
        e.candles = [Candle.from_list(r) for r in d.get("candles", [])]
        e.last_candle_start = e.candles[-1].start if e.candles else None
        e.position = Position(**d["position"]) if d.get("position") else None
        e.closed = [Position(**p) for p in d.get("closed", [])]
        return e
