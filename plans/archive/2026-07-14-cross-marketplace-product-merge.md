# Один товар — разные артикулы на WB и Ozon: верные выдачи по аналитике

Статус: завершён (код всех фаз; живой тест на реальных токенах не выполнен — нет доступа из песочницы)

## Проблема

`db.auto_populate_product_mapping` (см. `plans/2026-07-14-guided-onboarding-analytics.md`)
намеренно заводит WB и Ozon версии товара ОТДЕЛЬНЫМИ строками в `product_mapping`,
если их `display_name` не совпадает текстом — потому что у реального продавца
может быть `wb_article="БК50гр"` и `ozon_offer_id="КБ50"` для одного и того же
физического товара, и сопоставлять их по названию ненадёжно (риск склеить
себестоимость разных товаров). В итоге для такого товара:
- себестоимость приходится задавать дважды, в двух разных карточках каталога
- ABC-анализ, дашборд, отчёты Питера показывают ДВЕ строки вместо одной
  реальной позиции — искажают картину (два "средних" товара вместо одного топового)

Нужен способ сопоставлять такие пары надёжно — без риска ложного слияния (это
испортит себестоимость/маржу), но и без необходимости помнить руками, что
"КБ50 (WB)" и "КБ50 (Ozon)" — один и тот же товар.

## Решение (согласовано с пользователем)

1. **Штрихкод (barcode) как физический идентификатор.** Один и тот же товар
   почти всегда имеет одинаковый штрихкод на обеих площадках (требование
   самих МП при создании карточки) — надёжнее сравнения текста названий.
   Источники **без новых API-интеграций** (уточнено при разборе кода — обе
   ссылки уже вызываются в обычном синке):
   - WB: `/api/v1/supplier/stocks` (Statistics-токен, `WBClient.get_stocks`,
     вызывается в каждом `/sync`) — отдаёт `barcode` в каждой строке остатка,
     сейчас не парсится. НЕ через Content API `get_nm_ids` — тот требует
     отдельную категорию токена "Контент", которую текущий онбординг не
     запрашивает, штрихкод там был бы недоступен большинству пользователей.
   - Ozon: `/v3/product/info/list` (`OzonClient._get_sku_to_offer_id`,
     вызывается внутри `get_stocks`) — отдаёт `barcodes` (нужно проверить
     точное имя поля на живом ответе, задокументировать в коде), сейчас не
     парсится.
2. **Не сливать втихую.** Кандидат на слияние (совпал штрихкод) — не
   авто-мёрдж, а вопрос пользователю с кнопками Да/Нет. Ошибка слияния бьёт
   по себестоимости/марже — риск выше, чем неудобство лишнего вопроса.
3. **Ручной инструмент-fallback.** Для товаров без штрихкода (не все
   продавцы его аккуратно заполняют) — отдельная команда/визард "объединить
   два товара из каталога вручную".

## Фаза 1 — Штрихкоды: схема + сбор (без новых API-вызовов)

Файлы: `db.py`, `tools/marketplace.py`, `agents/max.py`.

- [x] `ALTER TABLE product_mapping ADD COLUMN IF NOT EXISTS wb_barcodes TEXT[], ADD COLUMN IF NOT EXISTS ozon_barcodes TEXT[]`
      (массив — у WB один `wb_article` может иметь несколько штрихкодов на
      разные размеры/цвета одной карточки; матчинг потом идёт по пересечению
      множеств, не по одному значению).
- [x] `WBClient.get_stocks()` (`tools/marketplace.py:221`) — добавить в каждый
      элемент `results` поле `"barcode": item.get("barcode", "")` (сырой
      штрихкод из ответа `/api/v1/supplier/stocks`, пустая строка если нет).
