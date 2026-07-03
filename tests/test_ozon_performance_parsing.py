"""
Regression-тесты на два бага из retrospectives/2026-06-21_ozon-drr-zip-finapi.md:

1. ZIP-баг: `zf.namelist()[0]` читал только первый CSV из архива —
   при N кампаниях в батче захватывались данные только ~1-2 из них (~10%).
2. stat_date-баг: агрегат за период хранился под одной датой (date_from),
   ежедневный синк создавал новую строку на каждый прогон —
   расходы искажались кратно числу синков (~7x на реальном инциденте).
"""
import io
import zipfile

from tools.marketplace import _extract_csv_texts_from_zip, _parse_ozon_ad_stats_csv


def _make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _make_campaign_csv(campaign_id: str, sku: str, views: int, clicks: int, spend: float) -> str:
    return (
        f";Рекламная кампания № {campaign_id}, период с 2026-06-01 по 2026-06-03\n"
        f"sku;Название товара;Показы;Клики;CTR (%);Расход, ₽, с НДС\n"
        f"{sku};Товар {sku};{views};{clicks};{round(clicks / views * 100, 2)};{spend}\n"
        f"Всего;;;{views};{clicks};;{spend}\n"
    )


def test_extract_csv_texts_from_zip_reads_all_files_not_just_first():
    raw = _make_zip({
        "campaign_1.csv": "content-1",
        "campaign_2.csv": "content-2",
        "campaign_3.csv": "content-3",
    })

    texts = _extract_csv_texts_from_zip(raw)

    assert len(texts) == 3
    assert texts == ["content-1", "content-2", "content-3"]


def test_parse_ad_stats_captures_all_campaigns_from_batch():
    csv_texts = [
        _make_campaign_csv("111", "SKU1", views=1000, clicks=50, spend=300.0),
        _make_campaign_csv("222", "SKU2", views=2000, clicks=80, spend=600.0),
        _make_campaign_csv("333", "SKU3", views=500, clicks=10, spend=90.0),
    ]
    campaign_names = {"111": "Кампания А", "222": "Кампания Б", "333": "Кампания В"}

    results = _parse_ozon_ad_stats_csv(csv_texts, campaign_names, "2026-06-01", "2026-06-01")

    seen_campaigns = {r["campaign_id"] for r in results}
    assert seen_campaigns == {"111", "222", "333"}


def test_parse_ad_stats_splits_period_aggregate_across_days_without_duplicating_spend():
    # 3-дневное окно, суммарный расход кампании за весь период — 300.
    csv_texts = [_make_campaign_csv("111", "SKU1", views=3000, clicks=300, spend=300.0)]
    campaign_names = {"111": "Кампания А"}

    results = _parse_ozon_ad_stats_csv(csv_texts, campaign_names, "2026-06-01", "2026-06-03")

    assert len(results) == 3
    assert {r["stat_date"] for r in results} == {"2026-06-01", "2026-06-02", "2026-06-03"}

    # Баг был: каждая строка хранила ПОЛНЫЙ агрегат (300) на каждый день синка,
    # из-за чего суммарный расход рос кратно числу ежедневных синков.
    # Корректно: расход разбит на равные доли, сумма по всем дням равна исходному агрегату.
    total_spend = sum(r["spend"] for r in results)
    assert abs(total_spend - 300.0) < 0.01
    for r in results:
        assert abs(r["spend"] - 100.0) < 0.01


def test_parse_ad_stats_skips_campaign_with_no_activity_rows():
    empty_csv = ";Рекламная кампания № 999, период с 2026-06-01 по 2026-06-01\nsku;Показы;Клики\nВсего;0;0\n"

    results = _parse_ozon_ad_stats_csv([empty_csv], {"999": "Пустая"}, "2026-06-01", "2026-06-01")

    assert results == []
