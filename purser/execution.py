"""Шаг 2: тонкое исполнение на демо-счёте: один ордер со стопом, сверка позиции, kill-switch

Порядок всегда один: расчёт размера, проверка риск-слоем, подтверждение владельца, повторная проверка, отправка, сверка с биржей.
"""

import time
import uuid
from collections.abc import Callable
from decimal import Decimal

from purser import bybit_private, bybit_public, limits, risk, settings, state
from purser.plan import Plan
from purser.position import Position
from purser.side_enum import SideEnum
from purser.sizing import position_qty
from purser.timeunits import MS_PER_SECOND

# Свой номер ордера: биржа принимает до 36 символов
LINK_ID_PREFIX = "purser-"
LINK_ID_RANDOM_LENGTH = 24


def _stops(now_ms: int) -> tuple[int, int]:
    """Сработавшие стоп-ордера за текущие сутки и неделю по UTC

    Args:
        now_ms: Текущее время, мс

    Returns:
        Число за сутки и число за неделю
    """
    hits = bybit_private.stop_hits(risk.week_start(now_ms))

    return sum(moment >= risk.day_start(now_ms) for moment in hits), len(hits)


def _describe(position: Position) -> str:
    """Позиция одной строкой для отчёта

    Args:
        position: Позиция

    Returns:
        Контракт, направление, размер, вход, стоп и цена ликвидации
    """
    return (f"{position.symbol} {position.side.name} {position.size} по {position.entry}, "
            f"стоп {position.stop or 'НЕТ'}, ликвидация {position.liquidation or 'нет данных'}")


def account(now_ms: int) -> str:
    """Состояние демо-счёта: депозит, пик, счётчики стопов, kill-switch и позиции

    Args:
        now_ms: Текущее время, мс

    Returns:
        Текст отчёта
    """
    equity = bybit_private.usdt_equity()
    today, week = _stops(now_ms)
    lines = [f"Депозит {equity} USDT, пик {state.update_peak(equity)} USDT",
             f"Сработавших стопов: сегодня {today} из {limits.DAILY_STOPS}, за неделю {week} из {limits.WEEKLY_STOPS}",
             f"Kill-switch: {'ВКЛЮЧЁН' if state.is_killed() else 'выключен'}",
             "Позиции:"]
    lines += [f"    {_describe(position)}" for position in bybit_private.open_positions()] or ["    нет"]

    return "\n".join(lines)


def _plan(symbol: str, side: SideEnum, stop: Decimal, target: Decimal | None, now_ms: int) -> Plan:
    """Считает размер и проверяет сделку риск-слоем по свежим данным биржи

    Args:
        symbol: Контракт
        side: Направление
        stop: Цена стопа
        target: Цена цели, None без цели
        now_ms: Текущее время, мс

    Returns:
        План с причинами отказа, если они есть

    Raises:
        ValueError: Нет такого торгуемого контракта
    """
    instrument = next((item for item in bybit_public.linear_perpetuals() if item.symbol == symbol), None)
    if instrument is None:
        raise ValueError(f"нет такого торгуемого бессрочного USDT-контракта: {symbol}")

    equity = bybit_private.usdt_equity()
    today, week = _stops(now_ms)
    positions = bybit_private.open_positions()
    # запас на проскальзывание и комиссии: вход считается хуже рыночной цены, поэтому размер меньше и риск с запасом
    entry = instrument.price * (1 + settings.slippage_reserve() * side.sign())
    qty = position_qty(instrument, equity, limits.RISK_PER_TRADE, limits.LEVERAGE_START, entry, stop)
    found = risk.violations(killed=state.is_killed(), equity=equity, peak=state.update_peak(equity), stops_today=today, stops_week=week,
                            open_positions=len(positions), side=side, entry=entry, stop=stop, target=target, qty=qty,
                            leverage=limits.LEVERAGE_CAP)

    if any(position.symbol == symbol for position in positions):
        found.append(f"по {symbol} уже есть открытая позиция")

    return Plan(symbol, side, instrument.price, entry, equity, qty, stop, target, found)


def _close_and_raise(position: Position, reason: str) -> None:
    """Закрывает позицию, которая не прошла сверку, и сообщает об этом ошибкой

    Args:
        position: Позиция
        reason: Что не так

    Raises:
        RuntimeError: Всегда: позиция закрыта или, если закрыть не вышло, оставлена с просьбой закрыть вручную
    """
    try:
        bybit_private.close(position)
    except (RuntimeError, *bybit_private.TRANSPORT_ERRORS) as exception:
        raise RuntimeError(f"{reason}. ЗАКРЫТЬ НЕ УДАЛОСЬ ({exception}): закройте {position.symbol} вручную на бирже") from exception

    raise RuntimeError(f"{reason}. Позиция закрыта")


def _reconcile(plan: Plan) -> None:
    """Сверяет позицию на бирже с планом: стоп, фактический риск, цена ликвидации

    Args:
        plan: Отправленный план

    Raises:
        RuntimeError: Позиции нет, она без стопа или нарушает лимиты: тогда она закрыта
    """
    for _ in range(settings.reconcile_attempts()):
        found = bybit_private.open_positions(plan.symbol)
        if found:
            break

        time.sleep(settings.reconcile_pause_seconds())
    else:
        raise RuntimeError(f"ордер по {plan.symbol} отправлен, но позиции на бирже нет: проверьте счёт вручную")

    position = found[0]
    if position.stop is None:
        _close_and_raise(position, f"позиция по {plan.symbol} оказалась без стопа на бирже")

    print(f"Открыто: {_describe(position)}")

    if position.size != plan.qty:
        print(f"ВНИМАНИЕ: размер на бирже {position.size}, отправлено {plan.qty}")

    if position.stop != plan.stop:
        print(f"ВНИМАНИЕ: стоп на бирже {position.stop}, отправлен {plan.stop}")

    loss = position.size * abs(position.entry - position.stop)
    if loss > plan.equity * limits.RISK_PER_TRADE:
        _close_and_raise(position, f"по цене входа с биржи убыток по стопу {loss:.2f} USDT больше {limits.RISK_PER_TRADE:.1%} депозита")

    if (position.liquidation is not None
            and abs(position.entry - position.stop) * limits.LIQUIDATION_DISTANCE_PER_STOP > abs(position.entry - position.liquidation)):
        _close_and_raise(position, "по цене ликвидации с биржи стоп дальше трети расстояния до ликвидации")


