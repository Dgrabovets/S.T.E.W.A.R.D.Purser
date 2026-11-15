"""План сделки: расчёт размера и результат проверки риск-слоем"""

from dataclasses import dataclass
from decimal import Decimal

from purser.side_enum import SideEnum


@dataclass(frozen=True)
class Plan:
    """План одной сделки до отправки ордера"""

    symbol: str
    side: SideEnum
    price: Decimal  # цена контракта сейчас
    entry: Decimal  # расчётная цена входа с запасом на проскальзывание и комиссии
    equity: Decimal
    qty: Decimal
    stop: Decimal
    target: Decimal | None
    violations: list[str]  # причины отказа, пусто, если сделку можно отправлять
