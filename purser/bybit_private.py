"""Приватные запросы Bybit с подписью: баланс, позиции, ордера

Работает только с демо-счётом: любой другой адрес отклоняется до сети. Запросы не повторяются сами:
повтор отправки ордера решает вызывающий код по `orderLinkId`.
"""

import hashlib
import hmac
import json
from json import JSONDecodeError
import time
from decimal import Decimal
from http.client import HTTPException
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from purser import settings
from purser.position import Position
from purser.side_enum import SideEnum
from purser.timeunits import MS_PER_SECOND

# Единственный адрес, на который разрешено слать приватные запросы на шаге 2
DEMO_URL = "https://api-demo.bybit.com"

# Сбои связи: сеть и таймаут, битый JSON, оборванный ответ. После них неизвестно, дошёл ли запрос.
# Ошибки настроек и неверные аргументы (ValueError) сюда не входят: они не значат, что ордер мог уйти
TRANSPORT_ERRORS = (OSError, JSONDecodeError, HTTPException)

# Сколько мс биржа допускает между временем в запросе и своим временем
RECV_WINDOW = "5000"

# Бессрочные USDT-контракты в едином торговом аккаунте, режим позиции односторонний
CATEGORY = "linear"
SETTLE_COIN = "USDT"
ACCOUNT_TYPE = "UNIFIED"
ONE_WAY_POSITION_INDEX = 0
MARKET = "Market"

# Режим маржи аккаунта: изолированная
ISOLATED_MARGIN = "ISOLATED_MARGIN"

# Код Bybit «плечо не изменилось»: нужное плечо уже стоит, это не ошибка
LEVERAGE_NOT_MODIFIED_CODE = 110043

# Вид изменения демо-средств: добавить, и статус отклонённого пополнения
DEMO_FUNDS_ADD = 0
DEMO_FUNDS_FAILED = "FAIL"

# Страницы ответов: позиций и ордеров у одного аккаунта на шаге 2 не больше, чем помещается
POSITIONS_LIMIT = 200
ORDER_HISTORY_LIMIT = 50

# Как биржа помечает ордер, сработавший как стоп-лосс (в том числе частично), и статус исполненного ордера
STOP_LOSS_TYPES = ("StopLoss", "PartialStopLoss")
FILLED = "Filled"


class NoRedirectHandler(HTTPRedirectHandler):
    """Не следует перенаправлениям: ключ и подпись в заголовках не должны уйти на другой адрес"""

    def redirect_request(self, req: Request, fp: object, code: int, msg: str, headers: object, newurl: str) -> None:
        """Отказывает в перенаправлении

        Args:
            req: Исходный запрос
            fp: Ответ с перенаправлением
            code: Код ответа
            msg: Сообщение ответа
            headers: Заголовки ответа
            newurl: Адрес, куда отправляли

        Returns:
            None: urllib поднимет HTTPError
        """
        return None


OPENER = build_opener(NoRedirectHandler)


def sign(secret: str, timestamp: str, key: str, payload: str) -> str:
    """Подпись запроса Bybit v5

    Args:
        secret: Секрет API
        timestamp: Время запроса, мс строкой
        key: Ключ API
        payload: Строка запроса для GET или тело JSON для POST, ровно как отправляется

    Returns:
        HMAC-SHA256 в шестнадцатеричном виде
    """
    return hmac.new(secret.encode(), f"{timestamp}{key}{RECV_WINDOW}{payload}".encode(), hashlib.sha256).hexdigest()


