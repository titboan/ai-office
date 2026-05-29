"""
tools/ntfy.py — push-уведомления через ntfy.sh.
Отправляет нативные iOS/Android пуши без приложения.
"""
from __future__ import annotations

import aiohttp
from loguru import logger


async def send_push(
    title: str,
    message: str,
    topic: str,
    priority: str = "default",  # min / low / default / high / urgent
) -> bool:
    """Отправить push-уведомление через ntfy.sh.

    Returns True если HTTP 200, False при ошибке.
    """
    if not topic:
        logger.warning("[ntfy] topic не задан — push не отправлен")
        return False

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"https://ntfy.sh/{topic}",
                data=message.encode("utf-8"),
                headers={
                    "Title":        title,
                    "Priority":     priority,
                    "Tags":         "alarm_clock",
                    "Content-Type": "text/plain; charset=utf-8",
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    logger.info(f"[ntfy] push sent | topic={topic!r} | title={title!r}")
                    return True
                raw = await resp.text()
                logger.error(f"[ntfy] HTTP {resp.status}: {raw[:200]}")
                return False
    except Exception as e:
        logger.error(f"[ntfy] exception: {e}")
        return False
