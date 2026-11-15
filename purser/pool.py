"""Шаг 1: пул монет для сводок, отбор по ликвидности, спреду и возрасту листинга"""

from decimal import Decimal

from purser import settings
from purser.sizing import Instrument
from purser.timeunits import DAYS_PER_YEAR, MS_PER_DAY

# Для таблицы: оборот в миллионах, доли цены в процентах
MILLION = 1_000_000
PERCENT = 100


def select(instruments: list[Instrument], now_ms: int) -> list[Instrument]:
    """Самые ликвидные контракты с историей от двух лет и узким спредом

    Args:
        instruments: Все контракты
        now_ms: Текущее время, мс

    Returns:
        Не больше `settings.pool_size()` контрактов по убыванию оборота
    """
    fit = [item for item in instruments
           if now_ms - item.launch_ms >= settings.min_history_days() * MS_PER_DAY and item.spread <= settings.max_spread()]

    return sorted(fit, key=lambda item: item.turnover_24h, reverse=True)[:settings.pool_size()]


def tradable(instrument: Instrument) -> bool:
    """Можно ли торговать монетой на маленьком депозите: шаг лота не дороже `settings.max_step_usdt()`

    Args:
        instrument: Контракт

    Returns:
        True, если шаг лота в USDT не больше `settings.max_step_usdt()`
    """
    return instrument.qty_step * instrument.price <= settings.max_step_usdt()


def report(pool: list[Instrument], now_ms: int) -> str:
    """Таблица пула: оборот, спред, текущее финансирование, возраст и пометка «можно торговать»

    Args:
        pool: Отобранные контракты
        now_ms: Текущее время, мс

    Returns:
        Текст таблицы для печати
    """
    lines = [f"{'Контракт':<16}{'Оборот 24ч, млн':>16}{'Спред, %':>10}{'Финанс., %':>12}{'Лет':>6}{'Шаг, USDT':>11}{'Торговать':>11}"]

    for item in pool:
        lines.append(f"{item.symbol:<16}{item.turnover_24h / MILLION:>16.0f}{item.spread * PERCENT:>10.4f}{item.funding_rate * PERCENT:>12.4f}"
                     f"{Decimal(now_ms - item.launch_ms) / MS_PER_DAY / DAYS_PER_YEAR:>6.1f}{item.qty_step * item.price:>11.2f}"
                     f"{'да' if tradable(item) else 'нет':>11}")

    return "\n".join(lines)
