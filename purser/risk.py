"""Риск-слой: проверка сделки по лимитам из `limits.py`, чистые функции без сети

Ордер уходит на биржу только при пустом списке нарушений. Интерфейс, сигналы и LLM эту проверку не обходят.
"""

from decimal import Decimal

from purser import limits
from purser.side_enum import SideEnum
from purser.timeunits import MS_PER_DAY

# Дней в неделе и номер дня недели у начала отсчёта времени: 1970-01-01 был четверг, считая понедельник нулевым
DAYS_PER_WEEK = 7
EPOCH_WEEKDAY = 3


def day_start(now_ms: int) -> int:
    """Начало текущих суток по UTC

    Args:
        now_ms: Текущее время, мс

    Returns:
        Полночь UTC, мс
    """
    return now_ms - now_ms % MS_PER_DAY


def week_start(now_ms: int) -> int:
    """Начало текущей недели по UTC

    Args:
        now_ms: Текущее время, мс

    Returns:
        Полночь понедельника UTC, мс
    """
    days = now_ms // MS_PER_DAY

    return (days - (days + EPOCH_WEEKDAY) % DAYS_PER_WEEK) * MS_PER_DAY


def violations(*, killed: bool,
               equity: Decimal,
               peak: Decimal,
               stops_today: int,
               stops_week: int,
               open_positions: int,
               side: SideEnum,
               entry: Decimal,
               stop: Decimal,
               target: Decimal | None,
               qty: Decimal,
               leverage: Decimal) -> list[str]:
    """Нарушения лимитов и правил для новой сделки

    Args:
        killed: Включён ли kill-switch
        equity: Депозит сейчас, USDT
        peak: Наибольший депозит за всё время без учёта пополнений, USDT
        stops_today: Сработавших стопов за текущие сутки
        stops_week: Сработавших стопов за текущую неделю
        open_positions: Открытых позиций: каждая может закончиться стопом, поэтому считается как будущий стоп
        side: Направление
        entry: Расчётная цена входа
        stop: Цена стопа
        target: Цена цели, None без цели
        qty: Количество контракта
        leverage: Потолок плеча, которое стоит на бирже

    Returns:
        Причины отказа текстом, пустой список, если сделку можно отправлять
    """
    found = []
    distance = (entry - stop) * side.sign()

    if killed:
        found.append("включён kill-switch: новые сделки запрещены, пока владелец не удалит файл-флаг")

    if equity <= peak * (1 - limits.PAUSE_DRAWDOWN):
        found.append(f"просадка от пика {peak} USDT достигла {limits.PAUSE_DRAWDOWN:.0%}: торговля встаёт, разбор сделок")

    if stops_today + open_positions >= limits.DAILY_STOPS:
        found.append(f"дневной лимит: стопов {stops_today} и открытых позиций {open_positions} при лимите {limits.DAILY_STOPS}, новых сделок нет")

    if stops_week + open_positions >= limits.WEEKLY_STOPS:
        found.append(f"недельный лимит: стопов {stops_week} и открытых позиций {open_positions} при лимите {limits.WEEKLY_STOPS}, новых сделок нет")

    if qty <= 0:
        found.append("позиция меньше минимума биржи или стоп равен цене входа")

    if distance <= 0:
        found.append("стоп стоит не с той стороны от цены входа")
    elif distance * limits.LIQUIDATION_DISTANCE_PER_STOP > entry / leverage:
        # shortcut: расстояние до ликвидации взято как цена / плечо, без поддерживающей маржи; после входа сверка берёт цену с биржи
        found.append("стоп дальше трети расстояния до цены ликвидации")

    if distance > 0 and qty * distance > equity * limits.RISK_PER_TRADE:
        found.append(f"убыток по стопу {qty * distance:.2f} USDT больше {limits.RISK_PER_TRADE:.1%} депозита")

    if qty * entry > equity * leverage:
        found.append(f"позиция больше потолка плеча {leverage}x")

    if target is not None and distance > 0 and (target - entry) * side.sign() < distance * limits.MIN_REWARD_TO_RISK:
        found.append(f"цель ближе {limits.MIN_REWARD_TO_RISK} расстояний до стопа")

    return found
