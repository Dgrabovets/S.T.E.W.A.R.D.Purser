"""Шаг 1.3: проверка качества скачанной истории: пропуски, порядок, дубли, аномальные свечи, обрыв данных"""

import csv
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from purser.timeunits import MS_PER_MINUTE

# Сколько находок одного файла показывать в отчёте, остальные считаются числом
SHOWN_FINDINGS = 5

# Самый длинный период финансирования у Bybit, часы: дольше между ставками значит пропуск
MAX_FUNDING_PERIOD_HOURS = 8

# Свеча старше стольких своих периодов считается «данные закончились»: недокачано или контракт снят с торгов
STALE_CANDLES = 2

# Минут в часе, для перевода часов финансирования в мс
MINUTES_PER_HOUR = 60


def _when(time_ms: int) -> str:
    """Время для отчёта

    Args:
        time_ms: Время, мс

    Returns:
        Дата и час по UTC
    """
    return datetime.fromtimestamp(time_ms / 1000, UTC).strftime("%Y-%m-%d %H:%M")


def read_rows(path: Path) -> list[list[str]]:
    """Строки CSV без заголовка

    Args:
        path: Файл истории

    Returns:
        Непустые строки как есть, значения строками
    """
    with path.open(newline="", encoding="utf-8") as file:
        return [row for row in csv.reader(file) if row][1:]


def check_klines(rows: list[list[str]], interval_minutes: int, max_move: Decimal) -> list[str]:
    """Проверяет свечи

    Args:
        rows: Строки: время мс, open, high, low, close, объём, оборот
        interval_minutes: Размер свечи в минутах
        max_move: Наибольшее правдоподобное движение цены за свечу (размах свечи или разрыв с прошлой), доля от цены

    Returns:
        Находки текстом, пустой список, если всё в порядке
    """
    findings, step, previous = [], interval_minutes * MS_PER_MINUTE, None

    for row in rows:
        time_ms = int(row[0])
        open_, high, low, close, volume = (Decimal(value) for value in row[1:6])

        if previous is not None:
            gap = time_ms - previous[0]
            if gap <= 0 or gap % step:
                findings.append(f"{_when(time_ms)}: дубль или нарушен порядок или сетка времени")
            elif gap > step:
                findings.append(f"{_when(previous[0])}: пропущено свечей {gap // step - 1}")

        if min(open_, high, low, close) <= 0 or low > high or not low <= open_ <= high or not low <= close <= high:
            findings.append(f"{_when(time_ms)}: цены противоречат друг другу (open {open_}, high {high}, low {low}, close {close})")
        else:
            move = max((high - low) / open_, abs(open_ / previous[1] - 1) if previous else Decimal(0))
            if move > max_move:
                findings.append(f"{_when(time_ms)}: резкое движение {move:.0%}")

        if volume == 0:
            findings.append(f"{_when(time_ms)}: нулевой объём")

        previous = (time_ms, close)

    return findings


def check_funding(rows: list[list[str]]) -> list[str]:
    """Проверяет ставки финансирования

    Args:
        rows: Строки: время мс, ставка

    Returns:
        Находки текстом: дубли, нарушенный порядок, пропуски длиннее самого длинного периода
    """
    findings, longest = [], MAX_FUNDING_PERIOD_HOURS * MINUTES_PER_HOUR * MS_PER_MINUTE

    for before, after in zip(rows, rows[1:]):
        gap = int(after[0]) - int(before[0])

        if gap <= 0:
            findings.append(f"{_when(int(after[0]))}: дубль или нарушен порядок")
        elif gap > longest:
            findings.append(f"{_when(int(before[0]))}: пропуск {gap // (MINUTES_PER_HOUR * MS_PER_MINUTE)} ч")

    return findings


def check_stale(last_ms: int, interval_minutes: int, now_ms: int) -> list[str]:
    """Проверяет, что данные доходят до сегодняшнего дня

    Args:
        last_ms: Время открытия последней свечи, мс
        interval_minutes: Размер свечи в минутах
        now_ms: Текущее время, мс

    Returns:
        Находка, если последняя свеча старше `STALE_CANDLES` периодов, иначе пусто
    """
    if now_ms - last_ms <= STALE_CANDLES * interval_minutes * MS_PER_MINUTE:
        return []

    return [f"данные закончились {_when(last_ms)}: недокачано или контракт снят с торгов"]


def _describe(path: Path, check: Callable[[list[list[str]]], list[str]]) -> list[str]:
    """Строки отчёта по одному файлу

    Args:
        path: Файл истории
        check: Проверка строк файла, вызывается только для непустого файла

    Returns:
        Строка итога и первые находки
    """
    if not path.exists():
        return [f"{path.name}: файла нет, запустите fetch"]

    rows = read_rows(path)
    findings = check(rows) if rows else ["файл пуст"]
    lines = [f"{path.name}: строк {len(rows)}, находок {len(findings)}" + ("" if findings else ", порядок")]
    lines += [f"    {finding}" for finding in findings[:SHOWN_FINDINGS]]
    if len(findings) > SHOWN_FINDINGS:
        lines.append(f"    ... и ещё {len(findings) - SHOWN_FINDINGS}")

    return lines


def stored_symbols(folder: Path) -> list[str]:
    """Контракты, по которым в папке есть история

    Args:
        folder: Папка с историей

    Returns:
        Названия по алфавиту, пусто, если папки нет
    """
    return sorted({path.name.split("_")[0] for path in folder.glob("*_*.csv")})


def report(folder: Path, symbols: list[str], intervals: tuple[str, ...], max_move: Decimal, now_ms: int) -> str:
    """Отчёт по файлам истории

    Args:
        folder: Папка с историей
        symbols: Контракты
        intervals: Размеры свечей в минутах строками
        max_move: Порог резкого движения, доля от цены
        now_ms: Текущее время, мс

    Returns:
        Текст отчёта: по файлу строка итога и первые находки
    """
    lines = []

    for symbol in symbols:
        for interval in intervals:
            lines += _describe(folder / f"{symbol}_{interval}.csv",
                               lambda rows, minutes=int(interval): (check_klines(rows, minutes, max_move)
                                                                    + check_stale(int(rows[-1][0]), minutes, now_ms)))

        lines += _describe(folder / f"{symbol}_funding.csv", check_funding)

    return "\n".join(lines)
