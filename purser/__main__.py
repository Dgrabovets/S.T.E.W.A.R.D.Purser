"""Командная строка: осуществимость, пул монет, история и исполнение на демо-счёте

Данные: `python -m purser feasibility --equity 40.63`, `python -m purser pool`, `python -m purser fetch`, `python -m purser check`
Демо-счёт: `python -m purser account`, `python -m purser order XRPUSDT long --stop 2.10`, `python -m purser kill`,
`python -m purser fund 41`, `python -m purser deposit 50`
"""

import argparse
import sys
import time
from decimal import Decimal, InvalidOperation

from purser import bybit_private, bybit_public, execution, feasibility, pool, quality, settings, state, store
from purser.side_enum import SideEnum
from purser.sizing import Instrument
from purser.timeunits import MS_PER_DAY, MS_PER_SECOND

# Сколько контрактов показывает отчёт осуществимости, если не указано `--top`
DEFAULT_TOP = 30


def _positive(text: str) -> Decimal:
    """Положительное конечное число из командной строки

    Args:
        text: Значение аргумента

    Returns:
        Число больше нуля

    Raises:
        argparse.ArgumentTypeError: Не число, NaN, бесконечность, ноль или отрицательное
    """
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"нужно число, получено «{text}»") from None

    if not value.is_finite() or value <= 0:
        raise argparse.ArgumentTypeError(f"нужно положительное число, получено «{text}»")

    return value


def _positive_int(text: str) -> int:
    """Положительное целое из командной строки

    Args:
        text: Значение аргумента

    Returns:
        Число больше нуля

    Raises:
        argparse.ArgumentTypeError: Не целое число или не больше нуля
    """
    if not text.isdigit() or int(text) <= 0:
        raise argparse.ArgumentTypeError(f"нужно целое число больше нуля, получено «{text}»")

    return int(text)


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

    audit = commands.add_parser("check", help="проверить качество скачанной истории: пропуски, дубли, аномальные свечи, обрыв данных")
    audit.add_argument("symbols", nargs="*", help="контракты; по умолчанию все, что есть в папке истории")

    fetch = commands.add_parser("fetch", help="докачать свечи и финансирование в папку истории")
    fetch.add_argument("symbols", nargs="*", help="контракты; по умолчанию текущий пул")

    commands.add_parser("account", help="состояние демо-счёта: депозит, позиции, счётчики стопов, kill-switch")

    order = commands.add_parser("order", help="открыть позицию на демо-счёте рыночным ордером со стопом")
    order.add_argument("symbol", help="контракт, например XRPUSDT")
    order.add_argument("side", choices=[side.name.lower() for side in SideEnum], help="направление")
    order.add_argument("--stop", type=_positive, required=True, help="цена стопа, без неё ордер не отправляется")
    order.add_argument("--target", type=_positive, help="цена цели, не ближе двух расстояний до стопа")

    deposit = commands.add_parser("deposit", help="записать пополнение счёта: пик депозита растёт на сумму; "
                         "запускайте до account и order, иначе пик возьмёт новый депозит и сумма учтётся дважды")
    deposit.add_argument("amount", type=_positive, help="сколько USDT добавлено на счёт")

    fund = commands.add_parser("fund", help="пополнить демо-счёт виртуальными USDT и записать пополнение")
    fund.add_argument("amount", type=_positive_int, help="сколько USDT добавить, только целое число, например 41")

    stop_all = commands.add_parser("kill", help="kill-switch: запретить новые сделки, закрыть все позиции и только потом снять ордера на демо-счёте")
    stop_all.add_argument("--reason", default="остановлено владельцем", help="причина, пишется в файл-флаг")

    # кодировка до разбора аргументов: иначе сообщение об ошибке на кириллице искажается в консоли Windows
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    args = parser.parse_args()
    now = int(time.time() * MS_PER_SECOND)

    if args.command == "feasibility":
        print(feasibility.report(bybit_public.linear_perpetuals(), args.equity, args.top))
    elif args.command == "check":
        print(quality.report(settings.data_dir(), [symbol.upper() for symbol in args.symbols] or quality.stored_symbols(settings.data_dir()),
                             settings.intervals(), settings.max_move(), now))
    elif args.command == "pool":
        print(pool.report(_current_pool(now), now))
    elif args.command == "account":
        print(execution.account(now))
    elif args.command == "order":
        if not execution.open_position(args.symbol.upper(), SideEnum[args.side.upper()], args.stop, args.target, now, input):
            sys.exit(1)
    elif args.command == "deposit":
        state.add_deposit(args.amount)
        print(f"Пополнение {args.amount} USDT учтено в пике депозита")
    elif args.command == "fund":
        bybit_private.add_demo_funds(args.amount)
        state.add_deposit(Decimal(args.amount))
        print(execution.account(now))
    elif args.command == "kill":
        execution.kill(args.reason)
    else:
        _fetch(_symbols(args.symbols, now, parser), now)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, *bybit_private.TRANSPORT_ERRORS) as exception:
        sys.exit(f"Ошибка: {exception}")
