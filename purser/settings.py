"""Настройки, которые можно менять без правки кода: окружение и файл `.env` в корне проекта

Реальное окружение сильнее файла. Лимиты риска сюда не входят: они живут только в `limits.py`.
"""

import os
from decimal import Decimal, InvalidOperation
from pathlib import Path

from purser import limits

# Корень проекта: рядом лежит `.env`, от него считается относительный путь папки истории
ROOT = Path(__file__).resolve().parent.parent

# Файл настроек создаёт владелец, список переменных в README
ENV_FILE = ROOT / ".env"

# Кавычки, в которые можно взять значение в `.env`
QUOTES = "\"'"


def load_env_file(path: Path) -> None:
    """Добавляет в окружение пары `KEY=VALUE` из файла, не перезаписывая уже заданные

    Args:
        path: Файл настроек, если его нет, ничего не происходит
    """
    if not path.exists():
        return

    # utf-8-sig: Блокнот Windows ставит в начало файла невидимую метку, она портит имя первой переменной
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        name, value = line.removeprefix("export ").split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in QUOTES:
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()  # комментарий в конце строки, у значения в кавычках он часть значения

        os.environ.setdefault(name.strip(), value)


def _text(name: str, default: str) -> str:
    """Значение настройки строкой

    Args:
        name: Имя переменной окружения
        default: Значение, если переменная не задана или пуста

    Returns:
        Значение без пробелов по краям
    """
    return os.environ.get(name, "").strip() or default


def _integer(name: str, default: str, minimum: int) -> int:
    """Целая настройка с проверкой нижней границы

    Args:
        name: Имя переменной окружения
        default: Значение по умолчанию строкой
        minimum: Наименьшее допустимое значение

    Returns:
        Число не меньше `minimum`

    Raises:
        ValueError: Не число или меньше `minimum`, в сообщении имя переменной
    """
    text = _text(name, default)
    try:
        value = int(text)
    except ValueError:
        raise ValueError(f"{name}: нужно целое число, получено «{text}»") from None

    if value < minimum:
        raise ValueError(f"{name}: не меньше {minimum}, получено {value}")

    return value


def _decimal(name: str, default: str, minimum: Decimal) -> Decimal:
    """Дробная настройка с проверкой нижней границы

    Args:
        name: Имя переменной окружения
        default: Значение по умолчанию строкой
        minimum: Наименьшее допустимое значение

    Returns:
        Число не меньше `minimum`

    Raises:
        ValueError: Не число или меньше `minimum`, в сообщении имя переменной
    """
    text = _text(name, default)
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"{name}: нужно число, получено «{text}»") from None

    if not value.is_finite() or value < minimum:  # NaN и бесконечность не годятся
        raise ValueError(f"{name}: не меньше {minimum}, получено {text}")

    return value


def base_url() -> str:
    """Адрес API Bybit: основной сайт, под демо и под отладку может быть другим

    Returns:
        Адрес без слеша в конце
    """
    return _text("PURSER_BASE_URL", "https://api.bybit.com").rstrip("/")


def trade_url() -> str:
    """Адрес API для приватных запросов: баланс, позиции, ордера

    Returns:
        Адрес без слеша в конце, по умолчанию демо-счёт
    """
    return _text("PURSER_TRADE_URL", "https://api-demo.bybit.com").rstrip("/")


def _secret(name: str) -> str:
    """Секрет из окружения, значение никуда не печатается

    Args:
        name: Имя переменной окружения

    Returns:
        Значение секрета

    Raises:
        ValueError: Переменная не задана или в значении недопустимые символы
    """
    value = _text(name, "")
    if not value:
        raise ValueError(f"{name}: не задано, впишите значение в .env")

    # само значение в сообщении не называется: оно секрет. Пробелы и служебные символы испортили бы заголовок запроса
    if not value.isascii() or not value.isprintable() or " " in value:
        raise ValueError(f"{name}: допустимы только печатные символы ASCII без пробелов")

    return value


def api_key() -> str:
    """Ключ API Bybit

    Returns:
        Ключ из `PURSER_API_KEY`
    """
    return _secret("PURSER_API_KEY")


def api_secret() -> str:
    """Секрет API Bybit

    Returns:
        Секрет из `PURSER_API_SECRET`
    """
    return _secret("PURSER_API_SECRET")


