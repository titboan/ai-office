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
import signal
from contextlib import suppress
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
    DanAgent,
    EvaAgent,
    MaxAgent,
    TinaAgent,
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
    "tina":   (TinaAgent,   "tina"),
}


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

                if eva_agent is None:
                    logger.warning("[digest_scheduler] Ева не найдена, пропускаем")
                    continue

                users = await get_distinct_digest_users()
                logger.info(f"[digest_scheduler] запуск дайджеста для {len(users)} пользователей")
                for user_chat_id in users:
                    # TG-каналы — только если Telethon подключён
                    if eva_agent._telethon_ready:
                        try:
                            await eva_agent.run_digest(user_chat_id, since=None)
                        except Exception as e:
                            logger.error(f"[digest_scheduler] tg user={user_chat_id} error: {e}")
                    # Email-дайджест и сортировка — не зависят от Telethon
                    try:
                        await eva_agent.run_email_digest(user_chat_id, since_days=1)
                    except Exception as e:
                        logger.error(f"[digest_scheduler] email user={user_chat_id} error: {e}")
                    try:
                        await eva_agent.run_sort_emails(user_chat_id, since_days=1)
                    except Exception as e:
                        logger.error(f"[digest_scheduler] sort user={user_chat_id} error: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[digest_scheduler] ошибка: {e}")
                await asyncio.sleep(60)

    digest_task = asyncio.create_task(_scheduled_digest_loop())
    logger.info("[main] Scheduled digest task запущен (каждый день 06:30 UTC)")

    max_agent   = next((a for a in started if isinstance(a, MaxAgent)), None)
    peter_agent = next((a for a in started if a.__class__.__name__ == "PeterAgent"), None)

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
                unique_chats = list({s["chat_id"] for s in shops})
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
                unique_chats = list({s["chat_id"] for s in shops})

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
                unique_chats = list({s["chat_id"] for s in shops})

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
                unique_chats = list({s["chat_id"] for s in shops})
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
                unique_chats = list({s["chat_id"] for s in shops})
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
                days_until_monday = (7 - now.weekday()) % 7
                if days_until_monday == 0 and now.hour >= 7:
                    days_until_monday = 7
                target = (now + timedelta(days=days_until_monday)).replace(
                    hour=7, minute=0, second=0, microsecond=0
                )
                wait_seconds = (target - now).total_seconds()
                logger.info(f"[weekly_audit] следующий запуск через {wait_seconds/3600:.1f} ч (пн 10:00 МСК)")
                await asyncio.sleep(wait_seconds)

                if peter_agent is None:
                    continue

                shops = await get_all_active_shops()
                unique_chats = list({s["chat_id"] for s in shops})

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
                unique_chats = list({s["chat_id"] for s in shops})

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
                unique_chats = list({s["chat_id"] for s in shops})
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

    dash_app = web.Application()
    dash_app.router.add_get("/api/dashboard", _handle_dashboard)
    dash_app.router.add_route("OPTIONS", "/api/dashboard", _handle_dashboard)
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
    for task in (status_task, digest_task, reviews_task, negative_task):
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
