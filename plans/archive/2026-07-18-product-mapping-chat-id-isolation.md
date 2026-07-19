# product_mapping — изоляция по chat_id и восстановимость схемы

**Дата:** 2026-07-18
**Статус:** завершён (2026-07-19)

## Контекст

Вынесено отдельным планом из `plans/2026-07-18-peter-analytics-accuracy.md` (Фаза 5) — это
правка центральной таблицы, от которой зависят каталог, себестоимость, цены, ставки и вся
net-margin аналитика, поэтому требует отдельного явного согласия перед стартом реализации
(правки схемы не начинать без отдельного «да» на каждую фазу ниже, отмеченную ⚠️).

Два связанных факта, найденных при подготовке плана:

1. **`product_mapping` не фильтруется по `chat_id`** — уникальность только по `display_name`,
   глобальная на все чаты, подключённые к боту (`db.py:2636, 2668` — уже
   задокументировано в коде как известное ограничение; `.claude/skills/db-schema/SKILL.md:160-167`
   явно называет это «станет реальным багом, если появится больше одного независимого
   магазина от разных людей»). Колонка `chat_id` в таблице, по SKILL.md, физически уже
   существует, но используется только в `save_price_recommendations`/`get_price_recommendations`
   (`db.py:2224, 2253`) — не для фильтрации основных запросов и не для уникальности.
2. **Базовая схема таблицы не воспроизводима из кода.** `db.py` содержит только
   `ALTER TABLE product_mapping ADD COLUMN IF NOT EXISTS ...` (строки 482-508 и другие) —
   ни одного `CREATE TABLE product_mapping`. Базовые колонки (`id`, `display_name` UNIQUE,
   `wb_article`, `ozon_offer_id`, `ozon_sku`, `chat_id`) нигде в репозитории не создаются —
   таблица была заведена вручную непосредственно в Railway Postgres. Если `init_db()`
   когда-нибудь понадобится прогнать на чистой БД (миграция на новый Postgres, восстановление
   из бэкапа без этой ручной правки) — процесс упадёт на первом же `ALTER TABLE product_mapping`,
   потому что таблицы не существует.

Пункт 2 — предпосылка для пункта 1 (нельзя надёжно менять уникальность колонки, которой нет в
воспроизводимой схеме), поэтому в плане идёт первой фазой.

## Фазы

### Фаза 1 — сверить реальную схему с кодом [x]
Выполнено 2026-07-19 через `railway run --service Postgres python` (`psql` локально недоступен,
`asyncpg` дал те же ответы). **Находка разошлась с SKILL.md**: колонка `chat_id` в
`product_mapping` физически **не существовала** — SKILL.md ошибался. Живой прод-баг как
следствие: `save_price_recommendations`/`get_price_recommendations`/`clear_price_recommendations`
(`db.py`) уже обращались к несуществующей колонке в `WHERE chat_id = $1` и падали с
`asyncpg.exceptions.UndefinedColumnError` при каждом вызове — `save_price_recommendations`
глотала ошибку в `agents/peter.py` (warning в лог, тихо), а `/apply_prices`
(`agents/max.py::cmd_apply_prices` → `get_price_recommendations`) падала пользователю
необработанным исключением. Реальная схема на момент проверки: 15 колонок
(`id, wb_article, ozon_offer_id, display_name, ozon_sku, wb_price, ozon_price,
prices_updated_at, wb_nm_id, category, infographic_updated_at, recommended_price_wb,
recommended_price_ozon, wb_barcodes, ozon_barcodes`), `UNIQUE(display_name)` и
`UNIQUE(wb_article, ozon_offer_id)`. `product_costs` — без своей `chat_id`, изоляция только
через `JOIN product_mapping`. Бот фактически single-tenant: единственный `chat_id=397443854`
во всех таблицах, где эта колонка есть (`marketplace_shops`, `tasks`), `product_mapping` —
18 строк, дубликатов `display_name` нет — backfill безопасен, коллизий не было.

