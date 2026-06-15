# План: Улучшение Макса и Питера — полный апгрейд аналитики

**Статус:** Фаза 2 завершена. Фаза 3 — следующая.  
**Дата:** 2026-06-15

---

## Контекст

После аудита агентов и изучения документации WB + Ozon API (июнь 2026):
- Все текущие API-эндпоинты у Макса **валидны и актуальны** ✅
- Найдены новые эндпоинты с высокой ценностью для аналитики
- Токены у Бориса есть и свежие ✅
- Реализуем 6 улучшений UX/аналитики (П1–П6) + новые API-источники данных

---

## Блок A: Улучшения UX и аналитики (П1–П6)

### A1 — Авто-синхрон финотчёта раз в неделю

**Проблема:** `/sync_fin` только ручная → Питер часто без NET-маржи.  
**Решение:** Фоновая задача, воскресенье 01:30 UTC.

- [x] Добавить `_scheduled_fin_sync_loop()` в `main.py`
- [x] Зарегистрировать рядом с `_scheduled_adv_sync_loop` (воскресенье 01:30 UTC)

```python
# Паттерн: аналогичен _scheduled_adv_sync_loop (03:00 UTC)
# Запускать воскресенье 01:30 UTC, days=90 для всех магазинов
```

---

### A2 — Команда `/data_status`

**Проблема:** Борис не видит что именно пустое без самостоятельной диагностики.

- [x] Добавить `cmd_data_status()` в `agents/max.py`
- [x] Зарегистрировать хэндлер в `_register_extra_handlers`

**Вывод команды:**
```
📊 Состояние данных

Таблица               Записей  Последний синхрон
orders (WB)           1 234    2 дня назад
orders (Ozon)         456      2 дня назад
financial_report      789      14 дней назад ⚠️
adv_stats             234      1 день назад
product_adv_stats     567      1 день назад
funnel_stats          0        ❌ пусто
stocks                123      1 день назад
questions             45       1 день назад

Себестоимость: 8 из 12 товаров ⚠️
Без себестоимости: Товар А, Товар Б, Товар В, Товар Г
```

**SQL на каждую таблицу:** `SELECT COUNT(*), MAX(date_col) WHERE chat_id=$1`

---

### A3 — MoM тренды в `/report` у Питера

**Проблема:** `daily_revenue_snapshot` заполняется каждую ночь, но Питер её игнорирует.

- [x] Добавить MoM-запрос в `_collect_data()` в `agents/peter.py`
- [x] Добавить MoM-блок в промпт `cmd_report` (ВАЖНО-секция)

```sql
SELECT DATE_TRUNC('month', snapshot_date) AS month,
       SUM(revenue) AS revenue, SUM(orders_count) AS orders
FROM daily_revenue_snapshot
WHERE chat_id=$1 AND snapshot_date >= NOW() - INTERVAL '60 days'
GROUP BY 1 ORDER BY 1
```

---

### A4 — Алерт ДРР > 25% после `/sync_adv`

- [x] Добавить `_check_drr_alerts(chat_id)` в `agents/max.py`
- [x] Вызвать после `sync_ad_stats()` в `cmd_sync_adv()` и `_scheduled_adv_sync_loop`

**SQL:** spend / revenue * 100 за 7 дней по товарам где spend > 500₽. Показывать только превышающих 25%.

---

### A5 — Алерт "кончаются остатки" (stock_days < 7)

- [x] Добавить `_check_stock_alerts(chat_id)` в `agents/max.py`
- [x] Вызвать в `cmd_sync` (после send_daily_summary) и в `_daily_snapshot_loop` (01:00 UTC)

**SQL:** `stock / (orders_14d / 14)` — stock_days, фильтр `< 7 OR stock = 0`

---

### A6 — ABC-анализ `/abc [период=30]` у Питера

- [x] Добавить `cmd_abc()` в `agents/peter.py`
- [x] Добавить `PETER_ABC_PROMPT`
- [x] Зарегистрировать хэндлер в `_register_extra_handlers` (peter.py)

**SQL:** накопительная сумма выручки → A (0-80%), B (80-95%), C (95-100%)  
**Вывод:** товары по группам с рекомендациями куда направить бюджет

---

## Блок B: Новые источники данных (из API-исследования)

### B1 — Вопросы покупателей (Ozon) — ПРИОРИТЕТ 🔥

**Почему важно:** Ozon Q&A (`/v1/question/list`) **НЕ требует Premium Plus** — в отличие от отзывов! Уже сейчас можно мониторить и отвечать на вопросы покупателей. Вопросы влияют на SEO карточки.

- [ ] Добавить `get_questions()` в `OzonClient` (`tools/marketplace.py`)
- [ ] Добавить `answer_question()` в `OzonClient`
- [ ] Создать таблицу `marketplace_questions`
- [ ] Добавить фоновый мониторинг в Макса (аналог review loop)

```python
# OzonClient
POST /v1/question/list  # {page: 1, page_size: 100, status: "not_answered"}
POST /v1/question/answer/create  # {question_id, text}
```