- [x] `OzonClient._get_sku_to_offer_id` (`tools/marketplace.py:1747`) —
      расширить (или добавить соседнюю функцию, если исходную используют ещё
      где-то — проверить перед правкой), чтобы дополнительно вытаскивать
      штрихкод(ы) из ответа `/v3/product/info/list`. Поле в реальном ответе
      Ozon может называться `barcode` (строка) или `barcodes` (массив) —
      обработать defensively оба варианта, залогировать пример строки как
      уже делает `get_stocks` (`logger.info(f"[Ozon.get_stocks] пример
      строки (ключи): ...")`) для последующей проверки на живых данных.
      Прокинуть штрихкод(ы) в `OzonClient.get_stocks()` — поле `"barcode"`
      в каждом элементе `results` (аналогично WB, можно объединить несколько
      штрихкодов через запятую если их больше одного на offer_id).
- [x] Новая функция `db.py::collect_and_save_barcodes(marketplace: str, stock_items: list[dict]) -> None`:
      группирует `barcode` по `product_id` из уже полученного списка остатков,
      для каждого product_id делает `UPDATE product_mapping SET wb_barcodes =
      (SELECT ARRAY(SELECT DISTINCT unnest(COALESCE(wb_barcodes, '{}') ||
      $2::text[]))) WHERE wb_article = $1` (аппенд-дедуп, не перезапись —
      штрихкоды копятся, не пропадают между синками). Симметрично для Ozon
      (`ozon_offer_id`/`ozon_barcodes`). Если товара ещё нет в `product_mapping`
      (первый синк, `auto_populate_product_mapping` ещё не создал строку) —
      `UPDATE` просто не находит строк, штрихкод подхватится на следующем
      синке — не критично, не нужно городить сложную синхронизацию порядка.
- [x] Вызвать `collect_and_save_barcodes` из `agents/max.py::sync_marketplace_data`
      (max.py:1437) сразу после цикла `upsert_stock` для каждой площадки —
      данные уже есть в памяти (`stocks`), новый DB-запрос не нужен.

Готово: `db.py:504` (ALTER), `db.py:2367` (`collect_and_save_barcodes`),
`tools/marketplace.py:258` (WB barcode), `tools/marketplace.py:1748-1791`
(`_get_sku_to_offer_id` теперь возвращает `{offer_id, barcodes}` — проверено,
вызывается только из `get_stocks`, второго вызывающего нет), `agents/max.py:1518-1524`.

⚠️ Основной вариант поля штрихкода в реальном ответе Ozon НЕ подтверждён —
код пробует `barcodes` (массив) первым, `barcode` (строка) вторым, добавлен
`logger.info` с ключами первой строки ответа для проверки на живых данных
после первого реального синка Ozon-магазина.

Побочный фикс субагента (не баг этой фичи, но найден по пути): `stocks`
объявлялась только внутри `try` в `sync_marketplace_data` — при ошибке
`get_stocks()` на первой итерации цикла по магазинам это `UnboundLocalError`,
а на второй и далее итерациях — использование `stocks` от ПРЕДЫДУЩЕГО
магазина (Python не создаёт новую область видимости на итерацию цикла).
Добавлено `stocks: list[dict] = []` перед `try`.

## Фаза 2 — Общий merge-примитив + список "не сливать снова"

Файл: `db.py`.

- [x] Новая таблица `product_merge_dismissed (wb_mapping_id BIGINT, ozon_mapping_id BIGINT, dismissed_at TIMESTAMPTZ DEFAULT now(), UNIQUE(wb_mapping_id, ozon_mapping_id))` —
      запоминает пары, которые пользователь пометил "нет, это разные товары",
      чтобы не спрашивать снова на каждом синке.
- [x] `db.py::merge_product_rows(keep_id: int, remove_id: int) -> None`:
      единственная связанная FK-таблица — `product_costs.mapping_id`
      (проверено — `product_adv_stats`/`product_funnel_stats`/
      `marketplace_financial_report`/`marketplace_stocks`/`marketplace_orders`
      джойнятся по сырым `wb_article`/`wb_nm_id`/`ozon_offer_id`/`ozon_sku`,
      НЕ по `mapping_id` — их трогать не нужно). Логика в одной транзакции:
      1. `COALESCE`-слияние полей (`wb_article`, `wb_nm_id`, `wb_price`,
         `wb_barcodes` [конкатенация+дедуп массивов], `ozon_offer_id`,
         `ozon_sku`, `ozon_price`, `ozon_barcodes`, `category`,
         `recommended_price_wb`, `recommended_price_ozon`) из `remove_id` в
         `keep_id` — брать значение `keep_id`, если оно не NULL, иначе из
         `remove_id`.
      2. `UPDATE product_costs SET mapping_id = $keep_id WHERE mapping_id =
         $remove_id` (конфликт `UNIQUE(mapping_id, marketplace)` не должен
         возникать — keep_id/remove_id по построению относятся к разным
         площадкам, но обернуть в `ON CONFLICT DO NOTHING` на всякий случай).
      3. `DELETE FROM product_mapping WHERE id = $remove_id`.