### Фаза 2 — зафиксировать базовую схему в коде (восстановимость) [x]
Добавлен `CREATE TABLE IF NOT EXISTS product_mapping (...)` в `db.py::_create_schema()` перед
`CREATE TABLE product_costs`/`product_cost_history` (у обеих FK на `product_mapping.id` — раньше
`init_db()` падал бы на пустой БД именно на них). Данные не трогались — только структура.

### Фаза 3 — найти всех потребителей перед изменением фильтрации (только чтение/анализ) [x]
По правилу `check-all-consumers-before-schema-change`
(`ai-clone/feedback/check-all-consumers-before-schema-change.md`) — прежде чем менять
поведение запросов, полный список мест, читающих/пишущих `product_mapping`/`product_costs`.
Черновик выше (составленный до реализации фазы) оказался неполным и с устаревшими номерами
строк — актуальный список найден Explore-агентом 2026-07-19:

**Ключевые находки:**
- `product_costs` физически **не имеет колонки `chat_id`** (`db.py:248-256`) — изоляция для
  неё возможна только через `JOIN product_mapping ON mapping_id`, зависит от Фазы 4.
- Реально фильтруют `product_mapping` по `chat_id` в WHERE самой таблицы только 5 мест:
  `save_price_recommendations`/`get_price_recommendations`/`clear_price_recommendations`
  (`db.py`), `_upload_infographic`, `_get_unadvertised_products`, `_analyze_promotion_margin`
  (`agents/max.py`). **Но** почти ни один INSERT в `product_mapping` (`cmd_map`, `_save_product`,
  `_handle_create_product`, `_auto_populate_side`) не пишет `chat_id` при создании строки —
  то есть эти 5 «уже фильтрующих» мест, вероятно, либо no-op (chat_id NULL у всех строк),
  либо работают только для узкой подвыборки, где chat_id когда-то проставили вручную.
  **Проверить в Фазе 1**: `SELECT DISTINCT chat_id, COUNT(*) FROM product_mapping GROUP BY chat_id`.
- Реального SQL в `agents/tina.py`, `agents/kevin.py`, `agents/kasper.py`, `tools/*.py` к этим
  таблицам нет (только текстовые упоминания в `tools/marketplace.py`).
- Найден потенциальный IDOR: `main.py::_handle_get_cost_history` (`GET /api/cost_history`)
  берёт `mapping_id` из query-параметра без проверки, что он принадлежит `chat_id` из
  Telegram initData — до включения multi-tenant не эксплуатируется (один владелец), но
  нужно закрыть в Фазе 4 вместе с остальным.

**Полный список файл:строка — функция — фильтр по chat_id:**

`db.py`:
| Строка | Функция | chat_id |
|---|---|---|
| 1412-1429 | `get_low_stocks` (JOIN) | частично (базовая таблица — да, JOIN к product_mapping — нет) |
| 2106-2135 | `find_product_id_in_text` | нет |
| 2314-2340 | `save_price_recommendations` | да |
| 2343-2365 | `get_price_recommendations` | да |
| 2368-2376 | `clear_price_recommendations` | да |
| 2379-2475 | `_auto_populate_side` | нет |
| 2478-2523 | `collect_and_save_barcodes` | нет (функция не принимает chat_id) |
| 2526-2599 | `merge_product_rows` (+ product_costs) | нет (функция не принимает chat_id) |
| 2602-2626 | `find_barcode_merge_candidates` (SELF JOIN) | нет |
| 2680-2694 | `set_product_cost` | нет (только mapping_id) |
| 2697-2729 | `set_product_cost_breakdown` (+ product_cost_history) | нет (только mapping_id) |
| 2732-2749 | `get_cost_history` | нет (только mapping_id+marketplace) |
| 2752-2783 | `get_product_costs_for_dashboard` | нет — параметр chat_id явно не используется (комментарий в коде) |
| 2786-2816 | `get_products_without_cost` | нет — аналогично |
| 2819-2825 | `count_products` | нет (глобальный COUNT) |

