"""Тесты шага 1: загрузка истории, запись в CSV и отбор пула, без сети"""

import io
import os
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest import mock

from purser import bybit_public, pool, settings, store
from purser.sizing import Instrument
from purser.timeunits import MS_PER_DAY

HOUR = 3_600_000
NOW = 5_000 * MS_PER_DAY


def coin(symbol: str, turnover: str, **overrides: object) -> Instrument:
    """Контракт по цене 1 USDT с шагом лота 1

    Args:
        symbol: Название контракта
        turnover: Оборот за 24 часа, USDT
        overrides: Поля `Instrument`, которые надо заменить

    Returns:
        Контракт для теста
    """
    return Instrument(symbol, price=D("1"), min_qty=D("1"), qty_step=D("1"), min_notional=D("5"), turnover_24h=D(turnover), **overrides)


class KlinesTests(unittest.TestCase):
    """Загрузка свечей и финансирования постранично"""

    def test_pages_are_joined_ascending_and_open_candle_is_dropped(self) -> None:
        """Страницы склеиваются по возрастанию, незакрытая свеча отбрасывается"""

        def fake_get(path: str, **params: str) -> dict:
            """Подмена запроса: свечи окна от новых к старым, как у биржи

            Args:
                path: Путь метода API, тут не используется
                params: Параметры запроса, нужны `start` и `end`

            Returns:
                Ответ с полем `list`
            """
            times = range(int(params["start"]), int(params["end"]) + 1, HOUR)

            return {"list": [[str(time), "1", "2", "0.5", "1.5", "10", "15"] for time in reversed(times)]}

        # 1500 полных часов и ещё полчаса: последняя свеча не закрыта
        end = 1500 * HOUR + HOUR // 2
        with mock.patch.object(bybit_public, "_get", fake_get):
            rows = bybit_public.klines("XRPUSDT", "60", 0, end)

        times = [int(row[0]) for row in rows]
        self.assertEqual(times, list(range(0, 1500 * HOUR, HOUR)))

    def test_klines_start_is_not_aligned_to_candle_grid(self) -> None:
        """Докачка с начала `last + 1` не теряет и не дублирует свечи"""
        def fake_get(path: str, **params: str) -> dict:
            """Подмена запроса: свечи окна от новых к старым, сетка сдвинута на 1 мс

            Args:
                path: Путь метода API, тут не используется
                params: Параметры запроса, нужны `start` и `end`

            Returns:
                Ответ с полем `list`
            """
            times = range(1, 2000 * HOUR, HOUR)
            fit = [time for time in times if int(params["start"]) <= time <= int(params["end"])]

            return {"list": [[str(time), "1", "2", "0.5", "1.5", "10", "15"] for time in reversed(fit)]}

        with mock.patch.object(bybit_public, "_get", fake_get):
            rows = bybit_public.klines("XRPUSDT", "60", 1, 1500 * HOUR + 1)

        self.assertEqual([int(row[0]) for row in rows], list(range(1, 1500 * HOUR, HOUR)))

    def test_funding_stops_when_exchange_does_not_move_back(self) -> None:
        """Если биржа снова отдаёт те же 200 ставок, загрузка заканчивается, а не зацикливается"""
        page = [{"fundingRateTimestamp": str(1_000 + index), "fundingRate": "0.0001"} for index in range(200, 0, -1)]

        with mock.patch.object(bybit_public, "_get", return_value={"list": page}) as get:
            rows = bybit_public.funding("XRPUSDT", 0, 10**6)

        self.assertEqual(len(rows), 400)
        self.assertEqual(get.call_count, 2)

    def test_funding_pages_go_back_until_short_page(self) -> None:
        """Финансирование читается страницами назад, пока страница не станет короткой"""
        stamps = list(range(0, 450 * HOUR, HOUR))

        def fake_get(path: str, **params: str) -> dict:
            """Подмена запроса: страница не больше 200 ставок от новых к старым

            Args:
                path: Путь метода API, тут не используется
                params: Параметры запроса, нужны `startTime` и `endTime`

            Returns:
                Ответ с полем `list`
            """
            fit = [stamp for stamp in stamps if int(params["startTime"]) <= stamp <= int(params["endTime"])]

            return {"list": [{"fundingRateTimestamp": str(stamp), "fundingRate": "0.0001"} for stamp in reversed(fit)][:200]}

        with mock.patch.object(bybit_public, "_get", fake_get):
            rows = bybit_public.funding("XRPUSDT", 0, 10**12)

        self.assertEqual([int(row[0]) for row in rows], stamps)


