"""Открытая позиция на бирже"""

from dataclasses import dataclass
from decimal import Decimal

from purser.side_enum import SideEnum


@dataclass(frozen=True)
class Position:
    """Открытая позиция по бессрочному контракту, как её видит биржа"""

    symbol: str
    side: SideEnum
    size: Decimal
    entry: Decimal
    stop: Decimal | None  # None: стопа на бирже нет
    liquidation: Decimal | None  # None: биржа не сообщила цену ликвидации
