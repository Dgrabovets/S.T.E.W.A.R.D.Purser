"""Запуск: python -m purser feasibility --equity 40.63"""

import argparse
import sys
from decimal import Decimal

from purser import bybit_public, feasibility


def main() -> None:
    parser = argparse.ArgumentParser(prog="purser")
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("feasibility", help="на каких монетах риск исполним при данном депозите")
    check.add_argument("--equity", type=Decimal, required=True, help="депозит в USDT")
    check.add_argument("--top", type=int, default=30, help="сколько самых ликвидных контрактов показать")

    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    print(feasibility.report(bybit_public.linear_perpetuals(), args.equity, args.top))


if __name__ == "__main__":
    main()
