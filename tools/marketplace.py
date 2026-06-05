"""
tools/marketplace.py — клиенты для Wildberries и Ozon Seller API.

Оба клиента: retry 3 раза при 429/500, silent-fail через loguru.
asyncio.TimeoutError не ретраится — сразу None + лог.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import aiohttp
from loguru import logger

_RETRY_STATUSES = {429, 500, 502, 503}
_RETRY_COUNT = 3
_RETRY_DELAY = 2.0          # секунды между попытками
_TIMEOUT     = aiohttp.ClientTimeout(total=30)   # глобальный таймаут запроса
_TIMEOUT_CHECK = aiohttp.ClientTimeout(total=10) # таймаут для check_connection


async def _request(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    headers: dict,
    json: Any = None,
    params: dict | None = None,
    label: str = "",
) -> dict | None:
    """HTTP-запрос с retry при 429/5xx. TimeoutError → None без retry."""
    for attempt in range(1, _RETRY_COUNT + 1):
        try:
            async with session.request(
                method, url,
                headers=headers,
                json=json,
                params=params,
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                if resp.status in _RETRY_STATUSES and attempt < _RETRY_COUNT:
                    logger.warning(
                        f"[marketplace] {label} HTTP {resp.status}, retry {attempt}/{_RETRY_COUNT}"
                    )
                    await asyncio.sleep(_RETRY_DELAY * attempt)
                    continue
                raw = await resp.text()
                logger.error(f"[marketplace] {label} HTTP {resp.status}: {raw[:200]}")
                return None
        except asyncio.TimeoutError:
            logger.error(f"[marketplace] timeout: {method} {url}")
            return None
        except Exception as e:
            if attempt < _RETRY_COUNT:
                logger.warning(f"[marketplace] {label} exception ({attempt}): {e}")
                await asyncio.sleep(_RETRY_DELAY * attempt)
            else:
                logger.error(f"[marketplace] {label} failed after {_RETRY_COUNT} attempts: {e}")
    return None


# ── Wildberries ────────────────────────────────────────────────────────────────

class WBClient:
    _BASE = "https://feedbacks-api.wildberries.ru"

    def __init__(self, api_token: str) -> None:
        self._token = api_token

    def _headers(self) -> dict:
        return {"Authorization": self._token, "Content-Type": "application/json"}

    async def get_new_reviews(self, since: datetime) -> list[dict]:
        """Вернуть неотвеченные отзывы."""
        reviews: list[dict] = []
        async with aiohttp.ClientSession() as session:
            data = await _request(
                session, "GET",
                f"{self._BASE}/api/v1/feedbacks",
                headers=self._headers(),
                params={"isAnswered": "false", "take": 100, "skip": 0},
                label="WB.get_new_reviews",
            )
        if not data:
            return reviews

        for item in data.get("data", {}).get("feedbacks", []):
            created_raw = item.get("createdDate", "")
            try:
                created = datetime.fromisoformat(created_raw.rstrip("Z")).replace(
                    tzinfo=since.tzinfo
                )
                if created < since:
                    continue
            except Exception:
                pass

            reviews.append({
                "review_id":    item.get("id", ""),
                "product_id":   str(item.get("subjectId", "") or ""),
                "product_name": item.get("subjectName", ""),
                "rating":       item.get("productValuation", 0),
                "text":         item.get("text", ""),
                "author":       item.get("userName", ""),
            })
        return reviews

    async def send_reply(self, review_id: str, text: str) -> bool:
        url = f"{self._BASE}/api/v1/feedbacks"
        body = {"id": review_id, "text": text}
        async with aiohttp.ClientSession() as session:
            # Пробуем PATCH; если 405 — пробуем POST
            for method in ("PATCH", "POST"):
                try:
                    async with session.request(
                        method, url,
                        headers=self._headers(),
                        json=body,
                        timeout=_TIMEOUT,
                    ) as resp:
                        raw = await resp.text()
                        if resp.status == 200:
                            return True
                        if resp.status == 405 and method == "PATCH":
                            logger.warning("[WB.send_reply] PATCH→405, пробую POST")
                            continue
                        logger.error(
                            f"[WB.send_reply({review_id[:8]})] {method} {resp.status}: {raw[:300]}"
                        )
                        return False
                except asyncio.TimeoutError:
                    logger.error(f"[marketplace] timeout: {method} {url}")
                    return False
                except Exception as e:
                    logger.error(f"[WB.send_reply] {method} exception: {e}")
                    return False
        return False

    async def check_connection(self) -> bool:
        """Проверить валидность токена (тестовый запрос)."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._BASE}/api/v1/feedbacks",
                    headers=self._headers(),
                    params={"isAnswered": "false", "take": 1, "skip": 0},
                    timeout=_TIMEOUT_CHECK,
                ) as resp:
                    logger.debug(f"[WB.check_connection] status={resp.status}")
                    return resp.status == 200
        except asyncio.TimeoutError:
            logger.error(f"[marketplace] timeout: GET {self._BASE}/api/v1/feedbacks")
            return False
        except Exception as e:
            logger.warning(f"[WB.check_connection] exception: {e}")
            return False