def _call(method: str, path: str, params: dict[str, object], ok_codes: tuple[int, ...] = ()) -> dict:
    """Один подписанный запрос к демо-счёту

    Args:
        method: `GET` или `POST`
        path: Путь метода API
        params: Параметры: строка запроса для GET, тело JSON для POST
        ok_codes: Коды ответа биржи, которые считаются успехом кроме 0

    Returns:
        Поле `result` ответа биржи

    Raises:
        RuntimeError: Адрес не демо-счёт или биржа вернула ошибку
        OSError: Сеть недоступна, таймаут или перенаправление, запрос мог дойти до биржи
        JSONDecodeError: Ответ не разобрать как JSON
        HTTPException: Ответ оборван
    """
    url = settings.trade_url()
    if url != DEMO_URL:
        raise RuntimeError(f"приватные запросы разрешены только на демо-счёт {DEMO_URL}, задан {url}")

    key, timestamp = settings.api_key(), str(int(time.time() * MS_PER_SECOND))
    headers = {"X-BAPI-API-KEY": key, "X-BAPI-TIMESTAMP": timestamp, "X-BAPI-RECV-WINDOW": RECV_WINDOW, "Content-Type": "application/json"}

    if method == "GET":
        payload = urlencode(params)
        request = Request(f"{url}{path}?{payload}", headers=headers, method=method)
    else:
        payload = json.dumps(params)
        request = Request(f"{url}{path}", data=payload.encode(), headers=headers, method=method)

    request.add_header("X-BAPI-SIGN", sign(settings.api_secret(), timestamp, key, payload))

    with OPENER.open(request, timeout=settings.timeout_seconds()) as response:
        answer = json.load(response)

    # ответ не той формы считается сбоем биржи: иначе KeyError вылетел бы мимо всех перехватов, например посреди kill
    if not isinstance(answer, dict) or "retCode" not in answer or "result" not in answer:
        raise RuntimeError(f"Bybit {path}: ответ не той формы")

    if answer["retCode"] not in (0, *ok_codes):
        raise RuntimeError(f"Bybit {path}: {answer['retCode']} {answer.get('retMsg')}")

    return answer["result"]


def _position(row: dict) -> Position:
    """Позиция из строки ответа биржи

    Args:
        row: Строка `position/list` с ненулевым размером

    Returns:
        Позиция, пустые и нулевые стоп и цена ликвидации становятся None
    """
    stop, liquidation = Decimal(row.get("stopLoss") or "0"), Decimal(row.get("liqPrice") or "0")

    return Position(symbol=row["symbol"],
                    side=SideEnum(row["side"]),
                    size=Decimal(row["size"]),
                    entry=Decimal(row["avgPrice"]),
                    stop=stop or None,
                    liquidation=liquidation or None)


def _market_order(symbol: str, side: SideEnum, qty: Decimal, **extra: object) -> dict:
    """Тело рыночного ордера

    Args:
        symbol: Контракт
        side: Направление
        qty: Количество, кратное шагу лота
        extra: Дополнительные поля: стоп, цель, номер ордера, `reduceOnly`

    Returns:
        Тело запроса `order/create`
    """
    return {"category": CATEGORY, "symbol": symbol, "side": side.value, "orderType": MARKET, "qty": format(qty, "f"),
            "positionIdx": ONE_WAY_POSITION_INDEX, **extra}


def usdt_equity() -> Decimal:
    """Депозит в USDT вместе с нереализованным результатом открытых позиций

    Returns:
        Средства в USDT
    """
    coins = _call("GET", "/v5/account/wallet-balance", {"accountType": ACCOUNT_TYPE, "coin": SETTLE_COIN})["list"][0]["coin"]

    return Decimal(coins[0]["equity"]) if coins else Decimal(0)


def add_demo_funds(amount: int) -> None:
    """Пополняет демо-счёт виртуальными USDT, настоящих денег запрос не касается

    Args:
        amount: Сколько USDT добавить, биржа принимает только целое число

    Raises:
        RuntimeError: Биржа отклонила пополнение
    """
    result = _call("POST", "/v5/account/demo-apply-money",
                   {"adjustType": DEMO_FUNDS_ADD, "utaDemoApplyMoney": [{"coin": SETTLE_COIN, "amountStr": str(amount)}]})

    # отказ приходит внутри успешного ответа, а не кодом ошибки
    if result.get("orderStatus") == DEMO_FUNDS_FAILED:
        raise RuntimeError(f"Bybit отклонил пополнение демо-счёта: {result.get('retMsg')}")


