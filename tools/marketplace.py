"""
tools/marketplace.py — клиенты для Wildberries и Ozon Seller API.

Оба клиента: retry 3 раза при 429/500, silent-fail через loguru.
asyncio.TimeoutError не ретраится — сразу None + лог.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
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

    async def get_new_reviews(self, since: datetime | None = None, max_rating: int = 5) -> list[dict]:
        """Вернуть неотвеченные отзывы. max_rating фильтрует по рейтингу ≤ N."""
        if since is None:
            since = datetime.now(timezone.utc) - timedelta(days=7)
        reviews: list[dict] = []
        url    = f"{self._BASE}/api/v1/feedbacks"
        params = {"isAnswered": "false", "take": 100, "skip": 0}
        logger.debug(f"[WB.get_new_reviews] since={since}, params={params}")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=self._headers(),
                    params=params,
                    timeout=_TIMEOUT,
                ) as resp:
                    text = await resp.text()
                    logger.debug(
                        f"[WB.get_new_reviews] status={resp.status}, body[:300]={text[:300]}"
                    )
                    if resp.status != 200:
                        return reviews
                    import json as _json
                    data = _json.loads(text)
        except asyncio.TimeoutError:
            logger.error(f"[marketplace] timeout: GET {url}")
            return reviews
        except Exception as e:
            logger.error(f"[WB.get_new_reviews] exception: {e}")
            return reviews
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

            parts = []
            if item.get("text"): parts.append(item["text"])
            if item.get("pros"): parts.append(f"Плюсы: {item['pros']}")
            if item.get("cons"): parts.append(f"Минусы: {item['cons']}")
            full_text = "\n".join(parts) if parts else ""

            rating = item.get("productValuation", 0)
            if rating > max_rating:
                continue

            reviews.append({
                "review_id":    item.get("id", ""),
                "product_id":   str(item.get("subjectId", "") or ""),
                "product_name": item.get("subjectName", ""),
                "rating":       rating,
                "text":         full_text,
                "author":       item.get("userName", ""),
            })
        return reviews

    async def send_reply(self, review_id: str, text: str) -> bool:
        url = f"{self._BASE}/api/v1/feedbacks/answer"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=self._headers(),
                    json={"id": review_id, "text": text},
                    timeout=_TIMEOUT,
                ) as resp:
                    raw = await resp.text()
                    if resp.status in (200, 204):
                        return True
                    logger.error(f"[WB.send_reply({review_id[:8]})] POST {resp.status}: {raw[:300]}")
                    return False
        except asyncio.TimeoutError:
            logger.error(f"[marketplace] timeout: POST {url}")
            return False
        except Exception as e:
            logger.error(f"[WB.send_reply] exception: {e}")
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


    async def get_stocks(self, statistics_token: str) -> list[dict]:
        """Остатки по складам. Требует отдельный Statistics API токен."""
        _STATS_BASE = "https://statistics-api.wildberries.ru"
        stats_headers = {"Authorization": f"Bearer {statistics_token}", "Content-Type": "application/json"}
        date_from = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00")
        async with aiohttp.ClientSession() as session:
            data = await _request(
                session, "GET",
                f"{_STATS_BASE}/api/v1/supplier/stocks",
                headers=stats_headers,
                params={"dateFrom": date_from},
                label="WB.get_stocks",
            )
        if not data:
            return []
        results = []
        for item in (data if isinstance(data, list) else []):
            results.append({
                "product_id":    str(item.get("nmId", "")),
                "product_name":  item.get("subject", "") or item.get("supplierArticle", ""),
                "warehouse_name": item.get("warehouseName", ""),
                "stock":         int(item.get("quantity", 0)),
                "reserved":      int(item.get("inWayToClient", 0)) + int(item.get("inWayFromClient", 0)),
            })
        return results

    async def get_sales(self, date_from: datetime, statistics_token: str) -> list[dict]:
        """Выкупленные заказы через Statistics API."""
        _STATS_BASE = "https://statistics-api.wildberries.ru"
        stats_headers = {"Authorization": f"Bearer {statistics_token}", "Content-Type": "application/json"}
        df_str = date_from.strftime("%Y-%m-%dT00:00:00")
        async with aiohttp.ClientSession() as session:
            data = await _request(
                session, "GET",
                f"{_STATS_BASE}/api/v1/supplier/sales",
                headers=stats_headers,
                params={"dateFrom": df_str, "flag": 1},
                label="WB.get_sales",
            )
        if not data:
            return []
        results = []
        for item in (data if isinstance(data, list) else []):
            finished = float(item.get("finishedPrice", 0) or 0)
            for_pay  = float(item.get("forPay", 0) or 0)
            results.append({
                "order_id":    item.get("srid", "") or item.get("odid", ""),
                "product_id":  str(item.get("nmId", "")),
                "product_name": item.get("subject", "") or item.get("supplierArticle", ""),
                "quantity":    int(item.get("quantity", 1) or 1),
                "price":       finished,
                "commission":  round(for_pay - finished, 2),
                "sale_date":   item.get("lastChangeDate", ""),
            })
        return results


# ── Ozon ──────────────────────────────────────────────────────────────────────

# Минимальный limit по API Ozon v2/review/list = 20
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
        now = datetime.now(since.tzinfo)
        reviews: list[dict] = []
        async with aiohttp.ClientSession() as session:
            data = await _request(
                session, "POST",
                f"{self._BASE}/v2/review/list",
                headers=self._headers(),
                json={
                    "sort_dir": "DESC",
                    "filter": {
                        "posting_date": {
                            "time_from": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "time_to":   now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        }
                    },
                    "limit":  _OZON_LIMIT,
                    "offset": 0,
                },
                label="Ozon.get_new_reviews",
            )
        if not data:
            return reviews

        for item in data.get("reviews", []):
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
                f"{self._BASE}/v1/review/comment/create",
                headers=self._headers(),
                json={"review_id": review_id, "text": text},
                label=f"Ozon.send_reply({review_id[:8]})",
            )
        return data is not None

    async def check_connection(self) -> bool:
        """Проверить валидность токена.
        200/400 → credentials верны; 401/403 → неверные.
        """
        url = f"{self._BASE}/v2/review/list"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=self._headers(),
                    json={"sort_dir": "DESC", "filter": {}, "limit": _OZON_LIMIT, "offset": 0},
                    timeout=_TIMEOUT_CHECK,
                ) as resp:
                    raw = await resp.text()
                    logger.debug(f"[Ozon.check_connection] status={resp.status} body={raw[:200]!r}")
                    return resp.status in (200, 400)
        except asyncio.TimeoutError:
            logger.error(f"[marketplace] timeout: POST {url}")
            return False
        except Exception as e:
            logger.warning(f"[Ozon.check_connection] exception: {e}")
            return False


    async def get_stocks(self, **_) -> list[dict]:
        """Остатки по складам через analytics API."""
        async with aiohttp.ClientSession() as session:
            data = await _request(
                session, "POST",
                f"{self._BASE}/v1/analytics/stock_on_warehouses",
                headers=self._headers(),
                json={"limit": 100, "offset": 0},
                label="Ozon.get_stocks",
            )
        if not data:
            return []
        results = []
        for item in data.get("result", {}).get("rows", []):
            results.append({
                "product_id":    str(item.get("offer_id", "")),
                "product_name":  item.get("item_name", "") or item.get("title", ""),
                "warehouse_name": item.get("warehouse_name", ""),
                "stock":         int(item.get("free_to_sell_amount", 0) or item.get("for_sale", 0)),
                "reserved":      int(item.get("reserved_amount", 0)),
            })
        return results

    async def get_sales(self, date_from: datetime, **_) -> list[dict]:
        """Выкупленные заказы через finance/realization."""
        async with aiohttp.ClientSession() as session:
            data = await _request(
                session, "POST",
                f"{self._BASE}/v1/finance/realization",
                headers=self._headers(),
                json={"month": date_from.month, "year": date_from.year},
                label="Ozon.get_sales",
            )
        if not data:
            return []
        results = []
        for item in data.get("result", {}).get("rows", []):
            price = float(item.get("sale_commission", 0) or 0)
            results.append({
                "order_id":    str(item.get("posting_number", "") or item.get("order_id", "")),
                "product_id":  str(item.get("offer_id", "")),
                "product_name": item.get("item_name", ""),
                "quantity":    int(item.get("quantity", 1) or 1),
                "price":       float(item.get("price", 0) or 0),
                "commission":  price,
                "sale_date":   item.get("accepted_date", ""),
            })
        return results


def make_client(shop: dict):
    """Фабрика: dict из marketplace_shops → WBClient или OzonClient."""
    mp    = shop["marketplace"]
    token = shop["api_token"]
    if mp == "wb":
        return WBClient(token)
    if mp == "ozon":
        return OzonClient(token, shop.get("client_id") or "")
    raise ValueError(f"Неизвестный маркетплейс: {mp}")
