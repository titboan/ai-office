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
import hashlib
import hmac
import json as _json
import os
import queue as _thread_queue
import signal
import threading
from decimal import Decimal
from urllib.parse import parse_qsl

from aiohttp import web

from loguru import logger

from config import config
from task_queue import get_active_tasks, get_recent_tasks
from agents import (
    MartaAgent,
    KevinAgent,
    KasperAgent,
    PeterAgent,
    ElinaAgent,
    AlexAgent,
    MaxAgent,
    TinaAgent,
)

# ────────────────────────────────────────────────────────────────────────────
#  DB log sink — пишет ERROR/CRITICAL в таблицу agent_logs
# ────────────────────────────────────────────────────────────────────────────

_pending_logs: _thread_queue.Queue = _thread_queue.Queue(maxsize=500)


def _db_log_sink(message) -> None:
    """Loguru sink: кладёт ERROR+ записи в thread-safe очередь без блокировки."""
    record = message.record
    try:
        _pending_logs.put_nowait({
            "level": record["level"].name,
            "name": record["name"],
            "message": record["message"],
            "exc": str(record["exception"]) if record["exception"] else None,
        })
    except _thread_queue.Full:
        pass  # очередь переполнена — дропаем, чтобы не тормозить агентов


async def _db_log_writer_loop() -> None:
    """Asyncio-таск: дренирует очередь и пишет батчами в agent_logs каждые 5 с.
    Раз в сутки удаляет записи старше 30 дней."""
    from db import get_pool
    pool = await get_pool()
    cleanup_counter = 0
    while True:
        await asyncio.sleep(5)
        batch = []
        try:
            while True:
                batch.append(_pending_logs.get_nowait())
        except _thread_queue.Empty:
            pass
        if batch:
            try:
                await pool.executemany(
                    "INSERT INTO agent_logs (level, logger_name, message, exc_text)"
                    " VALUES ($1, $2, $3, $4)",
                    [(r["level"], r["name"], r["message"], r["exc"]) for r in batch],
                )
            except Exception as exc:
                # Намеренно не логируем через logger — рекурсия
                print(f"[agent_logs writer] ошибка записи: {exc}", flush=True)
        cleanup_counter += 1
        if cleanup_counter >= 17280:  # ~24 ч при sleep(5)
            cleanup_counter = 0
            try:
                await pool.execute(
                    "DELETE FROM agent_logs WHERE ts < NOW() - INTERVAL '30 days'"
                )
            except Exception:
                pass


# Реестр: ключ → (класс агента, webhook-суффикс)
AGENTS: dict[str, tuple] = {
    "marta":  (MartaAgent,  "marta"),
    "kevin":  (KevinAgent,  "kevin"),
    "kasper": (KasperAgent, "kasper"),
    "peter":  (PeterAgent,  "peter"),
    "elina":  (ElinaAgent,  "elina"),
    "alex":   (AlexAgent,   "alex"),
    # "dan": (DanAgent, "dan"),  # заморожен: Pollinations.ai слишком медленный, заменить на DALL-E 3 если нужно
    # "eva": (EvaAgent, "eva"),  # заморожена: TELETHON_SESSION не получена, email-дайджест не актуален
    "max":    (MaxAgent,    "max"),
    "tina":   (TinaAgent,   "tina"),
}


# ────────────────────────────────────────────────────────────────────────────
#  Scheduling helpers
# ────────────────────────────────────────────────────────────────────────────

def _unique_chats(shops: list[dict]) -> list[int]:
    return list({s["chat_id"] for s in shops})


def _days_until_next_monday(now, hour: int) -> int:
    d = (7 - now.weekday()) % 7
    if d == 0 and now.hour >= hour:
        d = 7
    return d


# ────────────────────────────────────────────────────────────────────────────
#  Dashboard API helpers
# ────────────────────────────────────────────────────────────────────────────

def _validate_init_data(init_data: str, bot_token: str) -> dict | None:
    """Validate Telegram WebApp initData with HMAC-SHA256. Returns parsed dict or None."""
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None
    return parsed