def open_positions(symbol: str | None = None) -> list[Position]:
    """Открытые позиции по бессрочным USDT-контрактам

    Args:
        symbol: Контракт, без него все позиции

    Returns:
        Позиции с ненулевым размером
    """
    scope = {"symbol": symbol} if symbol else {"settleCoin": SETTLE_COIN}
    # shortcut: одна страница в POSITIONS_LIMIT позиций, при большем числе открытых позиций добавить чтение по курсору
    rows = _call("GET", "/v5/position/list", {"category": CATEGORY, "limit": str(POSITIONS_LIMIT), **scope})["list"]

    return [_position(row) for row in rows if Decimal(row["size"] or "0") > 0]


def stop_hits(start_ms: int) -> list[int]:
    """Времена срабатывания стоп-ордеров с указанного момента

    Закрытие вручную, kill-switch и сработавшая цель сюда не входят: считается только исполненный стоп-лосс.

    Args:
        start_ms: Начало периода, мс, не раньше семи суток назад

    Returns:
        Времена срабатывания в мс
    """
    # shortcut: одна страница в ORDER_HISTORY_LIMIT ордеров, при большем числе исполненных ордеров за неделю добавить чтение по курсору
    rows = _call("GET", "/v5/order/history",
                 {"category": CATEGORY, "orderStatus": FILLED, "startTime": str(start_ms), "limit": str(ORDER_HISTORY_LIMIT)})["list"]

    return [int(row["updatedTime"]) for row in rows if row.get("stopOrderType") in STOP_LOSS_TYPES]


def ensure_isolated_margin() -> None:
    """Переводит аккаунт на изолированную маржу, если она ещё не стоит"""
    if _call("GET", "/v5/account/info", {})["marginMode"] != ISOLATED_MARGIN:
        _call("POST", "/v5/account/set-margin-mode", {"setMarginMode": ISOLATED_MARGIN})


def set_leverage(symbol: str, leverage: Decimal) -> None:
    """Ставит плечо по контракту для обеих сторон

    Args:
        symbol: Контракт
        leverage: Плечо
    """
    _call("POST", "/v5/position/set-leverage",
          {"category": CATEGORY, "symbol": symbol, "buyLeverage": str(leverage), "sellLeverage": str(leverage)},
          ok_codes=(LEVERAGE_NOT_MODIFIED_CODE,))


def place_order(symbol: str, side: SideEnum, qty: Decimal, stop: Decimal, target: Decimal | None, link_id: str) -> None:
    """Отправляет рыночный ордер, стоп уходит на биржу в том же запросе

    Args:
        symbol: Контракт
        side: Направление
        qty: Количество, кратное шагу лота
        stop: Цена стопа
        target: Цена цели, None без цели
        link_id: Свой номер ордера: с тем же номером биржа второй ордер не примет

    Raises:
        ValueError: Размер, стоп или цель не положительны: для Bybit нулевой стоп значит «без стопа»
    """
    if qty <= 0 or stop <= 0 or (target is not None and target <= 0):
        raise ValueError(f"ордер без положительных размера, стопа и цели не отправляется: {qty}, {stop}, {target}")

    extra = {"stopLoss": format(stop, "f"), "orderLinkId": link_id}
    if target is not None:
        extra["takeProfit"] = format(target, "f")

    _call("POST", "/v5/order/create", _market_order(symbol, side, qty, **extra))


def order_exists(symbol: str, link_id: str) -> bool:
    """Дошёл ли ордер до биржи

    Args:
        symbol: Контракт
        link_id: Свой номер ордера

    Returns:
        True, если биржа знает ордер с таким номером
    """
    return bool(_call("GET", "/v5/order/realtime", {"category": CATEGORY, "symbol": symbol, "orderLinkId": link_id})["list"])


def cancel_all_orders() -> None:
    """Снимает все активные ордера по бессрочным USDT-контрактам, вместе с ними снимаются и стопы"""
    _call("POST", "/v5/order/cancel-all", {"category": CATEGORY, "settleCoin": SETTLE_COIN})


def close(position: Position) -> None:
    """Закрывает позицию целиком рыночным ордером, который может только уменьшать позицию

    Args:
        position: Позиция
    """
    _call("POST", "/v5/order/create", _market_order(position.symbol, position.side.opposite(), position.size, reduceOnly=True))
