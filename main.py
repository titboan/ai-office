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
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import parse_qsl

from aiohttp import web

from loguru import logger

from config import config
from task_queue import get_active_tasks, get_recent_tasks
from utils.loop_health import report_loop_failure, report_loop_success
from agents import (
    MartaAgent,
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
    "kasper": (KasperAgent, "kasper"),
    "peter":  (PeterAgent,  "peter"),
    "elina":  (ElinaAgent,  "elina"),
    "alex":   (AlexAgent,   "alex"),
    # "kevin": (KevinAgent, "kevin"),  # заморожен 2026-07-04: ни разу не использовался за месяц работы (создание лендингов/PR через Claude Code сессии удобнее и с ревью)
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


def _wait_interval(seconds: float):
    """Фиксированный интервал (мониторинг отзывов/вопросов и т.п.)."""
    return lambda: seconds


def _wait_daily_utc(hour: int, minute: int = 0):
    """Ближайшее наступление hour:minute UTC — сегодня, если ещё не прошло, иначе завтра."""
    def _compute() -> float:
        now = datetime.now(timezone.utc)
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()
    return _compute


def _wait_weekly_utc(weekday: int, hour: int, minute: int = 0):
    """Ближайшее наступление hour:minute UTC в день недели weekday
    (0=понедельник … 6=воскресенье, как datetime.weekday())."""
    def _compute() -> float:
        now = datetime.now(timezone.utc)
        days_until = (weekday - now.weekday()) % 7
        if days_until == 0 and (now.hour > hour or (now.hour == hour and now.minute >= minute)):
            days_until = 7
        target = (now + timedelta(days=days_until)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        return (target - now).total_seconds()
    return _compute


async def run_scheduled_loop(name: str, next_wait_seconds, task_fn, error_sleep: float = 60) -> None:
    """Общий раннер фонового цикла — убирает копипаст try/sleep/except из ~18 циклов main.py.

    next_wait_seconds — callable без аргументов, вызывается заново на каждой итерации
    (время до "следующего понедельника" каждый раз разное). task_fn — корутина-функция
    без аргументов с самой работой цикла. При ошибке — лог + пауза error_sleep, процесс
    не падает (как было в каждом цикле по отдельности)."""
    while True:
        try:
            wait_seconds = next_wait_seconds()
            if wait_seconds > 0:
                logger.info(f"[{name}] следующий запуск через {wait_seconds/3600:.1f} ч")
                await asyncio.sleep(wait_seconds)
            await task_fn()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[{name}] критическая ошибка: {e}")
            await asyncio.sleep(error_sleep)


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

    # Запускаем всех агентов. Марта — единственная точка входа в Telegram:
    # только она поднимает polling-соединение, остальные агенты живут
    # worker-only (обрабатывают делегированные задачи из очереди и
    # фоновые/расписанные задания, но не принимают сообщения напрямую).
    started = []
    for agent in agents:
        try:
            if isinstance(agent, MartaAgent):
                await agent.start_polling_async()
            else:
                await agent.start_worker_only_async()
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

    max_agent    = next((a for a in started if isinstance(a, MaxAgent)), None)
    peter_agent  = next((a for a in started if a.__class__.__name__ == "PeterAgent"), None)
    marta_agent  = next((a for a in started if isinstance(a, MartaAgent)), None)
    elina_agent  = next((a for a in started if a.__class__.__name__ == "ElinaAgent"), None)
    tina_agent   = next((a for a in started if isinstance(a, TinaAgent)), None)
    alex_agent   = next((a for a in started if a.__class__.__name__ == "AlexAgent"), None)
    kasper_agent = next((a for a in started if isinstance(a, KasperAgent)), None)
    if max_agent is not None and peter_agent is not None:
        max_agent._peter_agent = peter_agent
    if marta_agent is not None and max_agent is not None:
        marta_agent._max_agent = max_agent
        max_agent._marta_agent = marta_agent
    if marta_agent is not None and elina_agent is not None:
        marta_agent._elina_agent = elina_agent
    # Единственная точка входа/выхода для пользователя — бот Марты: даём ей прямой
    # доступ к остальным агентам, чтобы прокси-команды не зависели от очереди задач.
    if marta_agent is not None and peter_agent is not None:
        marta_agent._peter_agent = peter_agent
    if marta_agent is not None and tina_agent is not None:
        marta_agent._tina_agent = tina_agent
    if marta_agent is not None and alex_agent is not None:
        marta_agent._alex_agent = alex_agent
    if marta_agent is not None and kasper_agent is not None:
        marta_agent._kasper_agent = kasper_agent

    async def _reviews_task():
        """Обработка всех отзывов каждые 15 минут."""
        from db import get_all_active_shops

        if max_agent is None:
            return
        shops = await get_all_active_shops()
        unique_chats = _unique_chats(shops)
        for chat_id in unique_chats:
            try:
                await max_agent.process_reviews(chat_id)
                report_loop_success(f"reviews:{chat_id}")
            except Exception as e:
                logger.error(f"[reviews_scheduler] chat={chat_id} error: {e}")
                await report_loop_failure(f"reviews:{chat_id}", e)

    reviews_task = asyncio.create_task(
        run_scheduled_loop("reviews_scheduler", _wait_interval(15 * 60), _reviews_task)
    )

    async def _adv_sync_task():
        """Синхронизация рекламной статистики WB + Ozon раз в сутки в 03:00 UTC (06:00 МСК)."""
        from db import get_all_active_shops

        if max_agent is None:
            return
        shops = await get_all_active_shops()
        unique_chats = _unique_chats(shops)
        for chat_id in unique_chats:
            try:
                await max_agent.sync_ad_stats(chat_id)
                logger.info(f"[adv_sync] chat_id={chat_id} завершено")
                await max_agent._check_drr_alerts(chat_id)
                report_loop_success(f"adv_sync:{chat_id}")
            except Exception as e:
                logger.error(f"[adv_sync] chat_id={chat_id} ошибка: {e}")
                await report_loop_failure(f"adv_sync:{chat_id}", e)

    asyncio.create_task(
        run_scheduled_loop("adv_sync", _wait_daily_utc(3, 0), _adv_sync_task)
    )

    async def _fin_sync_task():
        """Еженедельный финотчёт — воскресенье 01:30 UTC (04:30 МСК)."""
        from db import get_all_active_shops

        if max_agent is None:
            return
        shops = await get_all_active_shops()
        unique_chats = _unique_chats(shops)
        for chat_id in unique_chats:
            try:
                await max_agent.sync_financial_report(chat_id, days=90)
                logger.info(f"[fin_sync] chat_id={chat_id} завершено")
                report_loop_success(f"fin_sync:{chat_id}")
            except Exception as e:
                logger.error(f"[fin_sync] chat_id={chat_id} ошибка: {e}")
                await report_loop_failure(f"fin_sync:{chat_id}", e)

    # weekday(): 0=пн … 6=вс
    asyncio.create_task(
        run_scheduled_loop("fin_sync", _wait_weekly_utc(6, 1, 30), _fin_sync_task)
    )

    async def _questions_task():
        """Мониторинг вопросов покупателей WB + Ozon каждые 15 минут."""
        from db import get_all_active_shops

        if max_agent is None:
            return
        shops = await get_all_active_shops()
        unique_chats = _unique_chats(shops)
        logger.info(f"[questions_scheduler] проверка вопросов для {len(unique_chats)} пользователей")
        for chat_id in unique_chats:
            try:
                results = await max_agent.process_questions(chat_id)
                found = sum(s.get("found", 0) for s in results.values())
                if found:
                    logger.info(f"[questions_scheduler] chat={chat_id}: {found} новых вопросов")
                report_loop_success(f"questions:{chat_id}")
            except Exception as e:
                logger.error(f"[questions_scheduler] chat={chat_id} error: {e}")
                await report_loop_failure(f"questions:{chat_id}", e)

    asyncio.create_task(
        run_scheduled_loop("questions_scheduler", _wait_interval(15 * 60), _questions_task)
    )

    async def _orders_sync_task():
        """Синхронизация заказов, продаж и остатков WB + Ozon каждый час."""
        from db import get_all_active_shops

        if max_agent is None:
            return
        shops = await get_all_active_shops()
        unique_chats = _unique_chats(shops)
        logger.info(f"[orders_sync] синхронизация для {len(unique_chats)} пользователей")
        for chat_id in unique_chats:
            try:
                await max_agent.sync_marketplace_data(chat_id)
                logger.info(f"[orders_sync] chat_id={chat_id} завершено")
                report_loop_success(f"orders_sync:{chat_id}")
            except Exception as e:
                logger.error(f"[orders_sync] chat_id={chat_id} ошибка: {e}")
                await report_loop_failure(f"orders_sync:{chat_id}", e)

    asyncio.create_task(
        run_scheduled_loop("orders_sync", _wait_interval(60 * 60), _orders_sync_task)
    )

    async def _weekly_audit_task():
        """Еженедельный аудит магазина — понедельник 07:00 UTC (10:00 МСК)."""
        from db import get_all_active_shops

        if peter_agent is None:
            return
        shops = await get_all_active_shops()
        unique_chats = _unique_chats(shops)
        for chat_id in unique_chats:
            try:
                await peter_agent.run_weekly_audit(chat_id)
                logger.info(f"[weekly_audit] chat_id={chat_id} завершено")
                report_loop_success(f"weekly_audit:{chat_id}")
            except Exception as e:
                logger.error(f"[weekly_audit] chat_id={chat_id} ошибка: {e}")
                await report_loop_failure(f"weekly_audit:{chat_id}", e)

    asyncio.create_task(
        run_scheduled_loop("weekly_audit", _wait_weekly_utc(0, 7, 0), _weekly_audit_task)
    )

    async def _daily_snapshot_task():
        """Ежедневно в 01:00 UTC фиксирует выручку вчера и текущие остатки."""
        from db import get_all_active_shops, upsert_daily_snapshot, upsert_stock_history, get_pool

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
                report_loop_success(f"snapshot_revenue:{chat_id}")
            except Exception as e:
                logger.error(f"[snapshot] revenue chat={chat_id}: {e}")
                await report_loop_failure(f"snapshot_revenue:{chat_id}", e)

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
                report_loop_success(f"snapshot_stocks:{chat_id}")
            except Exception as e:
                logger.error(f"[snapshot] stocks chat={chat_id}: {e}")
                await report_loop_failure(f"snapshot_stocks:{chat_id}", e)

    asyncio.create_task(
        run_scheduled_loop("snapshot", _wait_daily_utc(1, 0), _daily_snapshot_task)
    )

    async def _tender_digest_task():
        """Ежедневно в config.TENDER_SCAN_HOUR_UTC (08:00 МСК по умолчанию) — тендерный дайджест."""
        from db import get_all_active_shops

        if tina_agent is None:
            return
        shops = await get_all_active_shops()
        unique_chats = _unique_chats(shops)
        if not unique_chats:
            logger.info("[tender_scheduler] нет активных пользователей — пропускаем")
            return
        logger.info(f"[tender_scheduler] запуск дайджеста для {len(unique_chats)} пользователей")
        for user_chat_id in unique_chats:
            try:
                await tina_agent.send_daily_digest(user_chat_id)
                report_loop_success(f"tender_digest:{user_chat_id}")
            except Exception as e:
                logger.error(f"[tender_scheduler] user={user_chat_id} error: {e}")
                await report_loop_failure(f"tender_digest:{user_chat_id}", e)

    asyncio.create_task(
        run_scheduled_loop("tender_scheduler", _wait_daily_utc(config.TENDER_SCAN_HOUR_UTC, 0), _tender_digest_task)
    )

    async def _stock_alerts_task():
        """Ежедневно в STOCK_ALERT_HOUR_UTC — алерты по остаткам < STOCK_ALERT_DAYS_THRESHOLD дней."""
        from db import get_all_active_shops

        if max_agent is None:
            return
        shops = await get_all_active_shops()
        unique_chats = _unique_chats(shops)
        for chat_id in unique_chats:
            try:
                await max_agent._check_stock_alerts(chat_id, deduplicate=True)
                report_loop_success(f"stock_alerts:{chat_id}")
            except Exception as e:
                logger.error(f"[stock_alerts] chat_id={chat_id} ошибка: {e}")
                await report_loop_failure(f"stock_alerts:{chat_id}", e)

    asyncio.create_task(
        run_scheduled_loop(
            "stock_alerts",
            _wait_daily_utc(getattr(config, "STOCK_ALERT_HOUR_UTC", 10), 0),
            _stock_alerts_task,
        )
    )

    async def _promotions_weekly_task():
        """Еженедельно в понедельник 09:00 МСК (06:00 UTC) — сводка акций WB/Ozon."""
        from db import get_all_active_shops

        if max_agent is None:
            return
        shops = await get_all_active_shops()
        unique_chats = _unique_chats(shops)
        for chat_id in unique_chats:
            try:
                await max_agent._send_promotions_summary(chat_id)
                report_loop_success(f"promotions_weekly:{chat_id}")
            except Exception as e:
                logger.error(f"[promotions_weekly] chat_id={chat_id} ошибка: {e}")
                await report_loop_failure(f"promotions_weekly:{chat_id}", e)

    asyncio.create_task(
        run_scheduled_loop(
            "promotions_weekly", _wait_weekly_utc(0, 6, 0), _promotions_weekly_task, error_sleep=300
        )
    )

    async def _competitor_monitor_task():
        """Еженедельно в понедельник COMPETITOR_SCAN_HOUR_UTC — снапшот цен конкурентов WB."""
        from db import upsert_competitor_snapshot, get_top_keywords_for_competitors

        try:
            keywords = await get_top_keywords_for_competitors(limit=10)
            if not keywords:
                # fallback — захардкоженные ключи из конфига
                keywords = getattr(config, "COMPETITOR_KEYWORDS", [])
            if not keywords:
                logger.info("[competitor_monitor] нет ключевых слов — пропускаем")
                return

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
            report_loop_success("competitor_monitor")
        except Exception as e:
            logger.error(f"[competitor_monitor] ошибка: {e}")
            await report_loop_failure("competitor_monitor", e)
            raise

    asyncio.create_task(
        run_scheduled_loop(
            "competitor_monitor",
            _wait_weekly_utc(0, getattr(config, "COMPETITOR_SCAN_HOUR_UTC", 6), 0),
            _competitor_monitor_task,
            error_sleep=300,
        )
    )

    async def _daily_digest_task():
        """Ежедневный дайджест — каждый день в 18:00 UTC (21:00 МСК).

        Одно сообщение: бизнес-сводка Питера + дайджест задач Марты (если Питер
        недоступен — отправляется только дайджест Марты отдельным сообщением).
        """
        from db import get_all_active_shops

        if peter_agent is None and marta_agent is None:
            logger.warning("[daily_digest] Питер и Марта не найдены, пропускаем")
            return
        shops = await get_all_active_shops()
        unique_chats = _unique_chats(shops)
        for chat_id in unique_chats:
            try:
                task_digest = await marta_agent.build_task_digest_text() if marta_agent else None
                if peter_agent is not None:
                    await peter_agent.run_daily_digest(chat_id, extra_text=task_digest)
                elif task_digest:
                    await marta_agent.send_daily_digest(chat_id)
                logger.info(f"[daily_digest] chat_id={chat_id} завершено")
                report_loop_success(f"daily_digest:{chat_id}")
            except Exception as e:
                logger.error(f"[daily_digest] chat_id={chat_id} ошибка: {e}")
                await report_loop_failure(f"daily_digest:{chat_id}", e)

    asyncio.create_task(
        run_scheduled_loop(
            "daily_digest", _wait_daily_utc(getattr(config, "DAILY_DIGEST_HOUR_UTC", 18), 0), _daily_digest_task
        )
    )
    logger.info("[main] Daily digest task запущен (каждый день 18:00 UTC = 21:00 МСК)")

    async def _funnel_sync_task():
        """Воронка конверсии — ежедневно в 02:30 UTC."""
        from db import get_all_active_shops

        if max_agent is None:
            return
        shops = await get_all_active_shops()
        for chat_id in _unique_chats(shops):
            try:
                await max_agent.sync_funnel(chat_id)
                logger.info(f"[funnel_sync] chat_id={chat_id} завершено")
                report_loop_success(f"funnel_sync:{chat_id}")
            except Exception as e:
                logger.error(f"[funnel_sync] chat_id={chat_id} ошибка: {e}")
                await report_loop_failure(f"funnel_sync:{chat_id}", e)

    asyncio.create_task(
        run_scheduled_loop("funnel_sync", _wait_daily_utc(2, 30), _funnel_sync_task)
    )
    logger.info("[main] Funnel sync task запущен (ежедневно 02:30 UTC)")

    async def _returns_sync_task():
        """Аналитика возвратов — еженедельно в субботу 02:00 UTC."""
        from db import get_all_active_shops

        if max_agent is None:
            return
        shops = await get_all_active_shops()
        for chat_id in _unique_chats(shops):
            try:
                await max_agent.sync_returns(chat_id)
                logger.info(f"[returns_sync] chat_id={chat_id} завершено")
                report_loop_success(f"returns_sync:{chat_id}")
            except Exception as e:
                logger.error(f"[returns_sync] chat_id={chat_id} ошибка: {e}")
                await report_loop_failure(f"returns_sync:{chat_id}", e)

    # weekday(): 5 = суббота
    asyncio.create_task(
        run_scheduled_loop("returns_sync", _wait_weekly_utc(5, 2, 0), _returns_sync_task)
    )
    logger.info("[main] Returns sync task запущен (еженедельно сб 02:00 UTC)")

    async def _kpi_sync_task():
        """KPI продавца (рейтинг, штрафы) — ежедневно в 02:00 UTC."""
        from db import get_all_active_shops

        if max_agent is None:
            return
        shops = await get_all_active_shops()
        for chat_id in _unique_chats(shops):
            try:
                await max_agent.sync_shop_kpi(chat_id)
                logger.info(f"[kpi_sync] chat_id={chat_id} завершено")
                report_loop_success(f"kpi_sync:{chat_id}")
            except Exception as e:
                logger.error(f"[kpi_sync] chat_id={chat_id} ошибка: {e}")
                await report_loop_failure(f"kpi_sync:{chat_id}", e)

    asyncio.create_task(
        run_scheduled_loop("kpi_sync", _wait_daily_utc(2, 0), _kpi_sync_task)
    )
    logger.info("[main] KPI sync task запущен (ежедневно 02:00 UTC)")

    async def _auto_bid_task():
        """Авто-анализ ставок — ежедневно в 03:30 UTC (после синка рекламы в 03:00)."""
        from db import get_all_active_shops

        if max_agent is None:
            return
        shops = await get_all_active_shops()
        for chat_id in _unique_chats(shops):
            try:
                sent = await max_agent.auto_bid_suggest(chat_id)
                if sent:
                    logger.info(f"[auto_bid] chat_id={chat_id} отправлено {sent} предложений")
                report_loop_success(f"auto_bid:{chat_id}")
            except Exception as e:
                logger.error(f"[auto_bid] chat_id={chat_id} ошибка: {e}")
                await report_loop_failure(f"auto_bid:{chat_id}", e)

    asyncio.create_task(
        run_scheduled_loop("auto_bid", _wait_daily_utc(3, 30), _auto_bid_task)
    )
    logger.info("[main] Auto bid task запущен (ежедневно 03:30 UTC)")

    # ── Dashboard API (aiohttp) ───────────────────────────────────────────────
    _CORS_ORIGIN = config.DASHBOARD_URL or "*"

    async def _rate_limited(chat_id: int, endpoint: str, limit: int, window_sec: int) -> bool:
        """True — лимит превышен, запрос нужно отклонить. Fixed-window счётчик в Redis
        (INCR + EXPIRE на первом попадании), отдельный ключ на chat_id+endpoint. Без Redis
        (REDIS_URL не задан) не ограничиваем — fallback-dict агента не переживает рестарт
        процесса и не годится для счётчика."""
        if max_agent is None:
            return False
        redis = await max_agent._get_redis()
        if redis is None:
            return False
        key = f"ratelimit:{endpoint}:{chat_id}"
        try:
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, window_sec)
            return count > limit
        except Exception:
            return False

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
        if url_token and config.DASHBOARD_TOKEN and hmac.compare_digest(url_token, config.DASHBOARD_TOKEN):
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
        if await _rate_limited(chat_id, "dashboard", limit=30, window_sec=60):
            return web.Response(status=429, text="Too Many Requests", headers=cors)
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

        data["bid_suggestions"] = []
        if max_agent is not None:
            try:
                data["bid_suggestions"] = await max_agent._collect_bid_suggestions_for_dashboard(chat_id)
            except Exception as e:
                logger.error(f"[dashboard] bid_suggestions error: {e}")

        data["catalog"] = {"products": [], "shop_kpi": {}}
        if max_agent is not None:
            try:
                data["catalog"] = await max_agent._collect_catalog_for_dashboard(chat_id)
            except Exception as e:
                logger.error(f"[dashboard] catalog error: {e}")

        # fin_payout/regions_wb/infographic_ctr нужны только Telegram-отчётам Питера
        # (json.dumps(data) целиком в промпт) — фронтенд дашборда их не рендерит,
        # не гоняем лишний вес по сети. margin_wb/margin_ozon оставляем — GROSS-фоллбэк MarginChart.
        for _dead_field in ("fin_payout", "regions_wb", "infographic_ctr"):
            data.pop(_dead_field, None)
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
        if url_token and config.DASHBOARD_TOKEN and hmac.compare_digest(url_token, config.DASHBOARD_TOKEN):
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
        if await _rate_limited(chat_id, "timeline", limit=30, window_sec=60):
            return web.Response(status=429, text="Too Many Requests", headers=cors)
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

    async def _handle_apply_price(request: web.Request) -> web.Response:
        """POST /api/apply_price — применить рекомендованную цену прямо с дашборда.

        Только настоящий Telegram initData, без ?token= (та ссылка — read-only доступ для
        коллег, она не должна давать право на реальную запись цены на маркетплейс от имени
        владельца). Само применение — тот же MaxAgent._apply_price, что и кнопка "Применить"
        в /reprice, поэтому поведение (какой API дёргается, что обновляется в БД) идентично.
        """
        cors = {"Access-Control-Allow-Origin": _CORS_ORIGIN}
        if request.method == "OPTIONS":
            return web.Response(headers={
                **cors,
                "Access-Control-Allow-Headers": "X-Telegram-Init-Data, Content-Type",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
            })
        init_data = request.headers.get("X-Telegram-Init-Data", "")
        parsed = _validate_init_data(init_data, config.MARTA_BOT_TOKEN)
        if parsed is None:
            return web.Response(status=401, text="Unauthorized", headers=cors)
        try:
            user = _json.loads(parsed.get("user", "{}"))
            chat_id = int(user["id"])
        except Exception:
            return web.Response(status=400, text="Bad Request", headers=cors)
        if await _rate_limited(chat_id, "apply_price", limit=10, window_sec=60):
            return web.Response(status=429, text="Too Many Requests", headers=cors)
        if max_agent is None:
            return web.Response(status=503, text="Max agent not running", headers=cors)
        try:
            body = await request.json()
            mp = body.get("marketplace")
            product_id = str(body.get("product_id", "")).strip()
            new_price = int(body.get("new_price"))
        except Exception:
            return web.Response(status=400, text="Bad Request", headers=cors)
        if mp not in ("wb", "ozon") or not product_id or new_price <= 0:
            return web.Response(status=400, text="Bad Request", headers=cors)

        # Тот же лок, что и у кнопки "Применить" в /reprice (reprice_apply:{mp}:{product_id}) —
        # защищает от двойного клика/сетевого ретрая, и от гонки между дашбордом и Telegram
        # одновременно (общий Redis-ключ).
        lock = f"reprice_apply:{mp}:{product_id}"
        if not await max_agent._redis_acquire_lock(lock, "1", ttl=60):
            return web.json_response({"ok": False, "error": "already_applying"}, status=409, headers=cors)

        result = await max_agent._apply_price(chat_id, mp, product_id, new_price)
        return web.json_response({"ok": result["ok"], "detail": result["detail"]}, headers=cors)

    async def _handle_get_costs(request: web.Request) -> web.Response:
        """GET /api/costs — таблица себестоимости (закупка+логистика, упаковка+маркировка) для дашборда.

        Только настоящий Telegram initData, без ?token= — та же логика, что у apply_price/apply_bid.
        """
        cors = {"Access-Control-Allow-Origin": _CORS_ORIGIN}
        if request.method == "OPTIONS":
            return web.Response(headers={
                **cors,
                "Access-Control-Allow-Headers": "X-Telegram-Init-Data",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
            })
        init_data = request.headers.get("X-Telegram-Init-Data", "")
        parsed = _validate_init_data(init_data, config.MARTA_BOT_TOKEN)
        if parsed is None:
            return web.Response(status=401, text="Unauthorized", headers=cors)
        try:
            user = _json.loads(parsed.get("user", "{}"))
            chat_id = int(user["id"])
        except Exception:
            return web.Response(status=400, text="Bad Request", headers=cors)
        if await _rate_limited(chat_id, "costs", limit=30, window_sec=60):
            return web.Response(status=429, text="Too Many Requests", headers=cors)
        try:
            from db import get_product_costs_for_dashboard
            rows = await get_product_costs_for_dashboard(chat_id)
            numeric_fields = (
                "cost_wb", "purchase_logistics_wb", "packaging_marking_wb",
                "cost_ozon", "purchase_logistics_ozon", "packaging_marking_ozon",
            )
            costs = []
            for row in rows:
                item = dict(row)
                for field in numeric_fields:
                    val = item.get(field)
                    item[field] = float(val) if val is not None else None
                costs.append(item)
        except Exception as e:
            logger.error(f"[costs] error: {e}", exc_info=True)
            return web.Response(status=500, text="Internal Error", headers=cors)
        return web.json_response({"costs": costs}, headers=cors)

    async def _handle_set_cost(request: web.Request) -> web.Response:
        """POST /api/set_cost — сохранить разбивку себестоимости (закупка+логистика,
        упаковка+маркировка) прямо с дашборда.

        Только настоящий Telegram initData, без ?token=. Лок не нужен — upsert одной
        строки в свою БД идемпотентен, в отличие от apply_price/apply_bid не ходит во
        внешний API маркетплейса.
        """
        cors = {"Access-Control-Allow-Origin": _CORS_ORIGIN}
        if request.method == "OPTIONS":
            return web.Response(headers={
                **cors,
                "Access-Control-Allow-Headers": "X-Telegram-Init-Data, Content-Type",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
            })
        init_data = request.headers.get("X-Telegram-Init-Data", "")
        parsed = _validate_init_data(init_data, config.MARTA_BOT_TOKEN)
        if parsed is None:
            return web.Response(status=401, text="Unauthorized", headers=cors)
        try:
            user = _json.loads(parsed.get("user", "{}"))
            chat_id = int(user["id"])
        except Exception:
            return web.Response(status=400, text="Bad Request", headers=cors)
        if await _rate_limited(chat_id, "set_cost", limit=10, window_sec=60):
            return web.Response(status=429, text="Too Many Requests", headers=cors)
        try:
            body = await request.json()
            mp = body.get("marketplace")
            product_id = str(body.get("product_id", "")).strip()
            purchase_logistics = float(body.get("purchase_logistics"))
            packaging_marking = float(body.get("packaging_marking"))
        except Exception:
            return web.Response(status=400, text="Bad Request", headers=cors)
        if mp not in ("wb", "ozon") or not product_id:
            return web.Response(status=400, text="Bad Request", headers=cors)
        if purchase_logistics < 0 or packaging_marking < 0:
            return web.Response(status=400, text="Bad Request", headers=cors)

        try:
            from db import get_pool, set_product_cost_breakdown
            pool = await get_pool()
            async with pool.acquire() as conn:
                if mp == "wb":
                    row = await conn.fetchrow(
                        "SELECT id FROM product_mapping WHERE wb_article = $1", product_id,
                    )
                else:
                    row = await conn.fetchrow(
                        "SELECT id FROM product_mapping WHERE ozon_offer_id = $1", product_id,
                    )
            if not row:
                return web.Response(status=404, text="Not Found", headers=cors)
            mapping_id = row["id"]
            await set_product_cost_breakdown(mapping_id, mp, purchase_logistics, packaging_marking)
        except Exception as e:
            logger.error(f"[set_cost] error: {e}", exc_info=True)
            return web.Response(status=500, text="Internal Error", headers=cors)
        return web.json_response({"ok": True}, headers=cors)

    async def _handle_apply_bid(request: web.Request) -> web.Response:
        """POST /api/apply_bid — применить корректировку рекламной ставки прямо с дашборда.

        Тот же принцип, что и apply_price: только настоящий Telegram initData, без ?token=.
        WB — MaxAgent._apply_wb_bid_raw (CPM кампании), Ozon — _apply_ozon_bid_raw
        (per-SKU ставки, нужен shop_id — в WB он не нужен, магазин находится по chat_id).
        """
        cors = {"Access-Control-Allow-Origin": _CORS_ORIGIN}
        if request.method == "OPTIONS":
            return web.Response(headers={
                **cors,
                "Access-Control-Allow-Headers": "X-Telegram-Init-Data, Content-Type",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
            })
        init_data = request.headers.get("X-Telegram-Init-Data", "")
        parsed = _validate_init_data(init_data, config.MARTA_BOT_TOKEN)
        if parsed is None:
            return web.Response(status=401, text="Unauthorized", headers=cors)
        try:
            user = _json.loads(parsed.get("user", "{}"))
            chat_id = int(user["id"])
        except Exception:
            return web.Response(status=400, text="Bad Request", headers=cors)
        if await _rate_limited(chat_id, "apply_bid", limit=10, window_sec=60):
            return web.Response(status=429, text="Too Many Requests", headers=cors)
        if max_agent is None:
            return web.Response(status=503, text="Max agent not running", headers=cors)
        try:
            body = await request.json()
            mp = body.get("marketplace")
            campaign_id = str(body.get("campaign_id", "")).strip()
            direction = body.get("direction")
            delta_pct = int(body.get("delta_pct"))
            shop_id = body.get("shop_id")
        except Exception:
            return web.Response(status=400, text="Bad Request", headers=cors)
        if mp not in ("wb", "ozon") or not campaign_id or direction not in ("up", "down"):
            return web.Response(status=400, text="Bad Request", headers=cors)
        if mp == "ozon" and not shop_id:
            return web.Response(status=400, text="Bad Request", headers=cors)

        # Тот же лок, что и в _handle_bid_callback/_handle_ozbid_callback (bid_apply:{mp}:{campaign_id})
        # — защищает от двойного клика и от гонки между дашбордом и Telegram одновременно.
        lock = f"bid_apply:{mp}:{campaign_id}"
        if not await max_agent._redis_acquire_lock(lock, "1", ttl=60):
            return web.json_response({"ok": False, "error": "already_applying"}, status=409, headers=cors)

        if mp == "wb":
            result = await max_agent._apply_wb_bid_raw(chat_id, campaign_id, direction, delta_pct)
        else:
            result = await max_agent._apply_ozon_bid_raw(chat_id, str(shop_id), campaign_id, direction, delta_pct)
        return web.json_response(result, headers=cors)

    async def _handle_create_product(request: web.Request) -> web.Response:
        """POST /api/product — создать/обновить товар в реестре прямо с дашборда.

        Заменяет /map (agents/max.py::cmd_map) и текстовую часть пошагового Redis-wizard
        /add (agents/max.py::cmd_add, `catalog_add:{chat_id}`) — тот же
        INSERT ... ON CONFLICT (display_name) DO UPDATE, что и там. Поведение 1:1: name —
        уникальный ключ (совпадение по display_name перезаписывает существующую строку),
        wb_article/ozon_offer_id перезаписываются присланным значением (в т.ч. пустым —
        как и в /map), category — COALESCE (пустое значение не затирает уже сохранённую
        категорию). Не переносит себестоимость — она уже отдельно в /api/set_cost.
        """
        cors = {"Access-Control-Allow-Origin": _CORS_ORIGIN}
        if request.method == "OPTIONS":
            return web.Response(headers={
                **cors,
                "Access-Control-Allow-Headers": "X-Telegram-Init-Data, Content-Type",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
            })
        init_data = request.headers.get("X-Telegram-Init-Data", "")
        parsed = _validate_init_data(init_data, config.MARTA_BOT_TOKEN)
        if parsed is None:
            return web.Response(status=401, text="Unauthorized", headers=cors)
        try:
            user = _json.loads(parsed.get("user", "{}"))
            chat_id = int(user["id"])
        except Exception:
            return web.Response(status=400, text="Bad Request", headers=cors)
        if await _rate_limited(chat_id, "product", limit=10, window_sec=60):
            return web.Response(status=429, text="Too Many Requests", headers=cors)
        try:
            body = await request.json()
            name = str(body.get("name", "")).strip()
            wb_article = str(body.get("wb_article", "")).strip() or None
            ozon_offer_id = str(body.get("ozon_offer_id", "")).strip() or None
            category = str(body.get("category", "")).strip() or None
        except Exception:
            return web.Response(status=400, text="Bad Request", headers=cors)
        if not name:
            return web.Response(status=400, text="Bad Request", headers=cors)
        try:
            from db import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO product_mapping (display_name, wb_article, ozon_offer_id, category)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (display_name)
                    DO UPDATE SET wb_article    = EXCLUDED.wb_article,
                                  ozon_offer_id = EXCLUDED.ozon_offer_id,
                                  category      = COALESCE(EXCLUDED.category, product_mapping.category)
                    """,
                    name, wb_article, ozon_offer_id, category,
                )
        except Exception as e:
            logger.error(f"[product] error: {e}", exc_info=True)
            return web.Response(status=500, text="Internal Error", headers=cors)
        return web.json_response({"ok": True}, headers=cors)

    async def _handle_merge_product(request: web.Request) -> web.Response:
        """POST /api/merge_product — связать WB-товар без Ozon-пары с уже существующим в
        реестре Ozon-товаром без WB-пары (например, автоматически подтянутым синком заказов).

        Заменяет /merge_products (agents/max.py::cmd_merge_products, inline-пикер
        mergewiz:*) — воспроизводит финальный шаг того же wizard'a: находит обе строки
        product_mapping (WB-только и Ozon-только) и сливает их db.merge_product_rows()
        (COALESCE-приоритет у WB-строки, конкатенация штрихкодов, удаление дублирующей
        Ozon-строки) — идентичная SQL-логика, что и у mergewiz:confirm:.
        Идентификаторы — натуральные ключи (wb_article/ozon_offer_id), а не внутренние id:
        catalog.products (вкладка «Каталог») их и так отдаёт, отдельный mapping_id туда не
        заводили ради одной формы.
        """
        cors = {"Access-Control-Allow-Origin": _CORS_ORIGIN}
        if request.method == "OPTIONS":
            return web.Response(headers={
                **cors,
                "Access-Control-Allow-Headers": "X-Telegram-Init-Data, Content-Type",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
            })
        init_data = request.headers.get("X-Telegram-Init-Data", "")
        parsed = _validate_init_data(init_data, config.MARTA_BOT_TOKEN)
        if parsed is None:
            return web.Response(status=401, text="Unauthorized", headers=cors)
        try:
            user = _json.loads(parsed.get("user", "{}"))
            chat_id = int(user["id"])
        except Exception:
            return web.Response(status=400, text="Bad Request", headers=cors)
        if await _rate_limited(chat_id, "merge_product", limit=10, window_sec=60):
            return web.Response(status=429, text="Too Many Requests", headers=cors)
        try:
            body = await request.json()
            wb_article = str(body.get("wb_article", "")).strip()
            ozon_offer_id = str(body.get("ozon_offer_id", "")).strip()
        except Exception:
            return web.Response(status=400, text="Bad Request", headers=cors)
        if not wb_article or not ozon_offer_id:
            return web.Response(status=400, text="Bad Request", headers=cors)
        try:
            from db import get_pool, merge_product_rows
            pool = await get_pool()
            async with pool.acquire() as conn:
                wb_row = await conn.fetchrow(
                    "SELECT id FROM product_mapping WHERE wb_article = $1 AND ozon_offer_id IS NULL",
                    wb_article,
                )
                ozon_row = await conn.fetchrow(
                    "SELECT id FROM product_mapping WHERE ozon_offer_id = $1 AND wb_article IS NULL",
                    ozon_offer_id,
                )
            if not wb_row or not ozon_row:
                return web.json_response({"ok": False, "error": "not_found"}, status=404, headers=cors)
            await merge_product_rows(wb_row["id"], ozon_row["id"])
        except Exception as e:
            logger.error(f"[merge_product] error: {e}", exc_info=True)
            return web.Response(status=500, text="Internal Error", headers=cors)
        return web.json_response({"ok": True}, headers=cors)

    async def _handle_add_shop(request: web.Request) -> web.Response:
        """POST /api/add_shop — подключить магазин WB/Ozon (API-токен продавца) с дашборда.

        Заменяет /add_shop (agents/max.py::cmd_add_shop) — тот же db.add_marketplace_shop.

        Токен — чувствительное поле (полный доступ к аккаунту продавца на маркетплейсе):
        приходит только в JSON body (POST, не query/URL, как и остальные write-эндпоинты
        здесь), никогда не логируется — даже в except ниже пишем только тип исключения,
        не str(e)/exc_info, чтобы значение не просочилось в agent_logs, если драйвер вдруг
        отразит переданный параметр в тексте ошибки. Rate limit жёстче обычного (5/60 —
        остальные write-эндпоинты 10/60), т.к. риск при злоупотреблении выше.
        """
        cors = {"Access-Control-Allow-Origin": _CORS_ORIGIN}
        if request.method == "OPTIONS":
            return web.Response(headers={
                **cors,
                "Access-Control-Allow-Headers": "X-Telegram-Init-Data, Content-Type",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
            })
        init_data = request.headers.get("X-Telegram-Init-Data", "")
        parsed = _validate_init_data(init_data, config.MARTA_BOT_TOKEN)
        if parsed is None:
            return web.Response(status=401, text="Unauthorized", headers=cors)
        try:
            user = _json.loads(parsed.get("user", "{}"))
            chat_id = int(user["id"])
        except Exception:
            return web.Response(status=400, text="Bad Request", headers=cors)
        if await _rate_limited(chat_id, "add_shop", limit=5, window_sec=60):
            return web.Response(status=429, text="Too Many Requests", headers=cors)
        try:
            body = await request.json()
            mp = body.get("marketplace")
            api_token = str(body.get("api_token", "")).strip()
            client_id = str(body.get("client_id", "")).strip() or None
            shop_name = str(body.get("shop_name", "")).strip() or None
        except Exception:
            return web.Response(status=400, text="Bad Request", headers=cors)
        if mp not in ("wb", "ozon") or not api_token:
            return web.Response(status=400, text="Bad Request", headers=cors)
        if mp == "ozon" and not client_id:
            return web.Response(status=400, text="Bad Request", headers=cors)
        try:
            from db import add_marketplace_shop
            await add_marketplace_shop(chat_id, mp, api_token, client_id=client_id, shop_name=shop_name)
        except Exception as e:
            # Токен нигде не логируем — даже str(e) намеренно не пишем, только тип
            # исключения, на случай если ошибка драйвера/БД отразит значение параметра.
            logger.error(
                f"[add_shop] chat_id={chat_id} marketplace={mp} не удалось сохранить: {type(e).__name__}"
            )
            return web.Response(status=500, text="Internal Error", headers=cors)
        return web.json_response({"ok": True}, headers=cors)

    dash_app = web.Application()
    dash_app.router.add_get("/api/dashboard", _handle_dashboard)
    dash_app.router.add_route("OPTIONS", "/api/dashboard", _handle_dashboard)
    dash_app.router.add_get("/api/timeline", _handle_timeline)
    dash_app.router.add_route("OPTIONS", "/api/timeline", _handle_timeline)
    dash_app.router.add_post("/api/apply_price", _handle_apply_price)
    dash_app.router.add_route("OPTIONS", "/api/apply_price", _handle_apply_price)
    dash_app.router.add_post("/api/apply_bid", _handle_apply_bid)
    dash_app.router.add_route("OPTIONS", "/api/apply_bid", _handle_apply_bid)
    dash_app.router.add_get("/api/costs", _handle_get_costs)
    dash_app.router.add_route("OPTIONS", "/api/costs", _handle_get_costs)
    dash_app.router.add_post("/api/set_cost", _handle_set_cost)
    dash_app.router.add_route("OPTIONS", "/api/set_cost", _handle_set_cost)
    dash_app.router.add_post("/api/product", _handle_create_product)
    dash_app.router.add_route("OPTIONS", "/api/product", _handle_create_product)
    dash_app.router.add_post("/api/merge_product", _handle_merge_product)
    dash_app.router.add_route("OPTIONS", "/api/merge_product", _handle_merge_product)
    dash_app.router.add_post("/api/add_shop", _handle_add_shop)
    dash_app.router.add_route("OPTIONS", "/api/add_shop", _handle_add_shop)
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