class RetryTests(unittest.TestCase):
    """Повтор запроса при временной ошибке"""

    def test_get_returns_result_after_temporary_failure(self) -> None:
        """Первая попытка с ошибкой сети, вторая успешна: результат возвращается"""
        good = mock.MagicMock()
        good.__enter__.return_value = io.BytesIO(b'{"retCode": 0, "result": {"list": []}}')

        with mock.patch.object(bybit_public, "urlopen", side_effect=[OSError("сеть"), good]), \
             mock.patch.object(bybit_public.time, "sleep"), mock.patch.dict(os.environ, {"PURSER_ATTEMPTS": "3"}):
            self.assertEqual(bybit_public._get("/v5/market/kline"), {"list": []})

    def test_get_raises_exchange_error_after_all_attempts(self) -> None:
        """Ошибка биржи (`retCode` не 0) на всех попытках поднимается как RuntimeError"""
        bad = mock.MagicMock()
        bad.__enter__.side_effect = lambda: io.BytesIO(b'{"retCode": 10001, "retMsg": "bad"}')

        with mock.patch.object(bybit_public, "urlopen", return_value=bad), \
             mock.patch.object(bybit_public.time, "sleep"), mock.patch.dict(os.environ, {"PURSER_ATTEMPTS": "2"}):
            with self.assertRaisesRegex(RuntimeError, "bad"):
                bybit_public._get("/v5/market/kline")

    def test_get_does_not_retry_params_error(self) -> None:
        """Ошибка параметров (нет такого символа) не повторяется: она не исправится"""
        bad = mock.MagicMock()
        bad.__enter__.side_effect = lambda: io.BytesIO(b'{"retCode": 10001, "retMsg": "Symbol Is Invalid"}')

        with mock.patch.object(bybit_public, "urlopen", return_value=bad) as urlopen, \
             mock.patch.object(bybit_public.time, "sleep") as sleep, mock.patch.dict(os.environ, {"PURSER_ATTEMPTS": "3"}):
            with self.assertRaisesRegex(RuntimeError, "10001 Symbol Is Invalid"):
                bybit_public._get("/v5/market/kline")

        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()

    def test_get_retries_temporary_failure_then_raises(self) -> None:
        """Запрос пробуется `settings.attempts()` раз, потом исключение уходит наверх"""
        with mock.patch.object(bybit_public, "urlopen", side_effect=OSError("сеть")) as urlopen, \
             mock.patch.object(bybit_public.time, "sleep"), mock.patch.dict(os.environ, {"PURSER_ATTEMPTS": "3"}):
            with self.assertRaises(OSError):
                bybit_public._get("/v5/market/kline")

        self.assertEqual(urlopen.call_count, 3)


