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
                if resp.status == 403:
                    logger.warning(f"[marketplace] {label} HTTP 403: {raw[:200]}")
                else:
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


    async def get_nm_id_mapping(self, statistics_token: str) -> dict[str, str]:
        """Маппинг supplierArticle (lower) → nmId через Statistics stocks API.

        Не требует разрешения "Контент" — использует тот же токен, что и остатки.
        Возвращает {lower(supplierArticle): str(nmId)}.
        """
        import json as _json
        _STATS_BASE = "https://statistics-api.wildberries.ru"
        stats_headers = {"Authorization": f"Bearer {statistics_token}", "Content-Type": "application/json"}
        date_from = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00")
        url = f"{_STATS_BASE}/api/v1/supplier/stocks"
        data = None
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=stats_headers,
                params={"dateFrom": date_from},
                timeout=_TIMEOUT,
            ) as resp:
                raw = await resp.text()
                if resp.status != 200:
                    logger.error(f"[WB.get_nm_id_mapping] HTTP {resp.status}: {raw[:200]}")
                    return {}
                data = _json.loads(raw)
        result: dict[str, str] = {}
        for item in (data if isinstance(data, list) else []):
            article = str(item.get("supplierArticle") or "").strip()
            nm_id = str(item.get("nmId") or "").strip()
            if article and nm_id:
                # WB непоследователен: одни артикулы используют ',' другие '.' как разделитель.
                # Нормализуем к точке чтобы 'ГБ2,5' и 'ГБ2.5' совпадали.
                key = article.lower().replace(",", ".")
                result[key] = nm_id
        logger.info(f"[WB.get_nm_id_mapping] маппинг: {len(result)} артикулов → nmId")
        return result

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

    async def get_in_transit(self) -> list[dict]:
        """Товары в пути к складам WB: незакрытые FBO-поставки.

        Возвращает [{"product_id": supplierArticle, "qty": int}].
        Использует Marketplace API (не Statistics), обычно не блокируется.
        """
        _MP_BASE = "https://marketplace-api.wildberries.ru"
        headers = self._headers()
        supply_ids: list[str] = []

        try:
            async with aiohttp.ClientSession() as session:
                next_cursor = 0
                while True:
                    async with session.get(
                        f"{_MP_BASE}/api/v3/supplies",
                        headers=headers,
                        params={"limit": 1000, "next": next_cursor},
                        timeout=_TIMEOUT,
                    ) as resp:
                        if resp.status != 200:
                            raw = await resp.text()
                            logger.warning(f"[WB.get_in_transit] supplies HTTP {resp.status}: {raw[:200]}")
                            return []
                        data = await resp.json()
                    supplies = data.get("supplies") or []
                    for s in supplies:
                        if not s.get("done"):
                            supply_ids.append(s["id"])
                    next_val = data.get("next", 0)
                    if not supplies or not next_val:
                        break
                    next_cursor = next_val
        except Exception as e:
            logger.error(f"[WB.get_in_transit] supplies list error: {e}")
            return []

        if not supply_ids:
            return []

        totals: dict[str, int] = {}
        try:
            async with aiohttp.ClientSession() as session:
                for sid in supply_ids:
                    try:
                        async with session.get(
                            f"{_MP_BASE}/api/v3/supplies/{sid}/orders",
                            headers=headers,
                            params={"limit": 1000},
                            timeout=_TIMEOUT,
                        ) as resp:
                            if resp.status != 200:
                                continue
                            data = await resp.json()
                        for order in data.get("orders") or []:
                            article = str(order.get("article") or "").strip()
                            if article:
                                totals[article] = totals.get(article, 0) + 1
                    except Exception:
                        continue
        except Exception as e:
            logger.error(f"[WB.get_in_transit] orders error: {e}")

        logger.info(f"[WB.get_in_transit] {len(supply_ids)} поставок, {len(totals)} артикулов в пути")
        return [{"product_id": art, "qty": qty} for art, qty in totals.items()]

    async def get_supply_statuses(self) -> list[dict]:
        """Поставки WB с статусами и составом товаров (не-done).

        Возвращает [{"supply_id", "status_id", "status_name", "product_id", "qty", "warehouse_name"}].
        """
        _WB_STATUS: dict[int, str] = {
            1: "Не запланировано",
            2: "Запланировано",
            3: "Отгрузка разрешена",
            4: "Идёт приёмка",
            5: "Принято",
            6: "Отгружено на воротах",
            7: "В пути",
            8: "Транзит",
        }
        _MP_BASE = "https://marketplace-api.wildberries.ru"
        headers = self._headers()
        active_supplies: list[dict] = []

        try:
            async with aiohttp.ClientSession() as session:
                next_cursor = 0
                while True:
                    async with session.get(
                        f"{_MP_BASE}/api/v3/supplies",
                        headers=headers,
                        params={"limit": 1000, "next": next_cursor},
                        timeout=_TIMEOUT,
                    ) as resp:
                        if resp.status != 200:
                            raw = await resp.text()
                            logger.warning(f"[WB.get_supply_statuses] HTTP {resp.status}: {raw[:200]}")
                            return []
                        data = await resp.json()
                    supplies = data.get("supplies") or []
                    for s in supplies:
                        if not s.get("done"):
                            sid = int(s.get("statusID") or 0)
                            active_supplies.append({
                                "id": s["id"],
                                "status_id": sid,
                                "status_name": _WB_STATUS.get(sid, f"Статус {sid}"),
                                "warehouse_name": str(s.get("officeName") or ""),
                            })
                    next_val = data.get("next", 0)
                    if not supplies or not next_val:
                        break
                    next_cursor = next_val
        except Exception as e:
            logger.error(f"[WB.get_supply_statuses] list error: {e}")
            return []

        if not active_supplies:
            return []

        result: list[dict] = []
        try:
            async with aiohttp.ClientSession() as session:
                for sup in active_supplies:
                    try:
                        async with session.get(
                            f"{_MP_BASE}/api/v3/supplies/{sup['id']}/orders",
                            headers=headers,
                            params={"limit": 1000},
                            timeout=_TIMEOUT,
                        ) as resp:
                            if resp.status != 200:
                                continue
                            data = await resp.json()
                        article_qty: dict[str, int] = {}
                        for order in data.get("orders") or []:
                            article = str(order.get("article") or "").strip()
                            if article:
                                article_qty[article] = article_qty.get(article, 0) + 1
                        for article, qty in article_qty.items():
                            result.append({
                                "supply_id":      str(sup["id"]),
                                "status_id":      sup["status_id"],
                                "status_name":    sup["status_name"],
                                "product_id":     article,
                                "qty":            qty,
                                "warehouse_name": sup.get("warehouse_name", ""),
                            })
                    except Exception:
                        continue
        except Exception as e:
            logger.error(f"[WB.get_supply_statuses] orders error: {e}")

        logger.info(
            f"[WB.get_supply_statuses] {len(active_supplies)} поставок, "
            f"{len(result)} позиций"
        )
        return result

    async def get_acceptance_coefficients(self, box_type: str | None = "Короб") -> list[dict]:
        """Коэффициенты приёмки WB на ближайшие дни.

        Возвращает [{warehouseID, warehouseName, coefficient, boxTypeName, date}].
        coefficient=0 → не принимает, >0 → принимает (множитель стоимости логистики).
        box_type=None → вернуть все типы упаковки.
        """
        url = "https://supplies-api.wildberries.ru/api/v1/acceptance/coefficients"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self._headers(), timeout=_TIMEOUT) as resp:
                    if resp.status != 200:
                        raw = await resp.text()
                        logger.warning(f"[WB.get_acceptance_coefficients] HTTP {resp.status}: {raw[:200]}")
                        return []
                    data = await resp.json()
            result = []
            for item in (data or []):
                if box_type and item.get("boxTypeName") != box_type:
                    continue
                result.append({
                    "warehouseID":   item.get("warehouseID"),
                    "warehouseName": item.get("warehouseName", ""),
                    "coefficient":   item.get("coefficient", 0),
                    "boxTypeName":   item.get("boxTypeName", ""),
                    "date":          item.get("date", ""),
                })
            logger.info(f"[WB.get_acceptance_coefficients] box_type={box_type!r}: {len(result)} записей")
            return result
        except Exception as e:
            logger.error(f"[WB.get_acceptance_coefficients] {e}")
            return []

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
                "seller_price": float(item.get("priceWithDisc") or 0),
                "order_date":   item.get("date") or item.get("lastChangeDate", ""),
                "region":       item.get("oblastMarkingCode") or item.get("regionName", ""),
            })
        logger.info(f"[WB.get_orders_all] итого не отменённых: {len(results)}")
        logger.info(f"[WB.get_orders_all] sample order_ids: {[o.get('order_id') for o in results[:3]]}")
        return results

    async def get_current_prices(self) -> list[dict]:
        """Текущие листинговые цены всех товаров: /api/v2/list/goods/filter.
        Возвращает [{"product_id": vendorCode, "price": float}].
        price = discountedPrice (цена продавца со скидкой продавца, до СПП WB).
        """
        _PRICES_BASE = "https://discounts-prices-api.wildberries.ru"
        url = f"{_PRICES_BASE}/api/v2/list/goods/filter"
        results = []
        limit, offset = 1000, 0
        async with aiohttp.ClientSession() as session:
            while True:
                data = await _request(
                    session, "GET", url,
                    headers=self._headers(),
                    params={"limit": limit, "offset": offset},
                    label="WB.get_current_prices",
                )
                if not data:
                    break
                goods = (data.get("data") or {}).get("listGoods") or []
                for g in goods:
                    vendor_code = g.get("vendorCode")
                    sizes = g.get("sizes") or []
                    price = float(sizes[0].get("discountedPrice") or sizes[0].get("price") or 0) if sizes else 0
                    if vendor_code and price > 0:
                        results.append({"product_id": vendor_code, "price": price})
                if len(goods) < limit:
                    break
                offset += limit
        logger.info(f"[WB.get_current_prices] итого: {len(results)} товаров")
        return results

    async def update_prices(self, items: list[dict]) -> dict:
        """Обновить цены товаров на WB через /api/v2/upload/task.

        items: [{"nm_id": int, "price": int}] — price в рублях (итоговая цена без скидки).
        Возвращает {"success": bool, "upload_id": str}.
        """
        if not items:
            return {"success": True, "upload_id": ""}
        _PRICES_BASE = "https://discounts-prices-api.wildberries.ru"
        payload = {"data": [{"nmID": int(item["nm_id"]), "price": int(item["price"])} for item in items]}
        async with aiohttp.ClientSession() as session:
            data = await _request(
                session, "POST", f"{_PRICES_BASE}/api/v2/upload/task",
                headers=self._headers(), json=payload, label="WB.update_prices",
            )
        if not data:
            return {"success": False, "upload_id": ""}
        upload_id = (data.get("data") or {}).get("uploadID", "")
        logger.info(f"[WB.update_prices] upload_id={upload_id} товаров={len(items)}")
        return {"success": bool(upload_id), "upload_id": upload_id}

    async def get_competitor_prices(self, keyword: str, limit: int = 100) -> list[dict]:
        """Публичный API WB (без токена) — топ-N товаров по запросу.

        Возвращает [{"position", "product_name", "brand", "price", "rating", "review_count"}].
        Используется для еженедельного снапшота цен конкурентов.
        """
        url = "https://search.wb.ru/exactmatch/ru/common/v7/search"
        params = {
            "query": keyword,
            "limit": limit,
            "resultset": "catalog",
            "sort": "popular",
            "suppressSpellcheck": "false",
        }
        results = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        logger.warning(f"[WB.competitor_prices] HTTP {resp.status} для '{keyword}'")
                        return []
                    body = await resp.json(content_type=None)
            products = (body.get("data") or {}).get("products") or []
            for idx, p in enumerate(products[:limit], start=1):
                price_raw = p.get("salePriceU") or p.get("priceU") or 0
                results.append({
                    "position":     idx,
                    "product_name": p.get("name") or "",
                    "brand":        p.get("brand") or "",
                    "price":        round(price_raw / 100, 2) if price_raw else 0.0,
                    "rating":       float(p.get("rating") or 0),
                    "review_count": int(p.get("feedbacks") or 0),
                })
        except Exception as e:
            logger.error(f"[WB.competitor_prices] ошибка для '{keyword}': {e}")
        logger.info(f"[WB.competitor_prices] '{keyword}' → {len(results)} товаров")
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
        returns_count = 0
        for item in (data if isinstance(data, list) else []):
            sale_id = item.get("saleID", "")
            is_ret = sale_id.startswith("R")
            if is_ret:
                returns_count += 1
            for_pay = float(item.get("forPay", 0) or 0)
            results.append({
                "order_id":    sale_id or item.get("srid", "") or item.get("odid", ""),
                "product_id":  str(item.get("nmId", "")),
                "product_name": item.get("subject", "") or item.get("supplierArticle", ""),
                "quantity":    int(item.get("quantity", 1) or 1),
                "price":       for_pay,
                "commission":  0.0,
                "sale_date":   item.get("lastChangeDate", ""),
                "is_return":   is_ret,
            })
        logger.info(f"[WB.get_sales] продаж: {len(results) - returns_count}, возвратов: {returns_count}")
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

        # Только активные кампании (status=9) — у завершённых нет свежей статистики
        # Диагностика: логируем ВСЕ статусы чтобы понять почему нет кампаний
        campaign_ids = []
        all_statuses: dict[int, int] = {}
        for group in (count_data.get("adverts") or []):
            st = group.get("status", -1)
            cnt = group.get("count", 0)
            all_statuses[st] = all_statuses.get(st, 0) + cnt
            if st == 9:
                for adv in (group.get("advert_list") or []):
                    cid = adv.get("advertId")
                    if cid:
                        campaign_ids.append(cid)

        if all_statuses:
            logger.info(f"[WB.get_ad_stats] кампании по статусам: {all_statuses} "
                        f"(9=активные, 11=пауза, 7=завершены)")
        if not campaign_ids:
            logger.error("[WB.get_ad_stats] нет активных кампаний WB (status=9) — "
                         f"все статусы: {all_statuses}. "
                         f"WB per-product ДРР недоступен.")
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

            _campaigns_with_nm = 0
            _campaigns_no_nm = 0
            for item in (stats if isinstance(stats, list) else []):
                cid = item.get("advertId")
                campaign_nm_total = 0
                for day in (item.get("days") or []):
                    views  = int(day.get("views", 0) or 0)
                    clicks = int(day.get("clicks", 0) or 0)
                    spend  = float(day.get("sum", 0) or 0)
                    ctr    = round(float(day.get("ctr", 0) or 0), 2)
                    stat_date = day.get("date", "")[:10]
                    if not stat_date:
                        continue
                    # Собираем per-nm статистику для product_adv_stats
                    product_stats = []
                    apps = day.get("apps") or []
                    for app in apps:
                        for nm in (app.get("nm") or []):
                            nm_id = str(nm.get("nmId", ""))
                            if not nm_id:
                                continue
                            nm_views  = int(nm.get("views", 0) or 0)
                            nm_clicks = int(nm.get("clicks", 0) or 0)
                            nm_spend  = float(nm.get("sum", 0) or 0)
                            nm_ctr    = round(float(nm.get("ctr", 0) or 0), 2)
                            nm_orders = int(nm.get("orders", 0) or 0)
                            product_stats.append({
                                "product_id":    nm_id,
                                "views":         nm_views,
                                "clicks":        nm_clicks,
                                "ctr":           nm_ctr,
                                "spend":         nm_spend,
                                "orders_count":  nm_orders,
                            })
                    campaign_nm_total += len(product_stats)
                    results.append({
                        "campaign_id":   str(cid),
                        "campaign_name": str(cid),
                        "stat_date":     stat_date,
                        "views":         views,
                        "clicks":        clicks,
                        "ctr":           ctr,
                        "spend":         spend,
                        "product_stats": product_stats,
                    })
                if campaign_nm_total > 0:
                    _campaigns_with_nm += 1
                else:
                    _campaigns_no_nm += 1
                    logger.info(f"[WB.get_ad_stats] кампания {cid}: нет nm-разбивки (WB типы 4/5/6/9)")

            logger.info(f"[WB.get_ad_stats] batch: кампаний с nm={_campaigns_with_nm}, "
                        f"без nm={_campaigns_no_nm}")

        total_nm = sum(len(r["product_stats"]) for r in results)
        if total_nm == 0 and results:
            # WB не возвращает nm-разбивку для кампаний типов 4/5/6/9 через fullstats.
            # /adv/v0/advert и /adv/v1/promotion/adverts — оба вернули 404 (мертвы с 2025).
            # Сохраняем только агрегированную статистику по кампании без разбивки по товарам.
            logger.info(
                f"[WB.get_ad_stats] кампании типов 4/5/6/9 — nm-разбивка недоступна "
                f"(WB Ad API v0/v1 не работают). Сохраняем агрегат без product_stats."
            )

        logger.info(f"[WB.get_ad_stats] итого записей: {len(results)}, nm-записей: {total_nm}")
        return results

    async def get_nm_ids(self) -> dict[str, dict]:
        """Возвращает {lower(vendorCode): {"nm_id", "subject", "title", "description", "characteristics", "category"}}.

        nm_id нужен для join с product_adv_stats. subject/category — предмет карточки для
        автоматической категоризации в product_mapping. title/description/characteristics — для SEO.
        """
        import json as _json
        _CONTENT_BASE = "https://content-api.wildberries.ru"
        headers = {"Authorization": self._token, "Content-Type": "application/json"}
        result: dict[str, dict] = {}
        cursor: dict = {}

        async with aiohttp.ClientSession() as session:
            while True:
                body: dict = {"settings": {"filter": {"withPhoto": -1}, "cursor": {**cursor, "limit": 100}}}
                async with session.post(
                    f"{_CONTENT_BASE}/content/v2/get/cards/list",
                    headers=headers,
                    json=body,
                    timeout=_TIMEOUT,
                ) as resp:
                    raw = await resp.text()
                    if resp.status != 200:
                        logger.error(f"[WB.get_nm_ids] HTTP {resp.status}: {raw[:200]}")
                        break
                    data = _json.loads(raw)

                cards = (data.get("data") or {}).get("cards") or []
                for card in cards:
                    nm_id   = card.get("nmID")
                    vendor  = card.get("vendorCode", "")
                    subject = card.get("subjectName", "") or ""
                    desc    = card.get("description", "") or ""
                    chars   = card.get("characteristics") or []
                    title = ""
                    for ch in chars:
                        if ch.get("name", "").lower() in ("наименование", "название"):
                            vals = ch.get("value") or []
                            title = vals[0] if vals else ""
                            break
                    if not title:
                        title = subject
                    if nm_id and vendor:
                        result[vendor.lower()] = {
                            "nm_id":           str(nm_id),
                            "subject":         subject,
                            "title":           title,
                            "description":     desc,
                            "characteristics": chars,
                            "category":        subject,
                        }

                cur = (data.get("data") or {}).get("cursor") or {}
                if len(cards) < 100 or not cur.get("nmID"):
                    break
                cursor = {"updatedAt": cur.get("updatedAt", ""), "nmID": cur["nmID"]}

        logger.info(f"[WB.get_nm_ids] товаров: {len(result)}")
        return result

    async def get_financial_report(self, date_from: str, date_to: str, statistics_token: str) -> list[dict]:
        """Финансовый отчёт WB через /api/v5/supplier/reportDetailByPeriod.

        Возвращает агрегаты по (nm_id, report_week) для расчёта реальной рентабельности.
        Поле payout = ppvz_for_pay — фактическая выплата после всех удержаний.
        """
        import json as _json
        _STATS_BASE = "https://statistics-api.wildberries.ru"
        headers = {"Authorization": statistics_token}
        results: list[dict] = []
        rrdid = 0
        # Агрегируем в памяти: (nm_id, week) → суммы
        agg: dict[tuple, dict] = {}

        while True:
            params = {
                "dateFrom": date_from,
                "dateTo":   date_to,
                "rrdid":    rrdid,
                "limit":    100000,
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{_STATS_BASE}/api/v5/supplier/reportDetailByPeriod",
                    headers=headers, params=params, timeout=_TIMEOUT,
                ) as resp:
                    raw = await resp.text()
                    if resp.status == 429:
                        await asyncio.sleep(60)
                        continue
                    if resp.status != 200:
                        logger.error(f"[WB.get_financial_report] HTTP {resp.status}: {raw[:200]}")
                        break
                    rows = _json.loads(raw)

            if not rows:
                break

            for row in rows:
                # sa_name = supplierArticle — тот же артикул, что в product_mapping.wb_article
                # (nm_id — внутренний числовой ID карточки WB, с ним product_mapping не сматчится)
                sa_name  = str(row.get("sa_name", "") or "").strip()
                nm_id    = str(row.get("nm_id", "") or "")
                doc_type = row.get("doc_type_name", "")
                product_id = sa_name or nm_id
                if not product_id:
                    continue
                # Дата начала недели отчёта
                rp_str = (row.get("rr_dt") or row.get("create_dt") or date_from)[:10]
                try:
                    import datetime as _dt_mod
                    rp_date = _dt_mod.date.fromisoformat(rp_str)
                    # Округляем до начала недели (понедельник)
                    rp_date = rp_date - _dt_mod.timedelta(days=rp_date.weekday())
                except Exception:
                    rp_date = rp_str

                key = (product_id, rp_date)
                if key not in agg:
                    agg[key] = {
                        "product_id":  product_id,
                        "report_date": rp_date,
                        "quantity":    0,
                        "revenue":     0.0,
                        "payout":      0.0,
                        "commission":  0.0,
                        "logistics":   0.0,
                        "storage":     0.0,
                        "penalty":     0.0,
                    }
                a = agg[key]
                qty = int(row.get("quantity", 0) or 0)
                # Только doc_type_name == "Продажа" — это реальные продажи/возвраты товара.
                # Остальные строки (doc_type_name == "") — логистика, компенсации складских
                # операций и т.п.: тоже несут quantity, но не относятся к проданным штукам,
                # и раздувают знаменатель в NET-марже (нашли расхождение цены ~5x на КБ50).
                if doc_type == "Продажа":
                    # Возвраты имеют отрицательный qty и отрицательный ppvz_for_pay
                    a["quantity"] += qty
                    a["revenue"]  += float(row.get("retail_price_withdisc_rub", 0) or 0) * (qty if qty > 0 else 0)
                a["payout"]     += float(row.get("ppvz_for_pay", 0) or 0)
                a["commission"] += float(row.get("ppvz_vw", 0) or 0)
                a["logistics"]  += float(row.get("delivery_rub", 0) or 0)
                a["storage"]    += float(row.get("storage_fee", 0) or 0)
                a["penalty"]    += float(row.get("penalty", 0) or 0)
                rrdid = max(rrdid, int(row.get("rrd_id", 0) or 0))

            if len(rows) < 100000:
                break

        results = list(agg.values())
        logger.info(f"[WB.get_financial_report] {date_from}–{date_to}: {len(results)} агрегатов по nm_id/неделе")
        return results

    async def get_funnel_stats(self, date_from: str, date_to: str) -> list[dict]:
        """Воронка конверсии карточки WB через /api/v1/analytics/nm-report/grouped."""
        import json as _json
        url = "https://seller-analytics-api.wildberries.ru/api/v1/analytics/nm-report/grouped"
        headers = {"Authorization": self._token, "Content-Type": "application/json"}
        body = {
            "period": {"begin": date_from, "end": date_to},
            "timezone": "Europe/Moscow",
            "aggregationLevel": "day",
        }
        results = []
        page = 1
        while True:
            body["page"] = page
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=body, timeout=_TIMEOUT) as resp:
                    raw = await resp.text()
                    if resp.status == 429:
                        await asyncio.sleep(60)
                        continue
                    if resp.status != 200:
                        logger.error(f"[WB.get_funnel_stats] HTTP {resp.status}: {raw[:200]}")
                        break
                    data = _json.loads(raw)
            nm_ids = (data.get("data") or {}).get("nmIDs") or []
            if not nm_ids:
                break
            for item in nm_ids:
                nm_id = str(item.get("nmID", ""))
                if not nm_id:
                    continue
                for day in (item.get("history") or []):
                    views      = int(day.get("openCardCount", 0) or 0)
                    cart       = int(day.get("addToCartCount", 0) or 0)
                    orders     = int(day.get("ordersCount", 0) or 0)
                    buyouts    = int(day.get("buyoutsCount", 0) or 0)
                    v2c        = float(day.get("addToCartPercent", 0) or 0)
                    c2o        = float(day.get("cartToOrderPercent", 0) or 0)
                    position   = day.get("avgOrdersCountPerDay")
                    results.append({
                        "product_id":         nm_id,
                        "stat_date":          day.get("dt", date_from),
                        "views":              views,
                        "add_to_cart":        cart,
                        "orders_count":       orders,
                        "buyouts":            buyouts,
                        "avg_position":       float(position) if position is not None else None,
                        "conv_view_to_cart":  round(v2c, 2),
                        "conv_cart_to_order": round(c2o, 2),
                    })
            if (data.get("data") or {}).get("isNextPage"):
                page += 1
            else:
                break
        logger.info(f"[WB.get_funnel_stats] {date_from}–{date_to}: {len(results)} записей")
        return results

    async def get_campaigns_nms(self, campaign_ids: list[str]) -> dict[str, list[str]]:
        """Получить nm_id товаров для каждой кампании через /adv/v0/advert.

        Для кампаний типов 4/5/6/9 (поиск/каталог/карточка) WB не возвращает
        nm-разбивку в fullstats, но хранит список рекламируемых nm_id в поле
        params[].nms[]. Используется как fallback для распределения расходов.

        Возвращает {campaign_id: [nm_id_str, ...]}.
        """
        import json as _json
        result: dict[str, list[str]] = {}
        url = "https://advert-api.wildberries.ru/adv/v0/advert"
        headers = {"Authorization": self._token}

        for i, cid in enumerate(campaign_ids):
            if i > 0:
                await asyncio.sleep(0.25)  # 4 запроса/сек — безопасно для WB
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url, headers=headers, params={"id": cid}, timeout=_TIMEOUT
                    ) as resp:
                        raw = await resp.text()
                        if resp.status != 200:
                            logger.error(f"[WB.get_campaigns_nms] campaign {cid}: HTTP {resp.status} — {raw[:200]}")
                            continue
                        data = _json.loads(raw)
                ctype = int(data.get("type") or 0)
                params_key = "unitedParams" if ctype == 8 else "params"
                nms: list[str] = []
                for p in (data.get(params_key) or []):
                    for nm in (p.get("nms") or []):
                        if nm:
                            nms.append(str(nm))
                if nms:
                    result[cid] = nms
                    logger.info(f"[WB.get_campaigns_nms] кампания {cid} (type={ctype}): {len(nms)} nm_id")
                else:
                    logger.error(f"[WB.get_campaigns_nms] кампания {cid} (type={ctype}): nms пустые, ответ: {str(data)[:300]}")
            except Exception as e:
                logger.error(f"[WB.get_campaigns_nms] campaign {cid}: {e}")

        logger.info(f"[WB.get_campaigns_nms] итого: {len(result)}/{len(campaign_ids)} кампаний с nm_id")
        return result

    async def get_campaign_cpm(self, campaign_id: str) -> dict | None:
        """Получить тип, subject_id и текущую CPM-ставку кампании.
        Возвращает {"type": int, "subject_id": int, "cpm": int} или None при ошибке."""
        import json as _json
        url = f"https://advert-api.wildberries.ru/adv/v0/advert"
        headers = {"Authorization": self._token}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=headers, params={"id": campaign_id}, timeout=_TIMEOUT
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"[WB.get_campaign_cpm] HTTP {resp.status} for id={campaign_id}")
                        return None
                    data = _json.loads(await resp.text())
            ctype = int(data.get("type") or 0)
            # Auto (type=8) → unitedParams; Search (type=6) → params
            params_key = "unitedParams" if ctype == 8 else "params"
            params_list = data.get(params_key) or []
            if not params_list:
                return None
            p = params_list[0]
            subj = p.get("subject") or {}
            subject_id = int(subj.get("id") or p.get("subjectId") or 0)
            cpm = int(p.get("searchCPM") or p.get("cpm") or 0)
            return {"type": ctype, "subject_id": subject_id, "cpm": cpm}
        except Exception as e:
            logger.error(f"[WB.get_campaign_cpm] error: {e}")
            return None

    async def get_campaign_products_v2(self, campaign_ids: list[str]) -> dict[str, list[str]]:
        """Попытка получить nm_ids через POST /adv/v2/promotion/adverts.

        Если endpoint жив — возвращает {campaign_id: [nm_id, ...]}.
        Если 404 — тихо возвращает {} (ожидаемо для устаревших WB endpoints).
        Логирует полный ответ при успехе чтобы можно было скорректировать парсинг.
        """
        import json as _json
        result: dict[str, list[str]] = {}
        url = "https://advert-api.wildberries.ru/adv/v2/promotion/adverts"
        headers = {"Authorization": self._token, "Content-Type": "application/json"}
        ids = [int(cid) for cid in campaign_ids if cid.isdigit()]
        if not ids:
            return {}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, headers=headers,
                    json=ids,
                    timeout=_TIMEOUT,
                ) as resp:
                    raw = await resp.text()
                    if resp.status == 404:
                        logger.info("[WB.get_campaign_products_v2] POST /adv/v2/promotion/adverts → 404 (endpoint мёртв)")
                        return {}
                    if resp.status != 200:
                        logger.error(f"[WB.get_campaign_products_v2] HTTP {resp.status}: {raw[:300]}")
                        return {}
                    logger.info(f"[WB.get_campaign_products_v2] ✅ HTTP 200 — ответ: {raw[:800]}")
                    data = _json.loads(raw)
                    for item in (data if isinstance(data, list) else []):
                        cid = str(item.get("advertId") or item.get("id") or "")
                        nms: list[str] = []
                        params_key = "unitedParams" if int(item.get("type") or 0) == 8 else "params"
                        for p in (item.get(params_key) or []):
                            for nm in (p.get("nms") or []):
                                if nm:
                                    nms.append(str(nm))
                        if cid and nms:
                            result[cid] = nms
                    logger.info(f"[WB.get_campaign_products_v2] получено nm для {len(result)}/{len(ids)} кампаний")
        except Exception as e:
            logger.error(f"[WB.get_campaign_products_v2] exception: {e}")
        return result

    async def update_campaign_cpm(
        self, campaign_id: str, campaign_type: int, subject_id: int, new_cpm: int
    ) -> bool:
        """Установить новую ставку CPM для кампании WB."""
        url = "https://advert-api.wildberries.ru/adv/v0/cpm"
        headers = {"Authorization": self._token, "Content-Type": "application/json"}
        body = {
            "type": campaign_type,
            "cpm": new_cpm,
            "campaignId": int(campaign_id),
            "param": subject_id,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=body, timeout=_TIMEOUT) as resp:
                    text = await resp.text()
                    if resp.status not in (200, 201):
                        logger.error(f"[WB.update_campaign_cpm] HTTP {resp.status}: {text[:200]}")
                        return False
                    return True
        except Exception as e:
            logger.error(f"[WB.update_campaign_cpm] error: {e}")
            return False

    async def upload_product_photo(self, nm_id: str, photo_bytes: bytes, filename: str = "photo.jpg") -> bool:
        """Загрузить фото в карточку WB. nm_id — числовой nmID карточки."""
        url = "https://content-api.wildberries.ru/content/v3/media/save"
        headers = {"Authorization": self._token, "X-Nm-Id": str(nm_id)}
        try:
            data = aiohttp.FormData()
            data.add_field("uploadfile", photo_bytes, filename=filename, content_type="image/jpeg")
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, data=data, timeout=_TIMEOUT) as resp:
                    text = await resp.text()
                    if resp.status not in (200, 201):
                        logger.error(f"[WB.upload_photo] HTTP {resp.status}: {text[:200]}")
                        return False
                    return True
        except Exception as e:
            logger.error(f"[WB.upload_photo] error: {e}")
            return False

    async def get_promotions(self) -> list[dict]:
        """Список активных и предстоящих акций продавца WB."""
        import json as _json
        url = "https://dp-api.wildberries.ru/api/v2/promotion/catalogue"
        headers = {"Authorization": self._token, "Content-Type": "application/json"}
        results = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=_TIMEOUT) as resp:
                    raw = await resp.text()
                    if resp.status == 404:
                        logger.warning("[WB.get_promotions] endpoint 404 — акции WB недоступны через API")
                        return []
                    if resp.status != 200:
                        logger.error(f"[WB.get_promotions] HTTP {resp.status}: {raw[:200]}")
                        return []
                    data = _json.loads(raw)
            for promo in (data.get("data") or data.get("promotions") or []):
                results.append({
                    "promotion_id": str(promo.get("promotionID") or promo.get("id", "")),
                    "title":        promo.get("name") or promo.get("title", ""),
                    "discount_pct": float(promo.get("discount") or 0),
                    "start_date":   (promo.get("startDateTime") or promo.get("startDate", ""))[:10],
                    "end_date":     (promo.get("endDateTime") or promo.get("endDate", ""))[:10],
                    "product_ids":  [str(x) for x in (promo.get("nmIDs") or [])],
                })
        except Exception as e:
            logger.error(f"[WB.get_promotions] {e}", exc_info=True)
        logger.info(f"[WB.get_promotions] {len(results)} акций")
        return results

    async def get_shop_kpi(self) -> dict:
        """Рейтинг и KPI продавца WB через /api/v1/supplier/info."""
        import json as _json
        url = "https://seller-analytics-api.wildberries.ru/api/v1/supplier/info"
        headers = {"Authorization": self._token, "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=_TIMEOUT) as resp:
                    raw = await resp.text()
                    if resp.status == 404:
                        logger.warning("[WB.get_shop_kpi] endpoint 404 — KPI WB недоступны")
                        return {}
                    if resp.status != 200:
                        logger.error(f"[WB.get_shop_kpi] HTTP {resp.status}: {raw[:200]}")
                        return {}
                    data = _json.loads(raw)
            logger.info(f"[WB.get_shop_kpi] raw: {raw[:400]}")
            info = data.get("data") or data
            return {
                "rating":           float(info.get("rating") or 0),
                "return_pct":       float(info.get("returnPercent") or info.get("buyoutPercent") or 0),
                "cancellation_pct": float(info.get("cancelPercent") or 0),
                "penalty_count":    int(info.get("penaltyCount") or 0),
                "extra_data":       {k: v for k, v in info.items()
                                     if k not in ("rating", "returnPercent", "buyoutPercent", "cancelPercent", "penaltyCount")},
            }
        except Exception as e:
            logger.error(f"[WB.get_shop_kpi] {e}", exc_info=True)
            return {}

    async def get_questions(self, is_answered: bool = False, take: int = 100) -> list[dict]:
        """Вопросы покупателей WB через feedbacks-api.wildberries.ru (questions-api устарел)."""
        import json as _json
        _Q_BASE = "https://feedbacks-api.wildberries.ru"
        url = f"{_Q_BASE}/api/v1/questions"
        headers = {"Authorization": self._token, "Content-Type": "application/json"}
        params = {"isAnswered": str(is_answered).lower(), "take": take, "skip": 0}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params, timeout=_TIMEOUT) as resp:
                    raw = await resp.text()
                    if resp.status != 200:
                        logger.error(f"[WB.get_questions] HTTP {resp.status}: {raw[:200]}")
                        raise RuntimeError(f"WB questions API HTTP {resp.status}")
                    data = _json.loads(raw)
        except asyncio.TimeoutError:
            logger.error("[marketplace] timeout: WB.get_questions")
            return []
        except Exception as e:
            logger.error(f"[WB.get_questions] exception: {e}")
            return []
        results = []
        for item in (data.get("data") or {}).get("questions") or []:
            results.append({
                "question_id":   str(item.get("id", "")),
                "product_id":    str(item.get("productDetails", {}).get("nmId", "") or ""),
                "product_name":  item.get("productDetails", {}).get("productName", ""),
                "question_text": item.get("text", ""),
                "created_at":    item.get("createdDate", ""),
            })
        if not results:
            inner = data.get("data") or {}
            logger.warning(
                f"[WB.get_questions] 0 вопросов — countUnanswered={inner.get('countUnanswered')}, "
                f"keys={list(inner.keys())}, raw[:300]={str(data)[:300]}"
            )
        logger.info(f"[WB.get_questions] {len(results)} вопросов (is_answered={is_answered})")
        return results

    async def answer_question(self, question_id: str, text: str) -> bool:
        """Отправить ответ на вопрос покупателя WB.
        PATCH /api/v1/questions, body: {"id", "answer": {"text"}, "state": "wbRu"}
        Источник: eslazarev/wildberries-sdk specs/09-communications.yaml"""
        _Q_BASE = "https://feedbacks-api.wildberries.ru"
        url = f"{_Q_BASE}/api/v1/questions"
        headers = {"Authorization": self._token, "Content-Type": "application/json"}
        body = {"id": question_id, "answer": {"text": text}, "state": "wbRu"}
        logger.info(f"[WB.answer_question] PATCH id={question_id!r} text={text[:40]!r}")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.patch(url, headers=headers, json=body, timeout=_TIMEOUT) as resp:
                    raw = await resp.text()
                    if resp.status in (200, 204):
                        logger.info(f"[WB.answer_question({question_id[:8]})] OK {resp.status} resp={raw[:100]}")
                        return True
                    logger.error(f"[WB.answer_question({question_id[:8]})] PATCH {resp.status}: {raw[:200]}")
                    return False
        except asyncio.TimeoutError:
            logger.error("[marketplace] timeout: WB.answer_question")
            return False
        except Exception as e:
            logger.error(f"[WB.answer_question] exception: {e}")
            return False

    async def get_search_keywords(
        self,
        nm_ids: list[int],
        date_from: str,
        date_to: str,
        statistics_token: str = "",
    ) -> tuple[list[dict], int]:
        """Ключевые слова и позиции в поиске WB за период.

        Returns (results, http_status) — http_status 0 при exception.
        analytics-api требует statistics_token (категория «Аналитика» в ЛК).
        """
        import json as _json
        if not nm_ids:
            return [], 0
        url = "https://seller-analytics-api.wildberries.ru/api/v1/analytics/search-keywords"
        # Пробуем statistics_token — он имеет доступ к аналитике; основной токен как fallback
        token = statistics_token or self._token
        headers = {"Authorization": token, "Content-Type": "application/json"}
        # WB принимает nmIds как повторяющиеся query-params: ?nmIds=1&nmIds=2
        params = [("nmIds", nid) for nid in nm_ids[:20]]
        params += [("dateFrom", date_from), ("dateTo", date_to)]
        http_status = 0
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params, timeout=_TIMEOUT) as resp:
                    http_status = resp.status
                    raw = await resp.text()
                    if resp.status != 200:
                        logger.warning(f"[WB.get_search_keywords] HTTP {resp.status}: {raw[:300]}")
                        return [], http_status
                    data = _json.loads(raw)
        except asyncio.TimeoutError:
            logger.error("[marketplace] timeout: WB.get_search_keywords")
            return [], 0
        except Exception as e:
            logger.error(f"[WB.get_search_keywords] exception: {e}")
            return [], 0
        results = []
        for item in (data.get("data") or []):
            nm_id = str(item.get("nmId", ""))
            for kw in (item.get("keywords") or []):
                results.append({
                    "product_id":   nm_id,
                    "keyword":      kw.get("keyword", ""),
                    "position":     kw.get("position"),
                    "search_count": kw.get("searchCount"),
                    "ctr":          kw.get("ctr"),
                    "conv_rate":    kw.get("conversionRate"),
                    "stat_date":    date_to,
                })
        logger.info(f"[WB.get_search_keywords] {len(results)} записей для {len(nm_ids)} товаров")
        return results, http_status

    async def get_returns_analytics(
        self,
        date_from: str,
        date_to: str,
        statistics_token: str,
    ) -> list[dict]:
        """Аналитика возвратов WB за период из Statistics API."""
        import json as _json
        _STATS_BASE = "https://statistics-api.wildberries.ru"
        stats_headers = {"Authorization": f"Bearer {statistics_token}", "Content-Type": "application/json"}
        url = f"{_STATS_BASE}/api/v1/supplier/sales"
        data = None
        for attempt in range(2):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        url, headers=stats_headers,
                        params={"dateFrom": date_from, "flag": 1},
                        timeout=_TIMEOUT,
                    ) as resp:
                        if resp.status == 429:
                            logger.warning("[WB.get_returns_analytics] rate limit, жду 60 сек")
                            await asyncio.sleep(60)
                            continue
                        raw = await resp.text()
                        if resp.status != 200:
                            logger.error(f"[WB.get_returns_analytics] HTTP {resp.status}: {raw[:200]}")
                            return []
                        data = _json.loads(raw)
                        break
            except asyncio.TimeoutError:
                logger.error("[marketplace] timeout: WB.get_returns_analytics")
                return []
            except Exception as e:
                logger.error(f"[WB.get_returns_analytics] exception: {e}")
                return []
        if not data:
            return []
        agg: dict[str, dict] = {}
        for item in (data if isinstance(data, list) else []):
            sale_id = str(item.get("saleID", ""))
            if not sale_id.startswith("R"):
                continue
            nm_id = str(item.get("nmId", ""))
            name = item.get("subject", "") or item.get("supplierArticle", "")
            price = abs(float(item.get("forPay", 0) or 0))
            sale_date = (item.get("lastChangeDate", "") or "")[:10]
            key = (nm_id, sale_date)
            if key not in agg:
                agg[key] = {
                    "product_id":    nm_id,
                    "product_name":  name,
                    "stat_date":     sale_date,
                    "returns_count": 0,
                    "return_amount": 0.0,
                }
            agg[key]["returns_count"] += 1
            agg[key]["return_amount"] += price
        results = list(agg.values())
        logger.info(f"[WB.get_returns_analytics] {len(results)} агрегатов возвратов")
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

    async def get_questions(self, page_size: int = 100) -> list[dict]:
        """Неотвеченные вопросы покупателей Ozon через /v1/question/list."""
        results = []
        page = 1
        async with aiohttp.ClientSession() as session:
            while True:
                data = await _request(
                    session, "POST",
                    f"{self._BASE}/v1/question/list",
                    headers=self._headers(),
                    json={"page": page, "page_size": page_size, "status": "not_answered"},
                    label="Ozon.get_questions",
                )
                if not data:
                    break
                items = data.get("questions") or []
                for item in items:
                    results.append({
                        "question_id":   str(item.get("id", "")),
                        "product_id":    str(item.get("sku", "") or ""),
                        "product_name":  item.get("product_name", ""),
                        "question_text": item.get("question_text", "") or item.get("question", ""),
                        "created_at":    item.get("published_at", "") or item.get("created_at", ""),
                    })
                if len(items) < page_size:
                    break
                page += 1
        logger.info(f"[Ozon.get_questions] {len(results)} неотвеченных вопросов")
        return results

    async def answer_question(self, question_id: str, text: str) -> bool:
        """Ответить на вопрос покупателя Ozon через /v1/question/answer/create."""
        async with aiohttp.ClientSession() as session:
            data = await _request(
                session, "POST",
                f"{self._BASE}/v1/question/answer/create",
                headers=self._headers(),
                json={"question_id": question_id, "text": text},
                label=f"Ozon.answer_question({question_id[:8]})",
            )
        return data is not None

    async def get_returns_analytics(self, date_from: str, date_to: str) -> list[dict]:
        """Аналитика возвратов Ozon за период через /v1/analytics/data."""
        async with aiohttp.ClientSession() as session:
            data = await _request(
                session, "POST",
                f"{self._BASE}/v1/analytics/data",
                headers=self._headers(),
                json={
                    "date_from": date_from,
                    "date_to":   date_to,
                    "metrics":   ["returns", "return_amount", "return_rate"],
                    "dimension": [{"field": "sku"}, {"field": "title"}],
                    "filters":   [],
                    "sort":      [{"key": "return_amount", "order": "DESC"}],
                    "limit":     1000,
                    "offset":    0,
                },
                label="Ozon.get_returns_analytics",
            )
        if not data:
            return []
        results = []
        for row in (data.get("result", {}).get("data") or []):
            dims = row.get("dimensions") or []
            vals = row.get("metrics") or []
            sku  = dims[0].get("id", "") if len(dims) > 0 else ""
            name = dims[1].get("name", "") if len(dims) > 1 else ""
            results.append({
                "product_id":    str(sku),
                "product_name":  name,
                "returns_count": int(vals[0]) if len(vals) > 0 else 0,
                "return_amount": float(vals[1]) if len(vals) > 1 else 0.0,
                "return_rate":   float(vals[2]) if len(vals) > 2 else None,
                "stat_date":     date_to,
            })
        logger.info(f"[Ozon.get_returns_analytics] {len(results)} товаров с возвратами")
        return results

    async def _get_sku_to_offer_id(self, skus: list[int]) -> dict[int, str]:
        """Получить маппинг SKU → offer_id через /v3/product/info/list."""
        import json as _json
        if not skus:
            return {}
        url = f"{self._BASE}/v3/product/info/list"
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
        # v3 возвращает {"items": [...]} напрямую, без обёртки result
        for item in (data.get("items") or []):
            sku = item.get("sku")
            offer_id = str(item.get("offer_id") or "").strip()
            if sku and offer_id:
                mapping[int(sku)] = offer_id
        return mapping

    async def get_product_categories(self, offer_ids: list[str]) -> dict[str, str]:
        """Возвращает {offer_id: category_name} через product/info/list + description-category/tree.

        Вызывается при sync, чтобы авто-заполнить product_mapping.category для Ozon-товаров.
        Запрашивает только offer_ids у которых category IS NULL.
        """
        import json as _json
        if not offer_ids:
            return {}

        # 1. Получить description_category_id для каждого offer_id
        offer_to_cat_id: dict[str, int] = {}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._BASE}/v3/product/info/list",
                    headers=self._headers(),
                    json={"offer_id": offer_ids},
                    timeout=_TIMEOUT,
                ) as resp:
                    raw = await resp.text()
                    if resp.status != 200:
                        logger.error(f"[Ozon.get_product_categories] product/info/list {resp.status}: {raw[:200]}")
                        return {}
                    data = _json.loads(raw)
            for item in (data.get("items") or []):
                oid = str(item.get("offer_id") or "").strip()
                cat_id = item.get("description_category_id")
                if oid and cat_id:
                    offer_to_cat_id[oid] = int(cat_id)
        except Exception as e:
            logger.error(f"[Ozon.get_product_categories] product/info/list exception: {e}")
            return {}

        if not offer_to_cat_id:
            return {}

        # 2. Получить дерево категорий и построить плоский словарь {id: name}
        cat_names: dict[int, str] = {}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._BASE}/v2/description-category/tree",
                    headers=self._headers(),
                    json={},
                    timeout=_TIMEOUT,
                ) as resp:
                    raw = await resp.text()
                    if resp.status != 200:
                        logger.error(f"[Ozon.get_product_categories] category/tree {resp.status}: {raw[:200]}")
                    else:
                        tree_data = _json.loads(raw)

                        def _flatten(nodes: list) -> None:
                            for node in nodes:
                                cid = node.get("description_category_id")
                                name = node.get("category_name", "")
                                if cid:
                                    cat_names[int(cid)] = name
                                _flatten(node.get("children") or [])

                        _flatten(tree_data.get("result") or [])
        except Exception as e:
            logger.error(f"[Ozon.get_product_categories] category/tree exception: {e}")

        return {
            oid: cat_names.get(cat_id, "")
            for oid, cat_id in offer_to_cat_id.items()
            if cat_names.get(cat_id)
        }

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

        # Логируем первые поля одной строки для диагностики формата API
        if rows:
            sample_keys = list(rows[0].keys())
            logger.info(f"[Ozon.get_stocks] пример строки (ключи): {sample_keys}")
            logger.info(f"[Ozon.get_stocks] первая строка: {dict(rows[0])}")

        results = []
        skipped = 0
        for item in rows:
            sku = item.get("sku")
            offer_id = sku_map.get(int(sku), "") if sku else ""
            if not offer_id:
                # item_code в ответе Ozon — это offer_id продавца
                offer_id = str(item.get("item_code") or "").strip()
            if not offer_id:
                skipped += 1
                continue
            warehouse_name = str(item.get("warehouse_name") or "").strip()
            logger.info(
                f"[Ozon.get_stocks] {offer_id} @ warehouse={warehouse_name!r} "
                f"stock={item.get('free_to_sell_amount')}"
            )
            results.append({
                "product_id":    offer_id,
                "product_name":  item.get("item_name") or item.get("title") or offer_id,
                "warehouse_name": warehouse_name,
                "stock":         int(item.get("free_to_sell_amount", 0) or item.get("for_sale", 0)),
                "reserved":      int(item.get("reserved_amount", 0)),
                "in_transit":    int(item.get("incoming_amount", 0)),
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
                    "seller_price": round(rev / qty, 2) if qty > 0 else None,
                    "order_date":   df_str,
                })
            if len(rows) < 1000 or offset >= 10000:
                break
            offset += len(rows)
        logger.info(f"[Ozon.get_orders_analytics] {df_str}–{dt_str}: {len(results)} записей")
        sample_ids = [f"ozon_analytics_{r['product_id']}_{df_str}" for r in results[:3]]
        logger.info(f"[Ozon.get_orders_analytics] sample order_ids: {sample_ids}")
        return results

    async def get_warehouse_demand(self, date_from: str, date_to: str) -> list[dict]:
        """Заказы Ozon по SKU и складу из /v1/analytics/data (dimension: sku + warehouse).

        Возвращает [{sku, warehouse_name, qty}] — точный per-warehouse спрос.
        """
        import json as _json
        url = f"{self._BASE}/v1/analytics/data"
        results: list[dict] = []
        offset = 0
        while True:
            body = {
                "date_from": date_from,
                "date_to":   date_to,
                "dimension": ["sku", "warehouse"],
                "metrics":   ["ordered_units"],
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
                                logger.warning(f"[Ozon.get_warehouse_demand] rate limit, жду 60 сек (attempt {attempt+1})")
                                await asyncio.sleep(60)
                                continue
                            if resp.status != 200:
                                logger.warning(f"[Ozon.get_warehouse_demand] HTTP {resp.status}: {raw[:200]}")
                                break
                            data = _json.loads(raw)
                            break
                except asyncio.TimeoutError:
                    logger.error(f"[Ozon.get_warehouse_demand] timeout POST {url}")
                    break
                except Exception as e:
                    logger.error(f"[Ozon.get_warehouse_demand] {e}")
                    break
            if data is None:
                break
            rows = (data.get("result") or {}).get("data") or []
            for row in rows:
                dims = row.get("dimensions") or []
                qty = int((row.get("metrics") or [0])[0] or 0)
                if qty <= 0 or len(dims) < 2:
                    continue
                results.append({
                    "sku":           str(dims[0].get("id", "")),
                    "warehouse_name": str(dims[1].get("name", "")),
                    "qty":           qty,
                })
            if len(rows) < 1000 or offset >= 10000:
                break
            offset += len(rows)
        logger.info(f"[Ozon.get_warehouse_demand] {date_from}–{date_to}: {len(results)} строк по складам")
        return results

    async def get_financial_report(self, date_from: str, date_to: str) -> list[dict]:
        """Финансовый отчёт Ozon через /v3/finance/transaction/list (тип orders + returns).

        Агрегирует по (offer_id, неделя): payout, commission, logistics, penalty.
        """
        import json as _json
        import datetime as _dt_mod
        url = f"{self._BASE}/v3/finance/transaction/list"
        agg: dict[tuple, dict] = {}

        # Ozon отвергает запрос с периодом дольше месяца ("too long period, only
        # one month allowed") — без чанкинга весь финотчёт молча возвращает 0 строк
        # (ошибка только логируется, не выбрасывается), и /sync_fin выглядит успешным.
        d_from = _dt_mod.date.fromisoformat(date_from)
        d_to   = _dt_mod.date.fromisoformat(date_to)
        chunks: list[tuple[_dt_mod.date, _dt_mod.date]] = []
        chunk_start = d_from
        while chunk_start <= d_to:
            chunk_end = min(chunk_start + _dt_mod.timedelta(days=27), d_to)
            chunks.append((chunk_start, chunk_end))
            chunk_start = chunk_end + _dt_mod.timedelta(days=1)

        for chunk_from, chunk_to in chunks:
            for tx_type in ("orders", "returns"):
                page = 1
                while True:
                    body = {
                        "filter": {
                            "date": {
                                "from": f"{chunk_from.isoformat()}T00:00:00.000Z",
                                "to":   f"{chunk_to.isoformat()}T23:59:59.000Z",
                            },
                            "transaction_type": tx_type,
                        },
                        "page":      page,
                        "page_size": 1000,
                    }
                    data = None
                    for attempt in range(3):
                        try:
                            async with aiohttp.ClientSession() as session:
                                async with session.post(url, headers=self._headers(), json=body, timeout=_TIMEOUT) as resp:
                                    raw = await resp.text()
                                    if resp.status == 429:
                                        await asyncio.sleep(60)
                                        continue
                                    if resp.status != 200:
                                        logger.error(f"[Ozon.get_financial_report] HTTP {resp.status}: {raw[:200]}")
                                        break
                                    data = _json.loads(raw)
                                    break
                        except Exception as e:
                            logger.error(f"[Ozon.get_financial_report] {e}")
                            break
                    if data is None:
                        break

                    operations = (data.get("result") or {}).get("operations") or []
                    for op in operations:
                        op_items = op.get("items") or []
                        # /v3/finance/transaction/list отдаёт items[].sku (число), а не
                        # offer_id — раньше код искал offer_id, которого тут нет, и все
                        # строки тихо отфильтровывались (financial_report для Ozon был
                        # всегда пуст). product_id здесь = sku, джойн на product_mapping
                        # должен идти через ozon_sku, не ozon_offer_id (см. agents/peter.py).
                        offer_id = str(op_items[0].get("sku", "") or "") if op_items else ""
                        if not offer_id:
                            continue
                        # Дата начала недели
                        op_date_str = (op.get("operation_date") or date_from)[:10]
                        try:
                            op_date = _dt_mod.date.fromisoformat(op_date_str)
                            week_start = op_date - _dt_mod.timedelta(days=op_date.weekday())
                        except Exception:
                            week_start = op_date_str

                        key = (offer_id, week_start)
                        if key not in agg:
                            agg[key] = {
                                "product_id":  offer_id,
                                "report_date": week_start,
                                "quantity":    0,
                                "revenue":     0.0,
                                "payout":      0.0,
                                "commission":  0.0,
                                "logistics":   0.0,
                                "storage":     0.0,
                                "penalty":     0.0,
                            }
                        a = agg[key]
                        sign = 1 if tx_type == "orders" else -1
                        # /v3/finance/transaction/list НЕ отдаёт quantity (ни на уровне
                        # операции, ни в items[] — там только name/sku). quantity/revenue
                        # для Ozon считаются отдельно через get_realization_quantity_revenue()
                        # (/v2/finance/realization, единственный эндпоинт с ценой за штуку).
                        a["payout"]     += float(op.get("accruals_for_sale", 0) or 0)
                        a["commission"] += abs(float(op.get("sale_commission", 0) or 0))
                        a["logistics"]  += abs(float(op.get("delivery_charge", 0) or 0))
                        a["logistics"]  += abs(float(op.get("return_delivery_charge", 0) or 0))

                    if len(operations) < 1000:
                        break
                    page += 1

        results = list(agg.values())
        logger.info(f"[Ozon.get_financial_report] {date_from}–{date_to}: {len(results)} агрегатов")
        return results

    async def get_realization_quantity_revenue(self, date_from: str, date_to: str) -> list[dict]:
        """Количество и выручка по факту реализации через /v2/finance/realization.

        Единственный эндпоинт Ozon, где есть цена за штуку (seller_price_per_instance)
        и реальное количество (delivery_commission.quantity — строка отчёта может
        объединять несколько единиц одного товара, quantity != 1 для части строк).
        Отчёт месячный (year/month), report_date = первое число месяца.
        product_id = sku (строкой) — для единообразия с get_financial_report, где
        offer_id физически отсутствует в ответе API.
        """
        import json as _json
        import datetime as _dt_mod

        d_from = _dt_mod.date.fromisoformat(date_from)
        d_to   = _dt_mod.date.fromisoformat(date_to)
        months: set[tuple[int, int]] = set()
        cur = d_from.replace(day=1)
        while cur <= d_to:
            months.add((cur.year, cur.month))
            cur = (cur.replace(day=28) + _dt_mod.timedelta(days=4)).replace(day=1)

        agg: dict[tuple, dict] = {}
        url = f"{self._BASE}/v2/finance/realization"

        for year, month in sorted(months):
            data = None
            for attempt in range(3):
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(
                            url, headers=self._headers(),
                            json={"year": year, "month": month}, timeout=_TIMEOUT,
                        ) as resp:
                            raw = await resp.text()
                            if resp.status == 429:
                                await asyncio.sleep(60)
                                continue
                            if resp.status != 200:
                                logger.error(f"[Ozon.get_realization_quantity_revenue] HTTP {resp.status}: {raw[:200]}")
                                break
                            data = _json.loads(raw)
                            break
                except Exception as e:
                    logger.error(f"[Ozon.get_realization_quantity_revenue] {e}")
                    break
            if data is None:
                continue

            rows = (data.get("result") or {}).get("rows") or []
            # Дата = последний день месяца, не первое число: запросы на "последние N
            # дней" иначе не захватывают помесячные строки (см. report_date вместо
            # недельных WB-бакетов) — отчёт физически закрывается в конце месяца.
            next_month = (_dt_mod.date(year, month, 28) + _dt_mod.timedelta(days=4)).replace(day=1)
            report_date = next_month - _dt_mod.timedelta(days=1)
            for row in rows:
                sku = str(row.get("item", {}).get("sku", "") or "")
                if not sku:
                    continue
                qty   = int((row.get("delivery_commission") or {}).get("quantity", 0) or 0)
                price = float(row.get("seller_price_per_instance", 0) or 0)

                key = (sku, report_date)
                if key not in agg:
                    agg[key] = {"product_id": sku, "report_date": report_date, "quantity": 0, "revenue": 0.0}
                agg[key]["quantity"] += qty
                agg[key]["revenue"]  += price * qty

        results = list(agg.values())
        logger.info(f"[Ozon.get_realization_quantity_revenue] {date_from}–{date_to}: {len(results)} агрегатов")
        return results

    # Типы операций Ozon Finance API, относящихся к рекламным расходам.
    # Установлены эмпирически из логов (2026-06-21): transaction_type=all.
    # CPC и CPO дублируют данные Performance API, но Finance API — источник истины
    # для ДРР (совпадает с кабинетом продавца); Performance API остаётся для
    # разбивки по кампаниям/товарам в CtrRoas.
    _ADV_OPERATION_TYPES = frozenset({
        "OperationMarketplaceCostPerClick",   # Оплата за клик (CPC)
        "OperationPromotionWithCostPerOrder",  # Продвижение с оплатой за заказ (CPO)
        "OperationSubscriptionPremium",        # Подписка Premium
        "MarketplaceServiceBrandCommission",   # Продвижение бренда
    })

    async def get_fin_adv_spend(self, date_from: str, date_to: str) -> list[dict]:
        """Рекламные расходы Ozon из Finance API — все типы включая Premium и бренд.

        Использует /v3/finance/transaction/list (transaction_type=all) и фильтрует
        по _ADV_OPERATION_TYPES. Возвращает суммы по дням, совпадающие с разделом
        «Продвижение и реклама» в кабинете продавца (кроме «Бонусов продавца» — 2₽,
        пренебрежимо малы и не видны отдельным operation_type).
        """
        import json as _json
        url = f"{self._BASE}/v3/finance/transaction/list"
        daily: dict[str, float] = {}
        seen_op_ids: set = set()

        page = 1
        while True:
            body = {
                "filter": {
                    "date": {
                        "from": f"{date_from}T00:00:00.000Z",
                        "to":   f"{date_to}T23:59:59.000Z",
                    },
                    "transaction_type": "all",
                },
                "page":      page,
                "page_size": 1000,
            }
            data = None
            async with aiohttp.ClientSession() as s:
                for attempt in range(3):
                    try:
                        async with s.post(
                            url, json=body, headers=self._headers(),
                            timeout=aiohttp.ClientTimeout(total=30),
                        ) as resp:
                            raw = await resp.text()
                            if resp.status == 429:
                                await asyncio.sleep(60)
                                continue
                            if resp.status != 200:
                                logger.error(f"[Ozon.get_fin_adv_spend] HTTP {resp.status}: {raw[:200]}")
                                break
                            data = _json.loads(raw)
                            break
                    except Exception as e:
                        logger.error(f"[Ozon.get_fin_adv_spend] {e}")
                        break
            if data is None:
                break

            operations = (data.get("result") or {}).get("operations") or []
            for op in operations:
                op_id = op.get("operation_id")
                if op_id is not None:
                    if op_id in seen_op_ids:
                        continue
                    seen_op_ids.add(op_id)

                op_type = op.get("operation_type") or op.get("operation_type_name") or ""
                if op_type not in self._ADV_OPERATION_TYPES:
                    continue

                amount   = abs(float(op.get("amount", 0) or 0))
                op_date  = (op.get("operation_date") or date_from)[:10]
                if amount > 0:
                    daily[op_date] = daily.get(op_date, 0.0) + amount

            page_count = (data.get("result") or {}).get("page_count") or 1
            if page >= page_count or not operations:
                break
            page += 1

        results = [{"date": d, "adv_spend": round(v, 2)} for d, v in sorted(daily.items())]
        total = sum(r["adv_spend"] for r in results)
        logger.info(
            f"[Ozon.get_fin_adv_spend] {date_from}–{date_to}: "
            f"{len(results)} дней, итого {total:.0f}₽ (CPC+CPO+Premium+Бренд)"
        )
        return results

    async def get_funnel_stats(self, date_from: str, date_to: str) -> list[dict]:
        """Воронка конверсии карточки Ozon через /v1/analytics/data с метриками показов и корзины."""
        import json as _json
        url = f"{self._BASE}/v1/analytics/data"
        results = []
        offset = 0
        while True:
            body = {
                "date_from": date_from,
                "date_to":   date_to,
                "dimension": ["sku"],
                "metrics":   ["views", "conv_tocart", "ordered_units", "avg_search_position"],
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
                                await asyncio.sleep(60)
                                continue
                            if resp.status != 200:
                                logger.error(f"[Ozon.get_funnel_stats] HTTP {resp.status}: {raw[:200]}")
                                break
                            data = _json.loads(raw)
                            break
                except Exception as e:
                    logger.error(f"[Ozon.get_funnel_stats] exception: {e}")
                    break
            if data is None:
                break
            rows = (data.get("result") or {}).get("data") or []
            for row in rows:
                dims    = row.get("dimensions") or [{}]
                metrics = row.get("metrics") or [0, 0, 0, None]
                views      = int(metrics[0] or 0)
                conv_tocart = float(metrics[1] or 0)
                orders     = int(metrics[2] or 0)
                avg_pos    = float(metrics[3]) if len(metrics) > 3 and metrics[3] is not None else None
                add_to_cart = round(views * conv_tocart / 100) if views > 0 else 0
                results.append({
                    "product_id":         str((dims[0] if dims else {}).get("id", "")),
                    "stat_date":          date_from,
                    "views":              views,
                    "add_to_cart":        add_to_cart,
                    "orders_count":       orders,
                    "buyouts":            0,
                    "avg_position":       avg_pos,
                    "conv_view_to_cart":  round(conv_tocart, 2),
                    "conv_cart_to_order": round(orders / add_to_cart * 100, 2) if add_to_cart > 0 else 0,
                })
            if len(rows) < 1000 or offset >= 10000:
                break
            offset += len(rows)
        logger.info(f"[Ozon.get_funnel_stats] {date_from}–{date_to}: {len(results)} записей")
        return results

    async def get_promotions(self) -> list[dict]:
        """Список акций Ozon через /v2/promotion/list."""
        import json as _json
        url = f"{self._BASE}/v2/promotion/list"
        results = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=self._headers(), json={}, timeout=_TIMEOUT) as resp:
                    raw = await resp.text()
                    if resp.status != 200:
                        logger.error(f"[Ozon.get_promotions] HTTP {resp.status}: {raw[:200]}")
                        return []
                    data = _json.loads(raw)
            for promo in (data.get("result") or {}).get("actions", []):
                promo_id = str(promo.get("id", ""))
                if not promo_id:
                    continue
                results.append({
                    "promotion_id": promo_id,
                    "title":        promo.get("title") or promo.get("name", ""),
                    "discount_pct": float(promo.get("discount_value") or 0),
                    "start_date":   (promo.get("date_start") or "")[:10],
                    "end_date":     (promo.get("date_end") or "")[:10],
                    "product_ids":  [],
                })
        except Exception as e:
            logger.error(f"[Ozon.get_promotions] {e}", exc_info=True)
        logger.info(f"[Ozon.get_promotions] {len(results)} акций")
        return results

    async def get_available_promotions(self) -> list[dict]:
        """Доступные акции для вступления через /v1/actions."""
        import json as _json
        url = f"{self._BASE}/v1/actions"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self._headers(), timeout=_TIMEOUT) as resp:
                    raw = await resp.text()
                    if resp.status != 200:
                        logger.error(f"[Ozon.get_available_promotions] HTTP {resp.status}: {raw[:200]}")
                        return []
                    data = _json.loads(raw)
            results = []
            for a in (data.get("result") or {}).get("actions", []):
                action_id = str(a.get("id", ""))
                if not action_id:
                    continue
                results.append({
                    "action_id":    action_id,
                    "title":        a.get("title") or "",
                    "discount_pct": float(a.get("discount_value") or 0),
                    "start_date":   (a.get("date_start") or "")[:10],
                    "end_date":     (a.get("date_end") or "")[:10],
                    "freeze_date":  (a.get("freeze_date") or "")[:10],
                    "order_amount": int(a.get("potential_products_count") or 0),
                })
            logger.info(f"[Ozon.get_available_promotions] {len(results)} акций")
            return results
        except Exception as e:
            logger.error(f"[Ozon.get_available_promotions] {e}", exc_info=True)
            return []

    async def get_action_products(self, action_id: str) -> list[dict]:
        """Товары, доступные для вступления в акцию, через /v1/actions/products."""
        import json as _json
        url  = f"{self._BASE}/v1/actions/products"
        page = 0
        results = []
        try:
            async with aiohttp.ClientSession() as session:
                while True:
                    async with session.post(
                        url,
                        headers=self._headers(),
                        json={"action_id": int(action_id), "limit": 100, "offset": page * 100},
                        timeout=_TIMEOUT,
                    ) as resp:
                        raw = await resp.text()
                        if resp.status != 200:
                            logger.error(f"[Ozon.get_action_products] HTTP {resp.status}: {raw[:200]}")
                            break
                        data = _json.loads(raw)
                    items = (data.get("result") or {}).get("products") or []
                    for it in items:
                        results.append({
                            "product_id":    str(it.get("id", "")),
                            "offer_id":      str(it.get("offer_id", "")),
                            "name":          it.get("name") or "",
                            "price":         float(it.get("price") or 0),
                            "action_price":  float(it.get("action_price") or 0),
                            "max_action_price": float(it.get("max_action_price") or 0),
                            "add_mode":      it.get("add_mode") or "",
                        })
                    total = (data.get("result") or {}).get("total") or 0
                    if len(results) >= total or not items:
                        break
                    page += 1
        except Exception as e:
            logger.error(f"[Ozon.get_action_products] {e}", exc_info=True)
        return results

    async def join_promotion(self, action_id: str, products: list[dict]) -> tuple[int, int]:
        """Вступить в акцию. products = [{"product_id": int, "action_price": float}, ...].

        Возвращает (added_count, rejected_count).
        """
        import json as _json
        url = f"{self._BASE}/v1/actions/products/activate"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=self._headers(),
                    json={"action_id": int(action_id), "products": products},
                    timeout=_TIMEOUT,
                ) as resp:
                    raw = await resp.text()
                    if resp.status != 200:
                        logger.error(f"[Ozon.join_promotion] HTTP {resp.status}: {raw[:200]}")
                        return 0, len(products)
                    data = _json.loads(raw)
            result = data.get("result") or {}
            added    = int(result.get("product_count") or 0)
            rejected = int(result.get("rejected") or 0)
            logger.info(f"[Ozon.join_promotion] action={action_id} added={added} rejected={rejected}")
            return added, rejected
        except Exception as e:
            logger.error(f"[Ozon.join_promotion] {e}", exc_info=True)
            return 0, len(products)

    async def exit_promotion(self, action_id: str, product_ids: list[int]) -> bool:
        """Выйти из акции для указанных товаров через /v1/actions/products/deactivate."""
        import json as _json
        url = f"{self._BASE}/v1/actions/products/deactivate"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=self._headers(),
                    json={"action_id": int(action_id), "product_ids": product_ids},
                    timeout=_TIMEOUT,
                ) as resp:
                    raw = await resp.text()
                    if resp.status != 200:
                        logger.error(f"[Ozon.exit_promotion] HTTP {resp.status}: {raw[:200]}")
                        return False
                    logger.info(f"[Ozon.exit_promotion] action={action_id} products={len(product_ids)}")
                    return True
        except Exception as e:
            logger.error(f"[Ozon.exit_promotion] {e}", exc_info=True)
            return False

    async def get_shop_kpi(self) -> dict:
        """Рейтинг продавца Ozon через /v1/rating/summary.

        Реальная структура ответа: {"groups": [{"group_name": "...", "items": [...]}]}
        Каждый item: {"current_value": float, "name": str, "value_type": str, ...}
        """
        import json as _json
        url = f"{self._BASE}/v1/rating/summary"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=self._headers(), json={}, timeout=_TIMEOUT) as resp:
                    raw = await resp.text()
                    if resp.status != 200:
                        logger.error(f"[Ozon.get_shop_kpi] HTTP {resp.status}: {raw[:300]}")
                        return {}
                    data = _json.loads(raw)

            groups_list = data.get("groups") or (data.get("result") or {}).get("groups") or []

            # Собираем все items плоским списком с привязкой к group_name
            all_items: list[dict] = []
            for g in groups_list:
                gname = (g.get("group_name") or "").lower()
                for item in (g.get("items") or []):
                    all_items.append({**item, "_group": gname})

            def _first_val(keywords: list[str], field: str = "current_value") -> float:
                """Первый item у которого group_name или name содержит любое из keywords."""
                for item in all_items:
                    text = item.get("_group", "") + " " + (item.get("name") or "").lower()
                    if any(kw in text for kw in keywords):
                        return float(item.get(field) or item.get("current_value") or 0)
                return 0.0

            rating_val   = _first_val(["оценка", "рейтинг", "review"])
            cancellation = _first_val(["отмен", "cancel"])
            return_pct   = _first_val(["возврат", "return"])
            # штрафы часто целые числа
            penalty      = int(_first_val(["штраф", "нарушен", "penalty", "fine"]))

            logger.info(
                f"[Ozon.get_shop_kpi] rating={rating_val} cancel={cancellation} "
                f"return={return_pct} penalty={penalty} "
                f"groups={[g.get('group_name') for g in groups_list]}"
            )
            return {
                "rating":           rating_val,
                "return_pct":       return_pct,
                "cancellation_pct": cancellation,
                "penalty_count":    penalty,
                "extra_data":       {"groups": groups_list},
            }
        except Exception as e:
            logger.error(f"[Ozon.get_shop_kpi] {e}", exc_info=True)
            return {}


    async def get_current_prices(self, skus: list[int]) -> list[dict]:
        """Текущие цены товаров Ozon через /v3/product/info/list (по SKU).
        Возвращает [{"product_id": offer_id, "price": float}].
        """
        if not skus:
            return []
        url = f"{self._BASE}/v3/product/info/list"
        results = []
        chunk_size = 100
        async with aiohttp.ClientSession() as session:
            for i in range(0, len(skus), chunk_size):
                chunk = skus[i:i + chunk_size]
                data = await _request(
                    session, "POST", url,
                    headers=self._headers(),
                    json={"sku": chunk},
                    label="Ozon.get_current_prices",
                )
                if not data:
                    continue
                for item in (data.get("items") or []):
                    offer_id = item.get("offer_id")
                    try:
                        price = float(item.get("price") or 0)
                    except (ValueError, TypeError):
                        price = 0.0
                    if offer_id and price > 0:
                        results.append({"product_id": offer_id, "price": price})
        logger.info(f"[Ozon.get_current_prices] итого: {len(results)} товаров")
        return results

    async def update_prices(self, items: list[dict]) -> dict:
        """Обновить цены товаров на Ozon через /v1/product/import/prices.

        items: [{"offer_id": str, "price": float}].
        Возвращает {"success": bool, "task_id": int|None}.
        """
        if not items:
            return {"success": True, "task_id": None}
        url = f"{self._BASE}/v1/product/import/prices"
        payload = {
            "prices": [
                {
                    "offer_id":  item["offer_id"],
                    "price":     str(int(round(item["price"]))),
                    "old_price": "0",
                    "min_price": "0",
                }
                for item in items
            ]
        }
        async with aiohttp.ClientSession() as session:
            data = await _request(
                session, "POST", url,
                headers=self._headers(), json=payload, label="Ozon.update_prices",
            )
        if not data:
            return {"success": False, "task_id": None}
        task_id = (data.get("result") or {}).get("task_id")
        logger.info(f"[Ozon.update_prices] task_id={task_id} товаров={len(items)}")
        return {"success": True, "task_id": task_id}


    async def get_product_content(self, offer_ids: list[str]) -> dict[str, dict]:
        """Контент карточек: title, description, attributes.

        Шаг 1: /v3/product/info/attributes (батч 100) → name + attributes.
        Шаг 2: /v1/product/info/description (на каждый offer_id параллельно) → description.
        Возвращает {offer_id: {"title": str, "description": str, "characteristics": list}}.
        """
        import json as _json
        if not offer_ids:
            return {}

        result: dict[str, dict] = {}

        # Шаг 1: заголовок и атрибуты батчами по 100
        for i in range(0, len(offer_ids), 100):
            batch = offer_ids[i : i + 100]
            body = {
                "filter":   {"offer_id": batch, "visibility": "ALL"},
                "last_id":  "",
                "limit":    100,
                "sort_dir": "ASC",
            }
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{self._BASE}/v3/product/info/attributes",
                        headers=self._headers(),
                        json=body,
                        timeout=_TIMEOUT,
                    ) as resp:
                        if resp.status != 200:
                            logger.error(f"[Ozon.get_product_content] attributes HTTP {resp.status}: {(await resp.text())[:200]}")
                            continue
                        data = _json.loads(await resp.text())
                for item in (data.get("result") or []):
                    oid = str(item.get("offer_id") or "").strip()
                    if oid:
                        result[oid] = {
                            "title":           item.get("name") or "",
                            "description":     "",
                            "characteristics": item.get("attributes") or [],
                        }
            except Exception as e:
                logger.error(f"[Ozon.get_product_content] attributes batch {i}: {e}")

        # Шаг 2: описания параллельно (семафор 5)
        sem = asyncio.Semaphore(5)

        async def _fetch_desc(session: aiohttp.ClientSession, oid: str) -> None:
            async with sem:
                try:
                    async with session.post(
                        f"{self._BASE}/v1/product/info/description",
                        headers=self._headers(),
                        json={"offer_id": oid},
                        timeout=_TIMEOUT,
                    ) as resp:
                        if resp.status == 200:
                            data = _json.loads(await resp.text())
                            desc = (data.get("result") or {}).get("description") or ""
                            if oid in result:
                                result[oid]["description"] = desc
                except Exception as e:
                    logger.error(f"[Ozon.get_product_content] desc {oid}: {e}")

        if result:
            async with aiohttp.ClientSession() as session:
                await asyncio.gather(*[_fetch_desc(session, oid) for oid in list(result)])

        logger.info(f"[Ozon.get_product_content] получено {len(result)} карточек")
        return result

    async def update_product_description(self, offer_id: str, description: str) -> bool:
        """Обновить описание товара на Ozon через /v1/product/description/update."""
        import json as _json
        url = f"{self._BASE}/v1/product/description/update"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=self._headers(),
                    json={"items": [{"offer_id": offer_id, "description": description}]},
                    timeout=_TIMEOUT,
                ) as resp:
                    raw = await resp.text()
                    if resp.status != 200:
                        logger.error(f"[Ozon.update_desc] HTTP {resp.status}: {raw[:300]}")
                        return False
                    result = _json.loads(raw).get("result") or {}
                    if result.get("errors"):
                        logger.error(f"[Ozon.update_desc] API errors: {result['errors']}")
                        return False
                    logger.info(f"[Ozon.update_desc] offer_id={offer_id} ok")
                    return True
        except Exception as e:
            logger.error(f"[Ozon.update_desc] {e}", exc_info=True)
            return False

    async def update_product_name(self, offer_id: str, name: str) -> bool:
        """Обновить заголовок товара на Ozon: offer_id → item_id → /v1/product/name."""
        import json as _json
        try:
            async with aiohttp.ClientSession() as session:
                # Шаг 1: резолюция offer_id → item_id
                async with session.post(
                    f"{self._BASE}/v3/product/info/list",
                    headers=self._headers(),
                    json={"offer_id": [offer_id]},
                    timeout=_TIMEOUT,
                ) as resp:
                    raw = await resp.text()
                    if resp.status != 200:
                        logger.error(f"[Ozon.update_name] info/list HTTP {resp.status}: {raw[:300]}")
                        return False
                    items = _json.loads(raw).get("items") or []
                    if not items:
                        logger.error(f"[Ozon.update_name] offer_id={offer_id} не найден")
                        return False
                    item_id = items[0].get("id")
                    if not item_id:
                        logger.error(f"[Ozon.update_name] нет item_id для offer_id={offer_id}")
                        return False

            # Шаг 2: обновление названия
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._BASE}/v1/product/name",
                    headers=self._headers(),
                    json=[{"item_id": item_id, "name": name[:500]}],
                    timeout=_TIMEOUT,
                ) as resp:
                    raw = await resp.text()
                    if resp.status != 200:
                        logger.error(f"[Ozon.update_name] HTTP {resp.status}: {raw[:300]}")
                        return False
                    logger.info(f"[Ozon.update_name] offer_id={offer_id} item_id={item_id} ok")
                    return True
        except Exception as e:
            logger.error(f"[Ozon.update_name] {e}", exc_info=True)
            return False

    async def get_supply_statuses(self) -> list[dict]:
        """Активные заявки на поставку FBO с разбивкой по товарам.

        Возвращает [{"supply_id", "status_id", "status_name", "product_id", "qty"}].
        Поток: /v2/supply-order/list → /v2/supply-order/get (state + bundle_ids) →
               GET /v1/supply-order/bundle (contractor_item_code + quantity).
        Graceful fallback: если API недоступен — возвращает [].
        """
        import json as _json

        # Терминальные состояния — пропускаем
        SKIP_STATES = {
            "SUPPLY_ORDER_STATE_CANCELED", "SUPPLY_ORDER_STATE_CLOSED",
            "canceled", "closed",
        }

        # Карта читаемых имён для известных state-строк Ozon
        _OZON_STATE_NAME: dict[str, str] = {
            "SUPPLY_ORDER_STATE_DRAFT":           "Черновик",
            "SUPPLY_ORDER_STATE_NEW":             "Новая",
            "SUPPLY_ORDER_STATE_AWAITING_SUPPLY": "Готова к отгрузке",
            "SUPPLY_ORDER_STATE_ACCEPTED":        "Принята на склад",
            "SUPPLY_ORDER_STATE_IN_TRANSIT":      "В пути",
        }

        # Шаг 1: список ID заявок
        supply_order_ids: list[str] = []
        last_id = 0
        try:
            async with aiohttp.ClientSession() as session:
                while True:
                    async with session.post(
                        f"{self._BASE}/v2/supply-order/list",
                        headers=self._headers(),
                        json={"filter": {}, "paging": {"from_supply_order_id": last_id, "limit": 50}},
                        timeout=_TIMEOUT,
                    ) as resp:
                        if resp.status not in (200, 201):
                            raw = await resp.text()
                            logger.warning(
                                f"[Ozon.get_supply_statuses] /v2/supply-order/list "
                                f"HTTP {resp.status}: {raw[:200]}"
                            )
                            return []
                        data = _json.loads(await resp.text())
                    ids = data.get("supply_order_id") or []
                    supply_order_ids.extend(str(i) for i in ids)
                    last_id = int(data.get("last_supply_order_id") or 0)
                    if not ids or not last_id:
                        break
        except Exception as e:
            logger.warning(f"[Ozon.get_supply_statuses] list error: {e}")
            return []

        if not supply_order_ids:
            return []

        # Шаг 2: детали заявок → state + warehouse + supplies[].bundle_id
        # active_orders: [(supply_order_id, status_name, warehouse_name, bundle_id), ...]
        active_orders: list[tuple[str, str, str, str]] = []
        for i in range(0, len(supply_order_ids), 50):
            batch = supply_order_ids[i : i + 50]
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{self._BASE}/v2/supply-order/get",
                        headers=self._headers(),
                        json={"order_ids": batch},
                        timeout=_TIMEOUT,
                    ) as resp:
                        if resp.status not in (200, 201):
                            continue
                        data = _json.loads(await resp.text())
                for order in data.get("orders") or []:
                    state = str(order.get("state") or "")
                    if state in SKIP_STATES:
                        continue
                    supply_order_id = str(order.get("supply_order_id") or "")
                    status_name = _OZON_STATE_NAME.get(state, state or "Неизвестно")
                    # Имя склада Ozon (поле может быть warehouse.name или warehouse_name)
                    wh_obj = order.get("warehouse") or {}
                    warehouse_name = str(
                        wh_obj.get("name") or wh_obj.get("warehouse_name")
                        or order.get("warehouse_name") or ""
                    )
                    for sup in order.get("supplies") or []:
                        bundle_id = str(sup.get("bundle_id") or "")
                        if bundle_id:
                            active_orders.append((supply_order_id, status_name, warehouse_name, bundle_id))
            except Exception as e:
                logger.warning(f"[Ozon.get_supply_statuses] get batch {i}: {e}")

        if not active_orders:
            return []

        # Шаг 3: состав каждого bundle → offer_id + qty
        result: list[dict] = []
        for supply_order_id, status_name, warehouse_name, bundle_id in active_orders:
            last_bundle_id = ""
            while True:
                try:
                    params: list[tuple[str, str]] = [
                        ("bundle_ids", bundle_id),
                        ("limit", "100"),
                        ("is_asc", "true"),
                    ]
                    if last_bundle_id:
                        params.append(("last_id", last_bundle_id))
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            f"{self._BASE}/v1/supply-order/bundle",
                            headers=self._headers(),
                            params=params,
                            timeout=_TIMEOUT,
                        ) as resp:
                            if resp.status not in (200, 201):
                                break
                            data = _json.loads(await resp.text())
                    for item in data.get("items") or []:
                        offer_id = str(item.get("contractor_item_code") or "").strip()
                        if not offer_id:
                            offer_id = str(item.get("sku") or "")
                        qty = int(item.get("quantity") or 0)
                        if offer_id and qty > 0:
                            result.append({
                                "supply_id":      supply_order_id,
                                "status_id":      None,
                                "status_name":    status_name,
                                "product_id":     offer_id,
                                "qty":            qty,
                                "warehouse_name": warehouse_name,
                            })
                    if not data.get("has_next"):
                        break
                    last_bundle_id = str(data.get("last_id") or "")
                    if not last_bundle_id:
                        break
                except Exception as e:
                    logger.warning(
                        f"[Ozon.get_supply_statuses] bundle {bundle_id}: {e}"
                    )
                    break

        logger.info(
            f"[Ozon.get_supply_statuses] {len(supply_order_ids)} заявок, "
            f"{len(active_orders)} активных бандлов, {len(result)} позиций"
        )
        return result


