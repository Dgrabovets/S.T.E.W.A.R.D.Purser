"""Публичные данные Bybit без ключей и без отправки ордеров"""

import json
import time
from decimal import Decimal
from http.client import HTTPException
from urllib.parse import urlencode
from urllib.request import urlopen

from purser import settings
from purser.sizing import Instrument
from purser.timeunits import MS_PER_MINUTE

# Наибольший размер страницы, который отдаёт биржа: свечей, ставок финансирования и инструментов
KLINE_LIMIT = 1000
FUNDING_LIMIT = 200
INSTRUMENTS_LIMIT = 1000

# Код ошибки Bybit «неверные параметры» (например, нет такого символа): повтор запроса его не исправит
PARAMS_ERROR_CODE = 10001


def _get(path: str, **params: str) -> dict:
    """Один публичный GET-запрос к Bybit с повтором при временной ошибке

    Args:
        path: Путь метода API, например `/v5/market/kline`
        params: Параметры запроса, значения строками

    Returns:
        Поле `result` ответа биржи

    Raises:
        RuntimeError: Биржа вернула ошибку на всех попытках, а при неверных параметрах сразу, без повторов
        OSError: Сеть недоступна или таймаут на всех попытках
        ValueError: Ответ не разобрать как JSON на всех попытках
        HTTPException: Ответ оборван на всех попытках
    """
    attempts = settings.attempts()

    # биржа изредка отвечает временной ошибкой: повторяем, на последней попытке падаем
    for attempt in range(attempts):
        try:
            with urlopen(f"{settings.base_url()}{path}?{urlencode(params)}", timeout=settings.timeout_seconds()) as response:
                payload = json.load(response)

            if payload["retCode"] == 0:
                return payload["result"]

            error = RuntimeError(f"Bybit {path}: {payload['retCode']} {payload['retMsg']}")
            if payload["retCode"] == PARAMS_ERROR_CODE:
                raise error
        except (OSError, ValueError, HTTPException) as exception:  # сеть и таймаут, битый JSON, оборванный ответ
            error = exception

        if attempt < attempts - 1:
            time.sleep(settings.retry_pause_seconds())

    raise error


def linear_perpetuals() -> list[Instrument]:
    """Все торгуемые бессрочные USDT-контракты с ценой, оборотом, спредом и финансированием

    Returns:
        Контракты без предлистинга и без тех, у кого нет цены
    """
    tickers = {row["symbol"]: row for row in _get("/v5/market/tickers", category="linear")["list"]}
    instruments, cursor = [], ""

    while True:
        page = _get("/v5/market/instruments-info", category="linear", limit=str(INSTRUMENTS_LIMIT), **({"cursor": cursor} if cursor else {}))

        for row in page["list"]:
            ticker = tickers.get(row["symbol"])
            if ticker is None or Decimal(ticker["lastPrice"] or "0") == 0 or row["status"] != "Trading" or row["isPreListing"]:
                continue

            if row["contractType"] != "LinearPerpetual" or row["quoteCoin"] != "USDT":
                continue

            lot = row["lotSizeFilter"]
            price = Decimal(ticker["lastPrice"])
            bid, ask = Decimal(ticker.get("bid1Price") or "0"), Decimal(ticker.get("ask1Price") or "0")
            instruments.append(Instrument(symbol=row["symbol"],
                                          price=price,
                                          min_qty=Decimal(lot["minOrderQty"]),
                                          qty_step=Decimal(lot["qtyStep"]),
                                          min_notional=Decimal(lot.get("minNotionalValue") or "0"),
                                          turnover_24h=Decimal(ticker["turnover24h"] or "0"),
                                          # спред к середине стакана; пустой стакан считаем непригодным: спред 100%
                                          spread=(ask - bid) * 2 / (ask + bid) if bid > 0 and ask > 0 else Decimal(1),
                                          funding_rate=Decimal(ticker.get("fundingRate") or "0"),
                                          launch_ms=int(row["launchTime"])))

        cursor = page.get("nextPageCursor") or ""
        if not cursor:
            return instruments


def klines(symbol: str, interval: str, start: int, end: int) -> list[list[str]]:
    """Закрытые свечи с временем открытия от `start` до `end`

    Args:
        symbol: Контракт, например `XRPUSDT`
        interval: Размер свечи в минутах строкой, например `60`
        start: Начало периода, мс
        end: Конец периода, мс

    Returns:
        Строки по возрастанию времени: время открытия в мс, open, high, low, close, объём, оборот. Числа остаются строками биржи
    """
    step = int(interval) * MS_PER_MINUTE
    rows = []

    # окно ровно на KLINE_LIMIT свечей: так ответ не зависит от того, какой край биржа обрежет
    for window in range(start, end, KLINE_LIMIT * step):
        page = _get("/v5/market/kline", category="linear", symbol=symbol, interval=interval, limit=str(KLINE_LIMIT),
                    start=str(window), end=str(min(window + KLINE_LIMIT * step, end) - 1))
        rows += reversed(page["list"])

    # текущая свеча ещё не закрыта, её цены изменятся
    return [row for row in rows if int(row[0]) + step <= end]


def funding(symbol: str, start: int, end: int) -> list[list[str]]:
    """Ставки финансирования за период

    Args:
        symbol: Контракт, например `XRPUSDT`
        start: Начало периода, мс
        end: Конец периода, мс

    Returns:
        Строки по возрастанию времени: время в мс, ставка строкой биржи
    """
    rows = []

    # период у монет разный (1, 4 или 8 часов), поэтому идём страницами назад от `end`
    while True:
        page = _get("/v5/market/funding/history", category="linear", symbol=symbol, limit=str(FUNDING_LIMIT),
                    startTime=str(start), endTime=str(end))["list"]
        rows += [[row["fundingRateTimestamp"], row["fundingRate"]] for row in page]

        if len(page) < FUNDING_LIMIT:
            return rows[::-1]

        next_end = int(page[-1]["fundingRateTimestamp"]) - 1

        # биржа не сдвинула страницу назад: иначе цикл не кончится
        if next_end >= end:
            return rows[::-1]

        end = next_end