def _to_json_safe(obj):
    """Recursively convert asyncpg/Decimal/date objects to JSON-serializable types."""
    if isinstance(obj, dict):
        return {k: _to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_json_safe(v) for v in obj]
    if isinstance(obj, Decimal):
        return float(obj)
    if hasattr(obj, "isoformat"):  # date / datetime
        return obj.isoformat()
    return obj


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

    # Подключаем DB-логгер: ERROR/CRITICAL → таблица agent_logs
    logger.add(_db_log_sink, level="ERROR", format="{message}", enqueue=False)
    asyncio.create_task(_db_log_writer_loop())
    logger.info("[main] DB log sink подключён — ошибки пишутся в agent_logs")

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

    status_task = asyncio.create_task(asyncio.sleep(0))  # placeholder для graceful shutdown

    # Ева заморожена — digest loop отключён до получения TELETHON_SESSION

    max_agent   = next((a for a in started if isinstance(a, MaxAgent)), None)
    peter_agent = next((a for a in started if a.__class__.__name__ == "PeterAgent"), None)
    marta_agent = next((a for a in started if isinstance(a, MartaAgent)), None)
    if max_agent is not None and peter_agent is not None:
        max_agent._peter_agent = peter_agent
    if marta_agent is not None and max_agent is not None:
        marta_agent._max_agent = max_agent

    async def _scheduled_reviews_loop():
        """Обработка всех отзывов каждые 15 минут."""
        from db import get_all_active_shops

        _INTERVAL = 15 * 60

        while True:
            try:
                await asyncio.sleep(_INTERVAL)

                if max_agent is None:
                    continue

                shops = await get_all_active_shops()
                unique_chats = _unique_chats(shops)
                for chat_id in unique_chats:
                    try:
                        await max_agent.process_reviews(chat_id)
                    except Exception as e:
                        logger.error(f"[reviews_scheduler] chat={chat_id} error: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[reviews_scheduler] ошибка: {e}")
                await asyncio.sleep(60)

    reviews_task = asyncio.create_task(_scheduled_reviews_loop())

    async def _scheduled_adv_sync_loop():
        """Синхронизация рекламной статистики WB + Ozon раз в сутки в 06:00 UTC."""
        from datetime import datetime, timezone, timedelta
        from db import get_all_active_shops

        while True:
            try:
                now = datetime.now(timezone.utc)
                # Следующий запуск в 03:00 UTC (06:00 МСК) — до утренней сводки
                target = now.replace(hour=3, minute=0, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                wait_seconds = (target - now).total_seconds()
                logger.info(f"[adv_sync] следующий запуск через {wait_seconds/3600:.1f} ч")
                await asyncio.sleep(wait_seconds)

                if max_agent is None:
                    continue

                shops = await get_all_active_shops()
                unique_chats = _unique_chats(shops)

                for chat_id in unique_chats:
                    try:
                        await max_agent.sync_ad_stats(chat_id)
                        logger.info(f"[adv_sync] chat_id={chat_id} завершено")
                        await max_agent._check_drr_alerts(chat_id)
                    except Exception as e:
                        logger.error(f"[adv_sync] chat_id={chat_id} ошибка: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[adv_sync] критическая ошибка: {e}")
                await asyncio.sleep(60)

    asyncio.create_task(_scheduled_adv_sync_loop())

    async def _scheduled_fin_sync_loop():
        """Еженедельный финотчёт — воскресенье 01:30 UTC (04:30 МСК)."""
        from datetime import datetime, timezone, timedelta
        from db import get_all_active_shops

        while True:
            try:
                now = datetime.now(timezone.utc)
                # weekday(): 0=пн … 6=вс
                days_until_sunday = (6 - now.weekday()) % 7
                if days_until_sunday == 0 and (now.hour > 1 or (now.hour == 1 and now.minute >= 30)):
                    days_until_sunday = 7
                target = (now + timedelta(days=days_until_sunday)).replace(
                    hour=1, minute=30, second=0, microsecond=0
                )
                wait_seconds = (target - now).total_seconds()
                logger.info(f"[fin_sync] следующий запуск через {wait_seconds/3600:.1f} ч (вс 01:30 UTC)")
                await asyncio.sleep(wait_seconds)

                if max_agent is None:
                    continue

                shops = await get_all_active_shops()
                unique_chats = _unique_chats(shops)

                for chat_id in unique_chats:
                    try:
                        await max_agent.sync_financial_report(chat_id, days=90)
                        logger.info(f"[fin_sync] chat_id={chat_id} завершено")
                    except Exception as e:
                        logger.error(f"[fin_sync] chat_id={chat_id} ошибка: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[fin_sync] критическая ошибка: {e}")
                await asyncio.sleep(60)

    asyncio.create_task(_scheduled_fin_sync_loop())

    async def _scheduled_questions_loop():
        """Мониторинг вопросов покупателей WB + Ozon каждые 15 минут."""
        from db import get_all_active_shops

        _INTERVAL = 15 * 60  # 15 минут

        while True:
            try:
                await asyncio.sleep(_INTERVAL)

                if max_agent is None:
                    continue

                shops = await get_all_active_shops()
                unique_chats = _unique_chats(shops)
                logger.info(f"[questions_scheduler] проверка вопросов для {len(unique_chats)} пользователей")

                for chat_id in unique_chats:
                    try:
                        results = await max_agent.process_questions(chat_id)
                        found = sum(s.get("found", 0) for s in results.values())
                        if found:
                            logger.info(f"[questions_scheduler] chat={chat_id}: {found} новых вопросов")
                    except Exception as e:
                        logger.error(f"[questions_scheduler] chat={chat_id} error: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[questions_scheduler] ошибка: {e}")
                await asyncio.sleep(60)

    asyncio.create_task(_scheduled_questions_loop())

    async def _scheduled_orders_sync_loop():
        """Синхронизация заказов, продаж и остатков WB + Ozon каждый час."""
        from db import get_all_active_shops

        _INTERVAL = 60 * 60  # 1 час

        while True:
            try:
                await asyncio.sleep(_INTERVAL)

                if max_agent is None:
                    continue

                shops = await get_all_active_shops()
                unique_chats = _unique_chats(shops)
                logger.info(f"[orders_sync] синхронизация для {len(unique_chats)} пользователей")

                for chat_id in unique_chats:
                    try:
                        await max_agent.sync_marketplace_data(chat_id)
                        logger.info(f"[orders_sync] chat_id={chat_id} завершено")
                    except Exception as e:
                        logger.error(f"[orders_sync] chat_id={chat_id} ошибка: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[orders_sync] критическая ошибка: {e}")
                await asyncio.sleep(60)

    asyncio.create_task(_scheduled_orders_sync_loop())

    async def _weekly_audit_loop():
        """Еженедельный аудит магазина — понедельник 07:00 UTC (10:00 МСК)."""
        from datetime import datetime, timezone, timedelta
        from db import get_all_active_shops

        while True:
            try:
                now = datetime.now(timezone.utc)
                # Следующий понедельник 07:00 UTC
                days_until_monday = _days_until_next_monday(now, hour=7)
                target = (now + timedelta(days=days_until_monday)).replace(
                    hour=7, minute=0, second=0, microsecond=0
                )
                wait_seconds = (target - now).total_seconds()
                logger.info(f"[weekly_audit] следующий запуск через {wait_seconds/3600:.1f} ч (пн 10:00 МСК)")
                await asyncio.sleep(wait_seconds)

                if peter_agent is None:
                    continue

                shops = await get_all_active_shops()
                unique_chats = _unique_chats(shops)

                for chat_id in unique_chats:
                    try:
                        await peter_agent.run_weekly_audit(chat_id)
                        logger.info(f"[weekly_audit] chat_id={chat_id} завершено")
                    except Exception as e:
                        logger.error(f"[weekly_audit] chat_id={chat_id} ошибка: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[weekly_audit] критическая ошибка: {e}")
                await asyncio.sleep(60)

    asyncio.create_task(_weekly_audit_loop())

    async def _daily_snapshot_loop():
        """Ежедневно в 01:00 UTC фиксирует выручку вчера и текущие остатки."""
        from datetime import datetime, timezone, timedelta, date as _date
        from db import get_all_active_shops, upsert_daily_snapshot, upsert_stock_history, get_pool

        while True:
            try:
                now = datetime.now(timezone.utc)
                target = now.replace(hour=1, minute=0, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                wait_seconds = (target - now).total_seconds()
                logger.info(f"[snapshot] следующий запуск через {wait_seconds/3600:.1f}ч ({target.isoformat()})")
                await asyncio.sleep(wait_seconds)

                yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
                yesterday_start = datetime(yesterday.year, yesterday.month, yesterday.day, tzinfo=timezone.utc)
                yesterday_end   = yesterday_start + timedelta(days=1)

                pool = await get_pool()
                shops = await get_all_active_shops()
                unique_chats = _unique_chats(shops)

                for chat_id in unique_chats:
                    # Снимок выручки за вчера
                    try:
                        async with pool.acquire() as conn:
                            rows = await conn.fetch("""
                                SELECT marketplace,
                                       SUM(seller_price * quantity)::numeric(12,2) AS revenue,
                                       COUNT(*) AS orders_count,
                                       AVG(seller_price)::numeric(10,2)            AS avg_price
                                FROM marketplace_orders
                                WHERE chat_id = $1
                                  AND order_date >= $2 AND order_date < $3
                                GROUP BY marketplace
                            """, chat_id, yesterday_start, yesterday_end)
                        for r in rows:
                            await upsert_daily_snapshot(
                                snapshot_date=yesterday,
                                chat_id=chat_id,
                                marketplace=r["marketplace"],
                                revenue=float(r["revenue"] or 0),
                                orders_count=int(r["orders_count"] or 0),
                                avg_price=float(r["avg_price"] or 0),
                            )
                        logger.info(f"[snapshot] chat={chat_id} revenue snapshot: {len(rows)} строк за {yesterday}")
                    except Exception as e:
                        logger.error(f"[snapshot] revenue chat={chat_id}: {e}")

                    # Снимок остатков (текущие → история)
                    try:
                        async with pool.acquire() as conn:
                            stocks = await conn.fetch("""
                                SELECT marketplace, product_id, warehouse_name, stock
                                FROM marketplace_stocks
                                WHERE chat_id = $1
                            """, chat_id)
                        for s in stocks:
                            await upsert_stock_history(
                                snapshot_date=yesterday,
                                chat_id=chat_id,
                                marketplace=s["marketplace"],
                                product_id=s["product_id"],
                                warehouse_name=s["warehouse_name"] or "",
                                stock=s["stock"],
                            )
                        logger.info(f"[snapshot] chat={chat_id} stock history: {len(stocks)} позиций за {yesterday}")
                    except Exception as e:
                        logger.error(f"[snapshot] stocks chat={chat_id}: {e}")

                    # Алерт остатков после ежедневного снимка
                    if max_agent is not None:
                        try:
                            await max_agent._check_stock_alerts(chat_id)
                        except Exception as e:
                            logger.error(f"[snapshot] stock_alerts chat={chat_id}: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[snapshot] ошибка: {e}")
                await asyncio.sleep(60)

    asyncio.create_task(_daily_snapshot_loop())

    tina_agent = next((a for a in started if isinstance(a, TinaAgent)), None)

    async def _tender_digest_loop():
        """Ежедневно в 05:00 UTC (08:00 МСК) — тендерный дайджест."""
        from datetime import datetime, timezone, timedelta
        from db import get_all_active_shops

        while True:
            try:
                now    = datetime.now(timezone.utc)
                target = now.replace(hour=config.TENDER_SCAN_HOUR_UTC, minute=0, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                wait_seconds = (target - now).total_seconds()
                logger.info(f"[tender_scheduler] следующий запуск через {wait_seconds/3600:.1f}ч ({target.isoformat()})")
                await asyncio.sleep(wait_seconds)

                if tina_agent is None:
                    continue

                shops = await get_all_active_shops()
                unique_chats = _unique_chats(shops)
                if not unique_chats:
                    logger.info("[tender_scheduler] нет активных пользователей — пропускаем")
                    continue

                logger.info(f"[tender_scheduler] запуск дайджеста для {len(unique_chats)} пользователей")
                for user_chat_id in unique_chats:
                    try:
                        await tina_agent.send_daily_digest(user_chat_id)
                    except Exception as e:
                        logger.error(f"[tender_scheduler] user={user_chat_id} error: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[tender_scheduler] ошибка: {e}")
                await asyncio.sleep(60)

    asyncio.create_task(_tender_digest_loop())

    async def _stock_alerts_loop():
        """Ежедневно в STOCK_ALERT_HOUR_UTC — алерты по остаткам < STOCK_ALERT_DAYS_THRESHOLD дней."""
        from datetime import datetime, timezone, timedelta
        from db import get_all_active_shops

        while True:
            try:
                now    = datetime.now(timezone.utc)
                hour   = getattr(config, "STOCK_ALERT_HOUR_UTC", 10)
                target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                wait_seconds = (target - now).total_seconds()
                logger.info(f"[stock_alerts] следующий запуск через {wait_seconds/3600:.1f}ч ({target.isoformat()})")
                await asyncio.sleep(wait_seconds)

                if max_agent is None:
                    continue

                shops = await get_all_active_shops()
                unique_chats = _unique_chats(shops)
                for chat_id in unique_chats:
                    try:
                        await max_agent._check_stock_alerts(chat_id, deduplicate=True)
                    except Exception as e:
                        logger.error(f"[stock_alerts] chat_id={chat_id} ошибка: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[stock_alerts] критическая ошибка: {e}")
                await asyncio.sleep(60)

    asyncio.create_task(_stock_alerts_loop())

    async def _promotions_weekly_loop():
        """Еженедельно в понедельник 09:00 МСК (06:00 UTC) — сводка акций WB/Ozon."""
        from datetime import datetime, timezone, timedelta
        from db import get_all_active_shops

        while True:
            try:
                now  = datetime.now(timezone.utc)
                # Следующий понедельник 06:00 UTC
                days_until_monday = _days_until_next_monday(now, hour=6)
                target = (now + timedelta(days=days_until_monday)).replace(
                    hour=6, minute=0, second=0, microsecond=0
                )
                wait_seconds = (target - now).total_seconds()
                logger.info(f"[promotions_weekly] следующий запуск через {wait_seconds/3600:.1f}ч ({target.isoformat()})")
                await asyncio.sleep(wait_seconds)

                if max_agent is None:
                    continue

                shops = await get_all_active_shops()
                unique_chats = _unique_chats(shops)
                for chat_id in unique_chats:
                    try:
                        await max_agent._send_promotions_summary(chat_id)
                    except Exception as e:
                        logger.error(f"[promotions_weekly] chat_id={chat_id} ошибка: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[promotions_weekly] критическая ошибка: {e}")
                await asyncio.sleep(300)

    asyncio.create_task(_promotions_weekly_loop())

    async def _competitor_monitor_loop():
        """Еженедельно в понедельник COMPETITOR_SCAN_HOUR_UTC — снапшот цен конкурентов WB."""
        from datetime import datetime, timezone, timedelta
        from db import upsert_competitor_snapshot

        while True:
            try:
                now  = datetime.now(timezone.utc)
                hour = getattr(config, "COMPETITOR_SCAN_HOUR_UTC", 6)
                # следующий понедельник в нужный час
                days_until_monday = _days_until_next_monday(now, hour=hour)
                target = (now + timedelta(days=days_until_monday)).replace(
                    hour=hour, minute=0, second=0, microsecond=0
                )
                wait_seconds = (target - now).total_seconds()
                logger.info(f"[competitor_monitor] следующий запуск через {wait_seconds/3600:.1f}ч ({target.isoformat()})")
                await asyncio.sleep(wait_seconds)

                from db import get_top_keywords_for_competitors
                keywords = await get_top_keywords_for_competitors(limit=10)
                if not keywords:
                    # fallback — захардкоженные ключи из конфига
                    keywords = getattr(config, "COMPETITOR_KEYWORDS", [])
                if not keywords:
                    logger.info("[competitor_monitor] нет ключевых слов — пропускаем")
                    continue

                logger.info(f"[competitor_monitor] ключи ({len(keywords)}): {keywords[:3]}…")

                from tools.marketplace import WBClient
                from datetime import date
                client = WBClient("")   # публичный API — токен не нужен
                today  = date.today()
                all_rows: list[dict] = []

                for kw in keywords:
                    products = await client.get_competitor_prices(kw)
                    for p in products:
                        all_rows.append({**p, "keyword": kw, "marketplace": "wb", "snapshot_date": today})
                    await asyncio.sleep(3)   # пауза между запросами

                if all_rows:
                    await upsert_competitor_snapshot(all_rows)
                    logger.info(f"[competitor_monitor] сохранено {len(all_rows)} строк по {len(keywords)} запросам")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[competitor_monitor] ошибка: {e}")
                await asyncio.sleep(300)

    asyncio.create_task(_competitor_monitor_loop())

    async def _daily_digest_loop():
        """Ежедневный дайджест от Питера — каждый день в 18:00 UTC (21:00 МСК)."""
        from datetime import datetime, timezone, timedelta
        from db import get_all_active_shops

        while True:
            try:
                now  = datetime.now(timezone.utc)
                hour = getattr(config, "DAILY_DIGEST_HOUR_UTC", 18)
                target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                wait_seconds = (target - now).total_seconds()
                logger.info(f"[daily_digest] следующий запуск через {wait_seconds/3600:.1f}ч ({target.isoformat()})")
                await asyncio.sleep(wait_seconds)

                if peter_agent is None:
                    logger.warning("[daily_digest] Питер не найден, пропускаем")
                    continue

                shops = await get_all_active_shops()
                unique_chats = _unique_chats(shops)

                for chat_id in unique_chats:
                    try:
                        await peter_agent.run_daily_digest(chat_id)
                        logger.info(f"[daily_digest] chat_id={chat_id} завершено")
                    except Exception as e:
                        logger.error(f"[daily_digest] chat_id={chat_id} ошибка: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[daily_digest] критическая ошибка: {e}")
                await asyncio.sleep(60)

    asyncio.create_task(_daily_digest_loop())
    logger.info("[main] Daily digest task запущен (каждый день 18:00 UTC = 21:00 МСК)")

    async def _marta_digest_loop():
        """Дайджест задач от Марты — каждый день в 18:05 UTC (21:05 МСК)."""
        from datetime import datetime, timezone, timedelta
        from db import get_all_active_shops

        while True:
            try:
                now = datetime.now(timezone.utc)
                target = now.replace(hour=18, minute=5, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                await asyncio.sleep((target - now).total_seconds())

                if marta_agent is None:
                    continue
                shops = await get_all_active_shops()
                for chat_id in _unique_chats(shops):
                    try:
                        await marta_agent.send_daily_digest(chat_id)
                        logger.info(f"[marta_digest] chat_id={chat_id} отправлено")
                    except Exception as e:
                        logger.error(f"[marta_digest] chat_id={chat_id} ошибка: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[marta_digest] критическая ошибка: {e}")
                await asyncio.sleep(60)

    asyncio.create_task(_marta_digest_loop())
    logger.info("[main] Marta digest task запущен (18:05 UTC = 21:05 МСК)")

    async def _funnel_sync_loop():
        """Воронка конверсии — ежедневно в 02:30 UTC."""
        from datetime import datetime, timezone, timedelta
        from db import get_all_active_shops

        while True:
            try:
                now = datetime.now(timezone.utc)
                target = now.replace(hour=2, minute=30, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                await asyncio.sleep((target - now).total_seconds())

                if max_agent is None:
                    continue
                shops = await get_all_active_shops()
                for chat_id in _unique_chats(shops):
                    try:
                        await max_agent.sync_funnel(chat_id)
                        logger.info(f"[funnel_sync] chat_id={chat_id} завершено")
                    except Exception as e:
                        logger.error(f"[funnel_sync] chat_id={chat_id} ошибка: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[funnel_sync] критическая ошибка: {e}")
                await asyncio.sleep(60)

    asyncio.create_task(_funnel_sync_loop())
    logger.info("[main] Funnel sync task запущен (ежедневно 02:30 UTC)")

    async def _returns_sync_loop():
        """Аналитика возвратов — еженедельно в субботу 02:00 UTC."""
        from datetime import datetime, timezone, timedelta
        from db import get_all_active_shops

        while True:
            try:
                now = datetime.now(timezone.utc)
                # weekday(): 5 = суббота
                days_until_saturday = (5 - now.weekday()) % 7
                if days_until_saturday == 0 and now.hour >= 2:
                    days_until_saturday = 7
                target = (now + timedelta(days=days_until_saturday)).replace(
                    hour=2, minute=0, second=0, microsecond=0
                )
                await asyncio.sleep((target - now).total_seconds())

                if max_agent is None:
                    continue
                shops = await get_all_active_shops()
                for chat_id in _unique_chats(shops):
                    try:
                        await max_agent.sync_returns(chat_id)
                        logger.info(f"[returns_sync] chat_id={chat_id} завершено")
                    except Exception as e:
                        logger.error(f"[returns_sync] chat_id={chat_id} ошибка: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[returns_sync] критическая ошибка: {e}")
                await asyncio.sleep(60)

    asyncio.create_task(_returns_sync_loop())
    logger.info("[main] Returns sync task запущен (еженедельно сб 02:00 UTC)")

    async def _kpi_sync_loop():
        """KPI продавца (рейтинг, штрафы) — ежедневно в 02:00 UTC."""
        from datetime import datetime, timezone, timedelta
        from db import get_all_active_shops

        while True:
            try:
                now = datetime.now(timezone.utc)
                target = now.replace(hour=2, minute=0, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                await asyncio.sleep((target - now).total_seconds())

                if max_agent is None:
                    continue
                shops = await get_all_active_shops()
                for chat_id in _unique_chats(shops):
                    try:
                        await max_agent.sync_shop_kpi(chat_id)
                        logger.info(f"[kpi_sync] chat_id={chat_id} завершено")
                    except Exception as e:
                        logger.error(f"[kpi_sync] chat_id={chat_id} ошибка: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[kpi_sync] критическая ошибка: {e}")
                await asyncio.sleep(60)

    asyncio.create_task(_kpi_sync_loop())
    logger.info("[main] KPI sync task запущен (ежедневно 02:00 UTC)")

    async def _auto_bid_loop():
        """Авто-анализ ставок — ежедневно в 03:30 UTC (после синка рекламы в 03:00)."""
        from datetime import datetime, timezone, timedelta
        from db import get_all_active_shops

        while True:
            try:
                now = datetime.now(timezone.utc)
                target = now.replace(hour=3, minute=30, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                await asyncio.sleep((target - now).total_seconds())

                if max_agent is None:
                    continue
                shops = await get_all_active_shops()
                for chat_id in _unique_chats(shops):
                    try:
                        sent = await max_agent.auto_bid_suggest(chat_id)
                        if sent:
                            logger.info(f"[auto_bid] chat_id={chat_id} отправлено {sent} предложений")
                    except Exception as e:
                        logger.error(f"[auto_bid] chat_id={chat_id} ошибка: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[auto_bid] критическая ошибка: {e}")
                await asyncio.sleep(60)

    asyncio.create_task(_auto_bid_loop())
    logger.info("[main] Auto bid task запущен (ежедневно 03:30 UTC)")

    # ── Dashboard API (aiohttp) ───────────────────────────────────────────────
    _CORS_ORIGIN = config.DASHBOARD_URL or "*"

    async def _handle_dashboard(request: web.Request) -> web.Response:
        cors = {"Access-Control-Allow-Origin": _CORS_ORIGIN}
        if request.method == "OPTIONS":
            return web.Response(headers={
                **cors,
                "Access-Control-Allow-Headers": "X-Telegram-Init-Data",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
            })
        # Токен-доступ для коллег: ?token=SECRET → показываем данные владельца
        url_token = request.rel_url.query.get("token", "")
        if url_token and config.DASHBOARD_TOKEN and url_token == config.DASHBOARD_TOKEN:
            if not config.OWNER_CHAT_ID:
                return web.Response(status=503, text="OWNER_CHAT_ID not configured", headers=cors)
            chat_id = config.OWNER_CHAT_ID
        else:
            init_data = request.headers.get("X-Telegram-Init-Data", "")
            parsed = _validate_init_data(init_data, config.MARTA_BOT_TOKEN)
            if parsed is None:
                return web.Response(status=401, text="Unauthorized", headers=cors)
            try:
                user = _json.loads(parsed.get("user", "{}"))
                chat_id = int(user["id"])
            except Exception:
                return web.Response(status=400, text="Bad Request", headers=cors)
        if peter_agent is None:
            return web.Response(status=503, text="Peter agent not running", headers=cors)
        try:
            days = min(int(request.rel_url.query.get("days", "14")), 90)
            data = await peter_agent._collect_data(chat_id, days=days)
            adv = await peter_agent._collect_advanced_data(chat_id, days=days)
            # Добавляем выручку по дням для LineChart
            from db import get_pool
            from datetime import datetime, timedelta, timezone
            pool = await get_pool()
            date_from = (datetime.now(timezone.utc) - timedelta(days=days)).date()
            async with pool.acquire() as conn:
                daily_rows = await conn.fetch("""
                    SELECT order_date::date::text AS date,
                           marketplace,
                           SUM(seller_price * quantity)::numeric(12,2) AS revenue,
                           COUNT(*) AS orders
                    FROM marketplace_orders
                    WHERE chat_id = $1 AND order_date >= $2
                    GROUP BY order_date::date, marketplace
                    ORDER BY order_date::date
                """, chat_id, date_from)
                sales_rows = await conn.fetch("""
                    SELECT sale_date::date::text AS date,
                           marketplace,
                           SUM(price)::numeric(12,2) AS revenue,
                           COUNT(*) AS qty
                    FROM marketplace_sales
                    WHERE chat_id = $1 AND sale_date >= $2
                    GROUP BY sale_date::date, marketplace
                    ORDER BY sale_date::date
                """, chat_id, date_from)

            def _pivot(rows, val_key: str) -> list[dict]:
                p: dict[str, dict] = {}
                for row in rows:
                    d = row["date"]
                    p.setdefault(d, {"date": d, "wb": 0.0, "ozon": 0.0})
                    p[d][row["marketplace"]] = float(row[val_key] or 0)
                return list(p.values())

            data["revenue_by_day"] = _pivot(daily_rows, "revenue")
            data["orders_by_day"]  = _pivot(daily_rows, "orders")
            data["sales_by_day"]   = _pivot(sales_rows, "revenue")
            logger.info(f"[dashboard] chat={chat_id} days={days} "
                        f"daily_rows={len(daily_rows)} sales_rows={len(sales_rows)} "
                        f"revenue_by_day={len(data['revenue_by_day'])}")
        except Exception as e:
            logger.error(f"[dashboard] data error: {e}", exc_info=True)
            return web.Response(status=500, text="Internal Error", headers=cors)
        return web.json_response(_to_json_safe({**data, **adv}), headers=cors)

    async def _handle_timeline(request: web.Request) -> web.Response:
        cors = {"Access-Control-Allow-Origin": _CORS_ORIGIN}
        if request.method == "OPTIONS":
            return web.Response(headers={
                **cors,
                "Access-Control-Allow-Headers": "X-Telegram-Init-Data",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
            })
        url_token = request.rel_url.query.get("token", "")
        if url_token and config.DASHBOARD_TOKEN and url_token == config.DASHBOARD_TOKEN:
            if not config.OWNER_CHAT_ID:
                return web.Response(status=503, text="OWNER_CHAT_ID not configured", headers=cors)
            chat_id = config.OWNER_CHAT_ID
        else:
            init_data = request.headers.get("X-Telegram-Init-Data", "")
            parsed = _validate_init_data(init_data, config.MARTA_BOT_TOKEN)
            if parsed is None:
                return web.Response(status=401, text="Unauthorized", headers=cors)
            try:
                user = _json.loads(parsed.get("user", "{}"))
                chat_id = int(user["id"])
            except Exception:
                return web.Response(status=400, text="Bad Request", headers=cors)
        try:
            from db import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    WITH recent_chains AS (
                        SELECT te.chain_id,
                               MIN(te.created_at) AS started_at,
                               MAX(te.created_at) AS last_event_at
                        FROM task_events te
                        JOIN tasks t ON te.task_id = t.id
                        WHERE t.chat_id = $1
                          AND te.task_id IS NOT NULL
                          AND te.chain_id IS NOT NULL
                          AND te.created_at > NOW() - INTERVAL '7 days'
                        GROUP BY te.chain_id
                        ORDER BY started_at DESC
                        LIMIT 15
                    )
                    SELECT
                        te.chain_id,
                        te.agent_key,
                        te.event_type,
                        te.created_at,
                        rc.started_at         AS chain_started_at,
                        rc.last_event_at      AS chain_last_event_at,
                        EXTRACT(EPOCH FROM (rc.last_event_at - rc.started_at))::int AS duration_sec
                    FROM task_events te
                    JOIN recent_chains rc ON te.chain_id = rc.chain_id
                    ORDER BY rc.started_at DESC, te.created_at ASC
                """, chat_id)
            chains_map: dict = {}
            chain_order: list = []
            for row in rows:
                cid = row["chain_id"]
                if cid not in chains_map:
                    chains_map[cid] = {
                        "chain_id": cid[:8],
                        "started_at": row["chain_started_at"].isoformat(),
                        "duration_sec": row["duration_sec"],
                        "events": [],
                    }
                    chain_order.append(cid)
                chains_map[cid]["events"].append({
                    "agent_key": row["agent_key"] or "",
                    "event_type": row["event_type"],
                    "created_at": row["created_at"].isoformat(),
                })
            result_chains = []
            for cid in chain_order:
                chain = chains_map[cid]
                event_types = {e["event_type"] for e in chain["events"]}
                created = sum(1 for e in chain["events"] if e["event_type"] == "TASK_CREATED")
                completed = sum(1 for e in chain["events"] if e["event_type"] == "TASK_COMPLETED")
                if "TASK_FAILED" in event_types:
                    chain["status"] = "failed"
                elif created > 0 and completed >= created:
                    chain["status"] = "completed"
                else:
                    chain["status"] = "running"
                result_chains.append(chain)
        except Exception as e:
            logger.error(f"[timeline] error: {e}", exc_info=True)
            return web.Response(status=500, text="Internal Error", headers=cors)
        return web.json_response({"chains": result_chains}, headers=cors)

    dash_app = web.Application()
    dash_app.router.add_get("/api/dashboard", _handle_dashboard)
    dash_app.router.add_route("OPTIONS", "/api/dashboard", _handle_dashboard)
    dash_app.router.add_get("/api/timeline", _handle_timeline)
    dash_app.router.add_route("OPTIONS", "/api/timeline", _handle_timeline)
    dash_app.router.add_get("/health", lambda r: web.Response(text="ok"))
    dash_runner = web.AppRunner(dash_app)
    await dash_runner.setup()
    await web.TCPSite(dash_runner, "0.0.0.0", config.PORT).start()
    logger.info(f"[main] Dashboard API: http://0.0.0.0:{config.PORT}/api/dashboard")

    logger.info("[main] Negative reviews task запущен (каждые 15 минут)")
    logger.info("[main] Scheduled reviews task запущен (06:00, 11:00, 17:00 UTC)")
    logger.info("[main] Adv sync task запущен (03:00 UTC / 06:00 МСК)")
    logger.info("[main] Fin sync task запущен (вс 01:30 UTC / 04:30 МСК)")
    logger.info("[main] Weekly audit task запущен (пн 07:00 UTC / 10:00 МСК)")
    logger.info(f"[main] Tender digest task запущен ({config.TENDER_SCAN_HOUR_UTC}:00 UTC / 08:00 МСК)")
    logger.info("[main] Daily snapshot task запущен (01:00 UTC — выручка + остатки)")

    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass

    # Graceful shutdown
    for task in (status_task, reviews_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    logger.info("Получен сигнал остановки — завершаем агентов...")
    await dash_runner.cleanup()
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