`agents/peter.py` (везде «частично» = базовая таблица фильтрована `chat_id=$1`, JOIN/LATERAL к product_mapping/product_costs — нет):
285-292, 294-316, 318-343, 345-400, 420-430 (`_collect_data`), 589-610 `infographic_ctr`
(**нет вообще** — product_mapping в самом FROM без ограничения), 700-832 `_collect_advanced_data`
(реклама/ROAS/stock_velocity/funnel/abc), 1445-1488 `cmd_funnel`, 1629-1660
`_get_ozon_warehouse_demand` (**нет** — chat_id в сигнатуре есть, в запросе не используется),
1686-1736 `_collect_supply_data`, 1990-2040 `_collect_order_advice_data`, 2203-2252
`_collect_seo_audit_data`, 2749-2781 `cmd_returns`.

`agents/max.py`:
507-531 `_upload_infographic` (да), 1694-1762 `sync_marketplace_data`/`sync_prices` (нет),
1876-1908 `sync_ad_stats` (нет), 2400-2417 `_send_promotions_summary` (нет), 2480-2517
`sync_cards` (нет), 2556-2588 `_check_seo_drops` (частично), 3811-3830
`_get_catalog_products`/`_collect_catalog_for_dashboard` (нет — функция не принимает chat_id),
3922-3997 `_compute_margin_rows` (частично — cost_rows без chat_id), 4185-4224 `cmd_map`
(**нет** — chat_id не пишется при INSERT), 4226-4577 `cmd_camp`/`cmd_cost`/каталог-пикер/
`cmd_merge_products`/merge-wizard (нет), 4689-4724 `_handle_catalog_cost_text` (нет, нет колонки
у product_costs), 4814-4841 `_save_product` (`/add`, нет), 5198-5255 `_check_stock_alerts`
(частично), 5377-5466 `_collect_reprice_suggestions` (**нет** — базовая выборка товаров
глобальная), 5695-5794 `_apply_price` (**нет**, несмотря на chat_id в сигнатуре), 5796-5984
`_check_drr_alerts`/`_collect_bid_suggestions` (частично), 6410-6439
`_get_unadvertised_products` (да), 6621-6644 `_analyze_promotion_margin` (да).

`agents/elina.py`: 94-96 `_auto_sync_cards` (нет), 236-238/322-324 (делегирует в
`find_product_id_in_text`, нет).

`main.py`: 996-1036 `_handle_get_costs` (нет), 1038-1096 `_handle_set_cost` (**нет** — chat_id
известен из initData, но не используется для проверки владения), 1098-1146
`_handle_get_cost_history` (**нет, IDOR** — см. выше), 1203-1260 `_handle_create_product`
(нет), 1262-1319 `_handle_merge_product` (нет).

### Фаза 4 — включить изоляцию по chat_id [x]
- Backfill был no-op-безопасен (см. Фазу 1 — один `chat_id` на все 18 строк), поэтому миграция
  сразу проведена целиком, без отдельной паузы на решение по коллизиям.
- `db.py`: `ALTER TABLE product_mapping ADD COLUMN IF NOT EXISTS chat_id BIGINT`, backfill
  `WHERE chat_id IS NULL` от `config.OWNER_CHAT_ID` (если задан), `DROP CONSTRAINT IF EXISTS
  product_mapping_name_uniq` + `CREATE UNIQUE INDEX product_mapping_chat_display_uniq ON
  product_mapping (chat_id, display_name)`. `chat_id`-фильтрация добавлена во все функции
  db.py из таблицы Фазы 3 (`find_product_id_in_text`, `_auto_populate_side`,
  `collect_and_save_barcodes`, `find_barcode_merge_candidates`, `set_product_cost`,
  `set_product_cost_breakdown`, `get_cost_history` — с проверкой владения `mapping_id` через
  `product_mapping`, закрывает IDOR из `main.py::_handle_get_cost_history`,
  `get_product_costs_for_dashboard`, `get_products_without_cost`, `count_products`).
  `get_low_stocks` — JOIN дополнен `AND m.chat_id = $1`.
