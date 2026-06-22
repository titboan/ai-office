# WbNinjaBot-анализ: SEO-алерты + Региональная аналитика

**Дата:** 2026-06-22  
**Статус:** в работе

---

## Контекст

Анализ WbNinjaBot выявил две функции, которых нет в ai-office:
1. **SEO position alerts** — уведомление при падении позиций товара в поиске WB
2. **Региональная аналитика** — откуда приходят заказы, вклад регионов в выручку

Пользователь работает только по схеме FBO (слоты поставок не нужны, мгновенные уведомления о заказах — не приоритет).

**Ограничение:** WB закрыл эндпоинт `GET /api/v1/analytics/search-query-report` (осень 2025), поэтому keyword sync (`_sync_keywords` в `agents/max.py`) сейчас отключён. SEO-алерты строим поверх **исторических данных** в `product_search_keywords` и готовим логику к возобновлению API.

---

## Фаза 1: Региональная аналитика заказов

- [ ] **1.1** `tools/marketplace.py` — добавить `"region": item.get("oblastMarkingCode") or item.get("regionName", "")` в маппинг WB orders (≈строка 262)
- [ ] **1.2** `db.py` — `ALTER TABLE marketplace_orders ADD COLUMN IF NOT EXISTS region TEXT DEFAULT ''` в `_create_schema()`, обновить `save_order()` с параметром `region`
- [ ] **1.3** `agents/max.py` — передавать `region=o.get("region", "")` в `save_order()` при WB синке
- [ ] **1.4** `agents/peter.py` — добавить запрос в `_collect_data()`:
  ```sql
  SELECT region, COUNT(*) AS orders_cnt, SUM(seller_price * quantity) AS revenue
  FROM marketplace_orders
  WHERE chat_id = $1 AND marketplace = 'wb'
    AND order_date >= $2 AND region != ''
  GROUP BY region ORDER BY revenue DESC LIMIT 10
  ```
  Включить в возврат `_collect_data` как `"regions_wb"`. В промпт `/report` добавить блок "География заказов (топ-5 регионов WB)".

---

## Фаза 2: SEO Position Drop Alerts

- [ ] **2.1** `config.py` — добавить `config.SEO_POSITION_DROP_THRESHOLD = 10`
- [ ] **2.2** `agents/max.py` — новая функция `_check_seo_drops(chat_id, db)`:
  - Берёт два последних уникальных `stat_date` из `product_search_keywords`
  - Сравнивает `position` между датами для каждого ключевика
  - Возвращает список дропов ≥ порога
- [ ] **2.3** `agents/max.py` — команда `/seo_check`:
  - Если нет данных → "API временно недоступен (WB закрыл эндпоинт)"
  - Если дропы есть → `📉 «лакомства для собак»: 8 → 23 (-15)`
  - Если стабильно → "Позиции стабильны относительно [дата]"
- [ ] **2.4** `agents/max.py` — зарегистрировать `CommandHandler("seo_check", self.cmd_seo_check)`
- [ ] **2.5** В конце `_sync_keywords()` (после реактивации API) — вызывать `_check_seo_drops()` и отправлять алерт при дропах

---

## Файлы

| Файл | Изменения |
|---|---|
| `tools/marketplace.py` | +`region` в WB orders |
| `db.py` | +колонка `region`, обновление `save_order` |
| `agents/max.py` | +`region` в sync, +`_check_seo_drops`, +`/seo_check` |
| `agents/peter.py` | +региональный запрос в `_collect_data`, +блок в промпте |
| `config.py` | +`SEO_POSITION_DROP_THRESHOLD = 10` |

---

## Проверка

1. `/sync` → у WB-заказов в БД появляется `region`
2. `/report` у Питера → блок "География заказов"
3. `/seo_check` → либо список дропов, либо "API недоступен" с последним снимком