class OzonPerformanceClient:
    _BASE = "https://api-performance.ozon.ru"

    def __init__(self, client_id: str, client_secret: str, redis_client) -> None:
        self._client_id     = client_id
        self._client_secret = client_secret
        self._redis         = redis_client

    async def _get_token(self) -> str | None:
        """Получить токен из Redis или запросить новый. TTL 25 минут."""
        import json as _json
        # Пробуем Redis (ключ per-client_id — у разных магазинов разные токены)
        cache_key = f"ozon_perf_token:{self._client_id}"
        try:
            cached = await self._redis.get(cache_key)
            if cached:
                return cached.decode() if isinstance(cached, bytes) else cached
        except Exception as e:
            logger.warning(f"[OzonPerf] Redis get error: {e}")

        # Получаем новый токен
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._BASE}/api/client/token",
                    json={
                        "client_id":     self._client_id,
                        "client_secret": self._client_secret,
                        "grant_type":    "client_credentials",
                    },
                    timeout=_TIMEOUT,
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"[OzonPerf] token HTTP {resp.status}: {await resp.text()}")
                        return None
                    data = await resp.json()
                    token = data.get("access_token")
        except Exception as e:
            logger.error(f"[OzonPerf] token exception: {e}")
            return None

        # Кешируем на 25 минут
        try:
            await self._redis.setex(cache_key, 1500, token)
        except Exception as e:
            logger.warning(f"[OzonPerf] Redis set error: {e}")

        return token

    async def get_ad_stats(self, date_from: str, date_to: str) -> list[dict]:
        """Статистика рекламных кампаний Ozon Performance за период."""
        import json as _json, csv, io

        token = await self._get_token()
        if not token:
            return []
        headers = {"Authorization": f"Bearer {token}"}

        # Шаг 1: получить список кампаний — без фильтра по state, иначе теряем
        # завершённые/остановленные кампании (CPO/брендовые часто короткие).
        # Статистика всё равно запрашивается за конкретный период date_from–date_to,
        # кампании без активности в этом периоде отсеются позже при парсинге CSV.
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._BASE}/api/client/campaign",
                    headers=headers,
                    timeout=_TIMEOUT,
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"[OzonPerf] campaigns HTTP {resp.status}")
                        return []
                    campaigns_data = await resp.json()
        except Exception as e:
            logger.error(f"[OzonPerf] campaigns exception: {e}")
            return []

        all_campaigns = campaigns_data.get("list") or []
        # Фильтр: только кампании с реальным бюджетом (убирает REF_VK и прочие нулевые)
        # Исключаем мусорные типы: REF_VK и кампании с невалидным типом оплаты
        EXCLUDE_TYPES = {"REF_VK"}
        EXCLUDE_PAYMENT = {"CAMPAIGN_TYPE_INVALID"}
        active = [
            c for c in all_campaigns
            if c.get("id")
            and c.get("advObjectType") not in EXCLUDE_TYPES
            and c.get("PaymentType") not in EXCLUDE_PAYMENT
        ]
        if not active:
            active = [c for c in all_campaigns if c.get("id")]
        campaign_ids = [str(c["id"]) for c in active]
        campaign_names = {str(c["id"]): c.get("title") or str(c["id"]) for c in active}

        if not campaign_ids:
            logger.info("[OzonPerf] нет кампаний")
            return []
        logger.info(f"[OzonPerf] всего кампаний: {len(all_campaigns)}, после фильтра: {len(active)}")

        # Шаги 2-4: для каждого батча: создать UUID → поллить → скачать CSV
        # API допускает только 1 активный запрос — поэтому строго последовательно
        csv_texts = []
        async with aiohttp.ClientSession() as session:
            for i in range(0, len(campaign_ids), 10):
                batch = campaign_ids[i:i+10]
                batch_num = i // 10 + 1
                total_batches = (len(campaign_ids) + 9) // 10

                # Создать UUID для батча
                uuid = None
                for attempt in range(10):
                    try:
                        async with session.post(
                            f"{self._BASE}/api/client/statistics",
                            headers=headers,
                            json={"campaigns": batch, "dateFrom": date_from, "dateTo": date_to},
                            timeout=_TIMEOUT,
                        ) as resp:
                            if resp.status == 429:
                                logger.warning(f"[OzonPerf] batch {batch_num}/{total_batches} rate limit, жду 60 сек (attempt {attempt+1})")
                                await asyncio.sleep(60)
                                continue
                            if resp.status != 200:
                                logger.error(f"[OzonPerf] batch {batch_num} POST HTTP {resp.status}: {await resp.text()}")
                                break
                            task_data = await resp.json()
                            uuid = task_data.get("UUID")
                            break
                    except Exception as e:
                        logger.error(f"[OzonPerf] batch {batch_num} POST exception: {e}")
                        break

                if not uuid:
                    logger.warning(f"[OzonPerf] batch {batch_num}: UUID не получен, пропускаем")
                    continue

                # Поллим до готовности (до 4 минут)
                report_ready = False
                for attempt in range(24):
                    await asyncio.sleep(10)
                    try:
                        async with session.get(
                            f"{self._BASE}/api/client/statistics/{uuid}",
                            headers=headers,
                            timeout=_TIMEOUT,
                        ) as resp:
                            if resp.status != 200:
                                continue
                            poll_data = await resp.json()
                            state = poll_data.get("state") or poll_data.get("status")
                            logger.debug(f"[OzonPerf] batch {batch_num} poll {attempt+1} state={state}")
                            if state in ("OK", "READY", "done"):
                                report_ready = True
                                break
                    except Exception as e:
                        logger.warning(f"[OzonPerf] poll exception: {e}")

                if not report_ready:
                    logger.error(f"[OzonPerf] batch {batch_num} отчёт не готов за 4 мин")
                    continue

                # Скачать CSV
                try:
                    async with session.get(
                        f"{self._BASE}/api/client/statistics/report",
                        headers=headers,
                        params={"UUID": uuid},
                        timeout=_TIMEOUT,
                    ) as resp:
                        logger.info(f"[OzonPerf] batch {batch_num} report HTTP {resp.status} content-type={resp.content_type}")
                        if resp.status != 200:
                            body = await resp.text(errors="replace")
                            logger.error(f"[OzonPerf] batch {batch_num} report HTTP {resp.status}: {body[:200]}")
                            continue
                        raw_bytes = await resp.read()
                        logger.info(f"[OzonPerf] batch {batch_num} CSV bytes={len(raw_bytes)}")
                        if raw_bytes:
                            import zipfile as _zipfile
                            with _zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                                names = zf.namelist()
                                for fname in names:
                                    csv_text_file = zf.read(fname).decode("utf-8-sig", errors="replace")
                                    csv_texts.append(csv_text_file)
                            logger.info(f"[OzonPerf] batch {batch_num}/{total_batches} ZIP файлов: {len(names)}, строк в первом: {csv_texts[-len(names)].count(chr(10)) if names else 0}")
                except Exception as e:
                    logger.error(f"[OzonPerf] batch {batch_num} report exception: {e}")

        if not csv_texts:
            logger.error("[OzonPerf] нет CSV-данных")
            return []

        # Шаг 5: парсим CSV
        # Формат Ozon Performance CSV:
        #   строка 1: ";Рекламная кампания № ID, период ..."  — мета
        #   строка 2: заголовки колонок (sku;Название товара;...;Показы;Клики;CTR (%);Расход, ₽, с НДС;...)
        #   строки 3+: данные по SKU
        #   последняя: "Всего;;;;;N;N;..."  — агрегат, пропускаем
        results = []
        try:
            import re as _re
            for csv_text_batch in csv_texts:
                lines = csv_text_batch.splitlines()
                if len(lines) < 2:
                    continue
                # Извлекаем campaign_id из первой строки: "№ 28852510"
                m = _re.search(r"№\s*(\d+)", lines[0])
                if not m:
                    continue
                cid = m.group(1)
                # Парсим через DictReader начиная со строки заголовков (строка 2)
                header_and_data = "\n".join(lines[1:])
                reader = csv.DictReader(io.StringIO(header_and_data), delimiter=";")
                views_sum = clicks_sum = spend_sum = 0
                row_count = 0
                product_stats = []
                for row in reader:
                    sku_val = str(row.get("sku", "")).strip()
                    if sku_val.lower() in ("всего", ""):
                        continue
                    nm_views  = int(float(row.get("Показы", 0) or 0))
                    nm_clicks = int(float(row.get("Клики", 0) or 0))
                    nm_spend  = float(str(row.get("Расход, ₽, с НДС", 0) or 0).replace(",", "."))
                    views_sum  += nm_views
                    clicks_sum += nm_clicks
                    spend_sum  += nm_spend
                    row_count  += 1
                    nm_ctr = round(nm_clicks / nm_views * 100, 2) if nm_views else 0.0
                    product_stats.append({
                        "product_id":   sku_val,
                        "views":        nm_views,
                        "clicks":       nm_clicks,
                        "ctr":          nm_ctr,
                        "spend":        nm_spend,
                        "orders_count": 0,
                    })
                if row_count == 0:
                    continue  # кампания без активности — не пишем в БД
                ctr = round(clicks_sum / views_sum * 100, 2) if views_sum else 0.0
                # Разбиваем агрегат на дни: Ozon Performance API возвращает только
                # суммарный расход за период, не по дням. Храним ежедневную долю
                # (spend/days) на каждый день отдельно — это позволяет корректно
                # суммировать данные в дашборде без двойного счёта при ежедневных синках.
                from datetime import datetime as _dt, timedelta as _td
                try:
                    d0 = _dt.strptime(date_from, "%Y-%m-%d")
                    d1 = _dt.strptime(date_to,   "%Y-%m-%d")
                except ValueError:
                    d0 = d1 = _dt.now()
                days_count = max((d1 - d0).days + 1, 1)
                daily_spend   = round(spend_sum   / days_count, 4)
                daily_views   = views_sum   // days_count
                daily_clicks  = clicks_sum  // days_count
                for day_offset in range(days_count):
                    day_str = (d0 + _td(days=day_offset)).strftime("%Y-%m-%d")
                    day_ps  = [
                        {**ps, "spend": round(ps["spend"] / days_count, 4),
                               "views": ps["views"] // days_count,
                               "clicks": ps["clicks"] // days_count}
                        for ps in product_stats
                    ]
                    results.append({
                        "campaign_id":   cid,
                        "campaign_name": campaign_names.get(cid, cid),
                        "stat_date":     day_str,
                        "views":         daily_views,
                        "clicks":        daily_clicks,
                        "ctr":           ctr,
                        "spend":         daily_spend,
                        "product_stats": day_ps,
                    })
        except Exception as e:
            logger.error(f"[OzonPerf] CSV parse exception: {e}")

        logger.info(f"[OzonPerf] итого записей: {len(results)}")
        return results

    async def get_campaigns(self) -> list[dict]:
        """Список всех кампаний с текущим статусом и дневным бюджетом."""
        token = await self._get_token()
        if not token:
            return []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._BASE}/api/client/campaign",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=_TIMEOUT,
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"[OzonPerf] get_campaigns HTTP {resp.status}")
                        return []
                    data = await resp.json()
        except Exception as e:
            logger.error(f"[OzonPerf] get_campaigns exception: {e}")
            return []

        EXCLUDE_TYPES = {"REF_VK"}
        campaigns = []
        for c in (data.get("list") or []):
            if c.get("advObjectType") in EXCLUDE_TYPES:
                continue
            campaigns.append({
                "id":     str(c.get("id", "")),
                "title":  c.get("title") or str(c.get("id", "")),
                "state":  c.get("state", ""),
                "budget": float(c.get("dailyBudget") or 0),
                "type":   c.get("advObjectType", ""),
            })
        return campaigns

    async def _campaign_action(self, campaign_id: str, action: str) -> bool:
        """Выполнить действие над кампанией: 'activate' или 'deactivate'."""
        token = await self._get_token()
        if not token:
            return False
        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    f"{self._BASE}/api/client/campaign/{campaign_id}/{action}",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=_TIMEOUT,
                ) as resp:
                    if resp.status not in (200, 204):
                        body = await resp.text()
                        logger.error(f"[OzonPerf] {action} campaign={campaign_id} HTTP {resp.status}: {body[:200]}")
                        return False
                    return True
        except Exception as e:
            logger.error(f"[OzonPerf] {action} campaign={campaign_id} exception: {e}")
            return False

    async def pause_campaign(self, campaign_id: str) -> bool:
        """Поставить кампанию Ozon Performance на паузу."""
        ok = await self._campaign_action(campaign_id, "deactivate")
        if ok:
            logger.info(f"[OzonPerf] кампания {campaign_id} остановлена")
        return ok

    async def activate_campaign(self, campaign_id: str) -> bool:
        """Запустить кампанию Ozon Performance."""
        ok = await self._campaign_action(campaign_id, "activate")
        if ok:
            logger.info(f"[OzonPerf] кампания {campaign_id} запущена")
        return ok

    async def update_campaign_daily_budget(self, campaign_id: str, budget: float) -> bool:
        """Установить дневной бюджет кампании (в рублях)."""
        token = await self._get_token()
        if not token:
            return False
        try:
            async with aiohttp.ClientSession() as session:
                async with session.patch(
                    f"{self._BASE}/api/client/campaign/{campaign_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"dailyBudget": str(int(budget))},
                    timeout=_TIMEOUT,
                ) as resp:
                    if resp.status not in (200, 204):
                        body = await resp.text()
                        logger.error(f"[OzonPerf] update_budget campaign={campaign_id} HTTP {resp.status}: {body[:200]}")
                        return False
                    logger.info(f"[OzonPerf] кампания {campaign_id} бюджет → {budget}₽")
                    return True
        except Exception as e:
            logger.error(f"[OzonPerf] update_budget exception: {e}")
            return False

    async def get_campaign_bids(self, campaign_id: str) -> list[dict]:
        """Ставки по SKU в кампании. Возвращает [{"product_id": int, "bid": float}]."""
        token = await self._get_token()
        if not token:
            return []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._BASE}/api/client/campaign/{campaign_id}/bids",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=_TIMEOUT,
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"[OzonPerf] get_bids campaign={campaign_id} HTTP {resp.status}: {body[:200]}")
                        return []
                    data = await resp.json()
            raw = data.get("bids") or (data if isinstance(data, list) else [])
            return [{"product_id": b.get("product_id"), "bid": float(b.get("bid") or 0)} for b in raw]
        except Exception as e:
            logger.error(f"[OzonPerf] get_bids exception: {e}")
            return []

    async def update_campaign_bids(self, campaign_id: str, bids: list[dict]) -> bool:
        """Обновить ставки по SKU. bids = [{"product_id": int, "bid": float}]."""
        token = await self._get_token()
        if not token:
            return False
        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    f"{self._BASE}/api/client/campaign/{campaign_id}/bids",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"bids": bids},
                    timeout=_TIMEOUT,
                ) as resp:
                    if resp.status not in (200, 204):
                        body = await resp.text()
                        logger.error(f"[OzonPerf] update_bids campaign={campaign_id} HTTP {resp.status}: {body[:200]}")
                        return False
                    logger.info(f"[OzonPerf] bids обновлены campaign={campaign_id} ({len(bids)} SKU)")
                    return True
        except Exception as e:
            logger.error(f"[OzonPerf] update_bids exception: {e}")
            return False

    async def delete_campaign(self, campaign_id: str) -> bool:
        """Удалить кампанию Ozon Performance. Действие необратимо."""
        token = await self._get_token()
        if not token:
            return False
        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    f"{self._BASE}/api/client/campaign/{campaign_id}",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=_TIMEOUT,
                ) as resp:
                    if resp.status not in (200, 204):
                        body = await resp.text()
                        logger.error(f"[OzonPerf] delete campaign={campaign_id} HTTP {resp.status}: {body[:200]}")
                        return False
                    logger.info(f"[OzonPerf] кампания {campaign_id} удалена")
                    return True
        except Exception as e:
            logger.error(f"[OzonPerf] delete exception: {e}")
            return False

    async def create_campaign(
        self,
        title: str,
        daily_budget: float,
        adv_type: str = "SKU_SEARCH",
        from_date: str | None = None,
    ) -> str | None:
        """Создать кампанию Ozon Performance. Возвращает campaign_id или None.

        adv_type: SKU_SEARCH (поиск) | SKU_SHELF (полка) | MEDIA_BANNER (медийная).
        """
        from datetime import date as _date
        token = await self._get_token()
        if not token:
            return None
        body = {
            "title": title,
            "advObjectType": adv_type,
            "dailyBudget": str(int(daily_budget)),
            "fromDate": from_date or _date.today().isoformat(),
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._BASE}/api/client/campaign",
                    headers={"Authorization": f"Bearer {token}"},
                    json=body,
                    timeout=_TIMEOUT,
                ) as resp:
                    if resp.status not in (200, 201):
                        body_txt = await resp.text()
                        logger.error(f"[OzonPerf] create_campaign HTTP {resp.status}: {body_txt[:300]}")
                        return None
                    data = await resp.json()
            cid = str(data.get("id") or data.get("campaign_id") or "")
            if cid:
                logger.info(f"[OzonPerf] кампания создана id={cid} title={title!r} budget={daily_budget}₽")
            return cid or None
        except Exception as e:
            logger.error(f"[OzonPerf] create_campaign exception: {e}")
            return None


def make_client(shop: dict):
    """Фабрика: dict из marketplace_shops → WBClient или OzonClient."""
    mp    = shop["marketplace"]
    token = shop["api_token"]
    if mp == "wb":
        return WBClient(token)
    if mp == "ozon":
        return OzonClient(token, shop.get("client_id") or "")
    raise ValueError(f"Неизвестный маркетплейс: {mp}")