class StoreTests(unittest.TestCase):
    """Запись истории в CSV"""

    def test_empty_and_header_only_files_are_filled_from_scratch(self) -> None:
        """Пустой файл получает заголовок, файл с одним заголовком качается с `since`"""
        def fetch(start: int, end: int) -> list[list[str]]:
            """Подмена загрузчика: две строки, начиная с `start`

            Args:
                start: Начало периода, мс
                end: Конец периода, не используется

            Returns:
                Две строки
            """
            return [[str(start), "1"], [str(start + 1), "1"]]

        with tempfile.TemporaryDirectory() as folder:
            empty, header_only = Path(folder) / "empty.csv", Path(folder) / "header.csv"
            empty.write_text("")
            header_only.write_text("time_ms,rate\n")

            store.sync(empty, store.FUNDING_HEADER, fetch, since=10, now=99)
            store.sync(header_only, store.FUNDING_HEADER, fetch, since=10, now=99)

            self.assertEqual(empty.read_text().split(), ["time_ms,rate", "10,1", "11,1"])
            self.assertEqual(header_only.read_text().split(), ["time_ms,rate", "10,1", "11,1"])

    def test_corrupted_last_row_raises_clear_error(self) -> None:
        """Оборванная последняя строка даёт понятную ошибку, а не молчаливую порчу истории"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "x.csv"
            path.write_text("time_ms,rate\n100,1\nабв\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "повреждена"):
                store.sync(path, store.FUNDING_HEADER, lambda start, end: [], since=0, now=1)

    def test_row_with_missing_columns_is_reported_even_if_time_is_readable(self) -> None:
        """Строка, оборванная после первого столбца, считается повреждённой, хотя время в ней читается"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "x.csv"
            path.write_text("time_ms,rate\n100,1\n200\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "повреждена"):
                store.sync(path, store.FUNDING_HEADER, lambda start, end: [], since=0, now=1)

    def test_second_sync_appends_only_new_rows(self) -> None:
        """Повторный запуск дописывает только новые строки"""
        history = [[str(time), "1"] for time in (100, 200, 300)]

        def fetch(start: int, end: int) -> list[list[str]]:
            """Подмена загрузчика, которая нарочно игнорирует `start`: дубли должен отсечь sync

            Args:
                start: Начало периода, не используется
                end: Конец периода, мс

            Returns:
                Строки со временем не позже `end`
            """
            return [row for row in history if int(row[0]) <= end]

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sub" / "x.csv"

            self.assertEqual(store.sync(path, store.FUNDING_HEADER, fetch, since=0, now=200), 2)
            self.assertEqual(store.sync(path, store.FUNDING_HEADER, fetch, since=0, now=300), 1)
            self.assertEqual(store.sync(path, store.FUNDING_HEADER, fetch, since=0, now=300), 0)
            self.assertEqual(path.read_text().split(), ["time_ms,rate", "100,1", "200,1", "300,1"])


class PoolTests(unittest.TestCase):
    """Отбор пула и пометка «можно торговать»"""

    def setUp(self) -> None:
        """Фиксирует пороги пула, чтобы `.env` владельца не менял результат теста"""
        patcher = mock.patch.dict(os.environ, {"PURSER_POOL_SIZE": "10", "PURSER_MIN_HISTORY_DAYS": "730",
                                               "PURSER_MAX_SPREAD": "0.0005", "PURSER_MAX_STEP_USDT": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_select_drops_young_and_wide_spread_then_sorts_by_turnover(self) -> None:
        """Молодые и с широким спредом отсеиваются, остальные по убыванию оборота"""
        young = coin("NEWUSDT", "900", launch_ms=NOW - 100 * MS_PER_DAY)
        wide = coin("WIDEUSDT", "800", spread=D("0.002"))
        small, big = coin("SMALLUSDT", "10"), coin("BIGUSDT", "500")

        self.assertEqual(pool.select([young, wide, small, big], NOW), [big, small])

    def test_select_keeps_at_most_pool_size(self) -> None:
        """В пуле не больше `settings.pool_size()` контрактов"""
        coins = [coin(f"C{index}USDT", str(index)) for index in range(settings.pool_size() + 5)]

        self.assertEqual(len(pool.select(coins, NOW)), settings.pool_size())

    def test_report_marks_tradable_coins(self) -> None:
        """Таблица пула помечает «да» монету с мелким шагом и «нет» с крупным"""
        eth = Instrument("ETHUSDT", price=D("1800"), min_qty=D("0.01"), qty_step=D("0.01"), min_notional=D("5"),
                         launch_ms=NOW - 3 * 365 * MS_PER_DAY)

        lines = pool.report([eth, coin("XRPUSDT", "1")], NOW).splitlines()

        self.assertTrue(lines[1].rstrip().endswith("нет"))
        self.assertTrue(lines[2].rstrip().endswith("да"))

    def test_tradable_by_lot_step_cost(self) -> None:
        """Торговать можно, если шаг лота не дороже 1 USDT"""
        eth = Instrument("ETHUSDT", price=D("1800"), min_qty=D("0.01"), qty_step=D("0.01"), min_notional=D("5"))

        self.assertFalse(pool.tradable(eth))  # шаг 18 USDT
        self.assertTrue(pool.tradable(coin("XRPUSDT", "1")))  # шаг 1 USDT


if __name__ == "__main__":
    unittest.main()
