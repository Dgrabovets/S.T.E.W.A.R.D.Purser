"""Шаг 0: на каких монетах утверждённый риск исполним при данном депозите"""

from decimal import Decimal

from purser import limits
from purser.sizing import Instrument, min_order_qty


def report(instruments: list[Instrument], equity: Decimal, top: int) -> str:
    """Таблица по самым ликвидным контрактам: минимальный ордер и проходит ли он под потолок позиции

    Args:
        instruments: Все контракты
        equity: Депозит, USDT
        top: Сколько самых ликвидных показать

    Returns:
        Текст таблицы для печати
    """
    risk_usdt = equity * limits.RISK_PER_TRADE
    lines = [f"Депозит {equity} USDT, риск на сделку {risk_usdt:.2f} USDT ({limits.RISK_PER_TRADE:.1%})",
             f"Потолок позиции: {equity * limits.LEVERAGE_START:.2f} USDT без плеча, "
             f"{equity * limits.LEVERAGE_CAP:.2f} USDT при {limits.LEVERAGE_CAP}x",
             "",
             f"{'Контракт':<16}{'Оборот 24ч, млн':>16}{'Мин. ордер, USDT':>18}{'Шаг, USDT':>12}{'1x':>5}{f'{limits.LEVERAGE_CAP}x':>7}"]

    for instrument in sorted(instruments, key=lambda item: item.turnover_24h, reverse=True)[:top]:
        min_order = min_order_qty(instrument) * instrument.price
        fits = ["да" if min_order <= equity * leverage else "нет" for leverage in (limits.LEVERAGE_START, limits.LEVERAGE_CAP)]
        lines.append(f"{instrument.symbol:<16}{instrument.turnover_24h / 1_000_000:>16.0f}{min_order:>18.2f}"
                     f"{instrument.qty_step * instrument.price:>12.2f}{fits[0]:>5}{fits[1]:>7}")

    return "\n".join(lines)
