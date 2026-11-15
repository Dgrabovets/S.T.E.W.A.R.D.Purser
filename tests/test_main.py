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
