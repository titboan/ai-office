# Рекомендуемая рыночная ставка WB в системе управления рекламой

**Дата:** 2026-07-12
**Статус: завершён**

## Цель

Показывать рядом с авто-предложением по ставке WB (`/bid_adjust`, дашборд `BidSuggestions`)
официальную рекомендуемую/конкурентную ставку WB по аукциону, чтобы предупреждать о риске
перерасхода бюджета (ставка сильно выше рынка) или проигрыша аукциона (сильно ниже рынка).
Основная логика предложения остаётся ДРР-based — рыночная ставка добавлена как
предупреждающий сигнал поверх неё, не заменяет её.

**Ozon сознательно вне скоупа**: официального API для конкурентной/рекомендуемой ставки у
Ozon Performance API нет (проверено по докам, changelog'ам и двум независимым open-source
клиентам — Go и Python). Есть открытый нерешённый запрос продавцов (userecho #309) на
добавление такого поля, Ozon пока не сделал. Возвращаться к этому вопросу нет смысла, пока
Ozon не добавит поле в API.

## Фазы

### Фаза 1 — `WBClient.get_recommended_bid()` в `tools/marketplace.py` [x]
- Добавлен метод рядом с `get_campaign_cpm`/`update_campaign_cpm`.
- Первая версия парсила поля вслепую (по обрывкам из веб-поиска, doc dev.wildberries.ru
  отдаёт 403 на прямой фетч) — **исправлено**: реальная схема найдена через открытый
  community OpenAPI-спек `raw.githubusercontent.com/eslazarev/wildberries-sdk/main/specs/08-promotion.yaml`
  (raw.githubusercontent.com не блокируется, в отличие от dev.wildberries.ru).
- Подтверждённая схема: `GET /api/advert/v0/bids/recommendations` требует **оба** параметра —
  `advertId` (кампания) и `nmId` (артикул); рекомендация считается WB на пару, не только на
  кампанию. Ответ: `{"base": {"competitiveBid": {"bidKopecks": N}, "leadersBid": {...}, "top2": {...}},
  "normQueries": [...]}`. Берём `base.competitiveBid.bidKopecks`, переводим копейки в рубли
  (`/100`). nm_id получаем через уже существующий `get_campaigns_nms([campaign_id])`
  (подтверждённо рабочий endpoint, используется в `_sync_adv`).
- Метод по-прежнему логирует сырой ответ при HTTP 200 (`raw[:800]`) — схема взята из
  стороннего спека, а не из официальных доков или живого вызова на нашем ключе.
- **Всё ещё требуется после деплоя**: посмотреть Railway-логи `[WB.get_recommended_bid]` на
  первом реальном вызове `/bid_adjust`, свериться с реальным ответом на нашем токене (community
  спек мог отстать от прод-API), при необходимости поправить одной строкой.

### Фаза 2 — Интеграция в движок предложений (`agents/max.py`) [x]
- `wb_market_bid_flag(new_cpm, recommended_cpm)` — чистая функция сравнения с допуском
  `WB_BID_OVERSPEND_TOLERANCE_PCT`/`WB_BID_UNDERSPEND_TOLERANCE_PCT` (по 15%), рядом с
  `clamp_wb_cpm`/`clamp_ozon_bid`.
- Вызов `get_recommended_bid` + `wb_market_bid_flag` добавлен в WB-ветку `auto_bid_suggest`
  (Telegram) и в `_collect_bid_suggestions_for_dashboard` (дашборд), сразу после существующего
  вызова `get_campaign_cpm` — без лишнего round-trip. ДРР-логика (`_collect_bid_suggestions`)
  не тронута, живых API-вызовов там как не было, так и нет.
- Всё обёрнуто в `try/except` — недоступность WB market-эндпоинта не ломает существующие
  ДРР-предложения, просто не показывается рыночная строка.

### Фаза 3 — Конфиг [x]
`config.py`, секция CONSTANTS, рядом с `WB_MAX_CPM_RUB`:
```python
config.WB_BID_OVERSPEND_TOLERANCE_PCT  = 15  # % выше рекомендованной WB — риск перерасхода
config.WB_BID_UNDERSPEND_TOLERANCE_PCT = 15  # % ниже рекомендованной WB — риск проигрыша аукциона
```

### Фаза 4 — Telegram (`/bid_adjust`) [x]
В `auto_bid_suggest` (WB-ветка) добавлена строка после текущей/новой ставки:
`Рынок (WB): рекомендовано ~N ₽` + предупреждение `⚠️ ... риск перерасхода/проигрыша аукциона`,
если применимо. Ничего не показывается, если WB не отдал данные (не CPM-кампания, эндпоинт
недоступен). Логика применения ставки (`_handle_bid_callback`) не менялась — рыночная ставка
информационная, единственный жёсткий потолок — `clamp_wb_cpm`/`WB_MAX_CPM_RUB`, как и раньше.

### Фаза 5 — Дашборд [x]
- `_collect_bid_suggestions_for_dashboard`: в `row` добавлены `market_recommended_cpm`,
  `market_flag` (только для WB-строк, `None` для Ozon). Кэш 120с не менялся.
- `dashboard/src/api.ts`: `BidSuggestionRow` расширен полями `market_recommended_cpm`,
  `market_flag`.
- `dashboard/src/charts/BidSuggestions.tsx`: добавлена строка "рынок ~N ₽" с предупреждающим
  оформлением при `market_flag`.

### Фаза 6 — Тесты [x]
`tests/test_bid_clamp.py`: 4 новых теста на `wb_market_bid_flag` (в допуске / overspend /
underspend / нет рыночных данных). `pytest tests/test_bid_clamp.py` — 12/12 зелёные.

### Фаза 7 — Документация [x]
Строка про `GET /api/advert/v0/bids/recommendations` добавлена в `.claude/skills/max-api/SKILL.md`
(параметры `advertId`+`nmId`, схема ответа, источник спека, отсылка к этому плану).

## Известное ограничение / follow-up

Схема ответа WB `get_recommended_bid` подтверждена по стороннему community OpenAPI-спеку
(синхронизируется с реальным API), но **не** живым вызовом на нашем WB-токене — в этой
сессии нет доступа к базе данных с ключами магазинов и Railway CLI. Функция безопасна по
дизайну: если реальная схема на нашем аккаунте всё же отличается, `get_recommended_bid`
просто вернёт `None`, рыночная строка не покажется, ДРР-предложения продолжат работать как
раньше — ничего не сломается. Первая реальная проверка — по логам Railway
(`[WB.get_recommended_bid]`) после деплоя и ручного вызова `/bid_adjust` для WB CPM-кампании.
Follow-up заведён отдельным issue: https://github.com/titboan/ai-office/issues/13.
