# 🏢 AI Office — CLAUDE.md

> Этот файл читается автоматически Claude Code при каждой сессии.
> Последнее обновление: 2026-06-09

---

## Контекст проекта

ИИ-офис с командой агентов на базе Claude API, управляемый через Telegram. Каждый агент — отдельный Telegram бот. Все работают в одном Python процессе на Railway.

- **Репозиторий:** https://github.com/titboan/ai-office
- **Деплой:** Railway (один сервис, все агенты)
- **Статус:** 🟢 Работает в продакшне
- **ОС разработчика:** Windows, PowerShell (использовать `Select-String` вместо `grep`)

---

## Принципы системы

1. **Один Python процесс** — никаких микросервисов без реальной необходимости
2. **Postgres — источник истины.** Все задачи, состояния, результаты — только там
3. **Notion — UI и knowledge base.** Не транзакционный backend
4. **Агенты изолированы.** Никаких прямых вызовов между агентами — только через очередь задач
5. **Человек подтверждает опасные действия** — деплой, PR, внешние API
6. **Простота важнее фреймворков.** Никаких LangGraph, CrewAI, Celery, DSPy
7. **Эволюция, не переписывание.** Минимальный фикс поверх работающего
8. **Telegram — только уведомления.** Данные живут в Postgres и Notion

---

## Стек

- **Python 3.11**, PTB 22.7, asyncio, asyncpg, aiohttp
- **Redis** — память агентов, TTL, Redis locks (защита от двойного нажатия)
- **PostgreSQL** (Railway) — task queue, orders, stocks, reviews, adv stats
- **Claude API** — claude-sonnet-4-6, tool_use agentic loop, vision
- **Railway** — деплой, один сервис, все агенты в одном процессе
- **DataLens** — дашборды аналитики (datalens.yandex.cloud, не потребляет Railway ресурсы)
- **Notion API** — проекты, задачи, контент, статус офиса
- **GitHub API** — репо, файлы, PR, Pages (агент Кевин)

---

## Команда агентов

| Агент | Роль | agent_key | Инструменты |
|-------|------|-----------|-------------|
| 👩‍💼 Марта | Координатор, планировщик цепочек | marta | Task queue, chain planner, /status, /history, /cancel, /projects, кнопки, Claude Vision |
| 👨‍💻 Кевин | Разработчик, код, сайты | kevin | GitHub API (репо, файлы, PR, Pages), tool_use agentic loop |
| 🔍 Каспер | Исследователь, поиск | kasper | Tavily поиск, Notion Research |
| 📊 Питер | Бизнес-аналитик | peter | Notion Research DB |
| ✍️ Элина | Копирайтер, тексты | elina | Notion Content |
| 🗓️ Алекс | Планировщик, напоминания | alex | Notion Tasks, ntfy.sh (iOS push) |
| 🎨 Дэн | Дизайнер, изображения | dan | Pollinations.ai, GitHub API, Notion дизайн-система |
| 📰 Ева | Дайджест Telegram каналов | eva | Telethon MTProto, Notion Content |
| 🛒 Макс | Маркетплейс менеджер (WB+Ozon) | max | WB API, Ozon API, DataLens, группа партнёров |
| 📋 Тина | Тендерный агент (планируется) | tina | ЕИС API, Tavily, FastAPI дашборд |

---

## Архитектура

```
Ты (Telegram: текст / голос / фото+подпись)
      │
      ▼
Марта (Router + Chain Planner + Claude Vision + кнопки)
      │
      ├── Фото без подписи → кнопки выбора действия (Redis: pending_image)
      ├── Одиночная задача → enqueue_task() → агент
      └── Цепочка → _plan_chain() → Claude API → JSON план
                        │
                        ▼
                   [🚀 Запустить?] — подтверждение
                        │
                        ▼
              Notion страница проекта (создаётся сразу)
                        │
                        ▼
       Agent[0] → completed → _advance_chain() → Agent[1] → ... → Финал

Postgres Tasks Queue
  ├── queued → acknowledged → running → completed/failed/timeout
  ├── priority (0/10/20), correlation_id, retry_count, remind_at
  ├── chain_id, chain_index, chain_total, chain_plan (JSONB)
  ├── notion_page_id — прокидывается через всю цепочку
  └── SELECT FOR UPDATE SKIP LOCKED (без race conditions)

Фоновые задачи:
  ├── Статус офиса в Notion — каждую минуту
  ├── Макс: проверка отзывов — 09:00, 14:00, 20:00 МСК
  ├── Макс: проверка негативных отзывов — каждые 15 минут
  ├── Макс: ежедневная сводка магазина — 09:00 МСК
  ├── Макс: синхронизация рекламной статистики — 03:00 UTC
  └── Ева: дайджест каналов — 09:30 МСК (ожидает TELETHON_SESSION)
```

---

