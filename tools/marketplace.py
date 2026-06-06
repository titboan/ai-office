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
        url = f"{_STATS_BASE}/api/v1/supplier/stocks"
        data = None
        for attempt in range(2):
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=stats_headers,
                    params={"dateFrom": date_from},
                    timeout=_TIMEOUT,
                ) as resp:
                    if resp.status == 429:
                        logger.warning("[WB.get_stocks] rate limit, жду 60 сек")
                        await asyncio.sleep(60)
                        continue
                    raw = await resp.text()
                    if resp.status != 200:
                        logger.error(f"[WB.get_stocks] HTTP {resp.status}: {raw[:200]}")
                        return []
                    import json as _json
                    data = _json.loads(raw)
                    break
        if not data:
            return []
        results = []
        for item in (data if isinstance(data, list) else []):
            supplier_article = str(item.get("supplierArticle") or "").strip()
            product_id = supplier_article if supplier_article else str(item.get("nmId", ""))
            results.append({
                "product_id":    product_id,
                "product_name":  item.get("subject", "") or supplier_article,
                "warehouse_name": item.get("warehouseName", ""),
                "stock":         int(item.get("quantity", 0)),
                "reserved":      int(item.get("inWayToClient", 0)) + int(item.get("inWayFromClient", 0)),
            })
        return results

    async def get_orders(self, date_from: datetime, statistics_token: str) -> list[dict]:
        """Новые заказы через Statistics API (isCancel=false)."""
        _STATS_BASE = "https://statistics-api.wildberries.ru"
        stats_headers = {"Authorization": f"Bearer {statistics_token}", "Content-Type": "application/json"}
        df_str = date_from.strftime("%Y-%m-%dT%H:%M:%S")
        url = f"{_STATS_BASE}/api/v1/supplier/orders"
        data = None
        for attempt in range(2):
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=stats_headers,
                    params={"dateFrom": df_str, "flag": 1},
                    timeout=_TIMEOUT,
                ) as resp:
                    if resp.status == 429:
                        logger.warning("[WB.get_orders] rate limit, жду 60 сек")
                        await asyncio.sleep(60)
                        continue
                    raw = await resp.text()
                    if resp.status != 200:
                        logger.error(f"[WB.get_orders] HTTP {resp.status}: {raw[:200]}")
                        return []
                    import json as _json
                    data = _json.loads(raw)
                    logger.info(f"[WB.get_orders] HTTP {resp.status}, записей в ответе: {len(data) if isinstance(data, list) else '?'}")
                    break
        if not data:
            return []
        results = []
        for item in (data if isinstance(data, list) else []):
            if item.get("isCancel"):
                continue
            supplier_article = str(item.get("supplierArticle") or "").strip()
            results.append({
                "order_id":    str(item.get("srid", "") or item.get("orderId", "")),
                "product_id":  supplier_article or str(item.get("nmId", "")),
                "product_name": item.get("subject", "") or supplier_article,
                "price":       float(item.get("priceWithDisc") or item.get("finishedPrice") or (item.get("totalPrice", 0) or 0)),
                "order_date":  item.get("lastChangeDate", ""),
            })
        logger.info(f"[WB.get_orders] итого не отменённых: {len(results)}")
        return results

    async def get_orders_all(self, date_from: datetime, statistics_token: str) -> list[dict]:
        """Все заказы через Statistics API (flag=0 — все изменения за период включая сегодня)."""
        _STATS_BASE = "https://statistics-api.wildberries.ru"
        stats_headers = {"Authorization": f"Bearer {statistics_token}", "Content-Type": "application/json"}
        df_str = date_from.strftime("%Y-%m-%dT%H:%M:%S")
        url = f"{_STATS_BASE}/api/v1/supplier/orders"
        logger.info(f"[WB.get_orders_all] GET {url} dateFrom={df_str}")
        data = None
        for attempt in range(2):
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=stats_headers,
                    params={"dateFrom": df_str, "flag": 0},
                    timeout=_TIMEOUT,
                ) as resp:
                    if resp.status == 429:
                        logger.warning("[WB.get_orders_all] rate limit, жду 60 сек")
                        await asyncio.sleep(60)
                        continue
                    raw = await resp.text()
                    logger.info(f"[WB.get_orders_all] HTTP {resp.status}, тело: {raw[:200]}")
                    if resp.status != 200:
                        logger.error(f"[WB.get_orders_all] HTTP {resp.status}: {raw[:200]}")
                        return []
                    import json as _json
                    data = _json.loads(raw)
                    break
        if not data:
            return []
        orders_raw = data if isinstance(data, list) else []
        if orders_raw:
            logger.info(f"[WB.get_orders_all] first order keys: {list(orders_raw[0].keys())}")
            logger.info(f"[WB.get_orders_all] first order sample: {orders_raw[0]}")
        results = []
        for item in orders_raw:
            if item.get("isCancel"):
                continue
            supplier_article = str(item.get("supplierArticle") or "").strip()
            results.append({
                "order_id":    str(item.get("srid", "") or item.get("orderId", "")),
                "product_id":  supplier_article or str(item.get("nmId", "")),
                "product_name": item.get("subject", "") or supplier_article,
                "quantity":    int(item.get("quantity", 1) or 1),
                "price":       float(item.get("finishedPrice") or 0),
                "order_date":  item.get("lastChangeDate", ""),
            })
        logger.info(f"[WB.get_orders_all] итого не отменённых: {len(results)}")
        logger.info(f"[WB.get_orders_all] sample order_ids: {[o.get('order_id') for o in results[:3]]}")
        return results

    async def get_sales(self, date_from: datetime, statistics_token: str) -> list[dict]:
        """Выкупленные заказы через Statistics API."""
        _STATS_BASE = "https://statistics-api.wildberries.ru"
        stats_headers = {"Authorization": f"Bearer {statistics_token}", "Content-Type": "application/json"}
        df_str = date_from.strftime("%Y-%m-%dT00:00:00")
        url = f"{_STATS_BASE}/api/v1/supplier/sales"
        data = None
        for attempt in range(2):
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=stats_headers,
                    params={"dateFrom": df_str, "flag": 1},
                    timeout=_TIMEOUT,
                ) as resp:
                    if resp.status == 429:
                        logger.warning("[WB.get_sales] rate limit, жду 60 сек")
                        await asyncio.sleep(60)
                        continue
                    raw = await resp.text()
                    if resp.status != 200:
                        logger.error(f"[WB.get_sales] HTTP {resp.status}: {raw[:200]}")
                        return []
                    import json as _json
                    data = _json.loads(raw)
                    break
        if not data:
            return []
        results = []
        skipped_returns = 0
        for item in (data if isinstance(data, list) else []):
            if item.get("saleID", "").startswith("R"):
                skipped_returns += 1
                continue
            for_pay = float(item.get("forPay", 0) or 0)
            results.append({
                "order_id":    item.get("srid", "") or item.get("odid", ""),
                "product_id":  str(item.get("nmId", "")),
                "product_name": item.get("subject", "") or item.get("supplierArticle", ""),
                "quantity":    int(item.get("quantity", 1) or 1),
                "price":       for_pay,
                "commission":  0.0,
                "sale_date":   item.get("lastChangeDate", ""),
            })
        logger.info(f"[WB.get_sales] продаж: {len(results)}, возвратов пропущено: {skipped_returns}")
        return results

    async def get_ad_stats(self, date_from: str, date_to: str) -> list[dict]:
        """Статистика рекламных кампаний WB за период через /adv/v3/fullstats."""
        import json as _json
        _ADV_BASE = "https://advert-api.wildberries.ru"
        adv_headers = {"Authorization": self._token, "Content-Type": "application/json"}

        # Шаг 1: получить ID активных кампаний через /adv/v1/promotion/count
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{_ADV_BASE}/adv/v1/promotion/count",
                headers=adv_headers,
                timeout=_TIMEOUT,
            ) as resp:
                raw = await resp.text()
                if resp.status != 200:
                    logger.error(f"[WB.get_ad_stats] count HTTP {resp.status}: {raw[:200]}")
                    return []
                count_data = _json.loads(raw)

        # Статусы 7 (пауза), 9 (активна), 11 (на паузе без бюджета) — fullstats поддерживает все три
        campaign_ids = []
        for group in (count_data.get("adverts") or []):
            if group.get("status") in (7, 9, 11):
                for adv in (group.get("advert_list") or []):
                    cid = adv.get("advertId")
                    if cid:
                        campaign_ids.append(cid)

        if not campaign_ids:
            logger.info("[WB.get_ad_stats] нет кампаний")
            return []
        logger.info(f"[WB.get_ad_stats] кампаний для статистики: {len(campaign_ids)}")

        # Шаг 2: GET /adv/v3/fullstats — агрегированная статистика за период
        # Максимум 50 ID за раз, лимит 3 запроса/мин
        results = []
        for i in range(0, len(campaign_ids), 50):
            batch = campaign_ids[i:i+50]
            if i > 0:
                await asyncio.sleep(20)  # соблюдаем rate limit: 3 запроса/мин, интервал 20 сек
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{_ADV_BASE}/adv/v3/fullstats",
                    headers=adv_headers,
                    params={
                        "ids":       ",".join(str(cid) for cid in batch),
                        "beginDate": date_from,
                        "endDate":   date_to,
                    },
                    timeout=_TIMEOUT,
                ) as resp:
                    raw = await resp.text()
                    if resp.status == 429:
                        logger.warning("[WB.get_ad_stats] rate limit, жду 60 сек")
                        await asyncio.sleep(60)
                        continue
                    if resp.status != 200:
                        logger.error(f"[WB.get_ad_stats] fullstats HTTP {resp.status}: {raw[:200]}")
                        continue
                    stats = _json.loads(raw)

            for item in (stats if isinstance(stats, list) else []):
                cid = item.get("advertId")
                views  = int(item.get("views", 0) or 0)
                clicks = int(item.get("clicks", 0) or 0)
                spend  = float(item.get("sum", 0) or 0)
                ctr    = round(float(item.get("ctr", 0) or 0), 2)
                results.append({
                    "campaign_id":   str(cid),
                    "campaign_name": str(cid),  # имя получим отдельно если нужно
                    "stat_date":     date_from,  # агрегат за период, берём дату начала
                    "views":         views,
                    "clicks":        clicks,
                    "ctr":           ctr,
                    "spend":         spend,
                })

        logger.info(f"[WB.get_ad_stats] итого записей: {len(results)}")
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


    async def _get_sku_to_offer_id(self, skus: list[int]) -> dict[int, str]:
        """Получить маппинг SKU → offer_id через /v2/product/info/list."""
        import json as _json
        if not skus:
            return {}
        url = f"{self._BASE}/v2/product/info/list"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=self._headers(),
                    json={"sku": skus},
                    timeout=_TIMEOUT,
                ) as resp:
                    raw = await resp.text()
                    if resp.status != 200:
                        logger.error(f"[Ozon.get_stocks] product/info/list HTTP {resp.status}: {raw[:200]}")
                        return {}
                    data = _json.loads(raw)
        except Exception as e:
            logger.error(f"[Ozon.get_stocks] product/info/list exception: {e}")
            return {}
        mapping: dict[int, str] = {}
        for item in (data.get("result") or {}).get("items") or []:
            sku = item.get("sku")
            offer_id = str(item.get("offer_id") or "").strip()
            if sku and offer_id:
                mapping[int(sku)] = offer_id
        return mapping

    async def get_stocks(self, **_) -> list[dict]:
        """Остатки по складам: analytics API + обогащение offer_id через product/info/list."""
        import json as _json
        url = f"{self._BASE}/v2/analytics/stock_on_warehouses"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=self._headers(),
                    json={"limit": 100, "offset": 0},
                    timeout=_TIMEOUT,
                ) as resp:
                    raw = await resp.text()
                    logger.debug(f"[Ozon.get_stocks] POST {url} → {resp.status}: {raw[:200]!r}")
                    if resp.status != 200:
                        logger.error(f"[Ozon.get_stocks] HTTP {resp.status}: {raw[:200]}")
                        return []
                    data = _json.loads(raw)
        except asyncio.TimeoutError:
            logger.error(f"[marketplace] timeout: POST {url}")
            return []
        except Exception as e:
            logger.error(f"[Ozon.get_stocks] exception: {e}")
            return []
        if not data:
            return []
        rows = data.get("result", {}).get("rows", [])

        # Шаг 2: собрать уникальные SKU и получить маппинг → offer_id
        all_skus = list({int(r["sku"]) for r in rows if r.get("sku")})
        sku_map = await self._get_sku_to_offer_id(all_skus)
        mapped = sum(1 for s in all_skus if s in sku_map)
        without_offer = [s for s in all_skus if s not in sku_map]
        logger.info(f"[Ozon.get_stocks] SKU всего: {len(all_skus)}, получили offer_id: {mapped}, без маппинга: {len(without_offer)}")
        if without_offer:
            logger.warning(f"[Ozon.get_stocks] SKU без offer_id: {without_offer}")

        results = []
        skipped = 0
        for item in rows:
            sku = item.get("sku")
            offer_id = sku_map.get(int(sku), "") if sku else ""
            if not offer_id:
                skipped += 1
                continue
            results.append({
                "product_id":    offer_id,
                "product_name":  item.get("item_name") or item.get("title") or offer_id,
                "warehouse_name": item.get("warehouse_name", ""),
                "stock":         int(item.get("free_to_sell_amount", 0) or item.get("for_sale", 0)),
                "reserved":      int(item.get("reserved_amount", 0)),
            })
        if skipped:
            logger.warning(f"[Ozon.get_stocks] пропущено записей без offer_id: {skipped}")
        return results

    async def get_sales(self, date_from: datetime, **_) -> list[dict]:
        """Выкупленные отправления FBO (delivered) + FBS (delivered)."""
        import json as _json

        now = datetime.now(timezone.utc)

        def _parse_postings(postings: list, scheme: str) -> list[dict]:
            rows = []
            for posting in postings:
                posting_number = posting.get("posting_number", "")
                products       = posting.get("products") or []
                fin_products   = (posting.get("financial_data") or {}).get("products") or []
                sale_date      = posting.get("delivering_date") or posting.get("in_process_at", "")
                for i, prod in enumerate(products):
                    fin = fin_products[i] if i < len(fin_products) else {}
                    rows.append({
                        "order_id":    f"{posting_number}_{i}" if i > 0 else posting_number,
                        "product_id":  str(prod.get("offer_id", "")),
                        "product_name": prod.get("name", ""),
                        "quantity":    int(prod.get("quantity", 1) or 1),
                        "price":       float(fin.get("price", 0) or prod.get("price", 0) or 0),
                        "commission":  abs(float(fin.get("commission_amount", 0) or 0)),
                        "sale_date":   sale_date,
                        "scheme":      scheme,
                    })
            return rows

        async def _fetch_all(session, url: str, base_filter: dict, scheme: str) -> list[dict]:
            offset = 0
            total: list[dict] = []
            while True:
                body = {
                    "dir": "DESC",
                    "filter": base_filter,
                    "limit":  100,
                    "offset": offset,
                    "with":   {"financial_data": True},
                }
                try:
                    async with session.post(url, headers=self._headers(), json=body, timeout=_TIMEOUT) as resp:
                        raw = await resp.text()
                        logger.debug(f"[Ozon.get_sales/{scheme}] POST offset={offset} → {resp.status}: {raw[:100]!r}")
                        if resp.status != 200:
                            logger.error(f"[Ozon.get_sales/{scheme}] HTTP {resp.status}: {raw[:200]}")
                            break
                        data = _json.loads(raw)
                except asyncio.TimeoutError:
                    logger.error(f"[marketplace] timeout: POST {url}")
                    break
                except Exception as e:
                    logger.error(f"[Ozon.get_sales/{scheme}] exception: {e}")
                    break
                # FBO: {"postings": [...], "has_next": ...}
                # FBS: {"result": {"postings": [...], "has_next": ...}}
                if scheme == "fbo":
                    postings = data.get("postings") or []
                    has_next = data.get("has_next", False)
                else:
                    raw_result = data.get("result") or {}
                    postings = raw_result.get("postings") or [] if isinstance(raw_result, dict) else []
                    has_next = raw_result.get("has_next", False) if isinstance(raw_result, dict) else False
                total.extend(_parse_postings(postings, scheme))
                if not has_next or offset >= 2000:
                    break
                offset += len(postings)
            logger.info(f"[Ozon.get_sales/{scheme}/delivered] итого: {len(total)}")
            return total

        base_filter = {
            "since":  date_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to":     now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "delivered",
        }
        raw_results: list[dict] = []
        async with aiohttp.ClientSession() as session:
            raw_results.extend(await _fetch_all(session, f"{self._BASE}/v3/posting/fbo/list", base_filter, "fbo"))
            raw_results.extend(await _fetch_all(session, f"{self._BASE}/v3/posting/fbs/list", base_filter, "fbs"))

        # Дедупликация по order_id (posting_number)
        seen: set = set()
        results: list[dict] = []
        for r in raw_results:
            oid = r.get("order_id")
            if oid not in seen:
                seen.add(oid)
                results.append(r)
        logger.info(f"[Ozon.get_sales] до дедупликации: {len(raw_results)}, после: {len(results)}")
        return results


    async def get_orders(self, date_from: datetime, **_) -> list[dict]:
        """Активные заказы FBO (awaiting_packaging + awaiting_deliver + delivering)."""
        import json as _json

        now = datetime.now(timezone.utc)

        def _parse_postings(postings: list) -> list[dict]:
            rows = []
            for posting in postings:
                posting_number = posting.get("posting_number", "")
                products       = posting.get("products") or []
                fin_products   = (posting.get("financial_data") or {}).get("products") or []
                order_date     = posting.get("in_process_at", "")
                for i, prod in enumerate(products):
                    fin = fin_products[i] if i < len(fin_products) else {}
                    rows.append({
                        "order_id":    f"{posting_number}_{i}" if i > 0 else posting_number,
                        "product_id":  str(prod.get("offer_id", "")),
                        "product_name": prod.get("name", ""),
                        "quantity":    int(prod.get("quantity", 1) or 1),
                        "price":       float(fin.get("price", 0) or prod.get("price", 0) or 0),
                        "order_date":  order_date,
                    })
            return rows

        all_postings: list[dict] = []
        url = f"{self._BASE}/v3/posting/fbo/list"
        async with aiohttp.ClientSession() as session:
            for status in ("awaiting_packaging", "awaiting_deliver", "delivering"):
                offset = 0
                status_raw = 0
                while True:
                    body = {
                        "dir": "DESC",
                        "filter": {
                            "since":  date_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "to":     now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "status": status,
                        },
                        "limit":  100,
                        "offset": offset,
                        "with":   {"financial_data": True},
                    }
                    try:
                        async with session.post(
                            url,
                            headers=self._headers(),
                            json=body,
                            timeout=_TIMEOUT,
                        ) as resp:
                            raw = await resp.text()
                            if resp.status != 200:
                                logger.error(f"[Ozon.get_orders/{status}] HTTP {resp.status}: {raw[:200]}")
                                break
                            data = _json.loads(raw)
                    except asyncio.TimeoutError:
                        logger.error(f"[marketplace] timeout: POST {url} status={status}")
                        break
                    except Exception as e:
                        logger.error(f"[Ozon.get_orders/{status}] exception: {e}")
                        break
                    postings = data.get("postings") or []
                    logger.debug(f"[Ozon.get_orders/{status}] offset={offset}, postings_count={len(postings)}")
                    all_postings.extend(postings)
                    status_raw += len(postings)
                    if not data.get("has_next") or offset >= 2000:
                        break
                    offset += len(postings)
                logger.info(f"[Ozon.get_orders/{status}] сырых: {status_raw}")

        # Дедупликация по posting_number
        seen: set = set()
        unique_postings: list[dict] = []
        for p in all_postings:
            pn = p.get("posting_number")
            if pn not in seen:
                seen.add(pn)
                unique_postings.append(p)
        logger.info(f"[Ozon.get_orders] до дедупликации: {len(all_postings)}, после: {len(unique_postings)}")

        results = _parse_postings(unique_postings)
        logger.info(f"[Ozon.get_orders] итого позиций: {len(results)}")
        return results


    async def get_orders_analytics(self, date_from: datetime, date_to: datetime) -> list[dict]:
        """Аналитика заказов Ozon по SKU за период через /v1/analytics/data."""
        import json as _json
        url = f"{self._BASE}/v1/analytics/data"
        df_str = date_from.strftime("%Y-%m-%d")
        dt_str = date_to.strftime("%Y-%m-%d")
        results = []
        offset = 0
        while True:
            body = {
                "date_from": df_str,
                "date_to":   dt_str,
                "dimension": ["sku"],
                "metrics":   ["ordered_units", "revenue"],
                "limit":     1000,
                "offset":    offset,
            }
            data = None
            for attempt in range(3):
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(url, headers=self._headers(), json=body, timeout=_TIMEOUT) as resp:
                            raw = await resp.text()
                            if resp.status == 429:
                                logger.warning(f"[Ozon.get_orders_analytics] rate limit, жду 60 сек (attempt {attempt+1})")
                                await asyncio.sleep(60)
                                continue
                            if resp.status != 200:
                                logger.error(f"[Ozon.get_orders_analytics] HTTP {resp.status}: {raw[:200]}")
                                break
                            data = _json.loads(raw)
                            break
                except asyncio.TimeoutError:
                    logger.error(f"[marketplace] timeout: POST {url}")
                    break
                except Exception as e:
                    logger.error(f"[Ozon.get_orders_analytics] exception: {e}")
                    break
            if data is None:
                break
            rows = (data.get("result") or {}).get("data") or []
            for row in rows:
                dims    = row.get("dimensions") or [{}]
                metrics = row.get("metrics") or [0, 0]
                qty     = int(metrics[0] or 0)
                rev     = float(metrics[1] or 0)
                results.append({
                    "product_id":   str((dims[0] if dims else {}).get("id", "")),
                    "product_name": str((dims[0] if dims else {}).get("name", "")),
                    "quantity":     qty,
                    "price":        round(rev / qty, 2) if qty else 0.0,
                    "order_date":   df_str,
                })
            if len(rows) < 1000 or offset >= 10000:
                break
            offset += len(rows)
        logger.info(f"[Ozon.get_orders_analytics] {df_str}–{dt_str}: {len(results)} записей")
        sample_ids = [f"ozon_analytics_{r['product_id']}_{df_str}" for r in results[:3]]
        logger.info(f"[Ozon.get_orders_analytics] sample order_ids: {sample_ids}")
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
