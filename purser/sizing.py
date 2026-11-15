"""Размер позиции по риску. Чистые функции без сети: их читает и риск-слой, и проверка осуществимости."""

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal


@dataclass(frozen=True)
class Instrument:
    """Бессрочный контракт Bybit и его ограничения на размер ордера."""

    symbol: str
    price: Decimal
    min_qty: Decimal
    qty_step: Decimal
    min_notional: Decimal
    turnover_24h: Decimal = Decimal(0)


def _to_step(qty: Decimal, step: Decimal, rounding: str) -> Decimal:
    return (qty / step).to_integral_value(rounding=rounding) * step


def min_order_qty(instrument: Instrument) -> Decimal:
    """Наименьшее количество, которое биржа примет: и по лоту, и по стоимости ордера."""
    by_notional = _to_step(instrument.min_notional / instrument.price, instrument.qty_step, ROUND_CEILING)

    return max(instrument.min_qty, by_notional)


def position_qty(instrument: Instrument, equity: Decimal, risk: Decimal, leverage: Decimal, entry: Decimal, stop: Decimal) -> Decimal:
    """Количество контракта, при котором стоп стоит не больше `risk` от депозита. 0, если сделка невозможна.

    Округление всегда вниз: лучше рискнуть меньше, чем превысить лимит.
    """
    distance = abs(entry - stop)
    if distance == 0:
        return Decimal(0)

    by_risk = equity * risk / distance
    by_leverage = equity * leverage / entry
    qty = _to_step(min(by_risk, by_leverage), instrument.qty_step, ROUND_FLOOR)

    return qty if qty >= min_order_qty(instrument) else Decimal(0)
