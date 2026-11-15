"""Публичные данные Bybit. Ключей не требует и ордеров не отправляет."""

import json
from decimal import Decimal
from urllib.parse import urlencode
from urllib.request import urlopen

from purser.sizing import Instrument

BASE_URL = "https://api.bybit.com"


def _get(path: str, **params: str) -> dict:
    with urlopen(f"{BASE_URL}{path}?{urlencode(params)}", timeout=20) as response:
        payload = json.load(response)

    if payload["retCode"] != 0:
        raise RuntimeError(f"Bybit {path}: {payload['retMsg']}")

    return payload["result"]


def linear_perpetuals() -> list[Instrument]:
    """Все торгуемые бессрочные USDT-контракты с текущей ценой и оборотом."""
    tickers = {row["symbol"]: row for row in _get("/v5/market/tickers", category="linear")["list"]}
    instruments, cursor = [], ""

    while True:
        page = _get("/v5/market/instruments-info", category="linear", limit="1000", cursor=cursor)

        for row in page["list"]:
            ticker = tickers.get(row["symbol"])
            if ticker is None or not ticker["lastPrice"] or row["status"] != "Trading" or row["isPreListing"]:
                continue

            if row["contractType"] != "LinearPerpetual" or row["quoteCoin"] != "USDT":
                continue

            lot = row["lotSizeFilter"]
            instruments.append(Instrument(symbol=row["symbol"],
                                          price=Decimal(ticker["lastPrice"]),
                                          min_qty=Decimal(lot["minOrderQty"]),
                                          qty_step=Decimal(lot["qtyStep"]),
                                          min_notional=Decimal(lot.get("minNotionalValue") or "0"),
                                          turnover_24h=Decimal(ticker["turnover24h"] or "0")))

        cursor = page.get("nextPageCursor") or ""
        if not cursor:
            return instruments
