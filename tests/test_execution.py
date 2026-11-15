"""Тесты исполнения: порядок «риск, подтверждение, ордер, сверка» и kill-switch. Биржа подменена"""

import os
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path
from http.client import IncompleteRead
from unittest import mock

from purser import execution, limits, state
from purser.position import Position
from purser.side_enum import SideEnum
from purser.sizing import Instrument

DOGE = Instrument("DOGEUSDT", price=D("0.2"), min_qty=D("1"), qty_step=D("1"), min_notional=D("5"))

# Позиция, которую биржа показывает после ордера из теста: 152 это 7,5% от 40,63 USDT при стопе 0,02
OPENED = Position("DOGEUSDT", SideEnum.LONG, D("152"), D("0.2"), stop=D("0.18"), liquidation=D("0.05"))

# Настройки теста: запаса на проскальзывание нет (нижняя граница из `limits.py` в тесте снята), проверки позиции быстрые
SETTINGS = {"PURSER_SLIPPAGE_RESERVE": "0", "PURSER_RECONCILE_ATTEMPTS": "2", "PURSER_RECONCILE_PAUSE_SECONDS": "0"}


class ExecutionTestCase(unittest.TestCase):
    """Общая подготовка: подменённая биржа, папка состояния и ввод владельца"""

    def setUp(self) -> None:
        """Подменяет биржу и состояние: депозит 40,63 USDT, позиций и стопов нет, ордер открывает позицию `OPENED`"""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.addCleanup(mock.patch.stopall)
        self.positions: list[Position] = []
        self.private = mock.patch.object(execution, "bybit_private").start()
        self.private.TRANSPORT_ERRORS = (OSError, ValueError, IncompleteRead)
        self.private.usdt_equity.return_value = D("40.63")
        self.private.stop_hits.return_value = []
        self.private.open_positions.side_effect = lambda symbol=None: [item for item in self.positions if symbol in (None, item.symbol)]
        self.private.place_order.side_effect = lambda *args: setattr(self, "positions", [OPENED])
        mock.patch.object(execution.bybit_public, "linear_perpetuals", return_value=[DOGE]).start()
        mock.patch.object(state.settings, "state_dir", return_value=Path(folder.name)).start()
        mock.patch.dict(os.environ, SETTINGS).start()
        mock.patch.object(limits, "MIN_SLIPPAGE_RESERVE", D("0")).start()
        mock.patch.object(execution.time, "sleep").start()
        mock.patch("builtins.print").start()

    def open(self, answer: str = "DOGEUSDT", stop: str = "0.18") -> bool:
        """Запускает открытие лонга по DOGE

        Args:
            answer: Что владелец вводит на подтверждении
            stop: Цена стопа

        Returns:
            Результат `open_position`
        """
        self.confirm = mock.Mock(return_value=answer)

        return execution.open_position("DOGEUSDT", SideEnum.LONG, D(stop), None, 0, self.confirm)

    def assert_refused_before_confirmation(self) -> None:
        """Проверяет, что сделка отклонена без вопроса владельцу и без ордера"""
        self.assertFalse(self.open())
        self.confirm.assert_not_called()
        self.private.place_order.assert_not_called()