- `agents/peter.py`, `agents/max.py`, `agents/elina.py`, `main.py` — `chat_id` добавлен во все
  оставшиеся запросы/JOIN из таблицы Фазы 3, плюс прокинуты новые обязательные параметры в
  вызовы изменённых функций `db.py`. `cmd_map`/`_save_product`/`_handle_create_product` теперь
  пишут `chat_id` при INSERT. IDOR в `_handle_get_cost_history` закрыт (передаётся `chat_id`
  из проверенного Telegram initData). Побочный найденный баг: после смены уникальности три
  места (`main.py::_handle_create_product`, `agents/max.py::cmd_map`, `agents/max.py::_save_product`)
  использовали `ON CONFLICT (display_name)` — с новым индексом это отвалилось бы с
  `no unique or exclusion constraint matching`; исправлено на
  `ON CONFLICT (chat_id, display_name)`.
- Реализовано параллельными субагентами по файлам (db.py → main.py/max.py/peter.py/elina.py),
  каждый diff проверен оркестратором перед коммитом.

### Фаза 5 — проверка [x]
Docker/локальный Postgres в этом окружении недоступны — `init_db()` прогнан на той же прод-БД,
но в изолированной пустой Postgres-схеме (`search_path`, схема удалена после проверки) — та же
версия движка, без риска для данных. Результат:
- `init_db()` отрабатывает на пустой схеме без ошибок, `product_mapping` создаётся с нуля со
  всеми 16 колонками (включая `chat_id`) и обоими индексами
  (`product_mapping_wb_article_ozon_offer_id_key`, `product_mapping_chat_display_uniq`);
  `product_mapping_name_uniq` корректно отсутствует (дропнут тем же прогоном).
- `set_product_cost_breakdown` → `get_cost_history` для того же `chat_id` возвращает
  сохранённую запись (`cost=120`); для чужого `chat_id` — пустой список (IDOR закрыт);
  попытка записать себестоимость в чужой `mapping_id` молча отклонена (запись в
  `product_costs` не создаётся).
- `UNIQUE(chat_id, display_name)`: одинаковый `display_name` у разных `chat_id` — разрешён;
  повтор внутри одного `chat_id` — корректно отклонён `UniqueViolationError`.
- Отдельный HTTP/Telegram-прогон `/map`, `/cost`, `/add`, дашборда не делался (нет тестового
  Telegram-окружения в этой сессии, как и в прошлой проверке аналогичного рода —
  `plans/2026-07-15-cost-price-dashboard-editor.md`, Фаза 4) — вместо этого проверены
  напрямую функции, которые эти команды используют (см. выше).

## Известные ограничения, оставленные вне рамок (заведён GitHub issue)
- `agents/max.py::cmd_sync_sku` — читает/пишет `product_mapping` (`ozon_offer_id`, `ozon_sku`)
  без фильтрации по `chat_id`. Не входил в таблицу Фазы 3, обнаружен субагентом по ходу работы.
- `agents/max.py::_save_product` — `product_costs`-INSERT использует `ON CONFLICT (mapping_id)`
  без колонки `marketplace`, при этом реальный уникальный индекс —
  `(mapping_id, marketplace)` (см. `set_product_cost`/остальные вызовы в этом же файле).
  Похоже на давний баг, не связанный с этим планом — не трогали, чтобы не выходить за рамки
  задания.

## Файлы

| Файл | Изменения |
|---|---|
| `db.py` | Фаза 2 — `CREATE TABLE product_mapping`; Фаза 4 — `chat_id` колонка/backfill/UNIQUE, фильтрация во всех функциях из Фазы 3 |
| `agents/peter.py`, `agents/max.py`, `agents/elina.py`, `main.py` | Фаза 4 — `chat_id` в запросах из Фазы 3, новые параметры вызовов, фикс `ON CONFLICT`, фикс IDOR |
