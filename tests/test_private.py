"""Тесты приватных запросов: подпись, запрет не-демо адреса, разбор ответов. Сеть подменена"""

import hashlib
import hmac
import io
import json
import os
import unittest
from decimal import Decimal as D
from unittest import mock

from purser import bybit_private
from purser.position import Position
from purser.side_enum import SideEnum

# Тестовые ключ и секрет, к настоящим отношения не имеют
ENV = {"PURSER_API_KEY": "test-key", "PURSER_API_SECRET": "test-secret", "PURSER_TRADE_URL": bybit_private.DEMO_URL}


def answer(result: dict, code: int = 0) -> mock.MagicMock:
    """Подмена ответа биржи для `OPENER.open`

    Args:
        result: Поле `result`
        code: Код ответа биржи

    Returns:
        Объект, который работает в `with` и отдаёт JSON
    """
    response = mock.MagicMock()
    response.__enter__.side_effect = lambda: io.BytesIO(json.dumps({"retCode": code, "retMsg": "msg", "result": result}).encode())

    return response


def expected_sign(request: mock.MagicMock, payload: str) -> str:
    """Подпись, которую ждёт биржа, посчитанная независимо от кода

    Args:
        request: Отправленный запрос, из него берётся время
        payload: Строка запроса или тело

    Returns:
        HMAC-SHA256 от времени, ключа, окна и содержимого
    """
    text = f"{request.get_header('X-bapi-timestamp')}test-key{bybit_private.RECV_WINDOW}{payload}"

    return hmac.new(b"test-secret", text.encode(), hashlib.sha256).hexdigest()


class PrivateTestCase(unittest.TestCase):
    """Общая подготовка: тестовые ключи в окружении"""

    def setUp(self) -> None:
        """Подменяет окружение тестовыми ключами"""
        patcher = mock.patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)