## Рабочий процесс разработки

1. Claude в чате (claude.ai) анализирует задачу → пишет команду для Claude Code
2. Копируешь команду в Claude Code (расширение VS Code)
3. Claude Code применяет изменения в репо
4. Сообщаешь результат → продолжаем

**Claude в чате = архитектор и ментор. Claude Code = исполнитель.**

### Инструменты разработчика (подключены в claude.ai)

- **GitHub коннектор** — Claude в чате читает файлы репо напрямую
- **Notion коннектор** — Claude читает/пишет в Notion workspace
- **Project Knowledge** — CLAUDE.md и другие файлы репо как контекст

---

## Агент Макс — Маркетплейс менеджер

### Что умеет

- **Отзывы WB:** автоответ 3-5★, одобрение 1-2★ через Telegram
- **Отзывы Ozon:** ❌ недоступны на Premium Lite (нужен Plus/Pro ~20к/мес, не окупается)
- **Заказы:** синхронизация WB и Ozon в Postgres, статистика по дням с динамикой
- **Выкупы WB:** Statistics API, поле `forPay`, только saleID начинается с "S"
- **Остатки:** WB и Ozon по кластерам (11 регионов WB, кластеры Ozon), порог < 20 шт
- **Ежедневная сводка:** сегодня/вчера/неделю назад, 09:00 МСК
- **ИИ-агент в группе партнёров:** реагирует на "@бот" или "Макс" (голосовые без триггера игнорируются)
- **Реклама WB:** `marketplace_adv_stats`, синк через `/adv/v3/fullstats`, 03:00 UTC
- **Реклама Ozon:** Performance API + OAuth, токен кешируется в Redis (TTL 25 мин)

### Команды Макса

- `/start` — главное меню
- `/sync` — ручная синхронизация
- `/reset_checked` — сбросить дату проверки отзывов
- `/reset_orders` — пересинхронизировать все заказы

### Таблицы БД (Макс)

```sql
marketplace_shops        -- магазины: api_token, statistics_token, client_id
marketplace_reviews      -- отзывы: статус, generated_reply, final_reply
marketplace_orders       -- заказы: WB (finishedPrice) + Ozon (аналитика по SKU/дням)
marketplace_sales        -- выкупы: WB (forPay, saleID начинается с "S") + Ozon delivered
marketplace_stocks       -- остатки: WB (supplierArticle) + Ozon (offer_id)
marketplace_adv_stats    -- рекламная статистика WB и Ozon
wb_campaigns             -- названия WB кампаний (вручную, т.к. /adv/v1/promotion/adverts — 404)
product_mapping          -- WB артикулы ↔ Ozon offer_id (для DataLens)
```

### VIEW в БД

```sql
stocks_unified      -- marketplace_stocks + product_mapping (для DataLens)
adv_stats_unified   -- marketplace_adv_stats + wb_campaigns WHERE marketplace='wb'
adv_stats_summary   -- агрегат по кампаниям: total_views, total_clicks, avg_ctr, total_spend
```

### API особенности

**WB Statistics API** (отдельный токен, категория "Статистика"):
- Заказы: `/api/v1/supplier/orders` flag=0/1
- Продажи: `/api/v1/supplier/sales` — только saleID начинается с "S" (R = возврат)
- Остатки: `/api/v1/supplier/stocks`
- Rate limit: 429 → sleep 60 сек + retry
- Реклама: `/adv/v3/fullstats` — работает. `/adv/v1/promotion/adverts` — **404 (баг WB с окт 2025)**

**WB Feedbacks API** (основной токен):
- Список: `/api/v1/feedbacks` isAnswered=false
- Ответ: POST `/api/v1/feedbacks/answer` → 204

**Ozon API** (Client-Id + Api-Key, Premium Lite):
- Остатки: POST `/v2/analytics/stock_on_warehouses` + маппинг SKU→offer_id через `/v3/product/info/list`
  - ⚠️ `/v2/product/info/list` — устарел, возвращает 404. Только `/v3/`
  - v3 возвращает `{"items": [...]}` напрямую (без обёртки `result`)
- Заказы активные: POST `/v3/posting/fbo/list`
- Заказы история: POST `/v1/analytics/data` (агрегат, не точные данные)
- Выкупы: POST `/v3/posting/fbo/list` статус delivered
- Отзывы: ❌ Premium Plus/Pro

**Ozon Performance API** (OAuth, обязателен с апр 2026):
- Токен: POST `https://api-performance.ozon.ru/api/client/token` → кешируется в Redis 25 мин
- client_id формат: `цифры@advertising.performance.ozon.ru`
- Кампании: GET `/api/client/campaign` (только CAMPAIGN_STATE_RUNNING)
- Статистика (async, 3 шага): POST → UUID → GET poll (до state=OK) → GET report → CSV (Windows-1251)
- Батчи по 10 кампаний (лимит API), retry при 429
- Переменные: `OZON_PERFORMANCE_CLIENT_ID`, `OZON_PERFORMANCE_CLIENT_SECRET`