class OpenPositionTests(ExecutionTestCase):
    """Открытие позиции"""

    def test_confirmed_order_goes_with_stop_and_is_reconciled(self) -> None:
        """После подтверждения уходит ордер на 152 со стопом, плечо на бирже по потолку, позиция сверена"""
        self.assertTrue(self.open())

        symbol, side, qty, stop, target, link_id = self.private.place_order.call_args.args
        self.assertEqual((symbol, side, qty, stop, target), ("DOGEUSDT", SideEnum.LONG, D("152"), D("0.18"), None))
        self.assertLessEqual(len(link_id), 36)
        self.assertLessEqual(qty * D("0.02"), D("40.63") * limits.RISK_PER_TRADE)
        self.private.set_leverage.assert_called_once_with("DOGEUSDT", limits.LEVERAGE_CAP)
        self.private.ensure_isolated_margin.assert_called_once()

    def test_slippage_reserve_makes_position_smaller(self) -> None:
        """Запас на проскальзывание 3% сдвигает расчётный вход к стопу: количество меньше, убыток по цене входа с запасом в лимите"""
        with mock.patch.dict(os.environ, {"PURSER_SLIPPAGE_RESERVE": "0.03"}):
            self.assertTrue(self.open())

        qty = self.private.place_order.call_args.args[2]
        self.assertLess(qty, D("152"))
        self.assertLessEqual(qty * (D("0.2") * D("1.03") - D("0.18")), D("40.63") * limits.RISK_PER_TRADE)

    def test_declined_confirmation_sends_nothing(self) -> None:
        """Без точного названия контракта в ответе ордер не уходит"""
        self.assertFalse(self.open(answer="да"))

        self.private.place_order.assert_not_called()
        self.private.set_leverage.assert_not_called()

    def test_kill_switch_refuses(self) -> None:
        """Включённый kill-switch отклоняет сделку без вопроса"""
        state.set_killed("тест")

        self.assert_refused_before_confirmation()

    def test_stop_limit_refuses(self) -> None:
        """Три сработавших стопа за неделю отклоняют сделку без вопроса"""
        self.private.stop_hits.return_value = [1, 2, 3]

        self.assert_refused_before_confirmation()

    def test_same_symbol_already_open_refuses(self) -> None:
        """Вторая позиция по той же монете отклоняется"""
        self.positions = [OPENED]

        self.assert_refused_before_confirmation()

    def test_open_positions_count_towards_daily_limit(self) -> None:
        """Две открытые позиции по другим монетам исчерпывают дневной лимит стопов"""
        self.positions = [Position("AAAUSDT", SideEnum.LONG, D("1"), D("1"), D("0.9"), D("0.1")),
                          Position("BBBUSDT", SideEnum.LONG, D("1"), D("1"), D("0.9"), D("0.1"))]

        self.assert_refused_before_confirmation()

    def test_too_small_deposit_refuses(self) -> None:
        """Депозит, при котором позиция меньше минимума биржи, отклоняется"""
        self.private.usdt_equity.return_value = D("1")

        self.assert_refused_before_confirmation()

    def test_plan_changed_while_waiting_for_confirmation(self) -> None:
        """Если за время подтверждения kill-switch включили в другой консоли, ордер не уходит"""
        def confirm(question: str) -> str:
            """Подмена ввода владельца: пока он отвечает, включается kill-switch

            Args:
                question: Вопрос владельцу

            Returns:
                Верный ответ
            """
            state.set_killed("другая консоль")

            return "DOGEUSDT"

        self.assertFalse(execution.open_position("DOGEUSDT", SideEnum.LONG, D("0.18"), None, 0, confirm))

        self.private.place_order.assert_not_called()

    def test_lost_answer_is_resolved_by_link_id_without_second_order(self) -> None:
        """Ответ на ордер не пришёл: если биржа знает номер, идём к сверке, второй ордер не шлём"""
        def send(*args: object) -> None:
            """Подмена отправки: ордер доходит, а ответ теряется

            Args:
                args: Аргументы `place_order`
            """
            self.positions = [OPENED]
            raise OSError("таймаут")

        self.private.place_order.side_effect = send
        self.private.order_exists.return_value = True

        self.assertTrue(self.open())
        self.assertEqual(self.private.place_order.call_count, 1)
        self.assertEqual(self.private.order_exists.call_args.args[1], self.private.place_order.call_args.args[5])

    def test_lost_answer_and_unknown_order_raises(self) -> None:
        """Ответ не пришёл и биржа ордера не знает: ошибка уходит наверх, повторной отправки нет"""
        self.private.place_order.side_effect = OSError("таймаут")
        self.private.order_exists.return_value = False

        with self.assertRaises(OSError):
            self.open()

        self.assertEqual(self.private.place_order.call_count, 1)

    def test_lost_answer_and_failed_check_tells_to_check_account(self) -> None:
        """Ответ не пришёл и проверка тоже упала: владелец получает понятное сообщение, а не трейсбек сети"""
        self.private.place_order.side_effect = OSError("таймаут")
        self.private.order_exists.side_effect = OSError("нет сети")

        with self.assertRaisesRegex(RuntimeError, "проверьте счёт вручную"):
            self.open()