class CallTests(PrivateTestCase):
    """Подписанный запрос"""

    def test_get_signs_query_string(self) -> None:
        """У GET подписывается строка запроса, ключ уходит в заголовке, секрет никуда не уходит"""
        with mock.patch.object(bybit_private.OPENER, "open", return_value=answer({"list": []})) as opener:
            bybit_private.open_positions("XRPUSDT")

        request = opener.call_args.args[0]
        query = request.full_url.split("?", 1)[1]

        self.assertEqual(query, "category=linear&limit=200&symbol=XRPUSDT")
        self.assertEqual(request.get_header("X-bapi-sign"), expected_sign(request, query))
        self.assertEqual(request.get_header("X-bapi-api-key"), "test-key")
        self.assertNotIn("test-secret", request.full_url + str(request.header_items()))

    def test_post_signs_body_and_sends_stop_with_order(self) -> None:
        """У POST подписывается тело, а стоп уходит в том же запросе, что и ордер"""
        with mock.patch.object(bybit_private.OPENER, "open", return_value=answer({})) as opener:
            bybit_private.place_order("XRPUSDT", SideEnum.SHORT, D("15.0"), D("2.5"), None, "purser-1")

        request = opener.call_args.args[0]
        body = json.loads(request.data)

        self.assertEqual(request.get_header("X-bapi-sign"), expected_sign(request, request.data.decode()))
        self.assertEqual((body["side"], body["qty"], body["stopLoss"], body["orderLinkId"]), ("Sell", "15.0", "2.5", "purser-1"))
        self.assertNotIn("takeProfit", body)

    def test_demo_funds_request(self) -> None:
        """Пополнение демо-счёта просит добавить указанную сумму в USDT, а отказ внутри ответа становится ошибкой"""
        with mock.patch.object(bybit_private.OPENER, "open", return_value=answer({"orderStatus": "SUCCESS"})) as opener:
            bybit_private.add_demo_funds(41)

        body = json.loads(opener.call_args.args[0].data)
        self.assertEqual(body, {"adjustType": 0, "utaDemoApplyMoney": [{"coin": "USDT", "amountStr": "41"}]})

        with mock.patch.object(bybit_private.OPENER, "open", return_value=answer({"orderStatus": "FAIL", "retMsg": "precision"})):
            with self.assertRaisesRegex(RuntimeError, "precision"):
                bybit_private.add_demo_funds(41)

    def test_not_demo_url_is_refused_before_network(self) -> None:
        """Адрес боевого счёта отклоняется до сети"""
        with mock.patch.dict(os.environ, {"PURSER_TRADE_URL": "https://api.bybit.com"}), \
             mock.patch.object(bybit_private.OPENER, "open") as opener:
            with self.assertRaisesRegex(RuntimeError, "только на демо-счёт"):
                bybit_private.usdt_equity()

        opener.assert_not_called()

    def test_exchange_error_and_allowed_code(self) -> None:
        """Ошибка биржи поднимается, а «плечо не изменилось» ошибкой не считается"""
        with mock.patch.object(bybit_private.OPENER, "open", return_value=answer({}, code=110001)):
            with self.assertRaisesRegex(RuntimeError, "110001"):
                bybit_private.cancel_all_orders()

        with mock.patch.object(bybit_private.OPENER, "open", return_value=answer({}, code=bybit_private.LEVERAGE_NOT_MODIFIED_CODE)):
            bybit_private.set_leverage("XRPUSDT", D("1.25"))

    def test_answer_of_wrong_shape_is_exchange_error(self) -> None:
        """Ответ без `retCode`, без `result` или не словарь становится RuntimeError, а не KeyError"""
        for body in ('{}', '{"retCode": 0}', '[1]'):
            response = mock.MagicMock()
            response.__enter__.side_effect = lambda body=body: io.BytesIO(body.encode())

            with mock.patch.object(bybit_private.OPENER, "open", return_value=response), self.assertRaisesRegex(RuntimeError, "не той формы"):
                bybit_private.cancel_all_orders()

    def test_broken_json_is_transport_error_and_settings_error_is_not(self) -> None:
        """Битый JSON относится к сбоям связи, а ошибка настроек нет: она не значит, что ордер мог уйти"""
        response = mock.MagicMock()
        response.__enter__.side_effect = lambda: io.BytesIO(b"<html>")

        with mock.patch.object(bybit_private.OPENER, "open", return_value=response), self.assertRaises(bybit_private.TRANSPORT_ERRORS):
            bybit_private.cancel_all_orders()

        self.assertNotIn(ValueError, bybit_private.TRANSPORT_ERRORS)

    def test_key_with_forbidden_characters_is_rejected_without_printing_it(self) -> None:
        """Ключ с пробелом, переводом строки или не ASCII отклоняется, а сообщение не содержит значения"""
        for bad in ("abc def", "abc\x01def", "ключ"):
            with mock.patch.dict(os.environ, {"PURSER_API_KEY": bad}), mock.patch.object(bybit_private.OPENER, "open") as opener:
                with self.assertRaises(ValueError) as caught:
                    bybit_private.usdt_equity()

            self.assertNotIn(bad, str(caught.exception))
            opener.assert_not_called()

    def test_partial_stop_loss_counts_too(self) -> None:
        """Частично сработавший стоп-лосс тоже считается стопом"""
        orders = [{"stopOrderType": "PartialStopLoss", "updatedTime": "7"}]

        with mock.patch.object(bybit_private.OPENER, "open", return_value=answer({"list": orders})):
            self.assertEqual(bybit_private.stop_hits(0), [7])

    def test_missing_key_is_reported_by_name(self) -> None:
        """Без ключа в окружении ошибка называет переменную, а сеть не трогается"""
        with mock.patch.dict(os.environ, {"PURSER_API_KEY": ""}), mock.patch.object(bybit_private.OPENER, "open") as opener:
            with self.assertRaisesRegex(ValueError, "PURSER_API_KEY"):
                bybit_private.usdt_equity()

        opener.assert_not_called()


class ParseTests(PrivateTestCase):
    """Разбор ответов биржи"""

    def test_positions_skip_empty_and_missing_stop_is_none(self) -> None:
        """Строка с нулевым размером не позиция, пустой или нулевой стоп становится None"""
        rows = [{"symbol": "AUSDT", "side": "", "size": "0", "avgPrice": "0", "stopLoss": "", "liqPrice": ""},
                {"symbol": "BUSDT", "side": "Buy", "size": "5", "avgPrice": "2", "stopLoss": "0", "liqPrice": ""},
                {"symbol": "CUSDT", "side": "Sell", "size": "7", "avgPrice": "3", "stopLoss": "3.3", "liqPrice": "5.1"}]

        with mock.patch.object(bybit_private.OPENER, "open", return_value=answer({"list": rows})):
            found = bybit_private.open_positions()

        self.assertEqual([position.symbol for position in found], ["BUSDT", "CUSDT"])
        self.assertIsNone(found[0].stop)
        self.assertIsNone(found[0].liquidation)
        self.assertEqual((found[1].side, found[1].stop, found[1].liquidation), (SideEnum.SHORT, D("3.3"), D("5.1")))

    def test_equity_of_empty_account_is_zero(self) -> None:
        """Пустой счёт даёт депозит 0"""
        with mock.patch.object(bybit_private.OPENER, "open", return_value=answer({"list": [{"coin": []}]})):
            self.assertEqual(bybit_private.usdt_equity(), D(0))

    def test_only_filled_stop_loss_orders_count_as_stops(self) -> None:
        """Стопом считается только исполненный стоп-лосс: цель, закрытие вручную и пустое поле не считаются"""
        orders = [{"stopOrderType": "StopLoss", "updatedTime": "100"}, {"stopOrderType": "TakeProfit", "updatedTime": "200"},
                  {"stopOrderType": "", "updatedTime": "300"}, {"updatedTime": "400"}]

        with mock.patch.object(bybit_private.OPENER, "open", return_value=answer({"list": orders})) as opener:
            self.assertEqual(bybit_private.stop_hits(5), [100])

        self.assertIn("orderStatus=Filled", opener.call_args.args[0].full_url)
        self.assertIn("startTime=5", opener.call_args.args[0].full_url)


