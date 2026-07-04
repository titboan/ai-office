"""
tools/niche_analysis.py — агрегация публичных данных WB-поиска
(WBClient.get_competitor_prices) для оценки перспективности ниши перед
закупкой нового товара: сколько предложений, какой разброс цен, насколько
рынок сконцентрирован вокруг нескольких брендов.

Только то, что реально пришло от WB — без придуманных оценок маржи
(для этого нет данных: неизвестна ни реальная комиссия площадки по
конкретной категории, ни наша себестоимость непроданного товара).
"""
from __future__ import annotations

import statistics
from collections import Counter


def summarize_niche_competition(rows: list[dict], top_brands: int = 5) -> dict:
    """
    rows — результат WBClient.get_competitor_prices(keyword):
    [{"position", "product_name", "brand", "price", "rating", "review_count"}].
    """
    if not rows:
        return {
            "offers_count": 0,
            "price_min": None,
            "price_median": None,
            "price_max": None,
            "avg_rating": None,
            "total_reviews": 0,
            "top_brands": [],
            "top_brands_share_pct": None,
        }

    prices = [r["price"] for r in rows if r.get("price")]
    ratings = [r["rating"] for r in rows if r.get("rating")]
    reviews = [r.get("review_count") or 0 for r in rows]
    brands = Counter(r.get("brand") or "—" for r in rows)

    top = brands.most_common(top_brands)
    top_brands_offers = sum(count for _, count in top)

    return {
        "offers_count": len(rows),
        "price_min": min(prices) if prices else None,
        "price_median": round(statistics.median(prices), 2) if prices else None,
        "price_max": max(prices) if prices else None,
        "avg_rating": round(statistics.mean(ratings), 2) if ratings else None,
        "total_reviews": sum(reviews),
        "top_brands": [{"brand": b, "offers": c} for b, c in top],
        "top_brands_share_pct": round(top_brands_offers / len(rows) * 100, 1),
    }
