"""Направление сделки"""

from enum import Enum


class SideEnum(Enum):
    """Направление сделки, значение совпадает с полем `side` в API Bybit"""

    LONG = "Buy"
    SHORT = "Sell"

    def opposite(self) -> "SideEnum":
        """Обратное направление: им закрывается позиция

        Returns:
            Шорт для лонга и лонг для шорта
        """
        if self is SideEnum.LONG:
            return SideEnum.SHORT

        return SideEnum.LONG

    def sign(self) -> int:
        """Знак движения цены в пользу сделки

        Returns:
            1 для лонга, -1 для шорта
        """
        return 1 if self is SideEnum.LONG else -1
