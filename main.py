"""
AI Office — точка входа.

Режимы запуска:
  python main.py                      # все 6 агентов (AGENT_NAME не задан / "all")
  AGENT_NAME=all python main.py       # то же самое
  AGENT_NAME=marta python main.py     # только Марта (env-var)
  python main.py --agent marta        # только Марта (CLI-флаг)
  python main.py --agent marta --webhook  # Марта на вебхуке (Railway individual)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
from contextlib import suppress

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

# Реестр: ключ → (класс агента, webhook-суффикс)
AGENTS: dict[str, tuple] = {
    "marta":  (MartaAgent,  "marta"),
    "kevin":  (KevinAgent,  "kevin"),
    "kasper": (KasperAgent, "kasper"),
    "peter":  (PeterAgent,  "peter"),
    "elina":  (ElinaAgent,  "elina"),
    "alex":   (AlexAgent,   "alex"),
}


# ────────────────────────────────────────────────────────────────────────────
#  Многоагентный режим — все в одном event loop
# ────────────────────────────────────────────────────────────────────────────

async def run_all_async() -> None:
    """Запустить всех 6 агентов параллельно в одном asyncio event loop.

    Алгоритм (PTB 22.x non-blocking API):
      1. build_app()               — регистрация handlers
      2. app.initialize()          — открывает HTTP-сессию
      3. app.start()               — запускает update-processor
      4. app.updater.start_polling()  — фоновый polling (non-blocking, возвращает сразу)
      5. asyncio.Event().wait()    — держим event loop живым до SIGTERM/Ctrl+C
      6. stop_async()              — graceful shutdown всех агентов
    """
    # Инициализируем PostgreSQL (создаёт таблицу tasks если не существует)
    from db import init_db, close_db
    await init_db()

    agents = []
    for key, (agent_cls, _) in AGENTS.items():
        try:
            agents.append(agent_cls())
        except Exception as e:
            logger.error(f"Не удалось создать агента {key}: {e}")

    if not agents:
        raise RuntimeError("Ни один агент не был создан — проверь токены в .env")

    # Запускаем всех агентов
    started = []
    for agent in agents:
        try:
            await agent.start_polling_async()
            started.append(agent)
        except Exception as e:
            logger.error(f"[{agent.name}] Ошибка запуска: {e}")

    if not started:
        raise RuntimeError("Ни один агент не запустился")

    names = ", ".join(a.name for a in started)
    logger.info(f"✓ Запущено {len(started)}/{len(agents)} агентов: {names}")

    # Держим event loop живым до сигнала остановки
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    try:
        # Linux / Railway: нативные signal handlers
        loop.add_signal_handler(signal.SIGTERM, stop_event.set)
        loop.add_signal_handler(signal.SIGINT,  stop_event.set)
        logger.info("Signal handlers зарегистрированы (SIGTERM, SIGINT)")
    except (NotImplementedError, OSError):
        # Windows: loop.add_signal_handler не поддерживается.
        # Ctrl+C поднимет KeyboardInterrupt → asyncio.run() прервёт корутину.
        logger.info("Windows: signal handlers не поддерживаются, используй Ctrl+C")

    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass

    # Graceful shutdown
    logger.info("Получен сигнал остановки — завершаем агентов...")
    for agent in reversed(started):
        await agent.stop_async()
    logger.info("Все агенты остановлены.")

    await close_db()


# ────────────────────────────────────────────────────────────────────────────
#  Одноагентный режим — блокирующий (совместимость с run_polling/run_webhook)
# ────────────────────────────────────────────────────────────────────────────

def run_single_polling(agent_key: str) -> None:
    agent_cls, _ = AGENTS[agent_key]
    logger.info(f"Запуск агента «{agent_key}» | mode=polling")
    agent_cls().run_polling()


def run_single_webhook(agent_key: str) -> None:
    agent_cls, suffix = AGENTS[agent_key]
    logger.info(f"Запуск агента «{agent_key}» | mode=webhook")
    agent_cls().run_webhook(suffix)


# ────────────────────────────────────────────────────────────────────────────
#  CLI
# ────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AI Office — команда агентов в Telegram",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python main.py                        # все 6 агентов
  python main.py --agent marta          # только Марта
  python main.py --agent marta --webhook  # Марта на вебхуке
  AGENT_NAME=kevin python main.py       # Кевин через env
""",
    )
    parser.add_argument(
        "--agent",
        type=str,
        default=None,
        choices=list(AGENTS) + ["all"],
        metavar="NAME",
        help=f"Агент: {list(AGENTS)} | all (по умолчанию: all)",
    )
    parser.add_argument(
        "--webhook",
        action="store_true",
        help="Запустить в режиме webhook (Railway individual service)",
    )
    return parser.parse_args()


def main() -> None:
    config.validate()
    args = parse_args()

    # Приоритет: CLI --agent > AGENT_NAME env > "all"
    agent_key = args.agent or os.getenv("AGENT_NAME", "all") or "all"

    if agent_key == "all":
        # ── Многоагентный режим ──────────────────────────────────────────
        logger.info("Режим: ВСЕ агенты в одном процессе (asyncio)")
        try:
            asyncio.run(run_all_async())
        except KeyboardInterrupt:
            logger.info("Остановлено пользователем (Ctrl+C)")

    else:
        # ── Одноагентный режим ───────────────────────────────────────────
        if agent_key not in AGENTS:
            raise SystemExit(
                f"Неизвестный агент: '{agent_key}'. "
                f"Доступны: {list(AGENTS) + ['all']}"
            )
        if args.webhook:
            run_single_webhook(agent_key)
        else:
            run_single_polling(agent_key)


if __name__ == "__main__":
    main()
