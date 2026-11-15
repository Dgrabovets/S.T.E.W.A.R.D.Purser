"""Командная строка: проверка осуществимости, пул монет и докачка истории

Запуск: `python -m purser feasibility --equity 40.63`, `python -m purser pool`, `python -m purser fetch`
"""

import argparse
import sys
import time
from decimal import Decimal

from purser import bybit_public, feasibility, pool, settings, store
from purser.sizing import Instrument
from purser.timeunits import MS_PER_DAY, MS_PER_SECOND

# Сколько контрактов показывает отчёт осуществимости, если не указано `--top`
DEFAULT_TOP = 30


def _current_pool(now: int) -> list[Instrument]:
    """Пул монет по текущим данным биржи

    Args:
        now: Текущее время, мс

    Returns:
        Отобранные контракты
    """
    return pool.select(bybit_public.linear_perpetuals(), now)


def _symbols(requested: list[str], now: int, parser: argparse.ArgumentParser) -> list[str]:
    """Контракты для докачки: названные явно, а без названий текущий пул

    Args:
        requested: Названия из командной строки
        now: Текущее время, мс
        parser: Разборщик аргументов, через него сообщается об ошибке

    Returns:
        Названия в верхнем регистре
    """
    if not requested:
        return [item.symbol for item in _current_pool(now)]

    symbols = [symbol.upper() for symbol in requested]
    known = {item.symbol for item in bybit_public.linear_perpetuals()}
    unknown = [symbol for symbol in symbols if symbol not in known]
    if unknown:
        parser.error(f"нет таких торгуемых бессрочных USDT-контрактов: {', '.join(unknown)}")

    return symbols


def _fetch(symbols: list[str], now: int) -> None:
    """Докачивает в папку истории свечи и финансирование и печатает, сколько строк добавлено

    Args:
        symbols: Контракты
        now: Текущее время, мс
    """
    since = now - settings.history_days() * MS_PER_DAY

    for symbol in symbols:
        for interval in settings.intervals():
            added = store.sync(settings.data_dir() / f"{symbol}_{interval}.csv", store.KLINE_HEADER,
                               lambda start, end: bybit_public.klines(symbol, interval, start, end), since, now)
            print(f"{symbol} свечи {interval} мин: +{added}")

        added = store.sync(settings.data_dir() / f"{symbol}_funding.csv", store.FUNDING_HEADER,
                           lambda start, end: bybit_public.funding(symbol, start, end), since, now)
        print(f"{symbol} финансирование: +{added}")


def main() -> None:
    """Разбирает команду из аргументов и запускает её"""
    parser = argparse.ArgumentParser(prog="purser")
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("feasibility", help="на каких монетах риск исполним при данном депозите")
    check.add_argument("--equity", type=Decimal, required=True, help="депозит в USDT")
    check.add_argument("--top", type=int, default=DEFAULT_TOP, help="сколько самых ликвидных контрактов показать")

    commands.add_parser("pool", help="пул монет для сводок")

    fetch = commands.add_parser("fetch", help="докачать свечи и финансирование в папку истории")
    fetch.add_argument("symbols", nargs="*", help="контракты; по умолчанию текущий пул")

    # кодировка до разбора аргументов: иначе сообщение об ошибке на кириллице искажается в консоли Windows
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    args = parser.parse_args()
    now = int(time.time() * MS_PER_SECOND)

    if args.command == "feasibility":
        print(feasibility.report(bybit_public.linear_perpetuals(), args.equity, args.top))
    elif args.command == "pool":
        print(pool.report(_current_pool(now), now))
    else:
        _fetch(_symbols(args.symbols, now, parser), now)


if __name__ == "__main__":
    main()
