"""Тесты размера позиции и минимального ордера

Запуск: `python -m unittest discover tests`
"""

import unittest
from decimal import Decimal as D

from purser.sizing import Instrument, min_order_qty, position_qty

BTC = Instrument("BTCUSDT", price=D("65000"), min_qty=D("0.001"), qty_step=D("0.001"), min_notional=D("5"))
DOGE = Instrument("DOGEUSDT", price=D("0.2"), min_qty=D("1"), qty_step=D("1"), min_notional=D("5"))
# Депозит владельца и утверждённый риск на сделку
EQUITY, RISK = D("40.63"), D("0.075")


class SizingTests(unittest.TestCase):
    """Размер позиции и минимальный ордер"""

    def test_min_order_takes_larger_of_lot_and_notional(self) -> None:
        """Минимальный ордер берётся по большему из лота и стоимости"""
        self.assertEqual(min_order_qty(BTC), D("0.001"))  # лот 65 USDT больше порога 5 USDT
        self.assertEqual(min_order_qty(DOGE), D("25"))  # 5 USDT / 0.2

    def test_risk_sets_size_when_stop_is_wide(self) -> None:
        """При широком стопе размер задаёт риск"""
        # стоп 10% от цены: 3.04725 / 0.02 = 152.36, вниз до 152; убыток 3.04 <= 7.5% депозита
        qty = position_qty(DOGE, EQUITY, RISK, D("1"), entry=D("0.2"), stop=D("0.18"))

        self.assertEqual(qty, D("152"))
        self.assertLessEqual(qty * D("0.02"), EQUITY * RISK)

    def test_leverage_caps_size_when_stop_is_tight(self) -> None:
        """При тесном стопе размер срезает потолок плеча"""
        # стоп 1%: по риску вышло бы 1523, но без плеча позиция не больше депозита
        self.assertEqual(position_qty(DOGE, EQUITY, RISK, D("1"), entry=D("0.2"), stop=D("0.198")), D("203"))
        self.assertEqual(position_qty(DOGE, EQUITY, RISK, D("1.25"), entry=D("0.2"), stop=D("0.198")), D("253"))

    def test_short_uses_same_distance(self) -> None:
        """Для шорта расстояние до стопа считается так же, как для лонга"""
        self.assertEqual(position_qty(DOGE, EQUITY, RISK, D("1"), entry=D("0.2"), stop=D("0.22")), D("152"))

    def test_zero_when_below_exchange_minimum(self) -> None:
        """Ниже минимума биржи позиция равна 0"""
        # минимальный лот BTC стоит 65 USDT, депозит 40.63
        self.assertEqual(position_qty(BTC, EQUITY, RISK, D("1.25"), entry=D("65000"), stop=D("60000")), D("0"))

    def test_zero_when_stop_equals_entry(self) -> None:
        """Стоп на цене входа даёт 0, а не деление на ноль"""
        self.assertEqual(position_qty(DOGE, EQUITY, RISK, D("1"), entry=D("0.2"), stop=D("0.2")), D("0"))


if __name__ == "__main__":
    unittest.main()
