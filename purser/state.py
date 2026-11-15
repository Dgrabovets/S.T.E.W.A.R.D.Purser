"""Состояние между запусками: пик депозита и флаг kill-switch, файлы в отдельной папке состояния"""

from decimal import Decimal
from pathlib import Path

from purser import settings

# Имена файлов состояния
PEAK_FILE = "equity_peak.txt"
KILL_FILE = "KILL"


def kill_path() -> Path:
    """Файл-флаг kill-switch: пока он есть, новые сделки запрещены

    Returns:
        Путь к файлу, снимает запрет владелец, удаляя файл руками
    """
    return settings.state_dir() / KILL_FILE


def is_killed() -> bool:
    """Включён ли kill-switch

    Returns:
        True, если файл-флаг существует
    """
    return kill_path().exists()


def set_killed(reason: str) -> None:
    """Включает kill-switch

    Args:
        reason: Причина, записывается в файл-флаг
    """
    kill_path().parent.mkdir(parents=True, exist_ok=True)
    kill_path().write_text(reason + "\n", encoding="utf-8")


def _read_peak() -> Decimal:
    """Сохранённый пик депозита

    Returns:
        Пик, USDT, или 0, если файла ещё нет
    """
    path = settings.state_dir() / PEAK_FILE

    return Decimal(path.read_text(encoding="utf-8").strip()) if path.exists() else Decimal(0)


def _write_peak(peak: Decimal) -> None:
    """Сохраняет пик депозита

    Args:
        peak: Пик, USDT
    """
    path = settings.state_dir() / PEAK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{peak}\n", encoding="utf-8")


def update_peak(equity: Decimal) -> Decimal:
    """Запоминает наибольший депозит за всё время

    Args:
        equity: Депозит сейчас, USDT

    Returns:
        Пик с учётом текущего значения
    """
    peak = max(equity, _read_peak())
    _write_peak(peak)

    return peak


def add_deposit(amount: Decimal) -> None:
    """Учитывает пополнение: пик растёт на сумму, чтобы пополнение не маскировало просадку

    Args:
        amount: Сколько USDT добавлено на счёт
    """
    _write_peak(_read_peak() + amount)
