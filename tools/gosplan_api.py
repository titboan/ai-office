"""
tools/gosplan_api.py — async-клиент ГосПлан API v2 (https://v2.gosplan.info).

Бесплатный доступ до 1 августа 2026, далее потребуется GOSPLAN_API_KEY.
Rate limit: 600 req/min. Охватывает закупки по 44-ФЗ.

Документация: https://wiki.gosplan.info/Home
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
from loguru import logger

_BASE_URL    = "https://v2.gosplan.info"
_TIMEOUT     = aiohttp.ClientTimeout(total=30)
_MAX_RETRIES = 2

# Статусы тендеров ЕИС
STATUS_OPEN       = "Подача заявок"
STATUS_EVALUATION = "Рассмотрение заявок"
STATUS_COMPLETED  = "Завершена"
STATUS_CANCELED   = "Отменена"


class GosplanClient:
    """Async-клиент для ГосПлан API v2.

    Не требует инициализации аутентификации до 01.08.2026.
    Переиспользовать один экземпляр в рамках одного запроса.
    """

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Accept": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict | list | None:
        url = f"{_BASE_URL}{path}"
        for attempt in range(_MAX_RETRIES + 1):
            try:
                async with aiohttp.ClientSession(headers=self._headers(), timeout=_TIMEOUT) as session:
                    async with session.get(url, params=params) as resp:
                        if resp.status == 200:
                            return await resp.json()
                        if resp.status == 429:
                            logger.warning(f"[gosplan] Rate limit — ждём 10с")
                            await asyncio.sleep(10)
                            continue
                        text = await resp.text()
                        logger.error(f"[gosplan] HTTP {resp.status}: {text[:200]}")
                        return None
            except asyncio.TimeoutError:
                logger.warning(f"[gosplan] Timeout (попытка {attempt+1}/{_MAX_RETRIES+1})")
            except Exception as e:
                logger.error(f"[gosplan] Ошибка запроса: {e}")
                return None
        return None

    async def search_tenders(
        self,
        keyword: str = "",
        region_code: str = "",
        status: str = STATUS_OPEN,
        nmck_min: float = 0,
        nmck_max: float = 0,
        date_from: str = "",
        page: int = 1,
        per_page: int = 20,
    ) -> list[dict]:
        """Поиск тендеров по 44-ФЗ.

        Args:
            keyword: Ключевое слово (название товара/услуги).
            region_code: Код региона (23 = Краснодарский край).
            status: Статус закупки (по умолчанию «Подача заявок»).
            nmck_min: Минимальная НМЦК (руб.).
            nmck_max: Максимальная НМЦК (руб.). 0 = без ограничения.
            date_from: Дата публикации от (YYYY-MM-DD).
            page: Номер страницы.
            per_page: Тендеров на страницу (макс 100).

        Returns:
            Список тендеров. Пустой список если ничего не найдено или ошибка.
        """
        params: dict[str, Any] = {
            "page":    page,
            "perPage": min(per_page, 100),
        }
        if keyword:
            params["keyword"] = keyword
        if region_code:
            params["regionCode"] = region_code
        if status:
            params["status"] = status
        if nmck_min > 0:
            params["priceMin"] = nmck_min
        if nmck_max > 0:
            params["priceMax"] = nmck_max
        if date_from:
            params["datePublicationFrom"] = date_from

        logger.info(f"[gosplan] search_tenders: keyword={keyword!r} region={region_code} status={status!r}")
        data = await self._get("/fz44/lots", params=params)
        if data is None:
            return []
        if isinstance(data, list):
            return data
        # Обычно ответ: {"data": [...], "total": N, "page": N}
        if isinstance(data, dict):
            return data.get("data") or data.get("lots") or data.get("items") or []
        return []

    async def get_tender_detail(self, lot_id: str) -> dict | None:
        """Получить полную информацию о тендере по его ID.

        Returns:
            Словарь с данными тендера или None при ошибке.
        """
        logger.info(f"[gosplan] get_tender_detail: lot_id={lot_id!r}")
        return await self._get(f"/fz44/lots/{lot_id}")

    async def get_lot_documents(self, lot_id: str) -> list[dict]:
        """Получить список документов тендера (ТЗ, проект контракта).

        Returns:
            Список документов или пустой список.
        """
        logger.info(f"[gosplan] get_lot_documents: lot_id={lot_id!r}")
        data = await self._get(f"/fz44/lots/{lot_id}/documents")
        if data is None:
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("documents") or data.get("data") or []
        return []

    async def get_tender_participants(self, lot_id: str) -> list[dict]:
        """Получить список участников/победителей тендера.

        Returns:
            Список участников или пустой список.
        """
        logger.info(f"[gosplan] get_tender_participants: lot_id={lot_id!r}")
        data = await self._get(f"/fz44/lots/{lot_id}/participants")
        if data is None:
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("participants") or data.get("data") or []
        return []


def format_tender_summary(tender: dict) -> str:
    """Форматировать краткую карточку тендера для Telegram (HTML)."""
    title     = tender.get("lotName") or tender.get("name") or tender.get("title") or "—"
    lot_id    = tender.get("lotId") or tender.get("id") or ""
    nmck      = tender.get("initialPrice") or tender.get("nmck") or tender.get("price") or 0
    region    = tender.get("regionName") or tender.get("region") or ""
    deadline  = tender.get("submissionDeadline") or tender.get("deadline") or ""
    customer  = tender.get("customerName") or tender.get("customer") or ""
    status    = tender.get("status") or ""
    reg_num   = tender.get("regNumber") or tender.get("number") or ""

    nmck_fmt  = f"{float(nmck):,.0f}".replace(",", " ") if nmck else "—"
    deadline_short = deadline[:10] if deadline else "—"
    id_part   = reg_num or lot_id or "—"

    lines = [
        f"📋 <b>{title[:100]}</b>",
        f"💰 НМЦК: <b>{nmck_fmt} ₽</b>",
    ]
    if customer:
        lines.append(f"🏢 {customer[:80]}")
    if region:
        lines.append(f"📍 {region}")
    if deadline_short != "—":
        lines.append(f"⏰ Срок подачи: {deadline_short}")
    if status:
        lines.append(f"📌 Статус: {status}")
    if id_part != "—":
        lines.append(f"🔑 <code>{id_part}</code>")
    return "\n".join(lines)
