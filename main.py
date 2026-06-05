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
from tools.notion import update_status_page
from task_queue import get_active_tasks, get_recent_tasks
from agents import (
    MartaAgent,
    KevinAgent,
    KasperAgent,
    PeterAgent,
    ElinaAgent,
    AlexAgent,
    DanAgent,
    EvaAgent,
    MaxAgent,
)

# Реестр: ключ → (класс агента, webhook-суффикс)
AGENTS: dict[str, tuple] = {
    "marta":  (MartaAgent,  "marta"),
    "kevin":  (KevinAgent,  "kevin"),
    "kasper": (KasperAgent, "kasper"),
    "peter":  (PeterAgent,  "peter"),
    "elina":  (ElinaAgent,  "elina"),
    "alex":   (AlexAgent,   "alex"),
    "dan":    (DanAgent,    "dan"),
    "eva":    (EvaAgent,    "eva"),
    "max":    (MaxAgent,    "max"),
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

    async def _status_page_loop():
        redis = getattr(started[0], "_redis", None) if started else None
        while True:
            try:
                active = await get_active_tasks()
                recent = await get_recent_tasks(limit=20)
                await update_status_page(redis, active, recent)
            except Exception as e:
                logger.warning(f"[status_loop] error: {e}")
            await asyncio.sleep(60)

    status_task = asyncio.create_task(_status_page_loop())
    logger.info("[main] Фоновая задача обновления статуса запущена")

    # Ищем Еву среди запущенных агентов для scheduled digest
    eva_agent = next((a for a in started if isinstance(a, EvaAgent)), None)

    async def _scheduled_digest_loop():
        """Каждый день в 06:30 UTC (09:30 МСК) запускает дайджест для всех пользователей."""
        from datetime import datetime, timezone, timedelta
        from db import get_distinct_digest_users
        while True:
            try:
                now = datetime.now(timezone.utc)
                target = now.replace(hour=6, minute=30, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                wait_seconds = (target - now).total_seconds()
                logger.info(f"[digest_scheduler] следующий запуск через {wait_seconds/3600:.1f}ч ({target.isoformat()})")
                await asyncio.sleep(wait_seconds)

                if eva_agent is None or not eva_agent._telethon_ready:
                    logger.warning("[digest_scheduler] Ева не готова, пропускаем")
                    continue

                users = await get_distinct_digest_users()
                logger.info(f"[digest_scheduler] запуск дайджеста для {len(users)} пользователей")
                for user_chat_id in users:
                    try:
                        await eva_agent.run_digest(user_chat_id, since=None)
                    except Exception as e:
                        logger.error(f"[digest_scheduler] user={user_chat_id} error: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[digest_scheduler] ошибка: {e}")
                await asyncio.sleep(60)

    digest_task = asyncio.create_task(_scheduled_digest_loop())
    logger.info("[main] Scheduled digest task запущен (каждый день 06:30 UTC)")

    max_agent = next((a for a in started if isinstance(a, MaxAgent)), None)

    async def _scheduled_reviews_loop():
        """Запускает обработку отзывов в 06:00, 11:00, 17:00 UTC."""
        from datetime import datetime, timezone, timedelta
        from db import get_all_active_shops

        _FIRE_HOURS = {6, 11, 17}

        while True:
            try:
                now = datetime.now(timezone.utc)
                # Ближайший час из _FIRE_HOURS
                candidates = []
                for h in _FIRE_HOURS:
                    t = now.replace(hour=h, minute=0, second=0, microsecond=0)
                    if t <= now:
                        t += timedelta(days=1)
                    candidates.append(t)
                target = min(candidates)
                wait_seconds = (target - now).total_seconds()
                logger.info(f"[reviews_scheduler] следующий запуск через {wait_seconds/3600:.1f}ч ({target.isoformat()})")
                await asyncio.sleep(wait_seconds)

                if max_agent is None:
                    continue

                shops = await get_all_active_shops()
                unique_chats = list({s["chat_id"] for s in shops})
                logger.info(f"[reviews_scheduler] обработка отзывов для {len(unique_chats)} пользователей")

                all_results: dict = {}
                is_morning_run = (target.hour == 6)
                for chat_id in unique_chats:
                    try:
                        r = await max_agent.process_reviews(chat_id)
                        for mp, stats in r.items():
                            agg = all_results.setdefault(mp, {"found": 0, "auto_replied": 0, "pending": 0, "errors": 0})
                            for k in agg:
                                agg[k] += stats.get(k, 0)
                    except Exception as e:
                        logger.error(f"[reviews_scheduler] chat={chat_id} error: {e}")
                    # Ежедневная сводка только в утренний прогон (06:00 UTC)
                    if is_morning_run:
                        try:
                            await max_agent.send_daily_summary(chat_id)
                        except Exception as e:
                            logger.error(f"[reviews_scheduler] send_daily_summary chat={chat_id}: {e}")

                # Сводка в группу партнёров если задана
                if config.PARTNERS_GROUP_ID:
                    from zoneinfo import ZoneInfo
                    msk_now = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m %H:%M МСК")
                    _EMOJI = {"wb": "🟣 Wildberries", "ozon": "🔵 Ozon"}
                    total_found = sum(s["found"] for s in all_results.values())
                    if total_found == 0:
                        summary = f"📊 Сводка отзывов — {msk_now}\n\n✅ Новых отзывов нет."
                    else:
                        lines = [f"📊 Сводка отзывов — {msk_now}\n"]
                        for mp, s in all_results.items():
                            label = _EMOJI.get(mp, mp)
                            lines.append(f"{label}: найдено {s['found']}")
                            if s["found"]:
                                if s["auto_replied"]:
                                    lines.append(f"  └ автоответ: {s['auto_replied']}")
                                if s["pending"]:
                                    lines.append(f"  └ ожидают: {s['pending']}")
                        summary = "\n".join(lines)
                    try:
                        await max_agent.app.bot.send_message(
                            chat_id=config.PARTNERS_GROUP_ID,
                            text=summary,
                        )
                    except Exception as e:
                        logger.warning(f"[reviews_scheduler] group summary error: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[reviews_scheduler] ошибка: {e}")
                await asyncio.sleep(60)

    reviews_task = asyncio.create_task(_scheduled_reviews_loop())

    async def _negative_reviews_loop():
        """Каждые 15 минут проверяет негативные отзывы (1-2★)."""
        from db import get_all_active_shops

        while True:
            try:
                now = datetime.now(timezone.utc)
                # Следующий момент кратный 15 минутам
                minutes_to_next = 15 - (now.minute % 15)
                wait_seconds = minutes_to_next * 60 - now.second
                await asyncio.sleep(wait_seconds)

                if max_agent is None:
                    continue

                shops = await get_all_active_shops()
                unique_chats = list({s["chat_id"] for s in shops})
                for chat_id in unique_chats:
                    try:
                        await max_agent.check_negative_reviews(chat_id)
                    except Exception as e:
                        logger.error(f"[neg_scheduler] chat={chat_id} error: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[neg_scheduler] ошибка: {e}")
                await asyncio.sleep(60)

    negative_task = asyncio.create_task(_negative_reviews_loop())
    logger.info("[main] Negative reviews task запущен (каждые 15 минут)")
    logger.info("[main] Scheduled reviews task запущен (06:00, 11:00, 17:00 UTC)")

    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass

    # Graceful shutdown
    for task in (status_task, digest_task, reviews_task, negative_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

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
