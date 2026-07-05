"""
Тест на clamp_wb_cpm/clamp_ozon_bid — потолок безопасности для авто-корректировки
ставок (Фаза 3 плана 2026-07-05), независимый от расчёта delta_pct в _collect_bid_suggestions.
"""
import os

for _k in ("MARTA_BOT_TOKEN", "KASPER_BOT_TOKEN", "PETER_BOT_TOKEN", "ELINA_BOT_TOKEN",
           "ALEX_BOT_TOKEN", "MAX_BOT_TOKEN", "TINA_BOT_TOKEN", "ANTHROPIC_API_KEY",
           "GITHUB_TOKEN", "GITHUB_USERNAME", "DATABASE_URL"):
    os.environ.setdefault(_k, "x")

from config import config  # noqa: E402
from agents.max import clamp_wb_cpm, clamp_ozon_bid  # noqa: E402


def test_clamp_wb_cpm_down_applies_percent():
    assert clamp_wb_cpm(1000, "down", 20) == 800


def test_clamp_wb_cpm_up_applies_percent():
    assert clamp_wb_cpm(1000, "up", 15) == 1150


def test_clamp_wb_cpm_never_below_floor():
    assert clamp_wb_cpm(60, "down", 90) == 50


def test_clamp_wb_cpm_never_exceeds_ceiling():
    ceiling = config.WB_MAX_CPM_RUB
    assert clamp_wb_cpm(ceiling, "up", 50) == ceiling
    assert clamp_wb_cpm(ceiling * 10, "up", 5) == ceiling


def test_clamp_ozon_bid_down_applies_percent():
    assert clamp_ozon_bid(100.0, "down", 20) == 80.0


def test_clamp_ozon_bid_up_applies_percent():
    assert clamp_ozon_bid(40.0, "up", 15) == 46.0


def test_clamp_ozon_bid_never_below_floor():
    assert clamp_ozon_bid(1.5, "down", 90) == 1.0


def test_clamp_ozon_bid_never_exceeds_ceiling():
    ceiling = config.OZON_MAX_BID_RUB
    assert clamp_ozon_bid(ceiling, "up", 50) == ceiling
    assert clamp_ozon_bid(ceiling * 10, "up", 5) == ceiling
