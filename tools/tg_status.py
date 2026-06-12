"""Telegram pinned message status updater.

Keeps a single pinned message in OFFICE_GROUP_ID up-to-date with agent
statuses. Redis key `status:pinned_msg_id` stores the current message id.
"""

from __future__ import annotations

from datetime import datetime, timezone as _tz
from zoneinfo import ZoneInfo

from loguru import logger
from telegram import Bot
from telegram.error import BadRequest, TelegramError

from config import config
from utils.tg_format import bold, code, escape

_REDIS_KEY = "status:pinned_msg_id"

_AGENT_EMOJI = {
    "kasper": "🔍", "kevin": "👨‍💻", "peter": "📊",
    "elina": "✍️", "alex": "🗓️", "marta": "👩‍💼",
    "dan": "🎨", "eva": "📰", "max": "🛒",
}
_AGENT_NAMES = {
    "kasper": "Каспер", "kevin": "Кевин", "peter": "Питер",
    "elina": "Элина", "alex": "Алекс", "marta": "Марта",
    "dan": "Дэн", "eva": "Ева", "max": "Макс",
}
_ALL_AGENTS = ["marta", "kasper", "kevin", "peter", "elina", "alex", "dan", "eva", "max"]


def _build_status_text(active_tasks: list, recent_tasks: list) -> str:
    now = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m %H:%M")

    busy_agents = {
        t["assigned_agent"] for t in active_tasks
        if t.get("status") in ("running", "acknowledged")
    }
    completed_today = len(recent_tasks)
    errors_today = len([t for t in recent_tasks if t.get("status") == "failed"])

    lines: list[str] = []
    lines.append(f"🏢 {bold('AI Office')} — статус офиса")
    lines.append(f"<i>обновлено {escape(now)} МСК</i>\n")

    lines.append(bold("Метрики"))
    lines.append(
        f"агентов: {code(str(len(_ALL_AGENTS)))}  "
        f"в работе: {code(str(len(busy_agents)))}  "
        f"выполнено: {code(str(completed_today))}  "
        f"ошибок: {code(str(errors_today))}"
    )
    lines.append("")

    lines.append(bold("Агенты"))
    for agent_key in _ALL_AGENTS:
        emoji = _AGENT_EMOJI.get(agent_key, "🤖")
        name = _AGENT_NAMES.get(agent_key, agent_key)
        if agent_key in busy_agents:
            task = next((t for t in active_tasks if t["assigned_agent"] == agent_key), None)
            if task:
                payload = task["payload"]
                short = (payload[:50] + "…") if len(payload) > 50 else payload
                created_at = task["created_at"]
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=_tz.utc)
                wait = datetime.now(_tz.utc) - created_at
                wait_str = (
                    f"{int(wait.total_seconds() // 60)} мин"
                    if wait.total_seconds() >= 60
                    else f"{int(wait.total_seconds())} сек"
                )
                lines.append(f"⚙️ {emoji} {bold(name)} — {escape(short)} ({wait_str})")
            else:
                lines.append(f"⚙️ {emoji} {bold(name)} — в работе")
        else:
            lines.append(f"✅ {emoji} {name} — свободен")

    if recent_tasks:
        lines.append("")
        lines.append(bold("Последние задачи"))
        for task in recent_tasks[:5]:
            a_emoji = _AGENT_EMOJI.get(task["assigned_agent"], "🤖")
            payload = task["payload"]
            short = (payload[:50] + "…") if len(payload) > 50 else payload
            finished_at = task.get("finished_at")
            finished = (
                finished_at.astimezone(ZoneInfo("Europe/Moscow")).strftime("%H:%M")
                if finished_at else "—"
            )
            status_icon = "✅" if task.get("status") == "completed" else "❌"
            lines.append(f"{status_icon} {a_emoji} {escape(short)} [{finished}]")

    return "\n".join(lines)


async def update_status_pinned(
    bot: Bot,
    redis,
    active_tasks: list,
    recent_tasks: list,
) -> None:
    chat_id = config.OFFICE_GROUP_ID
    if not chat_id:
        return

    text = _build_status_text(active_tasks, recent_tasks)

    async def _send_and_pin() -> int:
        msg = await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        try:
            await bot.pin_chat_message(chat_id=chat_id, message_id=msg.message_id, disable_notification=True)
        except TelegramError as e:
            logger.warning(f"[tg_status] pin failed: {e}")
        return msg.message_id

    # Try to get existing pinned message id from Redis
    msg_id: int | None = None
    if redis:
        try:
            stored = await redis.get(_REDIS_KEY)
            if stored:
                msg_id = int(stored)
        except Exception as e:
            logger.warning(f"[tg_status] redis get error: {e}")

    if msg_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=text,
                parse_mode="HTML",
            )
            return
        except BadRequest as e:
            logger.warning(f"[tg_status] edit failed ({e}), sending new message")
        except TelegramError as e:
            logger.warning(f"[tg_status] edit error: {e}")
            return

    # Send new and pin
    try:
        new_id = await _send_and_pin()
        if redis:
            try:
                await redis.set(_REDIS_KEY, str(new_id))
            except Exception as e:
                logger.warning(f"[tg_status] redis set error: {e}")
    except TelegramError as e:
        logger.error(f"[tg_status] send failed: {e}")