class OrderTests(PrivateTestCase):
    """Тела ордеров и запросов на изменение счёта"""

    def sent(self, call: object) -> dict:
        """Выполняет вызов с подменённой сетью и возвращает тело отправленного запроса

        Args:
            call: Функция без аргументов, которая обращается к бирже

        Returns:
            Тело запроса как словарь
        """
        with mock.patch.object(bybit_private.OPENER, "open", return_value=answer({})) as opener:
            call()

        return json.loads(opener.call_args.args[0].data)

    def test_order_with_target_and_validation(self) -> None:
        """Цель уходит как takeProfit, а нулевой размер, стоп или цель не отправляются совсем"""
        body = self.sent(lambda: bybit_private.place_order("XRPUSDT", SideEnum.LONG, D("40.3"), D("0.87"), D("1.11"), "purser-1"))

        self.assertEqual((body["takeProfit"], body["stopLoss"], body["side"], body["orderType"]), ("1.11", "0.87", "Buy", "Market"))

        with mock.patch.object(bybit_private.OPENER, "open") as opener:
            for qty, stop, target in ((D("0"), D("1"), None), (D("1"), D("0"), None), (D("1"), D("1"), D("0"))):
                with self.assertRaises(ValueError):
                    bybit_private.place_order("XRPUSDT", SideEnum.LONG, qty, stop, target, "purser-1")

        opener.assert_not_called()

    def test_close_is_reduce_only_in_opposite_direction(self) -> None:
        """Закрытие идёт обратной стороной, на весь размер и только на уменьшение позиции"""
        position = Position("XRPUSDT", SideEnum.LONG, D("40.3"), D("0.95"), D("0.87"), D("0.19"))

        body = self.sent(lambda: bybit_private.close(position))

        self.assertEqual((body["side"], body["qty"], body["reduceOnly"]), ("Sell", "40.3", True))
        self.assertEqual(SideEnum.SHORT.opposite(), SideEnum.LONG)
        self.assertEqual((SideEnum.LONG.sign(), SideEnum.SHORT.sign()), (1, -1))

    def test_isolated_margin_is_set_only_when_needed(self) -> None:
        """Маржа переключается, только если стоит не изолированная"""
        with mock.patch.object(bybit_private.OPENER, "open", return_value=answer({"marginMode": "ISOLATED_MARGIN"})) as opener:
            bybit_private.ensure_isolated_margin()

        self.assertEqual(opener.call_count, 1)

        with mock.patch.object(bybit_private.OPENER, "open", return_value=answer({"marginMode": "REGULAR_MARGIN"})) as opener:
            bybit_private.ensure_isolated_margin()

        self.assertEqual(opener.call_count, 2)
        self.assertEqual(json.loads(opener.call_args.args[0].data), {"setMarginMode": "ISOLATED_MARGIN"})

    def test_order_exists_by_link_id(self) -> None:
        """Ордер существует, если биржа вернула непустой список по своему номеру"""
        with mock.patch.object(bybit_private.OPENER, "open", return_value=answer({"list": [{"orderLinkId": "purser-1"}]})):
            self.assertTrue(bybit_private.order_exists("XRPUSDT", "purser-1"))

        with mock.patch.object(bybit_private.OPENER, "open", return_value=answer({"list": []})):
            self.assertFalse(bybit_private.order_exists("XRPUSDT", "purser-1"))

    def test_redirect_is_not_followed(self) -> None:
        """Перенаправление не выполняется: ключ и подпись не уходят на другой адрес"""
        handler = bybit_private.NoRedirectHandler()

        self.assertIsNone(handler.redirect_request(mock.Mock(), None, 302, "Found", {}, "https://evil.test"))


if __name__ == "__main__":
    unittest.main()
