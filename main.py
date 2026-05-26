"""
AI Office — точка входа.

Запуск:
  python main.py                     # polling (разработка, один агент за раз)
  python main.py --agent marta       # только Марта, polling
  python main.py --webhook --agent marta   # Railway / production
"""

from __future__ import annotations

import argparse
import sys

from loguru import logger

from config import config
from agents import (
    MartaAgent,
    KevinAgent,
    KasperAgent,
    PeterAgent,
    ElinaAgent,
    AlexAgent,
)

# Реестр агентов: ключ → (класс, суффикс вебхука)
# PTB 22.x: run_polling() / run_webhook() — синхронные, управляют event loop сами.
# Запускать несколько агентов в одном процессе через asyncio.gather() нельзя.
# На Railway каждый агент — отдельный сервис (отдельный процесс).
AGENTS: dict[str, tuple] = {
    "marta":  (MartaAgent,  "marta"),
    "kevin":  (KevinAgent,  "kevin"),
    "kasper": (KasperAgent, "kasper"),
    "peter":  (PeterAgent,  "peter"),
    "elina":  (ElinaAgent,  "elina"),
    "alex":   (AlexAgent,   "alex"),
}


def run_polling(agent_key: str) -> None:
    if agent_key not in AGENTS:
        raise SystemExit(f"Неизвестный агент: {agent_key}. Доступны: {list(AGENTS)}")
    agent_cls, _ = AGENTS[agent_key]
    agent_cls().run_polling()


def run_webhook(agent_key: str) -> None:
    if agent_key not in AGENTS:
        raise SystemExit(f"Неизвестный агент: {agent_key}. Доступны: {list(AGENTS)}")
    agent_cls, suffix = AGENTS[agent_key]
    agent_cls().run_webhook(suffix)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI Office — команда агентов в Telegram")
    parser.add_argument(
        "--webhook", action="store_true",
        help="Запустить в режиме webhook (для Railway)"
    )
    parser.add_argument(
        "--agent", type=str, default="marta",
        choices=list(AGENTS),
        help="Имя агента для запуска (по умолчанию: marta)"
    )
    return parser.parse_args()


def main() -> None:
    config.validate()
    args = parse_args()

    logger.info(f"Запуск агента «{args.agent}» | webhook={args.webhook}")

    if args.webhook:
        run_webhook(args.agent)
    else:
        run_polling(args.agent)


if __name__ == "__main__":
    main()
