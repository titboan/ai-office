# Ozon API: item_id vs offer_id — асимметрия

## Правило

При работе с Ozon Seller API разные эндпоинты используют разные идентификаторы товара.
Перед добавлением нового write-метода — проверь документацию: нужен ли offer_id или item_id.

## Детали

| Эндпоинт | Идентификатор | Примечание |
|---|---|---|
| `/v1/product/description/update` | `offer_id` (str) | Напрямую, без дополнительных запросов |
| `/v1/product/name` | `item_id` (int) | Требует резолюции через `/v3/product/info/list` |
| `/v1/product/attributes/update` | `offer_id` (str) | Но нужен `attribute_id` по категории |
| `/v3/product/info/list` | `offer_id` → возвращает `id` (item_id) | Используется как справочник |

## Как резолвить offer_id → item_id

```python
async with session.post(
    f"{self._BASE}/v3/product/info/list",
    headers=self._headers(),
    json={"offer_id": [offer_id]},
) as resp:
    items = (await resp.json()).get("items") or []
    item_id = items[0].get("id") if items else None
```

Если `items` пустой — товар не найден (возможно archived). Нужно сообщить пользователю, не падать молча.

## Источник

Добавлен при реализации `OzonClient.update_product_name()` (сессия 2026-06-26).