def state_dir() -> Path:
    """Папка состояния: пик депозита и флаг kill-switch, отдельно от истории, чтобы очистка истории их не трогала

    Returns:
        Путь к папке, относительный считается от корня проекта
    """
    return ROOT / _text("PURSER_STATE_DIR", "state")


def slippage_reserve() -> Decimal:
    """Запас на проскальзывание и комиссии при расчёте размера

    Returns:
        Доля от цены: 0.003 это 0,3%, вход считается хуже рыночной цены на эту долю

    Raises:
        ValueError: Запас меньше `limits.MIN_SLIPPAGE_RESERVE`: нижняя граница это лимит риска и живёт в `limits.py`
    """
    return _decimal("PURSER_SLIPPAGE_RESERVE", "0.003", limits.MIN_SLIPPAGE_RESERVE)


def reconcile_attempts() -> int:
    """Сколько раз проверять позицию на бирже после ордера или закрытия

    Returns:
        Число проверок, не меньше 1
    """
    return _integer("PURSER_RECONCILE_ATTEMPTS", "5", 1)


def reconcile_pause_seconds() -> int:
    """Пауза между проверками позиции на бирже

    Returns:
        Секунды
    """
    return _integer("PURSER_RECONCILE_PAUSE_SECONDS", "1", 0)


def attempts() -> int:
    """Сколько раз пробовать запрос к бирже при временной ошибке

    Returns:
        Число попыток, не меньше 1
    """
    return _integer("PURSER_ATTEMPTS", "3", 1)


def retry_pause_seconds() -> int:
    """Сколько секунд ждать между попытками

    Returns:
        Пауза в секундах
    """
    return _integer("PURSER_RETRY_PAUSE_SECONDS", "2", 0)


def timeout_seconds() -> int:
    """Сколько секунд ждать ответ биржи, прежде чем считать попытку неудачной

    Returns:
        Таймаут в секундах, не меньше 1
    """
    return _integer("PURSER_TIMEOUT_SECONDS", "20", 1)


def data_dir() -> Path:
    """Папка с историей свечей и финансирования

    Returns:
        Путь к папке, относительный считается от корня проекта, а не от места запуска
    """
    return ROOT / _text("PURSER_DATA_DIR", "data")


def intervals() -> tuple[str, ...]:
    """Размеры свечей, которые качаем

    Returns:
        Минуты строками: 60 это час, 240 это четыре часа

    Raises:
        ValueError: Пустой список или элемент не число
    """
    text = _text("PURSER_INTERVALS", "60,240")
    parts = tuple(part.strip() for part in text.split(","))

    if not all(part.isdigit() and int(part) > 0 for part in parts):
        raise ValueError(f"PURSER_INTERVALS: нужны минуты через запятую, например 60,240, получено «{text}»")

    return parts


def pool_size() -> int:
    """Сколько монет в пуле для сводок

    Returns:
        Число монет, не меньше 1
    """
    return _integer("PURSER_POOL_SIZE", "10", 1)


def min_history_days() -> int:
    """Моложе стольких дней монета в пул не идёт

    Returns:
        Число суток, по умолчанию два года
    """
    return _integer("PURSER_MIN_HISTORY_DAYS", "730", 1)


def history_days() -> int:
    """За сколько суток качаем историю свечей и финансирования

    Returns:
        Число суток, по умолчанию два года
    """
    return _integer("PURSER_HISTORY_DAYS", "730", 1)


def max_move() -> Decimal:
    """Наибольшее правдоподобное движение цены за одну свечу, выше проверка качества отмечает аномалию

    Returns:
        Доля от цены: 0.25 это 25%
    """
    return _decimal("PURSER_MAX_MOVE", "0.25", Decimal(0))


def max_spread() -> Decimal:
    """Наибольший спред монеты в пуле

    Returns:
        Доля от цены: 0.0005 это 0,05%, около тейкерской комиссии
    """
    return _decimal("PURSER_MAX_SPREAD", "0.0005", Decimal(0))


def max_step_usdt() -> Decimal:
    """Наибольшая стоимость шага лота, при которой монета помечается «можно торговать»

    Returns:
        Стоимость шага лота в USDT
    """
    return _decimal("PURSER_MAX_STEP_USDT", "1", Decimal(0))


load_env_file(ENV_FILE)
