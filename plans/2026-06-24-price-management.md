# Управление ценами — применение рекомендаций Питера

**Дата:** 2026-06-24  
**Статус:** завершён

## Цель

Замкнуть петлю: Питер считает рекомендованную цену → она сохраняется в БД →
Макс предлагает применить одной командой → пользователь подтверждает → цены обновляются на WB и Ozon.

## Фазы

### Фаза 1 — db.py: новые колонки + хелперы [x]
- `ALTER TABLE product_mapping ADD COLUMN IF NOT EXISTS recommended_price_wb NUMERIC(10,2)`
- `ALTER TABLE product_mapping ADD COLUMN IF NOT EXISTS recommended_price_ozon NUMERIC(10,2)`
- Функция `save_price_recommendations(pool, chat_id, items)` — upsert по `(chat_id, wb_article)`
- Функция `get_price_recommendations(pool, chat_id)` — SELECT где хотя бы одна рекомендация не null

### Фаза 2 — agents/peter.py: сохранять рекомендации [x]
- В `cmd_report()`, после вычисления `net_margin`, вызвать `save_price_recommendations`
  для строк где `recommended_price_wb IS NOT NULL OR recommended_price_ozon IS NOT NULL`
- Сопоставление: peter использует `product_name`; нужно получить `wb_article` из `product_mapping`

### Фаза 3 — agents/max.py: команда /apply_prices [x]
- `cmd_apply_prices(update, context)`:
  1. `get_price_recommendations(chat_id)` → список
  2. Если пусто → "Нет рекомендаций. Запусти `/report` у Питера."
  3. Показать таблицу: название | текущая цена | рекомендованная | разница
  4. InlineKeyboard:
     - [✅ Применить WB] — только wb-рекомендации
     - [✅ Применить Ozon] — только ozon-рекомендации
     - [✅ Применить всё]
     - [❌ Отмена]
  5. Callback-обработчик `_handle_price_apply_callback`:
     - Вызывает WBClient/OzonClient.update_prices()
     - Обновляет `product_mapping.wb_price / ozon_price` из текущих данных
     - Сбрасывает `recommended_price_wb/ozon = NULL` после успеха
     - Отправляет итог

## Файлы

| Файл | Изменения |
|---|---|
| `db.py` | +2 колонки, +2 функции |
| `agents/peter.py` | сохранять рек. цены после расчёта |
| `agents/max.py` | +`cmd_apply_prices`, +callback-обработчик |