### DataLens дашборд

- **URL:** https://datalens.yandex/zhnao5ut1xvmj
- **Подключение:** maglev.proxy.rlwy.net:12614, база railway
- Чарты: заказы, выручка, остатки, отзывы (WB), реклама (CTR, клики, показы, расходы по кампаниям)

---

## Агент Ева — Дайджест Telegram каналов

**Статус: код готов, ожидает TELETHON_SESSION**

Нужен второй номер телефона → генерация StringSession через Replit.

### Команды

- `/digest [3d|12h|ДАТА]` — дайджест за период
- `/add_channel @username` / `/remove_channel` / `/channels`

### Таблицы БД

```sql
digest_channels  -- каналы: chat_id, username, title, added_by, last_checked_at
```

---

## Известные проблемы и ограничения

| Проблема | Описание | Решение |
|----------|----------|---------|
| `Conflict` при деплое | Telegram polling конфликт при рестарте | Норма, само проходит 30-60 сек |
| WB 429 rate limit | Statistics API при частых запросах | sleep 60 сек + retry (реализовано) |
| WB Advert API 404 | `/adv/v1/promotion/adverts` не работает с окт 2025 | Названия кампаний вносить вручную в `wb_campaigns` |
| Ozon отзывы | PermissionDenied на Premium Lite | Нужен Plus/Pro (~20к/мес) — не окупается |
| Ozon заказы | `/v1/analytics/data` — агрегат, не реальные заказы | Принято как ограничение |
| Ева нет сессии | Telethon требует StringSession | Второй номер + Replit |
| Дэн медленный | Pollinations.ai 30-120 сек на изображение | timeout=600, принято |
| Кевин max_tokens | Потолок 16000 токенов у sonnet-4 | Большие лендинги могут обрезаться |
| Notion Unclosed connection | aiohttp не закрывает соединения к api.notion.com | Некритично, мониторим |

---

## Отладка (PowerShell, Windows)

```powershell
# Логи
railway logs --tail 200
railway logs --tail 100 | Select-String -Pattern "max|error|sync"
railway logs --tail 50  | Select-String -Pattern "WB|Ozon|marketplace"
railway logs --tail 100 | Select-String -Pattern "adv|OzonPerf|реклама"

# Статус задач — через Telegram
# /status у Марты (активные)
# /history (выполненные)
```

**Типичные поломки Макса:**
- `rate limit` → само пройдёт через 60 сек
- `Chat not found` → проверить PARTNERS_GROUP_ID в Railway Variables
- `Message is too long` → сообщение > 4096 символов, разбивается автоматически
- Ozon `PermissionDenied` → отзывы недоступны на текущем тарифе

---

## Переменные окружения

```env
ANTHROPIC_API_KEY=

MARTA_BOT_TOKEN=
KEVIN_BOT_TOKEN=
KASPER_BOT_TOKEN=
PETER_BOT_TOKEN=
ELINA_BOT_TOKEN=
ALEX_BOT_TOKEN=
DEN_BOT_TOKEN=
EVA_BOT_TOKEN=
MAX_BOT_TOKEN=

OFFICE_GROUP_ID=
PARTNERS_GROUP_ID=       # ID группы партнёров для Макса

DATABASE_URL=            # Railway PostgreSQL (maglev.proxy.rlwy.net:12614)
REDIS_URL=               # Railway Redis

TAVILY_API_KEY=
GROQ_API_KEY=
GITHUB_TOKEN=
GITHUB_USERNAME=

NOTION_TOKEN=
NOTION_PARENT_PAGE_ID=
NOTION_PROJECTS_DB=
NOTION_TASKS_DB=
NOTION_IDEAS_DB=
NOTION_RESEARCH_DB=
NOTION_CONTENT_DB=
NOTION_STATUS_PAGE_ID=

NTFY_TOPIC=

# Ева
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELETHON_SESSION=        # StringSession через Replit

# Тина (планируется)
DASHBOARD_TOKEN_1=
DASHBOARD_TOKEN_2=

# Ozon Performance
OZON_PERFORMANCE_CLIENT_ID=     # цифры@advertising.performance.ozon.ru
OZON_PERFORMANCE_CLIENT_SECRET=

CLAUDE_MODEL=claude-sonnet-4-6
PORT=8080
```

---

## Дорожная карта

### ✅ Выполнено (Phase 1-6.6)

