"""
utils/loop_health.py — алерт владельцу, если один и тот же фоновый цикл
(синк WB/Ozon и т.п. в main.py) повторно падает для одного и того же
пользователя. Раньше такие ошибки только логировались в Railway — если,
например, протухал токен WB/Ozon, синк мог молча не работать днями.
"""
from __future__ import annotations

from loguru import logger

from config import config
from tools.ntfy import send_push

_ALERT_AFTER = 3          # сколько сбоев подряд перед первым алертом
_RENOTIFY_EVERY = 10       # повторный алерт каждые N сбоев, если проблема не уходит

_failure_counts: dict[str, int] = {}


async def report_loop_failure(key: str, error: Exception) -> None:
    """Вызывать из except-блока фонового цикла при ошибке для конкретного chat_id."""
    count = _failure_counts.get(key, 0) + 1
    _failure_counts[key] = count
    if count == _ALERT_AFTER or count % _RENOTIFY_EVERY == 0:
        if not config.NTFY_TOPIC:
            logger.warning(f"[loop_health] {key}: {count} сбоев подряд, но NTFY_TOPIC не настроен")
            return
        await send_push(
            title=f"⚠️ {key}: {count} сбоев подряд",
            message=str(error)[:300],
            topic=config.NTFY_TOPIC,
            priority="high",
        )


def report_loop_success(key: str) -> None:
    """Вызывать после успешного прохода цикла — сбрасывает счётчик сбоев."""
    _failure_counts.pop(key, None)
