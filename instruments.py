"""Instrument master download and symbol/token lookup for FYERS API and Angel One."""

import datetime as dt
import json
import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import config

log = logging.getLogger("instruments")

MASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
CACHE = Path(__file__).with_name("scrip_master.json")

# Exchange codes used by Angel One WebSocket feed.
WS_EXCHANGE = {"NSE": 1, "NFO": 2, "BSE": 3, "BFO": 4, "MCX": 5}


@dataclass
class Instrument:
    exchange: str        # "NSE" or "NFO"
    token: str          # token (Angel) or full symbol string (FYERS)
    symbol: str          # trading symbol, e.g. NSE:NIFTY50-INDEX or NIFTY27OCT26FUT
    name: str
    lot_size: int = 1
    expiry: Optional[dt.date] = None

    @property
    def ws_exchange(self) -> int:
        return WS_EXCHANGE.get(self.exchange, 1)

    def __str__(self):
        return f"{self.symbol} ({self.exchange}:{self.token})"


def load_master(force: bool = False) -> list:
    """Return the scrip master (for Angel One token lookups), downloading it once per day."""
    if not force and CACHE.exists():
        cached_day = dt.date.fromtimestamp(CACHE.stat().st_mtime)
        if cached_day == dt.date.today():
            try:
                return json.loads(CACHE.read_text())
            except Exception:
                pass
    log.info("downloading instrument master")
    try:
        raw = urllib.request.urlopen(MASTER_URL, timeout=120).read()
        CACHE.write_bytes(raw)
        return json.loads(raw)
    except Exception as exc:
        log.warning("failed to download scrip master: %s", exc)
        if CACHE.exists():
            return json.loads(CACHE.read_text())
        return []


def _row_to_instrument(row: dict) -> Instrument:
    expiry = None
    if row.get("expiry"):
        try:
            expiry = dt.datetime.strptime(row["expiry"], "%d%b%Y").date()
        except Exception:
            pass
    return Instrument(
        exchange=row["exch_seg"],
        token=str(row["token"]),
        symbol=row["symbol"],
        name=row["name"],
        lot_size=int(row.get("lotsize") or 1),
        expiry=expiry,
    )


# ---- FYERS Symbol Resolvers ----

def fyers_nifty_index() -> Instrument:
    return Instrument(
        exchange="NSE",
        token="NSE:NIFTY50-INDEX",
        symbol="NSE:NIFTY50-INDEX",
        name="Nifty 50",
        lot_size=1,
    )


def fyers_nifty_future(today: dt.date) -> Instrument:
    """
    Build the FYERS symbol for nearest NIFTY futures contract.
    Nifty monthly futures expire on the last Thursday of the month.
    Format: NSE:NIFTYYYMMMxFUT (e.g. NSE:NIFTY26OCTFUT)
    """
    # Calculate target month based on rollover cutoff
    cutoff = today + dt.timedelta(days=config.FUT_ROLLOVER_DAYS)
    
    # Simple month/year calculation for futures symbol
    yr_short = cutoff.strftime("%y")
    month_short = cutoff.strftime("%b").upper()
    sym = f"NSE:NIFTY{yr_short}{month_short}FUT"
    
    return Instrument(
        exchange="NSE",
        token=sym,
        symbol=sym,
        name="NIFTY FUT",
        lot_size=65,  # Standard Nifty lot size
        expiry=cutoff,
    )


def fyers_stock(symbol: str) -> Instrument:
    clean_sym = symbol.upper().removesuffix("-EQ")
    full_sym = f"NSE:{clean_sym}-EQ"
    return Instrument(
        exchange="NSE",
        token=full_sym,
        symbol=full_sym,
        name=clean_sym,
        lot_size=1,
    )


# ---- Angel One Symbol Resolvers ----

def nifty_index(master: list) -> Instrument:
    for row in master:
        if row["exch_seg"] == "NSE" and row["symbol"] == "Nifty 50":
            return _row_to_instrument(row)
    raise LookupError("Nifty 50 index not found in instrument master")


def nifty_future(master: list, today: dt.date) -> Instrument:
    """Nearest NIFTY future whose expiry is at least FUT_ROLLOVER_DAYS away."""
    rows = [r for r in master
            if r["exch_seg"] == "NFO" and r["name"] == "NIFTY" and r["instrumenttype"] == "FUTIDX"]
    futs = sorted((_row_to_instrument(r) for r in rows), key=lambda i: i.expiry)
    cutoff = today + dt.timedelta(days=config.FUT_ROLLOVER_DAYS)
    for f in futs:
        if f.expiry and f.expiry >= cutoff:
            return f
    if futs:
        return futs[0]
    raise LookupError("no NIFTY future found beyond the rollover cutoff")


def stock(master: list, symbol: str) -> Instrument:
    want = f"{symbol.upper()}-EQ"
    for row in master:
        if row["exch_seg"] == "NSE" and row["symbol"] == want:
            return _row_to_instrument(row)
    raise LookupError(f"stock {want} not found in instrument master")


def resolve(master: Optional[list], today: dt.date):
    """
    Return (signal_instrument, trade_instrument) for the configured mode and broker.
    Signals are computed on the first; orders are placed on the second.
    """
    broker = getattr(config, "BROKER", "FYERS").upper()
    mode = config.INSTRUMENT.upper()

    if broker == "FYERS":
        if mode == "FUTURES":
            return fyers_nifty_index(), fyers_nifty_future(today)
        if mode == "STOCK":
            s = fyers_stock(config.STOCK_SYMBOL)
            return s, s
        raise ValueError(f"config.INSTRUMENT must be FUTURES or STOCK, got {config.INSTRUMENT!r}")
    else:
        if master is None:
            master = load_master()
        if mode == "FUTURES":
            return nifty_index(master), nifty_future(master, today)
        if mode == "STOCK":
            s = stock(master, config.STOCK_SYMBOL)
            return s, s
        raise ValueError(f"config.INSTRUMENT must be FUTURES or STOCK, got {config.INSTRUMENT!r}")
