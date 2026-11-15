"""Тесты разбора списка контрактов Bybit, без сети"""

import unittest
from decimal import Decimal as D
from unittest import mock

from purser import bybit_public


def row(symbol: str, **overrides: object) -> dict:
    """Строка инструмента как у биржи

    Args:
        symbol: Название контракта
        overrides: Поля, которые надо заменить

    Returns:
        Строка инструмента для ответа `instruments-info`
    """
    base = {"symbol": symbol, "status": "Trading", "isPreListing": False, "contractType": "LinearPerpetual", "quoteCoin": "USDT",
            "launchTime": "1000", "lotSizeFilter": {"minOrderQty": "1", "qtyStep": "1", "minNotionalValue": "5"}}

    return base | overrides


def ticker(symbol: str, **overrides: str) -> dict:
    """Строка тикера как у биржи

    Args:
        symbol: Название контракта
        overrides: Поля, которые надо заменить

    Returns:
        Строка тикера для ответа `tickers`
    """
    base = {"symbol": symbol, "lastPrice": "2", "turnover24h": "100", "bid1Price": "1.99", "ask1Price": "2.01", "fundingRate": "0.0001"}

    return base | overrides


class LinearPerpetualsTests(unittest.TestCase):
    """Разбор контрактов: фильтры, спред и постраничная загрузка"""

    def test_filters_spread_and_pagination(self) -> None:
        """Предлистинг, нулевая цена и не-USDT отбрасываются, пустой стакан даёт спред 100%, страницы склеиваются"""
        tickers = [ticker("OKUSDT"), ticker("ZEROUSDT", lastPrice="0"), ticker("NOBOOKUSDT", bid1Price="", ask1Price=""),
                   ticker("PREUSDT"), ticker("USDCUSDT"), ticker("LASTUSDT")]
        pages = [{"list": [row("OKUSDT"), row("ZEROUSDT"), row("NOBOOKUSDT")], "nextPageCursor": "next"},
                 {"list": [row("PREUSDT", isPreListing=True), row("USDCUSDT", quoteCoin="USDC"), row("LASTUSDT")], "nextPageCursor": ""}]

        def fake_get(path: str, **params: str) -> dict:
            """Подмена запроса: тикеры или страница инструментов по курсору

            Args:
                path: Путь метода API
                params: Параметры запроса, нужен `cursor`

            Returns:
                Ответ биржи
            """
            if path.endswith("tickers"):
                return {"list": tickers}

            return pages[1] if params.get("cursor") == "next" else pages[0]

        with mock.patch.object(bybit_public, "_get", fake_get):
            found = {item.symbol: item for item in bybit_public.linear_perpetuals()}

        self.assertEqual(sorted(found), ["LASTUSDT", "NOBOOKUSDT", "OKUSDT"])
        self.assertEqual(found["NOBOOKUSDT"].spread, D(1))
        self.assertEqual(found["OKUSDT"].spread, D("0.01"))

    def test_spread_is_relative_to_mid_price_and_zero_bid_is_unusable(self) -> None:
        """Спред считается к середине стакана, а нулевая заявка делает монету непригодной (спред 100%)"""
        def fake_get(path: str, **params: str) -> dict:
            """Подмена запроса: два тикера и одна страница инструментов

            Args:
                path: Путь метода API
                params: Параметры запроса, не используются

            Returns:
                Ответ биржи
            """
            if path.endswith("tickers"):
                return {"list": [ticker("WIDEUSDT", lastPrice="100", bid1Price="90", ask1Price="110"), ticker("ZEROUSDT", bid1Price="0")]}

            return {"list": [row("WIDEUSDT"), row("ZEROUSDT")], "nextPageCursor": ""}

        with mock.patch.object(bybit_public, "_get", fake_get):
            found = {item.symbol: item for item in bybit_public.linear_perpetuals()}

        self.assertEqual(found["WIDEUSDT"].spread, D("0.2"))  # 20 / 100
        self.assertEqual(found["ZEROUSDT"].spread, D(1))


if __name__ == "__main__":
    unittest.main()
