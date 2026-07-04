from tools.niche_analysis import summarize_niche_competition


def _row(brand, price, rating=4.5, reviews=10):
    return {"position": 1, "product_name": "x", "brand": brand, "price": price,
            "rating": rating, "review_count": reviews}


def test_empty_rows():
    result = summarize_niche_competition([])
    assert result["offers_count"] == 0
    assert result["price_median"] is None
    assert result["top_brands"] == []


def test_price_and_review_aggregation():
    rows = [
        _row("BrandA", 100, rating=4.0, reviews=5),
        _row("BrandA", 200, rating=5.0, reviews=15),
        _row("BrandB", 300, rating=4.5, reviews=20),
    ]
    result = summarize_niche_competition(rows)
    assert result["offers_count"] == 3
    assert result["price_min"] == 100
    assert result["price_median"] == 200
    assert result["price_max"] == 300
    assert result["avg_rating"] == 4.5
    assert result["total_reviews"] == 40


def test_market_concentration_flagged_by_top_brands_share():
    # один бренд занимает всю нишу — top_brands_share_pct должен показать это
    rows = [_row("Monopoly", p) for p in (100, 110, 120, 130)]
    result = summarize_niche_competition(rows, top_brands=5)
    assert result["top_brands"] == [{"brand": "Monopoly", "offers": 4}]
    assert result["top_brands_share_pct"] == 100.0


def test_fragmented_market_lower_concentration():
    rows = [_row(f"Brand{i}", 100 + i * 10) for i in range(10)]
    result = summarize_niche_competition(rows, top_brands=5)
    assert result["offers_count"] == 10
    assert result["top_brands_share_pct"] == 50.0  # top-5 из 10 разных брендов = 50%


def test_missing_price_or_rating_ignored_in_stats():
    rows = [
        {"position": 1, "product_name": "x", "brand": "B", "price": 0, "rating": 0, "review_count": 3},
        _row("B", 150, rating=4.2, reviews=7),
    ]
    result = summarize_niche_competition(rows)
    assert result["offers_count"] == 2
    assert result["price_min"] == 150  # price=0 отфильтрован
    assert result["avg_rating"] == 4.2  # rating=0 отфильтрован
    assert result["total_reviews"] == 10
