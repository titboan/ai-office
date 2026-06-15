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
                "seller_price": float(item.get("priceWithDisc") or 0),
                "order_date":   item.get("date") or item.get("lastChangeDate", ""),
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
        campaign_ids = []
        for group in (count_data.get("adverts") or []):
            if group.get("status") == 9:
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
                    for app in (day.get("apps") or []):
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

        logger.info(f"[WB.get_ad_stats] итого записей: {len(results)}")
        return results

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
                nm_id    = str(row.get("nm_id", "") or "")
                doc_type = row.get("doc_type_name", "")
                if not nm_id:
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

                key = (nm_id, rp_date)
                if key not in agg:
                    agg[key] = {
                        "product_id":  nm_id,
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
                # Возвраты имеют отрицательный qty и отрицательный ppvz_for_pay
                a["quantity"]   += qty
                a["revenue"]    += float(row.get("retail_price_withdisc_rub", 0) or 0) * (qty if qty > 0 else 0)
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
        """Вопросы покупателей WB через questions-api.wildberries.ru."""
        import json as _json
        _Q_BASE = "https://questions-api.wildberries.ru"
        url = f"{_Q_BASE}/api/v1/questions"
        headers = {"Authorization": self._token, "Content-Type": "application/json"}
        params = {"isAnswered": str(is_answered).lower(), "take": take, "skip": 0}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, params=params, timeout=_TIMEOUT) as resp:
                    raw = await resp.text()
                    if resp.status != 200:
                        logger.error(f"[WB.get_questions] HTTP {resp.status}: {raw[:200]}")
                        return []
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
        logger.info(f"[WB.get_questions] {len(results)} вопросов (is_answered={is_answered})")
        return results

    async def answer_question(self, question_id: str, text: str) -> bool:
        """Отправить ответ на вопрос покупателя WB."""
        import json as _json
        _Q_BASE = "https://questions-api.wildberries.ru"
        url = f"{_Q_BASE}/api/v1/questions"
        headers = {"Authorization": self._token, "Content-Type": "application/json"}
        body = {"id": question_id, "text": text, "state": "wbGoodsQaStatePublished"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.patch(url, headers=headers, json=body, timeout=_TIMEOUT) as resp:
                    if resp.status in (200, 204):
                        return True
                    raw = await resp.text()
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

    async def get_financial_report(self, date_from: str, date_to: str) -> list[dict]:
        """Финансовый отчёт Ozon через /v3/finance/transaction/list (тип orders + returns).

        Агрегирует по (offer_id, неделя): payout, commission, logistics, penalty.
        """
        import json as _json
        import datetime as _dt_mod
        url = f"{self._BASE}/v3/finance/transaction/list"
        agg: dict[tuple, dict] = {}

        for tx_type in ("orders", "returns"):
            page = 1
            while True:
                body = {
                    "filter": {
                        "date": {"from": f"{date_from}T00:00:00.000Z", "to": f"{date_to}T23:59:59.000Z"},
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
                    offer_id = str(op_items[0].get("offer_id", "") or "") if op_items else ""
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
                    a["quantity"]   += sign * int(op.get("quantity", 0) or 0)
                    a["payout"]     += float(op.get("accruals_for_sale", 0) or 0)
                    a["commission"] += abs(float(op.get("sale_commission", 0) or 0))
                    a["logistics"]  += abs(float(op.get("delivery_charge", 0) or 0))
                    a["logistics"]  += abs(float(op.get("return_delivery_charge", 0) or 0))
                    # Точная выручка из items[].price — цена товара по каждой позиции
                    item_revenue = sum(
                        float(it.get("price", 0) or 0) * int(it.get("quantity", 0) or 0)
                        for it in op_items
                    )
                    a["revenue"] += sign * item_revenue

                if len(operations) < 1000:
                    break
                page += 1

        results = list(agg.values())
        logger.info(f"[Ozon.get_financial_report] {date_from}–{date_to}: {len(results)} агрегатов")
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

    async def get_shop_kpi(self) -> dict:
        """Рейтинг продавца Ozon через /v1/rating/summary."""
        import json as _json
        url = f"{self._BASE}/v1/rating/summary"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=self._headers(), json={}, timeout=_TIMEOUT) as resp:
                    raw = await resp.text()
                    if resp.status != 200:
                        logger.error(f"[Ozon.get_shop_kpi] HTTP {resp.status}: {raw[:200]}")
                        return {}
                    data = _json.loads(raw)
            result = data.get("result") or {}
            groups = {g.get("key"): g for g in (result.get("groups") or [])}
            rating_val   = result.get("total_score") or result.get("rating") or 0
            cancellation = (groups.get("cancellation_rate") or {}).get("value") or 0
            return_pct   = (groups.get("return_rate") or {}).get("value") or 0
            penalty      = int((groups.get("penalty") or {}).get("value") or 0)
            return {
                "rating":           float(rating_val),
                "return_pct":       float(return_pct),
                "cancellation_pct": float(cancellation),
                "penalty_count":    penalty,
                "extra_data":       result,
            }
        except Exception as e:
            logger.error(f"[Ozon.get_shop_kpi] {e}", exc_info=True)
            return {}


class OzonPerformanceClient:
    _BASE = "https://api-performance.ozon.ru"

    def __init__(self, client_id: str, client_secret: str, redis_client) -> None:
        self._client_id     = client_id
        self._client_secret = client_secret
        self._redis         = redis_client

    async def _get_token(self) -> str | None:
        """Получить токен из Redis или запросить новый. TTL 25 минут."""
        import json as _json
        # Пробуем Redis
        try:
            cached = await self._redis.get("ozon_perf_token")
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
            await self._redis.setex("ozon_perf_token", 1500, token)
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

        # Шаг 1: получить список кампаний
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._BASE}/api/client/campaign",
                    headers=headers,
                    params={"state": "CAMPAIGN_STATE_RUNNING"},
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
        logger.info(f"[OzonPerf] всего RUNNING: {len(all_campaigns)}, после фильтра: {len(active)}")

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
                                csv_text_batch = zf.read(zf.namelist()[0]).decode("utf-8-sig", errors="replace")
                            csv_texts.append(csv_text_batch)
                            logger.info(f"[OzonPerf] batch {batch_num}/{total_batches} CSV получен, строк: {csv_text_batch.count(chr(10))}")
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
                results.append({
                    "campaign_id":   cid,
                    "campaign_name": campaign_names.get(cid, cid),
                    "stat_date":     date_from,
                    "views":         views_sum,
                    "clicks":        clicks_sum,
                    "ctr":           ctr,
                    "spend":         spend_sum,
                    "product_stats": product_stats,
                })
        except Exception as e:
            logger.error(f"[OzonPerf] CSV parse exception: {e}")

        logger.info(f"[OzonPerf] итого записей: {len(results)}")
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