class ReconcileTests(ExecutionTestCase):
    """Сверка позиции с биржей после ордера"""

    def open_as(self, position: Position) -> None:
        """Открывает позицию, а биржа показывает вместо неё другую

        Args:
            position: Что биржа показывает после ордера
        """
        self.private.place_order.side_effect = lambda *args: setattr(self, "positions", [position])
        self.open()

    def test_position_without_stop_is_closed(self) -> None:
        """Позиция без стопа на бирже сразу закрывается, и об этом сообщается ошибкой"""
        naked = Position("DOGEUSDT", SideEnum.LONG, D("152"), D("0.2"), stop=None, liquidation=None)

        with self.assertRaisesRegex(RuntimeError, "без стопа"):
            self.open_as(naked)

        self.private.close.assert_called_once_with(naked)

    def test_position_without_stop_and_failed_close_tells_to_close_manually(self) -> None:
        """Если закрыть позицию без стопа не вышло, сообщение прямо просит закрыть её вручную"""
        naked = Position("DOGEUSDT", SideEnum.LONG, D("152"), D("0.2"), stop=None, liquidation=None)
        self.private.close.side_effect = OSError("нет сети")

        with self.assertRaisesRegex(RuntimeError, "вручную"):
            self.open_as(naked)

    def test_actual_risk_above_limit_closes_position(self) -> None:
        """Биржа исполнила хуже: убыток по стопу от фактического входа выше 7,5%, позиция закрывается"""
        worse = Position("DOGEUSDT", SideEnum.LONG, D("152"), D("0.21"), stop=D("0.18"), liquidation=D("0.05"))

        with self.assertRaisesRegex(RuntimeError, "убыток по стопу"):
            self.open_as(worse)

        self.private.close.assert_called_once_with(worse)

    def test_liquidation_too_close_closes_position(self) -> None:
        """Цена ликвидации с биржи ближе трёх расстояний до стопа: позиция закрывается"""
        near = Position("DOGEUSDT", SideEnum.LONG, D("152"), D("0.2"), stop=D("0.18"), liquidation=D("0.15"))

        with self.assertRaisesRegex(RuntimeError, "ликвидации"):
            self.open_as(near)

        self.private.close.assert_called_once_with(near)

    def test_missing_position_after_order_raises(self) -> None:
        """Ордер ушёл, а позиции нет: ошибка с просьбой проверить счёт"""
        self.private.place_order.side_effect = None

        with self.assertRaisesRegex(RuntimeError, "позиции на бирже нет"):
            self.open()


class AccountTests(ExecutionTestCase):
    """Отчёт о счёте"""

    def test_stops_are_split_by_day_and_week(self) -> None:
        """Стопы за сегодня и за неделю считаются раздельно по границам UTC"""
        day = 86_400_000
        now = 10 * day + 5_000  # день 10 от начала отсчёта, внутри недели, которая началась в день 7
        self.private.stop_hits.return_value = [now - 100, 9 * day, 8 * day]

        self.assertEqual(execution._stops(now), (1, 3))

    def test_account_report_shows_stops_kill_and_positions(self) -> None:
        """Отчёт показывает депозит, пик, счётчики, kill-switch и позиции без стопа помечает словом НЕТ"""
        self.private.stop_hits.return_value = []
        self.positions = [Position("AAAUSDT", SideEnum.LONG, D("1"), D("1"), None, None)]

        text = execution.account(0)

        self.assertIn("Депозит 40.63 USDT, пик 40.63 USDT", text)
        self.assertIn("Сработавших стопов: сегодня 0 из 2, за неделю 0 из 3", text)
        self.assertIn("Kill-switch: выключен", text)
        self.assertIn("стоп НЕТ", text)


class KillTests(ExecutionTestCase):
    """Kill-switch"""

    def test_flag_is_set_even_if_exchange_is_down(self) -> None:
        """Флаг ставится первым: даже при недоступной бирже новые сделки уже запрещены"""
        self.private.open_positions.side_effect = OSError("нет сети")

        with self.assertRaises(OSError):
            execution.kill("тест")

        self.assertTrue(state.is_killed())

    def test_positions_are_closed_before_orders_are_cancelled(self) -> None:
        """Отмена ордеров снимает стопы, поэтому она идёт после закрытия позиций"""
        self.positions = [OPENED]
        self.private.close.side_effect = lambda position: setattr(self, "positions", [])

        execution.kill("тест")

        calls = [call[0] for call in self.private.method_calls if call[0] in ("close", "cancel_all_orders")]
        self.assertEqual(calls, ["close", "cancel_all_orders"])

    def test_failed_close_keeps_orders_and_stops(self) -> None:
        """Позиция не закрылась: ордера со стопами не снимаются, ошибка называет монету, остальные позиции всё равно закрываются"""
        other = Position("AAAUSDT", SideEnum.LONG, D("1"), D("1"), D("0.9"), D("0.1"))
        self.positions = [OPENED, other]
        self.private.close.side_effect = lambda position: (_ for _ in ()).throw(OSError("нет сети")) if position is OPENED \
            else setattr(self, "positions", [OPENED])

        with self.assertRaisesRegex(RuntimeError, "DOGEUSDT"):
            execution.kill("тест")

        self.assertEqual(self.private.close.call_count, 2)
        self.private.cancel_all_orders.assert_not_called()


class StateTests(unittest.TestCase):
    """Пик депозита между запусками"""

    def test_peak_only_grows_and_deposit_raises_it(self) -> None:
        """Пик не падает вместе с депозитом, а пополнение поднимает его на сумму, не маскируя просадку"""
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(state.settings, "state_dir", return_value=Path(folder)):
            self.assertEqual(state.update_peak(D("40.63")), D("40.63"))
            self.assertEqual(state.update_peak(D("50")), D("50"))
            self.assertEqual(state.update_peak(D("30")), D("50"))

            state.add_deposit(D("50"))

            self.assertEqual(state.update_peak(D("80")), D("100"))


if __name__ == "__main__":
    unittest.main()