```sql
CREATE TABLE marketplace_questions (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT,
    marketplace TEXT DEFAULT 'ozon',
    question_id TEXT UNIQUE,
    product_id TEXT,
    product_name TEXT,
    question_text TEXT,
    status TEXT DEFAULT 'new',  -- new / answered / skipped
    generated_answer TEXT,
    final_answer TEXT,
    created_at TIMESTAMP,
    answered_at TIMESTAMP
);
```

---

### B2 — Вопросы покупателей (WB)

**Аналог для WB:** `/api/v1/questions/list` на questions-api.wildberries.ru

- [ ] Добавить `get_questions()` в `WBClient`
- [ ] Добавить `answer_question()` в `WBClient`
- [ ] Использовать ту же таблицу `marketplace_questions` (marketplace='wb')

---

### B3 — Позиция в поиске Ozon

**Почему важно:** `/v1/analytics/data` уже используем — достаточно добавить метрику `avg_search_position`. Никакого нового эндпоинта.

- [x] В `OzonClient.get_funnel_stats()` добавить `"avg_search_position"` в metrics (metrics[3])
- [x] Колонка `avg_position` уже есть в `product_funnel_stats` — данные пишутся через существующий `upsert_funnel_stat`
- [x] Питер использует в `/funnel` для объяснения причин низких просмотров (avg_position уже в запросе)

---

### B4 — Ключевые слова и позиции WB

**Эндпоинт:** `GET /api/v1/analytics/search-keywords` на seller-analytics-api.wildberries.ru  
Возвращает: keyword, search_count, position, CTR, conversion_rate

- [ ] Добавить `get_search_keywords()` в `WBClient`
- [ ] Создать таблицу `product_search_keywords`
- [ ] Команда `/sync_keywords` у Макса
- [ ] Данные передавать Питеру и Элине (копирайтер)

```sql
CREATE TABLE product_search_keywords (
    chat_id BIGINT,
    marketplace TEXT,
    product_id TEXT,
    keyword TEXT,
    position INT,
    search_count BIGINT,
    ctr NUMERIC,
    conv_rate NUMERIC,
    stat_date DATE,
    UNIQUE(chat_id, marketplace, product_id, keyword, stat_date)
);
```

---

### B5 — Аналитика возвратов

**WB:** `GET /api/v1/analytics/returns-report` — причины возвратов по товарам  
**Ozon:** `POST /v1/analytics/data` с metrics `["returns", "return_amount", "return_rate"]`

- [ ] Добавить `get_returns_analytics()` в `WBClient` и `OzonClient`
- [ ] Создать таблицу `product_returns_analytics`
- [ ] В Питере добавить блок "Топ-причины возвратов" в `/report`

---

## Блок C: Навигация — решение проблемы "не помню команды"

### Концепция: Главное меню `/menu` у Макса и Питера

Одна команда открывает весь арсенал с кнопками — не надо помнить команды.

### C1 — `/menu` у Макса (главное меню с inline-кнопками)

**Структура меню (2 уровня):**

```
📱 Главное меню

[📊 Аналитика]   [🔄 Синхронизация]
[⭐ Отзывы]      [🛒 Товары]
[🔧 Диагностика] [📖 Справка]
```

После нажатия раскрывается подменю с описанием категории:

**📊 Аналитика →**
```
Изучаем как идут продажи, где тратим деньги зря и что с конверсией

[📈 Отчёт 14 дней]  [📋 Аудит 30 дней]
[💰 ДРР (реклама)]  [🔽 Воронка продаж]
[🔤 ABC-анализ]     [◀ Назад]
```

**🔄 Синхронизация →**
```
Подтягиваем свежие данные с WB и Ozon в нашу базу

[📦 Данные]      [📣 Реклама]
[💳 Финотчёт]    [🎯 Воронка]
[📦 SKU Ozon]    [◀ Назад]
```

**⭐ Отзывы/Вопросы →**
```
Отвечаем на отзывы и вопросы покупателей на WB и Ozon

[📥 Ждут отправки]  [📊 Статистика]
[❓ Вопросы WB]     [❓ Вопросы Ozon]
[◀ Назад]
```

**🛒 Товары →**
```
Каталог товаров, себестоимость, синхронизация SKU

[📦 Список]      [➕ Добавить]
[💰 Себестоим.]  [🏪 Магазины]
[⭐ KPI]         [◀ Назад]
```

**🔧 Диагностика →**
```
Проверяем состояние данных и подключений

[🩺 data_status] [🔑 Магазины]
[◀ Назад]
```

- [x] Добавить `cmd_menu()` в `agents/max.py` с двухуровневым inline-keyboard
- [x] Callback-хэндлеры для каждого подменю (pattern `menu_*`)
- [x] Добавить кнопку `📋 Меню` в `/start` (в `_build_keyboard`, callback `menu_back`)

### C2 — `/menu` у Питера

```
📊 Питер — Аналитика

[📈 Отчёт 14 дней]  [📋 Аудит 30 дней]
[💰 ДРР анализ]     [🔽 Воронка]
[🔡 ABC товары]     [❓ Свой вопрос]
```