# ── Ozon ──────────────────────────────────────────────────────────────────────

# Минимальный limit по API Ozon v1/review/list = 20
_OZON_LIMIT = 20

class OzonClient:
    _BASE = "https://api-seller.ozon.ru"

    def __init__(self, api_token: str, client_id: str) -> None:
        self._token = api_token
        self._client_id = client_id

    def _headers(self) -> dict:
        return {
            "Client-Id":    self._client_id,
            "Api-Key":      self._token,
            "Content-Type": "application/json",
        }

    async def get_new_reviews(self, since: datetime) -> list[dict]:
        reviews: list[dict] = []
        async with aiohttp.ClientSession() as session:
            data = await _request(
                session, "POST",
                f"{self._BASE}/v1/review/list",
                headers=self._headers(),
                json={"sort_dir": "DESC", "limit": _OZON_LIMIT, "offset": 0},
                label="Ozon.get_new_reviews",
            )
        if not data:
            return reviews

        for item in data.get("reviews", []):
            created_raw = item.get("created_at", "")
            try:
                created = datetime.fromisoformat(created_raw.rstrip("Z")).replace(
                    tzinfo=since.tzinfo
                )
                if created < since:
                    continue
            except Exception:
                pass

            reviews.append({
                "review_id":    item.get("review_uuid", ""),
                "product_id":   str(item.get("sku", "") or ""),
                "product_name": item.get("product_name", ""),
                "rating":       item.get("rating", 0),
                "text":         (item.get("text") or {}).get("positive", "") or "",
                "author":       item.get("reviewer_name", ""),
            })
        return reviews

    async def send_reply(self, review_id: str, text: str) -> bool:
        async with aiohttp.ClientSession() as session:
            data = await _request(
                session, "POST",
                f"{self._BASE}/v1/review/seller-comment/create",
                headers=self._headers(),
                json={"review_uuid": review_id, "text": text},
                label=f"Ozon.send_reply({review_id[:8]})",
            )
        return data is not None

    async def check_connection(self) -> bool:
        """Проверить валидность токена.
        200/400 → credentials верны; 401/403 → неверные.
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._BASE}/v1/review/list",
                    headers=self._headers(),
                    json={"sort_dir": "DESC", "limit": _OZON_LIMIT, "offset": 0},
                    timeout=_TIMEOUT_CHECK,
                ) as resp:
                    logger.debug(f"[Ozon.check_connection] status={resp.status}")
                    return resp.status in (200, 400)
        except asyncio.TimeoutError:
            logger.error(f"[marketplace] timeout: POST {self._BASE}/v1/review/list")
            return False
        except Exception as e:
            logger.warning(f"[Ozon.check_connection] exception: {e}")
            return False


def make_client(shop: dict):
    """Фабрика: dict из marketplace_shops → WBClient или OzonClient."""
    mp    = shop["marketplace"]
    token = shop["api_token"]
    if mp == "wb":
        return WBClient(token)
    if mp == "ozon":
        return OzonClient(token, shop.get("client_id") or "")
    raise ValueError(f"Неизвестный маркетплейс: {mp}")