- 9 агентов: Марта, Кевин, Каспер, Питер, Элина, Алекс, Дэн, Ева, Макс
- asyncio event loop, Postgres task queue, Redis память
- Worker loop, retry, таймауты, приоритеты, correlation ID, chain planner
- Голосовые (Groq Whisper), Vision (Claude), кнопки
- Scheduled reminders → ntfy.sh
- GitHub интеграция, Notion (5 баз + статус офиса)
- Дэн — Pollinations.ai + GitHub commit
- Макс — WB+Ozon: отзывы, заказы, выкупы, остатки, сводка, ИИ-агент в группе
- Реклама WB: `marketplace_adv_stats`, DataLens дашборды, scheduled task 03:00 UTC
- Реклама Ozon: Performance API + OAuth, Redis кеш токена (25 мин TTL)
- DataLens: заказы, выручка, остатки, отзывы, реклама (CTR, расходы, сводная таблица)
- Ева — код готов, ожидает TELETHON_SESSION
- `product_mapping`: маппинг WB↔Ozon артикулов, VIEW `stocks_unified` для DataLens
- VIEW `adv_stats_unified` и `adv_stats_summary` для DataLens рекламной аналитики
- `wb_campaigns`: 10 кампаний заполнены вручную (API `/adv/v1/promotion/adverts` → 404)
- Кнопка 🔄 Синхронизировать в меню Макса
- **Фикс:** автотриггер голосовых в группе убран (требуется @mention или "Макс")
- **Фикс:** Ozon `product/info/list` v2→v3 (37 SKU восстановлены, маппинг работает)
- **Фикс:** guard против пустых `product_id` в `upsert_stock`
- **Фикс:** fallback `product_name` на `offer_id` в `get_stocks`

### 🔵 В работе

- [ ] **Ева** — второй номер телефона → TELETHON_SESSION
- [ ] **Ozon реклама** — первый запуск `sync_ad_stats` 09.06 в 03:00 UTC, проверить результат

### 🟡 Следующие шаги

- [ ] **Execution Trace** — таблица `task_events` в Postgres (Phase 6)
- [ ] **Ежедневный дайджест Марты** — отчёт в Notion каждый вечер
- [ ] **Тина** — тендерный агент (ЕИС API)

### ⚪ PHASE 7 — AI Operating System

- [ ] Persistent company context — профиль компании в system prompt
- [ ] Механизм уточнений — агент задаёт вопрос, ждёт ответа, продолжает
- [ ] Approval Gates по уровням риска (0/1/2/3)
- [ ] Autonomous workflow: задача → план → код → тесты → PR → деплой
- [ ] QA агент, AI PM функции у Марты

---

## Антипаттерны (не делать)

- ❌ LangGraph / CrewAI / MetaGPT — оверинжиниринг для соло
- ❌ Celery / RabbitMQ / Kafka — не нужно на этом масштабе
- ❌ DSPy / GEPA — для исследователей
- ❌ Kubernetes / микросервисы — преждевременно
- ❌ Notion как transactional backend
- ❌ Переписывать с нуля — только эволюция
- ❌ Полный текст агентов в Telegram — только в Notion
- ❌ DALL-E 3 для Дэна — Pollinations.ai бесплатно
- ❌ Ева/Тину в отдельные Railway сервисы — лишние расходы
- ❌ Паниковать из-за Conflict при деплое — это норма
- ❌ Ozon Premium Plus ради API отзывов — не окупается (20к/мес)

---

## Полезные ссылки

### Проект
- GitHub: https://github.com/titboan/ai-office
- Railway: https://railway.app
- DataLens: https://datalens.yandex/zhnao5ut1xvmj
- Anthropic Console: https://console.anthropic.com

### Документация по компонентам
- [Claude API](https://docs.anthropic.com/en/docs/overview)
- [Claude Models](https://docs.anthropic.com/en/docs/about-claude/models/overview)
- [Tool use](https://docs.anthropic.com/en/docs/build-with-claude/tool-use)
- [Prompt engineering](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/overview)
- [PTB Docs](https://python-telegram-bot.readthedocs.io/en/stable/)
- [PTB Examples](https://github.com/python-telegram-bot/python-telegram-bot/tree/master/examples)
- [asyncpg](https://magicstack.github.io/asyncpg/current/)
- [Redis Python](https://redis-py.readthedocs.io/en/stable/)
- [Loguru](https://loguru.readthedocs.io/en/stable/)
- [Telethon](https://docs.telethon.dev/en/stable/)
- [Notion API](https://developers.notion.com/reference/intro)
- [Tavily](https://docs.tavily.com/)
- [Railway Docs](https://docs.railway.app/)

### Маркетплейсы
- [WB Statistics API](https://openapi.wildberries.ru/statistics/api/ru/)
- [WB Feedbacks API](https://openapi.wildberries.ru/feedbacks/api/ru/)
- [WB Advert API](https://openapi.wildberries.ru/promotion/api/ru/)
- [Ozon Seller API](https://docs.ozon.ru/api/seller/)
- [Ozon Performance API](https://performance.ozon.ru/api/docs)
