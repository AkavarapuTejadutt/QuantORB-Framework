"""Candles from the historical API, live LTP from WebSocket, and tick-built candle fallback."""

import datetime as dt
import logging
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from fyers_apiv3.FyersWebsocket import data_ws

try:
    from SmartApi.smartWebSocketV2 import SmartWebSocketV2
except ImportError:
    SmartWebSocketV2 = None

import config
from instruments import Instrument

log = logging.getLogger("market_data")
IST = ZoneInfo("Asia/Kolkata")

INTERVAL_NAMES = {1: "ONE_MINUTE", 3: "THREE_MINUTE", 5: "FIVE_MINUTE", 10: "TEN_MINUTE",
                  15: "FIFTEEN_MINUTE", 30: "THIRTY_MINUTE", 60: "ONE_HOUR"}


@dataclass
class Candle:
    start: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def close_time(self) -> dt.datetime:
        return self.start + dt.timedelta(minutes=config.CANDLE_MINUTES)

    @property
    def label(self) -> str:
        return self.start.strftime("%H:%M")

    def to_list(self):
        return [self.start.isoformat(), self.open, self.high, self.low, self.close, self.volume]

    @classmethod
    def from_list(cls, row):
        return cls(dt.datetime.fromisoformat(row[0]), float(row[1]), float(row[2]),
                   float(row[3]), float(row[4]), float(row[5]) if len(row) > 5 else 0.0)


# ---- FYERS Historical Candle Fetcher ----

def fetch_candles_fyers(fyers_client, inst: Instrument, day: dt.date, retries: int = 3) -> Optional[List[Candle]]:
    """
    Fetch a day's candles from FYERS historical API.
    Returns [] when the exchange has no data (holiday / before open) and None on API failure.
    """
    data = {
        "symbol": inst.token,
        "resolution": str(config.CANDLE_MINUTES),
        "date_format": "1",
        "range_from": day.strftime("%Y-%m-%d"),
        "range_to": day.strftime("%Y-%m-%d"),
        "cont_flag": "1",
    }
    for attempt in range(1, retries + 1):
        try:
            resp = fyers_client.history(data=data)
            if resp and resp.get("s") == "ok":
                candles_raw = resp.get("candles") or []
                result = []
                for row in candles_raw:
                    # row format: [timestamp, open, high, low, close, volume]
                    ts_epoch = row[0]
                    dt_start = dt.datetime.fromtimestamp(ts_epoch, tz=IST)
                    result.append(Candle(
                        start=dt_start,
                        open=float(row[1]),
                        high=float(row[2]),
                        low=float(row[3]),
                        close=float(row[4]),
                        volume=float(row[5]) if len(row) > 5 else 0.0,
                    ))
                return result
            if resp and resp.get("s") == "no_data":
                return []
            log.warning("FYERS candle fetch attempt %d rejected: %s", attempt, (resp or {}).get("message"))
        except Exception as exc:
            log.warning("FYERS candle fetch attempt %d raised: %s", attempt, exc)
        time.sleep(1)
    return None


# ---- Angel One Historical Candle Fetcher ----

def fetch_candles_angel(api, inst: Instrument, day: dt.date, retries: int = 3) -> Optional[List[Candle]]:
    """A day's candles from Angel One historical API."""
    params = {
        "exchange": inst.exchange,
        "symboltoken": inst.token,
        "interval": INTERVAL_NAMES[config.CANDLE_MINUTES],
        "fromdate": f"{day:%Y-%m-%d} 09:00",
        "todate": f"{day:%Y-%m-%d} 15:35",
    }
    for attempt in range(1, retries + 1):
        level = logging.WARNING if attempt == retries else logging.DEBUG
        try:
            resp = api.getCandleData(params)
        except Exception as exc:
            log.log(level, "Angel candle fetch attempt %d raised: %s", attempt, str(exc)[:120])
            time.sleep(1)
            continue
        if resp and resp.get("status"):
            return [Candle.from_list(r) for r in (resp.get("data") or [])]
        log.log(level, "Angel candle fetch attempt %d rejected: %s", attempt, (resp or {}).get("message"))
        time.sleep(1)
    return None


def fetch_candles(client, inst: Instrument, day: dt.date, retries: int = 3) -> Optional[List[Candle]]:
    """Generic candle fetcher delegating to configured broker."""
    broker = getattr(config, "BROKER", "FYERS").upper()
    if broker == "FYERS":
        return fetch_candles_fyers(client, inst, day, retries)
    return fetch_candles_angel(client, inst, day, retries)


# ---- Tick-based Candle Builder ----

class CandleBuilder:
    """Builds candles from ticks. Used only when historical API fails for a candle."""

    def __init__(self):
        self._lock = threading.Lock()
        self._done: Dict[dt.datetime, Candle] = {}
        self._cur: Optional[Candle] = None

    @staticmethod
    def _bucket(ts: dt.datetime) -> dt.datetime:
        m = ts.minute - ts.minute % config.CANDLE_MINUTES
        return ts.replace(minute=m, second=0, microsecond=0)

    def on_tick(self, price: float, ts: dt.datetime):
        b = self._bucket(ts)
        with self._lock:
            if self._cur is None or self._cur.start != b:
                if self._cur is not None:
                    self._done[self._cur.start] = self._cur
                self._cur = Candle(b, price, price, price, price)
            else:
                c = self._cur
                c.high = max(c.high, price)
                c.low = min(c.low, price)
                c.close = price

    def get(self, start: dt.datetime) -> Optional[Candle]:
        with self._lock:
            return self._done.get(start)


# ---- FYERS WebSocket Tick Feed ----

