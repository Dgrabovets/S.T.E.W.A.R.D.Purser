"""Тесты настроек из окружения и файла `.env`"""

import os
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest import mock

from purser import settings


class SettingsTests(unittest.TestCase):
    """Чтение настроек: значения по умолчанию, окружение и файл"""

    def test_default_when_not_set(self) -> None:
        """Без переменной берётся значение по умолчанию"""
        with mock.patch.dict(os.environ, clear=True):
            self.assertEqual(settings.attempts(), 3)
            self.assertEqual(settings.max_spread(), D("0.0005"))
            self.assertEqual(settings.intervals(), ("60", "240"))

    def test_environment_overrides_default(self) -> None:
        """Переменная окружения сильнее значения по умолчанию"""
        with mock.patch.dict(os.environ, {"PURSER_POOL_SIZE": "5", "PURSER_BASE_URL": "https://example.test/"}):
            self.assertEqual(settings.pool_size(), 5)
            self.assertEqual(settings.base_url(), "https://example.test")

    def test_file_fills_missing_but_does_not_override_environment(self) -> None:
        """Файл `.env` добавляет недостающее, но не перезаписывает заданное в окружении"""
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, {"PURSER_POOL_SIZE": "7"}, clear=True):
            path = Path(folder) / ".env"
            path.write_text("# комментарий\nPURSER_POOL_SIZE=3\nPURSER_ATTEMPTS = \"4\"\n\nмусор без равно\n", encoding="utf-8")

            settings.load_env_file(path)

            self.assertEqual(settings.pool_size(), 7)
            self.assertEqual(settings.attempts(), 4)

    def test_missing_file_is_ignored(self) -> None:
        """Нет файла: настройки остаются по умолчанию, без ошибки"""
        settings.load_env_file(Path("нет-такого-файла.env"))

    def test_invalid_value_names_the_variable(self) -> None:
        """Ошибка в значении называет переменную: буквы вместо числа, ноль попыток, пустой интервал"""
        getters = {"PURSER_ATTEMPTS": settings.attempts, "PURSER_POOL_SIZE": settings.pool_size,
                   "PURSER_MAX_SPREAD": settings.max_spread, "PURSER_INTERVALS": settings.intervals}

        for name, value in (("PURSER_ATTEMPTS", "abc"), ("PURSER_ATTEMPTS", "0"), ("PURSER_POOL_SIZE", "-3"),
                            ("PURSER_MAX_SPREAD", "NaN"), ("PURSER_INTERVALS", "60,,240")):
            with mock.patch.dict(os.environ, {name: value}), self.assertRaisesRegex(ValueError, name):
                getters[name]()

    def test_file_with_bom_and_quotes(self) -> None:
        """Метка BOM в начале файла и кавычки вокруг значения разбираются правильно"""
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, clear=True):
            path = Path(folder) / ".env"
            path.write_bytes("\ufeffPURSER_ATTEMPTS='5'\n".encode("utf-8"))

            settings.load_env_file(path)

            self.assertEqual(settings.attempts(), 5)

    def test_data_dir_is_relative_to_project_root(self) -> None:
        """Относительная папка истории считается от корня проекта, а не от места запуска"""
        with mock.patch.dict(os.environ, {"PURSER_DATA_DIR": "history"}):
            self.assertEqual(settings.data_dir(), settings.ROOT / "history")


if __name__ == "__main__":
    unittest.main()