При нажатии кнопки — сразу запускает команду. "❓ Свой вопрос" просит ввести текст.

- [x] Добавить `cmd_menu()` в `agents/peter.py`
- [x] Каждая кнопка показывает описание и clickable-команду

### C3 — Контекстные подсказки после синхрона

После `/sync_adv`:
```
✅ Реклама обновлена
[💰 Смотреть ДРР]
```

После `/sync`:
```
✅ Данные обновлены — запроси /report у Питера
[📈 Открыть отчёт]
```

- [x] В конце `cmd_sync` и `cmd_sync_adv` добавить inline-кнопку следующего шага (callback `menu_c3:*`)

### C4 — Inline-кнопки "Что дальше?" у Питера

После `/report`:
```
[💰 ДРР]  [🔽 Воронка]  [🔡 ABC]  [📋 Аудит]
```

После `/drr`:
```
[🔽 Воронка конверсии]  [📈 Полный отчёт]
```

- [x] Добавить `after_markup` в `_send_answer()` у Питера
- [x] Callback-хэндлер `pnext:*` в `agents/peter.py`

### C5 — `/help` — полное справочное руководство

```
📖 Руководство по AI Office

🔄 ДАННЫЕ
/sync — заказы, остатки и продажи с WB/Ozon
/sync_adv — статистика рекламных кампаний
/sync_fin — финотчёт: комиссии, штрафы, выплаты
/sync_funnel — просмотры и конверсия карточки
/data_status — что заполнено в базе, что пустое

📊 АНАЛИТИКА (Питер)
/report — отчёт за 14 дней: выручка, маржа, реклама
/report цель=500000 — сравнение с целью
/audit — оценка X/10, SWOT, топ-5 действий
/drr — ДРР и ROAS (норма WB 15-20%, Ozon 10-15%)
/funnel — просмотры → корзина → заказ → выкуп
/abc — ABC-анализ: какие товары дают 80% выручки

⭐ ОТЗЫВЫ И ВОПРОСЫ
/pending — отзывы ожидающие одобрения
/reviews — статистика сегодня
(вопросы покупателей — работают как отзывы)

🛒 ТОВАРЫ
/products — каталог с себестоимостью
/cost — задать себестоимость (нужно для NET-маржи!)
/add или /map — добавить/обновить товар
/shop_kpi — рейтинг продавца
```

- [x] Добавить `cmd_help()` в `agents/max.py`
- [x] Зарегистрировать `/help` в `_register_extra_handlers`
- [x] Кнопка `ℹ️ Справка` в главном меню (callback menu_help)

---

## Порядок реализации

**Фаза 1 — Навигация и быстрые wins (1-2 дня):**
1. C1 — `/menu` у Макса + C5 `/help`
2. C2 — `/menu` у Питера
3. A2 — `/data_status`
4. A1 — авто-финотчёт
5. A5 — алерт остатки + C3 (контекстные кнопки)
6. B3 — позиция Ozon (добавить одну метрику в get_funnel_stats)

**Фаза 2 — Аналитика (2-3 дня):**
7. A3 — MoM тренды в `/report`
8. A4 — алерт ДРР
9. A6 — `/abc`
10. C4 — inline-кнопки "Что дальше?" у Питера

**Фаза 3 — Новые источники данных (3-4 дня):**
11. B1 — Ozon вопросы (без Premium Plus!)
12. B2 — WB вопросы
13. B4 — ключевые слова WB
14. B5 — возвраты (WB + Ozon)

---

## Затронутые файлы

| Файл | Что меняется |
|------|-------------|
| `agents/max.py` | +`cmd_menu`, `cmd_help`, `cmd_data_status`, `_scheduled_fin_sync_loop`, `_check_stock_alerts`, `_check_drr_alerts`, question loop, `menu_*` callbacks |
| `agents/peter.py` | +`cmd_menu`, `cmd_abc`, MoM в `_collect_data`, `PETER_ABC_PROMPT`, "Что дальше?" кнопки |
| `tools/marketplace.py` | +`get_questions`, `answer_question`, `get_returns_analytics`, `get_search_keywords` |
| `main.py` | регистрация хэндлеров + новые loops |
| БД | +`marketplace_questions`, `product_search_keywords`, `product_returns_analytics` |

---

## Верификация

- [ ] `/menu` у Макса: все 5 подменю открываются, кнопки запускают нужные команды
- [ ] `/menu` у Питера: все кнопки работают, "Свой вопрос" просит ввод
- [ ] `/help` выглядит читаемо в Telegram
- [ ] `/data_status` показывает реальные данные для chat_id
- [ ] Авто-финотчёт: видно в логах Railway (воскресенье 01:30 UTC)
- [ ] Алерт остатки: тест с stock=0 → уведомление приходит
- [ ] Алерт ДРР: тест с высоким spend → уведомление приходит
- [ ] MoM тренды: `daily_revenue_snapshot` имеет данные за 2+ месяца
- [ ] `/abc`: товары корректно по группам, сумма долей = 100%
- [ ] Ozon вопросы: `/v1/question/list` отвечает (не 403)
- [ ] Позиция Ozon: `avg_search_position` в воронке
