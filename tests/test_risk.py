"""Тесты риск-слоя: лимиты из `limits.py` и границы суток и недели"""

import unittest
from datetime import UTC, datetime
from decimal import Decimal as D

from purser import risk
from purser.side_enum import SideEnum
from purser.timeunits import MS_PER_SECOND


def check(**overrides: object) -> list[str]:
    """Проверка сделки, которая проходит все лимиты, с заменой отдельных условий

    Args:
        overrides: Аргументы `risk.violations`, которые надо заменить

    Returns:
        Найденные нарушения
    """
    base = {"killed": False, "equity": D("40.63"), "peak": D("40.63"), "stops_today": 0, "stops_week": 0, "open_positions": 0,
            "side": SideEnum.LONG, "entry": D("100"), "stop": D("90"), "target": None, "qty": D("0.1"), "leverage": D("1.25")}

    return risk.violations(**(base | overrides))


class ViolationsTests(unittest.TestCase):
    """Отказы риск-слоя"""

    def test_clean_trade_passes_for_long_and_short(self) -> None:
        """Сделка в пределах лимитов проходит в обе стороны"""
        self.assertEqual(check(), [])
        self.assertEqual(check(side=SideEnum.SHORT, stop=D("110"), target=D("80")), [])

    def test_kill_switch_blocks(self) -> None:
        """Включённый kill-switch запрещает сделку"""
        self.assertIn("kill-switch", check(killed=True)[0])

    def test_drawdown_blocks_at_forty_percent_from_peak(self) -> None:
        """Просадка ровно 40% от пика останавливает торговлю, чуть меньше нет"""
        self.assertIn("просадка", check(peak=D("100"), equity=D("60"))[0])
        self.assertEqual(check(peak=D("100"), equity=D("60.01")), [])

    def test_daily_and_weekly_stop_limits(self) -> None:
        """Два стопа за сутки и три за неделю запрещают новые сделки"""
        self.assertEqual(check(stops_today=1, stops_week=2), [])
        self.assertIn("дневной лимит", check(stops_today=2, stops_week=2)[0])
        self.assertIn("недельный лимит", check(stops_week=3)[0])

    def test_open_positions_count_as_future_stops(self) -> None:
        """Открытые позиции входят в счёт: при двух лимитных стопах за сутки вторая позиция проходит, третья нет"""
        self.assertEqual(check(open_positions=1), [])
        self.assertIn("дневной лимит", check(open_positions=2)[0])
        self.assertIn("дневной лимит", check(stops_today=1, open_positions=1)[0])
        self.assertIn("недельный лимит", check(stops_week=1, open_positions=2, stops_today=0)[-1])

    def test_stop_on_wrong_side(self) -> None:
        """Стоп выше входа у лонга и ниже у шорта отклоняется, как и стоп на цене входа"""
        self.assertIn("не с той стороны", check(stop=D("101"))[0])
        self.assertIn("не с той стороны", check(side=SideEnum.SHORT, stop=D("99"))[0])
        self.assertIn("не с той стороны", check(stop=D("100"))[0])

    def test_stop_not_farther_than_third_of_liquidation_distance(self) -> None:
        """При плече 1,25 ликвидация в 80 от входа 100: стоп на 26 проходит, на 27 нет"""
        self.assertEqual(check(stop=D("74")), [])
        self.assertIn("трети расстояния", check(stop=D("73"))[0])

    def test_target_at_least_two_risks_away(self) -> None:
        """Цель ближе двух расстояний до стопа отклоняется"""
        self.assertEqual(check(target=D("120")), [])
        self.assertIn("цель ближе", check(target=D("119"))[0])
        self.assertIn("цель ближе", check(side=SideEnum.SHORT, stop=D("110"), target=D("81"))[0])

    def test_risk_layer_checks_loss_and_notional_itself(self) -> None:
        """Размер, пришедший не из расчёта, тоже проверяется: убыток выше 7,5% и позиция выше потолка плеча отклоняются"""
        self.assertIn("убыток по стопу", check(qty=D("0.31"))[0])  # 3.1 USDT при лимите 3.047
        self.assertEqual(check(qty=D("0.3")), [])
        self.assertIn("потолка плеча", check(qty=D("0.6"), stop=D("99.9"))[0])  # 60 USDT при потолке 50.79
        self.assertIn("минимума биржи", check(qty=D("0"))[0])


class CalendarTests(unittest.TestCase):
    """Границы суток и недели по UTC"""

    def test_day_and_week_start(self) -> None:
        """Воскресенье 2026-11-15 15:30 UTC: сутки с полуночи, неделя с понедельника 2026-11-09"""
        def ms(*date: int) -> int:
            """Время UTC в мс

            Args:
                date: Год, месяц, день и дальше по желанию часы и минуты

            Returns:
                Миллисекунды с начала отсчёта
            """
            return int(datetime(*date, tzinfo=UTC).timestamp()) * MS_PER_SECOND

        now = ms(2026, 11, 15, 15, 30)

        self.assertEqual(risk.day_start(now), ms(2026, 11, 15))
        self.assertEqual(risk.week_start(now), ms(2026, 11, 9))
        self.assertEqual(risk.week_start(ms(2026, 11, 9)), ms(2026, 11, 9))


if __name__ == "__main__":
    unittest.main()
