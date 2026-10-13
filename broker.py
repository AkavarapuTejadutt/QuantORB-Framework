"""Order execution. PaperBroker simulates fills at live LTP; FyersLiveBroker and LiveBroker send real orders."""

import datetime as dt
import logging
import time
from dataclasses import dataclass

import config
from instruments import Instrument

log = logging.getLogger("broker")


@dataclass
class Fill:
    price: float
    order_id: str
    time: dt.datetime


class PaperBroker:
    name = "PAPER"

    def __init__(self, feed, tz=None):
        self._feed = feed
        self._tz = tz
        self._count = 0

    def market_order(self, inst: Instrument, side: str, qty: int) -> Fill:
        price = self._feed.ltp(inst.token)
        if price is None:
            raise RuntimeError(f"no live price for {inst.symbol}; cannot simulate fill")
        self._count += 1
        return Fill(price, f"PAPER-{self._count}", dt.datetime.now(self._tz))


class FyersLiveBroker:
    name = "LIVE (FYERS)"

    def __init__(self, fyers_client, feed, tz=None):
        self._fyers = fyers_client
        self._feed = feed
        self._tz = tz

    def market_order(self, inst: Instrument, side: str, qty: int) -> Fill:
        side_val = 1 if side.upper() == "BUY" else -1
        product_type = getattr(config, "PRODUCT_TYPE", "INTRADAY").upper()
        if product_type not in ("INTRADAY", "MARGIN", "CNC", "CO", "BO"):
            product_type = "INTRADAY"

        data = {
            "symbol": inst.token,
            "qty": qty,
            "type": 2,                  # 2: Market Order
            "side": side_val,           # 1: BUY, -1: SELL
            "productType": product_type,
            "limitPrice": 0,
            "stopPrice": 0,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
        }

        resp = self._fyers.place_order(data=data)
        if not resp or resp.get("s") != "ok":
            msg = (resp or {}).get("message", "no response from FYERS")
            raise RuntimeError(f"FYERS order rejected: {msg}")

        order_id = str(resp.get("id"))
        log.info("FYERS order %s sent: %s %d x %s", order_id, side, qty, inst.symbol)
        return self._wait_for_fill(order_id, inst)

    def _wait_for_fill(self, order_id: str, inst: Instrument) -> Fill:
        deadline = time.time() + config.ORDER_FILL_TIMEOUT
        last_status = "unknown"
        while time.time() < deadline:
            try:
                book_resp = self._fyers.orderbook()
            except Exception as exc:
                log.warning("FYERS orderbook fetch failed: %s", exc)
                book_resp = None

            orders = (book_resp or {}).get("orderBook", [])
            for row in orders:
                if str(row.get("id")) != order_id:
                    continue
                # FYERS order status: 2 = Filled, 1 = Canceled, 5 = Rejected, 6 = Pending
                st_code = row.get("status")
                if st_code == 2:
                    price = float(row.get("tradedPrice") or 0)
                    if price <= 0:
                        price = self._feed.ltp(inst.token) or 0.0
                    return Fill(price, order_id, dt.datetime.now(self._tz))
                if st_code in (1, 5):
                    raise RuntimeError(f"FYERS order {order_id} failed (status {st_code}): {row.get('message', '')}")
                last_status = f"status_code_{st_code}"

            time.sleep(1)
        raise RuntimeError(f"FYERS order {order_id} not filled within timeout (last status: {last_status})")


class LiveBroker:
    name = "LIVE (ANGEL)"

    def __init__(self, api, feed, tz=None):
        self._api = api
        self._feed = feed
        self._tz = tz

    def market_order(self, inst: Instrument, side: str, qty: int) -> Fill:
        params = {
            "variety": "NORMAL",
            "tradingsymbol": inst.symbol,
            "symboltoken": inst.token,
            "transactiontype": side,           # BUY or SELL
            "exchange": inst.exchange,
            "ordertype": "MARKET",
            "producttype": config.PRODUCT_TYPE,
            "duration": "DAY",
            "price": "0",
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(qty),
        }
        resp = self._api.placeOrderFullResponse(params)
        if not resp or not resp.get("status"):
            msg = (resp or {}).get("message", "no response")
            raise RuntimeError(f"order rejected by broker: {msg}")
        order_id = str(resp["data"]["orderid"])
        log.info("order %s sent: %s %d x %s", order_id, side, qty, inst.symbol)
        return self._wait_for_fill(order_id)

    def _wait_for_fill(self, order_id: str) -> Fill:
        deadline = time.time() + config.ORDER_FILL_TIMEOUT
        last_status = "unknown"
        while time.time() < deadline:
            try:
                book = self._api.orderBook()
            except Exception as exc:
                log.warning("order book fetch failed: %s", exc)
                book = None
            for row in ((book or {}).get("data") or []):
                if str(row.get("orderid")) != order_id:
                    continue
                last_status = str(row.get("status") or row.get("orderstatus") or "").lower()
                if last_status == "complete":
                    price = float(row.get("averageprice") or 0)
                    if price <= 0:
                        price = self._feed.ltp(str(row.get("symboltoken"))) or 0.0
                    return Fill(price, order_id, dt.datetime.now(self._tz))
                if last_status in ("rejected", "cancelled"):
                    raise RuntimeError(f"order {order_id} {last_status}: {row.get('text', '')}")
            time.sleep(1)
        raise RuntimeError(f"order {order_id} not filled within timeout (last status: {last_status})")
