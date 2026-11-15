"""Размер позиции по риску, чистые функции без сети для риск-слоя и проверки осуществимости"""

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal


@dataclass(frozen=True)
class Instrument:
    """Бессрочный контракт Bybit и его ограничения на размер ордера"""

    symbol: str
    price: Decimal
    min_qty: Decimal
    qty_step: Decimal
    min_notional: Decimal
    turnover_24h: Decimal = Decimal(0)
    spread: Decimal = Decimal(0)  # доля от цены на момент запроса
    funding_rate: Decimal = Decimal(0)  # текущая ставка за один период
    launch_ms: int = 0  # время листинга, мс


def _to_step(qty: Decimal, step: Decimal, rounding: str) -> Decimal:
    """Приводит количество к кратному шагу лота

    Args:
        qty: Исходное количество
        step: Шаг лота
        rounding: Режим округления `decimal`, например `ROUND_FLOOR`

    Returns:
        Количество, кратное шагу
    """
    return (qty / step).to_integral_value(rounding=rounding) * step


def min_order_qty(instrument: Instrument) -> Decimal:
    """Наименьшее количество, которое биржа примет: и по лоту, и по стоимости ордера

    Args:
        instrument: Контракт с ограничениями биржи

    Returns:
        Большее из минимального лота и количества на минимальную стоимость, округлённое вверх по шагу
    """
    by_notional = _to_step(instrument.min_notional / instrument.price, instrument.qty_step, ROUND_CEILING)

    return max(instrument.min_qty, by_notional)


def position_qty(instrument: Instrument, equity: Decimal, risk: Decimal, leverage: Decimal, entry: Decimal, stop: Decimal) -> Decimal:
    """Количество контракта, при котором стоп стоит не больше `risk` от депозита

    Округление всегда вниз: лучше рискнуть меньше, чем превысить лимит.

    Args:
        instrument: Контракт с ограничениями биржи
        equity: Депозит, USDT
        risk: Доля депозита, которую можно потерять на сделке
        leverage: Потолок плеча, срезает позицию при тесном стопе
        entry: Цена входа
        stop: Цена стопа

    Returns:
        Количество, кратное шагу лота, или 0, если сделка невозможна: стоп равен входу или позиция меньше минимума биржи
    """
    distance = abs(entry - stop)
    if distance == 0:
        return Decimal(0)

    by_risk = equity * risk / distance
    by_leverage = equity * leverage / entry
    qty = _to_step(min(by_risk, by_leverage), instrument.qty_step, ROUND_FLOOR)

    return qty if qty >= min_order_qty(instrument) else Decimal(0)
