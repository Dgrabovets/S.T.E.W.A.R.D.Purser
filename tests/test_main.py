"""Тесты командной строки: выбор контрактов и докачка, без сети"""

import argparse
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest import mock

from purser import __main__ as main_module
from purser.sizing import Instrument


def coin(symbol: str) -> Instrument:
    """Контракт для теста

    Args:
        symbol: Название контракта

    Returns:
        Контракт по цене 1 USDT
    """
    return Instrument(symbol, price=D("1"), min_qty=D("1"), qty_step=D("1"), min_notional=D("5"))


class PositiveTests(unittest.TestCase):
    """Числа из командной строки"""

    def test_only_positive_finite_numbers_pass(self) -> None:
        """Ноль, отрицательные, NaN, бесконечность и не числа отклоняются"""
        self.assertEqual(main_module._positive("0.87"), D("0.87"))

        for bad in ("0", "-1", "nan", "inf", "-inf", "abc"):
            with self.subTest(bad=bad), self.assertRaises(argparse.ArgumentTypeError):
                main_module._positive(bad)


class CommandTests(unittest.TestCase):
    """Диспетчер команд демо-счёта"""

    def run_main(self, *argv: str) -> None:
        """Запускает `main` с аргументами, вывод и кодировку не трогает

        Args:
            argv: Аргументы командной строки после имени программы
        """
        with mock.patch("sys.argv", ["purser", *argv]), mock.patch("builtins.print"), \
             mock.patch.object(main_module.sys.stdout, "reconfigure"), mock.patch.object(main_module.sys.stderr, "reconfigure"):
            main_module.main()

    def test_refused_order_exits_with_code_one(self) -> None:
        """Отказ риск-слоя или владельца даёт код выхода 1, чтобы его видел скрипт"""
        with mock.patch.object(main_module.execution, "open_position", return_value=False), self.assertRaises(SystemExit) as caught:
            self.run_main("order", "XRPUSDT", "long", "--stop", "0.87")

        self.assertEqual(caught.exception.code, 1)

    def test_fund_records_deposit_only_after_exchange_accepted(self) -> None:
        """Пополнение пишется в пик только после успеха биржи: при отказе пик не меняется"""
        with mock.patch.object(main_module.bybit_private, "add_demo_funds", side_effect=RuntimeError("отказ")), \
             mock.patch.object(main_module.state, "add_deposit") as add_deposit, self.assertRaises(RuntimeError):
            self.run_main("fund", "41")

        add_deposit.assert_not_called()

    def test_deposit_and_bad_amounts(self) -> None:
        """`deposit` пишет сумму в пик, а ноль и отрицательные суммы отклоняются разбором аргументов"""
        with mock.patch.object(main_module.state, "add_deposit") as add_deposit:
            self.run_main("deposit", "50")

        add_deposit.assert_called_once_with(D("50"))

        for argv in (("deposit", "0"), ("deposit", "-5"), ("fund", "0"), ("fund", "-5"), ("fund", "1.5")):
            with self.subTest(argv=argv), mock.patch("sys.stderr"), self.assertRaises(SystemExit):
                self.run_main(*argv)


class SymbolsTests(unittest.TestCase):
    """Выбор контрактов для докачки"""

    def test_unknown_symbol_stops_before_download(self) -> None:
        """Неизвестное имя останавливает запуск с понятной ошибкой, имена приводятся к верхнему регистру"""
        parser = argparse.ArgumentParser(prog="purser")

        with mock.patch.object(main_module.bybit_public, "linear_perpetuals", return_value=[coin("XRPUSDT")]):
            self.assertEqual(main_module._symbols(["xrpusdt"], 0, parser), ["XRPUSDT"])

            with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
                main_module._symbols(["XRPUSDT", "FOOUSDT"], 0, parser)


class FetchTests(unittest.TestCase):
    """Докачка в папку истории"""

    def test_fetch_writes_candles_and_funding_for_each_interval(self) -> None:
        """Для каждой монеты пишутся файлы свечей по всем таймфреймам и файл финансирования"""
        candle = ["0", "1", "2", "0.5", "1.5", "10", "15"]

        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(main_module.settings, "data_dir", return_value=Path(folder)), \
             mock.patch.object(main_module.settings, "intervals", return_value=("60", "240")), \
             mock.patch.object(main_module.bybit_public, "klines", return_value=[candle]), \
             mock.patch.object(main_module.bybit_public, "funding", return_value=[["0", "0.0001"]]), \
             mock.patch("builtins.print"):
            main_module._fetch(["XRPUSDT"], 10**9)

            self.assertEqual(sorted(path.name for path in Path(folder).iterdir()),
                             ["XRPUSDT_240.csv", "XRPUSDT_60.csv", "XRPUSDT_funding.csv"])


if __name__ == "__main__":
    unittest.main()
