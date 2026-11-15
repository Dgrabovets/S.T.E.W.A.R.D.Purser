"""История в CSV: файл только дописывается, повторный запуск докачивает новое"""

import csv
from collections.abc import Callable
from pathlib import Path

# Столбцы файла свечей и файла финансирования
KLINE_HEADER = ["time_ms", "open", "high", "low", "close", "volume", "turnover"]
FUNDING_HEADER = ["time_ms", "rate"]


def _last_time(path: Path) -> int | None:
    """Время последней сохранённой строки

    Args:
        path: CSV-файл истории

    Returns:
        Время в мс или None, если файла нет или в нём только заголовок

    Raises:
        ValueError: Последняя строка не начинается с числа, например запись оборвалась
    """
    if not path.exists():
        return None

    with path.open(newline="", encoding="utf-8") as file:
        rows = [row for row in csv.reader(file) if row]

    try:
        return int(rows[-1][0]) if len(rows) > 1 else None
    except ValueError:
        raise ValueError(f"{path}: последняя строка повреждена, удалите её и запустите докачку снова") from None


def sync(path: Path, header: list[str], fetch: Callable[[int, int], list[list[str]]], since: int, now: int) -> int:
    """Дописывает в файл строки новее последней сохранённой

    Args:
        path: CSV-файл, создаётся вместе с папкой
        header: Заголовок для нового файла
        fetch: Загрузчик `fetch(start, end)`: строки по возрастанию времени, время в первом столбце
        since: Откуда качать, если файла ещё нет, мс
        now: Конец периода, мс

    Returns:
        Сколько строк добавлено
    """
    last = _last_time(path)
    start = since if last is None else last + 1
    rows = [row for row in fetch(start, now) if int(row[0]) >= start]
    is_new = not path.exists() or path.stat().st_size == 0
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, lineterminator="\n")
        if is_new:
            writer.writerow(header)

        writer.writerows(rows)

    return len(rows)
