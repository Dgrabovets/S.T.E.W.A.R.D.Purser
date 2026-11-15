"""Тесты проверки качества истории"""

import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path

from purser import quality
from purser.timeunits import MS_PER_MINUTE

HOUR = 60 * MS_PER_MINUTE
MAX_MOVE = D("0.25")


def candle(index: int, open_: str = "10", high: str = "11", low: str = "9", close: str = "10", volume: str = "5") -> list[str]:
    """Часовая свеча по номеру

    Args:
        index: Номер часа от начала
        open_: Цена открытия
        high: Максимум
        low: Минимум
        close: Цена закрытия
        volume: Объём

    Returns:
        Строка в формате файла свечей
    """
    return [str(index * HOUR), open_, high, low, close, volume, "50"]


class KlinesTests(unittest.TestCase):
    """Проверка свечей"""

    def test_clean_series_has_no_findings(self) -> None:
        """Ровный ряд без аномалий не даёт находок"""
        self.assertEqual(quality.check_klines([candle(index) for index in range(5)], 60, MAX_MOVE), [])

    def test_gap_duplicate_and_order(self) -> None:
        """Пропуск считается в свечах, дубль и обратный порядок отмечаются"""
        gap = quality.check_klines([candle(0), candle(3)], 60, MAX_MOVE)
        duplicate = quality.check_klines([candle(0), candle(0)], 60, MAX_MOVE)
        reverse = quality.check_klines([candle(2), candle(1)], 60, MAX_MOVE)

        self.assertIn("пропущено свечей 2", gap[0])
        self.assertIn("дубль или нарушен порядок", duplicate[0])
        self.assertIn("дубль или нарушен порядок", reverse[0])

    def test_contradicting_prices_and_zero_volume(self) -> None:
        """Цена вне [low, high], low выше high, нулевая цена и нулевой объём отмечаются"""
        rows = [candle(0, close="12"), candle(1, high="8", low="9"), candle(2, low="0"), candle(3, volume="0")]
        findings = quality.check_klines(rows, 60, MAX_MOVE)

        self.assertEqual(sum("противоречат" in finding for finding in findings), 3)
        self.assertEqual(sum("нулевой объём" in finding for finding in findings), 1)

    def test_big_move_inside_candle_and_between_candles(self) -> None:
        """Резкое движение ловится по размаху свечи и по разрыву с прошлой свечой"""
        wide = quality.check_klines([candle(0, high="14", low="9")], 60, MAX_MOVE)  # размах 50%
        jump = quality.check_klines([candle(0), candle(1, open_="15", high="16", low="14", close="15")], 60, MAX_MOVE)

        self.assertIn("резкое движение 50%", wide[0])
        self.assertIn("резкое движение 50%", jump[0])


class FundingAndStaleTests(unittest.TestCase):
    """Проверка финансирования и обрыва данных"""

    def test_funding_gap_longer_than_longest_period(self) -> None:
        """Пропуск дольше 8 часов отмечается, обычные периоды 1, 4 и 8 часов нет"""
        ok = [[str(hours * HOUR), "0.0001"] for hours in (0, 1, 5, 13)]
        broken = [["0", "0.0001"], [str(9 * HOUR), "0.0001"], [str(9 * HOUR), "0.0001"]]

        self.assertEqual(quality.check_funding(ok), [])
        self.assertEqual(len(quality.check_funding(broken)), 2)

    def test_stale_data(self) -> None:
        """Последняя свеча старше двух периодов считается обрывом данных"""
        self.assertEqual(quality.check_stale(0, 60, 2 * HOUR), [])
        self.assertIn("данные закончились", quality.check_stale(0, 60, 3 * HOUR)[0])


class ReportTests(unittest.TestCase):
    """Отчёт по папке истории"""

    def test_report_covers_missing_empty_and_clean_files(self) -> None:
        """Нет файла, пустой файл и чистый файл описываются по-разному, лишние находки сворачиваются"""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            header = "time_ms,open,high,low,close,volume,turnover\n"
            (root / "AAAUSDT_60.csv").write_text(header, encoding="utf-8")
            (root / "BBBUSDT_60.csv").write_text(header + "\n".join(",".join(candle(index)) for index in range(3)) + "\n", encoding="utf-8")
            (root / "CCCUSDT_60.csv").write_text(header + "\n".join(",".join(candle(index * 2)) for index in range(10)) + "\n", encoding="utf-8")

            self.assertEqual(quality.stored_symbols(root), ["AAAUSDT", "BBBUSDT", "CCCUSDT"])

            text = quality.report(root, ["AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"], ("60",), MAX_MOVE, 3 * HOUR)

        self.assertIn("AAAUSDT_60.csv: строк 0, находок 1\n    файл пуст", text)
        self.assertIn("BBBUSDT_60.csv: строк 3, находок 0, порядок", text)
        self.assertIn("... и ещё", text)
        self.assertIn("DDDUSDT_60.csv: файла нет", text)
        self.assertIn("AAAUSDT_funding.csv: файла нет", text)


if __name__ == "__main__":
    unittest.main()
