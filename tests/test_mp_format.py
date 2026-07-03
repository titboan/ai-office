from utils.mp_format import mp_label, mp_emoji, split_by_marketplace


def test_mp_label_known_marketplaces():
    assert mp_label("wb") == "🟣 WB"
    assert mp_label("ozon") == "🔵 Ozon"


def test_mp_label_unknown_falls_back_to_raw_value():
    assert mp_label("aliexpress") == "aliexpress"


def test_mp_emoji_known_and_unknown():
    assert mp_emoji("wb") == "🟣"
    assert mp_emoji("ozon") == "🔵"
    assert mp_emoji("aliexpress") == "⚪"


def test_split_by_marketplace():
    items = [
        {"marketplace": "wb", "id": 1},
        {"marketplace": "ozon", "id": 2},
        {"marketplace": "wb", "id": 3},
    ]
    wb, ozon = split_by_marketplace(items)
    assert [i["id"] for i in wb] == [1, 3]
    assert [i["id"] for i in ozon] == [2]
