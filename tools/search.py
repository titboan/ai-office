"""
tools/search.py — веб-поиск через Tavily API.

Используется агентом Каспер для получения актуальной информации из интернета.
Если TAVILY_API_KEY не задан — возвращает заглушку без ошибки.
"""

from __future__ import annotations

import asyncio

from loguru import logger
from tavily import AsyncTavilyClient

from config import config

# Количество результатов, которые попадают в контекст Клода
_MAX_RESULTS: int = 3


async def search_web(query: str) -> str:
    """Выполнить веб-поиск и вернуть топ-3 результата в виде текста.

    Args:
        query: Поисковый запрос на любом языке.

    Returns:
        Отформатированная строка с результатами — готова для вставки в промпт.
        Если ключ не задан или поиск упал — возвращает сообщение об ошибке
        (не поднимает исключение, чтобы Каспер мог ответить без поиска).
    """
    if not config.TAVILY_API_KEY:
        logger.warning("[search_web] TAVILY_API_KEY не задан — поиск недоступен")
        return "⚠️ Веб-поиск недоступен: TAVILY_API_KEY не задан."

    logger.info(f"[search_web] Запрос: {query!r}")

    try:
        client = AsyncTavilyClient(api_key=config.TAVILY_API_KEY)
        try:
            response = await asyncio.wait_for(
                client.search(
                    query=query,
                    max_results=_MAX_RESULTS,
                    search_depth="advanced",
                    include_answer=True,
                ),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            raise Exception("Tavily timeout")

        parts: list[str] = []

        # Краткий синтез от Tavily (если есть)
        if response.get("answer"):
            parts.append(f"📌 Краткий ответ: {response['answer']}\n")

        # Топ результаты
        results: list[dict] = response.get("results", [])
        if not results:
            return "🔍 Поиск не дал результатов по данному запросу."

        for i, r in enumerate(results[:_MAX_RESULTS], start=1):
            title   = r.get("title",   "Без заголовка")
            url     = r.get("url",     "")
            content = r.get("content", "").strip()

            # Обрезаем контент до 400 символов — не захламляем контекст
            if len(content) > 400:
                content = content[:400].rsplit(" ", 1)[0] + "…"

            parts.append(f"{i}. **{title}**\n   {url}\n   {content}")

        formatted = "\n\n".join(parts)
        logger.info(f"[search_web] Получено {len(results)} результатов")
        return formatted

    except Exception as e:
        logger.error(f"[search_web] Ошибка поиска: {e}")
        return f"⚠️ Ошибка веб-поиска: {e}"
