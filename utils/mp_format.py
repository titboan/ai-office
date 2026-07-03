"""
utils/mp_format.py — единое форматирование площадки (WB/Ozon) для Telegram-вывода.

Раньше "🟣 WB" / "🔵 Ozon" реализовывались по-своему в десятке мест в max.py и peter.py
(где-то с текстом, где-то только эмодзи) — при следующей правке легко поправить
одно место и получить рассинхрон в остальных.
"""
from __future__ import annotations

_LABEL = {"wb": "🟣 WB", "ozon": "🔵 Ozon"}
_EMOJI = {"wb": "🟣", "ozon": "🔵"}


def mp_label(marketplace: str) -> str:
    """'wb' → '🟣 WB', 'ozon' → '🔵 Ozon'."""
    return _LABEL.get(marketplace, marketplace)


def mp_emoji(marketplace: str) -> str:
    """'wb' → '🟣', 'ozon' → '🔵'."""
    return _EMOJI.get(marketplace, "⚪")


def split_by_marketplace(items: list[dict], key: str = "marketplace") -> tuple[list[dict], list[dict]]:
    """Разделить список записей на (wb_items, ozon_items) по полю marketplace."""
    wb = [i for i in items if i.get(key) == "wb"]
    ozon = [i for i in items if i.get(key) == "ozon"]
    return wb, ozon