class FyersTickFeed:
    """Thread-safe last-traded-price store fed by FYERS Data Socket V3."""

    def __init__(self, client_id: str, access_token: str, instruments: List[Instrument],
                 builder_token: Optional[str] = None, builder: Optional[CandleBuilder] = None, tz=None):
        self._client_id = client_id
        self._token = access_token
        self._instruments = instruments
        self._builder_token = builder_token
        self._builder = builder
        self._tz = tz or IST
        self._ltp: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._ws = None
        self._thread: Optional[threading.Thread] = None
        self.connected = False
        self.started_at: float = 0.0
        self.last_tick_at: float = 0.0

    def _on_message(self, message):
        # Extract ltp from FYERS websocket message payload
        if isinstance(message, dict):
            symbol = message.get("symbol")
            ltp = message.get("ltp") or message.get("lp")
            if ltp is not None and symbol:
                val = float(ltp)
                with self._lock:
                    self._ltp[symbol] = val
                self.last_tick_at = time.time()
                if self._builder is not None and symbol == self._builder_token:
                    self._builder.on_tick(val, dt.datetime.now(self._tz))

    def _on_connect(self):
        self.connected = True
        symbols = [inst.token for inst in self._instruments]
        log.info("FYERS tick feed connected, subscribing to %s", symbols)
        if self._ws:
            self._ws.subscribe(symbols=symbols, data_type="SymbolUpdate")

    def _on_close(self, *args):
        self.connected = False
        log.warning("FYERS tick feed closed")

    def _on_error(self, message):
        log.warning("FYERS tick feed error: %s", message)

    def start(self):
        ws_token = f"{self._client_id}:{self._token}"
        self._ws = data_ws.FyersDataSocket(
            access_token=ws_token,
            write_to_file=False,
            log_path="",
            on_connect=self._on_connect,
            on_close=self._on_close,
            on_error=self._on_error,
            on_message=self._on_message,
        )
        self.started_at = time.time()
        self._thread = threading.Thread(target=self._ws.connect, name="fyers-tick-feed", daemon=True)
        self._thread.start()

    def stop(self):
        self.connected = False
        try:
            if self._ws is not None:
                self._ws.close_connection()
        except Exception:
            pass

    def restart(self):
        log.warning("restarting FYERS tick feed")
        self.stop()
        time.sleep(2)
        self.start()

    def ltp(self, token: str) -> Optional[float]:
        with self._lock:
            return self._ltp.get(token)

    def stale(self, seconds: float) -> bool:
        last = self.last_tick_at or self.started_at
        return (time.time() - last) > seconds


# ---- Angel One WebSocket Tick Feed ----

class TickFeed:
    """Thread-safe last-traded-price store fed by SmartAPI WebSocket V2."""

    def __init__(self, api_key: str, client_code: str, jwt_token: str, feed_token: str,
                 instruments: List[Instrument], builder_token: Optional[str] = None,
                 builder: Optional[CandleBuilder] = None, tz=None):
        if SmartWebSocketV2 is None:
            raise RuntimeError("SmartApi SDK is not installed")
        self._api_key = api_key
        self._client_code = client_code
        self._jwt = jwt_token if jwt_token.startswith("Bearer ") else "Bearer " + jwt_token
        self._feed_token = feed_token
        self._instruments = instruments
        self._builder_token = builder_token
        self._builder = builder
        self._tz = tz or IST
        self._ltp: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._ws = None
        self._thread: Optional[threading.Thread] = None
        self.connected = False
        self.started_at: float = 0.0
        self.last_tick_at: float = 0.0

    def _on_open(self, wsapp):
        groups: Dict[int, List[str]] = {}
        for inst in self._instruments:
            groups.setdefault(inst.ws_exchange, []).append(inst.token)
        token_list = [{"exchangeType": ex, "tokens": toks} for ex, toks in groups.items()]
        self._ws.subscribe("orb", SmartWebSocketV2.LTP_MODE, token_list)
        self.connected = True
        log.info("tick feed connected, subscribed %d instrument(s)", len(self._instruments))

    def _on_data(self, wsapp, msg):
        price = msg.get("last_traded_price")
        token = str(msg.get("token", ""))
        if price is None or not token:
            return
        price = price / 100.0
        with self._lock:
            self._ltp[token] = price
        self.last_tick_at = time.time()
        if self._builder is not None and token == self._builder_token:
            self._builder.on_tick(price, dt.datetime.now(self._tz))

    def _on_error(self, *args):
        log.warning("tick feed error: %s", " ".join(str(a) for a in args[-2:]))

    def _on_close(self, *args):
        self.connected = False
        log.warning("tick feed closed")

    def start(self):
        ws = SmartWebSocketV2(self._jwt, self._api_key, self._client_code, self._feed_token,
                              max_retry_attempt=5, retry_delay=5)
        ws.on_open = self._on_open
        ws.on_data = self._on_data
        ws.on_error = self._on_error
        ws.on_close = self._on_close
        ws._on_close = lambda wsapp, *args: self._on_close(wsapp)
        self._ws = ws
        self.started_at = time.time()
        self._thread = threading.Thread(target=ws.connect, name="tick-feed", daemon=True)
        self._thread.start()

    def stop(self):
        self.connected = False
        try:
            if self._ws is not None:
                self._ws.MAX_RETRY_ATTEMPT = 0
                self._ws.close_connection()
        except Exception:
            pass

    def restart(self):
        log.warning("restarting tick feed")
        self.stop()
        time.sleep(2)
        self.start()

    def ltp(self, token: str) -> Optional[float]:
        with self._lock:
            return self._ltp.get(token)

    def stale(self, seconds: float) -> bool:
        last = self.last_tick_at or self.started_at
        return (time.time() - last) > seconds
