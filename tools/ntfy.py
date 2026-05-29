"""
tools/ntfy.py — push-уведомления через ntfy.sh.
Отправляет нативные iOS/Android пуши без приложения.
"""
from __future__ import annotations

import asyncio

import aiohttp
from loguru import logger


def _latin1_safe(text: str) -> str:
    """HTTP заголовки должны быть latin-1. Кириллица и emoji — заменяем на '?'."""
    return text.encode("latin-1", errors="replace").decode("latin-1")


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

    url = f"https://ntfy.sh/{topic}"
    logger.debug(f"ntfy_request | url={url} | title={title!r}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                data=message.encode("utf-8"),
                headers={
                    "Title":        _latin1_safe(title),
                    "Priority":     priority,
                    "Tags":         "alarm_clock",
                    "Content-Type": "text/plain; charset=utf-8",
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                body = await resp.text()
                logger.debug(
                    f"ntfy_response | status={resp.status} | body={body[:100]}"
                )
                if resp.status == 200:
                    return True
                logger.error(f"ntfy_http_error | status={resp.status} | body={body[:200]}")
                return False

    except asyncio.TimeoutError:
        logger.error(f"ntfy_timeout | topic={topic!r}")
        return False
    except Exception as e:
        logger.error(f"ntfy_error | topic={topic!r} | error={type(e).__name__}: {e}")
        return False