- [x] `db.py::find_barcode_merge_candidates() -> list[dict]`: джойн
      `product_mapping a` (wb-товар, `ozon_offer_id IS NULL`,
      `wb_barcodes` не пусто) с `product_mapping b` (ozon-товар,
      `wb_article IS NULL`, `ozon_barcodes` не пусто) по `a.wb_barcodes &&
      b.ozon_barcodes` (пересечение массивов), исключая пары из
      `product_merge_dismissed`. Возвращает `{wb_id, wb_name, ozon_id,
      ozon_name, matched_barcode}`.

Готово: `product_merge_dismissed` (db.py:509), `merge_product_rows`
(db.py:2424), `find_barcode_merge_candidates` (db.py:2498). COALESCE-слияние
скалярных полей — одним атомарным SQL `UPDATE ... FROM product_mapping AS rem`
(self-join на вторую строку внутри той же транзакции), не Python-сборкой —
исключает гонки между чтением и записью.

⚠️ Критичный фикс оркестратора после ревью: `except asyncpg.UniqueViolationError`
вокруг `UPDATE product_costs` был БЕЗ вложенного `async with conn.transaction()`
(savepoint). В Postgres/asyncpg ошибка одного `execute` внутри транзакции
переводит её в aborted-состояние — простого `try/except` в Python
недостаточно, чтобы транзакция могла продолжиться: следующий `DELETE FROM
product_mapping` упал бы с "current transaction is aborted", и весь мерж
(включая уже выполненный COALESCE-UPDATE) откатился бы целиком, а исключение
улетело бы наружу необработанным — комментарий кода утверждал обратное. Та
же категория бага, что уже правильно чинили в `_auto_populate_side`
(guided-onboarding фича) — там вложенный savepoint был, здесь его забыли.
Обернул `UPDATE product_costs` в `async with conn.transaction():`.

## Фаза 3 — Проактивное предложение слияния (подтверждение кнопками)

Файл: `agents/max.py`.

- [x] После шага "заказы/остатки" в `_run_full_sync_with_progress`
      (там уже вызывается `_auto_populate_products`, штрихкоды к этому
      моменту сохранены Фазой 1) — вызвать `db.find_barcode_merge_candidates()`.
      Если есть кандидаты — по каждому отдельное сообщение: «По штрихкоду
      похоже, что «{wb_name}» (WB) и «{ozon_name}» (Ozon) — один товар.
      Объединить?» с кнопками Да/Нет (`InlineKeyboardButton`,
      `callback_data="merge:yes:{wb_id}:{ozon_id}"` / `"merge:no:..."`).
      Не блокировать этим синк — отправить сообщения и продолжить (сам синк
      уже фоновая задача с Фазы 4 прошлой фичи, `_run_post_onboarding_flow`).
- [x] Тот же вызов имеет смысл и в обычном `/sync` (не только при онбординге) —
      кандидаты могут появиться позже (штрихкоды дозаполняются по мере
      синков). Само по себе безопасно вызывать на каждом синке — уже
      смёрженные и уже отклонённые пары не всплывут снова.
- [x] Новый `CallbackQueryHandler(self._handle_merge_callback, pattern=r"^merge:")`
      в `_register_extra_handlers`. На "Да" → `db.merge_product_rows`,
      подтверждение с объединённым названием. На "Нет" → запись в
      `product_merge_dismissed`.