def open_position(symbol: str, side: SideEnum, stop: Decimal, target: Decimal | None, now_ms: int, confirm: Callable[[str], str]) -> bool:
    """Открывает позицию рыночным ордером со стопом после проверки лимитов и подтверждения

    Args:
        symbol: Контракт
        side: Направление
        stop: Цена стопа
        target: Цена цели, None без цели
        now_ms: Текущее время, мс
        confirm: Вопрос владельцу: получает текст, возвращает ответ

    Returns:
        True, если ордер отправлен и позиция сверена, False при отказе риск-слоя или владельца

    Raises:
        ValueError: Нет такого торгуемого контракта
        RuntimeError: Биржа отклонила запрос, ответ на ордер потерян или сверка не сошлась
    """
    plan = _plan(symbol, side, stop, target, now_ms)
    loss = plan.qty * abs(plan.entry - stop)

    print(f"План: {symbol} {side.name}, рыночный ордер, цена около {plan.price}, расчётный вход с запасом {plan.entry:.6f}")
    print(f"    количество {plan.qty}, стоимость {plan.qty * plan.entry:.2f} USDT при депозите {plan.equity} USDT")
    print(f"    стоп {stop}, цель {target or 'нет'}, убыток по стопу {loss:.2f} USDT ({loss / plan.equity if plan.equity else 0:.2%} депозита)")

    if plan.violations:
        print("Отказ риск-слоя:")
        print("\n".join(f"    {reason}" for reason in plan.violations))

        return False

    if confirm(f"Для отправки на демо-счёт введите {symbol}: ").strip().upper() != symbol:
        print("Отменено, ордер не отправлен")

        return False

    # пока владелец думал, цена, депозит и флаги могли измениться: ордер уходит только если свежий план не хуже показанного
    fresh = _plan(symbol, side, stop, target, int(time.time() * MS_PER_SECOND))
    if fresh.violations or fresh.qty < plan.qty:
        print("План изменился, пока шло подтверждение, ордер не отправлен. Причины:")
        print("\n".join(f"    {reason}" for reason in fresh.violations) or f"    размер по свежей цене {fresh.qty} меньше показанного {plan.qty}")

        return False

    bybit_private.ensure_isolated_margin()
    # плечо на бирже по потолку, а размер посчитан по стартовому: так под маржу и комиссию остаётся запас
    bybit_private.set_leverage(symbol, limits.LEVERAGE_CAP)
    link_id = f"{LINK_ID_PREFIX}{uuid.uuid4().hex[:LINK_ID_RANDOM_LENGTH]}"

    try:
        bybit_private.place_order(symbol, side, plan.qty, stop, target, link_id)
    except bybit_private.TRANSPORT_ERRORS as exception:
        # ответ не пришёл: ордер мог дойти. Второй раз не шлём, а спрашиваем биржу по своему номеру
        try:
            reached = bybit_private.order_exists(symbol, link_id)
        except (RuntimeError, *bybit_private.TRANSPORT_ERRORS):
            raise RuntimeError(f"ответ на ордер по {symbol} не пришёл и проверить его не удалось: "
                               "ордер мог уйти, проверьте счёт вручную") from exception

        if not reached:
            raise

    _reconcile(plan)

    return True


def _wait_flat() -> list[Position]:
    """Ждёт, пока позиции закроются, и возвращает оставшиеся

    Returns:
        Позиции, которые не закрылись за отведённое число проверок
    """
    for _ in range(settings.reconcile_attempts()):
        left = bybit_private.open_positions()
        if not left:
            break

        time.sleep(settings.reconcile_pause_seconds())

    return left


def kill(reason: str) -> None:
    """Kill-switch: запрещает новые сделки, закрывает все позиции и только потом снимает ордера

    Args:
        reason: Причина, записывается в файл-флаг

    Raises:
        RuntimeError: Какие-то позиции не закрылись: ордера со стопами тогда не снимаются
    """
    # флаг первым: даже если биржа недоступна, новые сделки уже запрещены
    state.set_killed(reason)
    errors = []

    # одна неудача не останавливает остальные позиции
    for position in bybit_private.open_positions():
        try:
            bybit_private.close(position)
            print(f"Отправлено закрытие: {_describe(position)}")
        except (RuntimeError, *bybit_private.TRANSPORT_ERRORS) as exception:
            errors.append(f"{position.symbol}: {exception}")

    left = _wait_flat()
    if left:
        # отмена ордеров снимает и стопы: пока позиция открыта, их трогать нельзя
        raise RuntimeError(f"не закрылись позиции {', '.join(position.symbol for position in left)}, ордера со стопами НЕ сняты. "
                           f"Закройте вручную. Ошибки: {'; '.join(errors) or 'нет'}")

    bybit_private.cancel_all_orders()
    print(f"Kill-switch включён. Чтобы снять запрет, удалите файл {state.kill_path()}")