Готово: `_suggest_product_merges`/`_handle_merge_callback` (agents/max.py:2292,
2322), регистрация хендлера (max.py:7199), вызов из `_run_full_sync_with_progress`
(max.py:2228) и из `send_daily_summary`/`/sync` (max.py:3268),
`db.dismiss_merge_candidate` (db.py:2527).

⚠️ Известное ограничение (не блокирует фичу, оставлено как есть): если
пользователь получил предложение слияния и НЕ нажал ни одну кнопку, а потом
снова запустил `/sync` — та же пара будет предложена ПОВТОРНО (новым
сообщением, старое с кнопками остаётся рабочим). `find_barcode_merge_candidates`
трекает только "отклонено"/"смёржено", не "уже показано, ждём ответа". При
частых `/sync` это может нагенерить несколько одинаковых сообщений подряд.
Не критично (кнопки в старых сообщениях по-прежнему рабочие, повторный клик
безопасен — `merge_product_rows` идемпотентна), но если станет раздражать —
можно добавить Redis-флаг "уже предложено, не повторять N часов".

## Фаза 4 — Ручной инструмент «Объединить два товара» (fallback без штрихкода)

Файл: `agents/max.py`.

- [x] Команда (например `/merge_products`) или кнопка в `/products` —
      двухшаговый визард по паттерну уже существующих button-driven
      визардов (`costpick:`, `addcat:`): список WB-only товаров → выбор →
      список Ozon-only товаров → выбор → подтверждение → `db.merge_product_rows`.
      Redis-состояние `merge_wizard:{chat_id}`, TTL как у остальных визардов.
- [x] `/cancel` — умеет прерывать и этот визард (по аналогии с `cost_wizard`).

Готово: `cmd_merge_products`/`_handle_merge_wizard_callback` (agents/max.py:4522,
4540), callback-префикс `mergewiz:` (не пересекается с `merge:` из Фазы 3 —
проверено, `^merge:` не матчит `mergewiz:...`), регистрация в
`_register_extra_handlers`, `/cancel` чистит `merge_wizard:{chat_id}`. SQL —
прямые запросы в командах (по образцу `_handle_catalog_cost_callback`), не
через новую функцию в `db.py` — файл не трогался в этой фазе.

## Фаза 5 — Документация

- [x] `.claude/skills/db-schema/SKILL.md`: описать `wb_barcodes`/`ozon_barcodes`,
      `product_merge_dismissed`, `merge_product_rows`, `find_barcode_merge_candidates`.
- [x] `.claude/skills/max-api/SKILL.md`: раздел про слияние товаров —
      автоматическое предложение по штрихкоду + ручной fallback.

## Проверка в конце

- [ ] Живой тест на реальных токенах: проверить точное имя поля штрихкода в
      ответе Ozon `/v3/product/info/list` (barcode vs barcodes) — в песочнице
      недоступно, разница между заглушкой и реальным полем может тихо
      обнулить весь Ozon-мэтчинг. **НЕ выполнено в этой сессии.**
- [x] Граничный случай: у товара несколько штрихкодов и они пересекаются
      ЧАСТИЧНО с другим товаром (например, опечатка в стороннем штрихкоде) —
      не страшно, т.к. финальное решение всегда за пользователем (кнопка).
- [x] Граничный случай: `merge_product_rows` вызван для уже слитой/удалённой
      строки (двойной клик по кнопке Да) — проверка существования обеих строк
      в начале транзакции, идемпотентно выходит без падения.
- [x] Грепнуто `parse_mode=` по всем новым сообщениям (`merge`/`_suggest_product`/
      `_handle_merge`) — нигде не используется, названия товаров с площадки
      идут как plain text, риск `&`/`<` не воспроизводится.

Статус: код всех 4 фаз реализован, проверен по диффам (найдены и исправлены
2 реальных бага при ревью — savepoint для UPDATE product_costs в
merge_product_rows, и не-transaction-related UnboundLocalError/утечка stocks
между магазинами в sync_marketplace_data) и закоммичен. Не хватает живого
теста на реальных Ozon-токенах — конкретно нужно проверить имя поля
штрихкода в ответе `/v3/product/info/list` по логам первого реального синка.
