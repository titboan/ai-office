# 🏢 AI Office — Документация проекта

> Последнее обновление: 2026-06-12

---

## Принципы системы

1. **Один Python процесс**, пока это возможно — никаких микросервисов без реальной необходимости.
2. **Postgres — источник истины.** Все задачи, состояния, результаты — только там.
3. **Notion — пользовательский интерфейс и knowledge base.** Не транзакционный backend.
4. **Каждый агент ничего не знает о других агентах напрямую.** Все взаимодействия через очередь задач.
5. **Человек подтверждает потенциально опасные действия** — деплой, PR, внешние write-API.
6. **Простота важнее модных AI-фреймворков.** Никаких LangGraph, CrewAI, Celery, DSPy.
7. **Эволюция, не переписывание.** Каждое изменение — минимальный фикс поверх работающего.
8. **Telegram — основной канал управления.** Реальные данные живут в Postgres и Notion.

---

## Обзор

ИИ-офис с командой из 10 агентов на базе Claude API, управляемый через Telegram. Каждый агент — отдельный Telegram-бот. Все работают в одном Python процессе на Railway.

**Репозиторий:** <https://github.com/titboan/ai-office>
**Деплой:** Railway (один сервис, все агенты в одном процессе)
**Статус:** 🟢 Работает в продакшне

Интерактивная схема архитектуры: [`docs/index.html`](docs/index.html)

---

## Команда агентов

| Агент | Роль | agent_key | Инструменты |
|-------|------|-----------|-------------|
| 👩‍💼 Марта | Координатор, планировщик цепочек | marta | Task queue, chain planner, /status, /history, /cancel, /projects, кнопки, Claude Vision |
| 👨‍💻 Кевин | Разработчик, код, сайты | kevin | GitHub API (репо, файлы, PR, Pages), tool_use agentic loop |
| 🔍 Каспер | Исследователь, поиск | kasper | Tavily поиск, Notion Research |
| 📊 Питер | Бизнес-аналитик, маркетплейсы | peter | PostgreSQL (заказы, остатки, реклама), Notion Research DB |
| ✍️ Элина | Копирайтер, тексты | elina | Notion Content |
| 🗓️ Алекс | Планировщик, напоминания | alex | Notion Tasks, ntfy.sh (iOS push), /testpush |
| 🎨 Дэн | Дизайнер, изображения | dan | Pollinations.ai, GitHub API (изображения), Notion дизайн-система |
| 📰 Ева | Сводка каналов + email-дайджест | eva | Telethon MTProto (ждёт API credentials), Email IMAP, Notion Content |
| 🛒 Макс | Менеджер маркетплейсов WB+Ozon | max | WB Feedbacks API, Ozon Seller API, Ozon Performance API, PostgreSQL |
| 🏛️ Тина | Тендерный агент 44-ФЗ | tina | ГосПлан API v2, Tavily, PostgreSQL (ждёт деплой) |

---

## Архитектура

```
Ты (Telegram: текст / голос / фото+подпись)
      │
      ▼
Марта (Router + Chain Planner + Claude Vision + кнопки)
      │
      ├── Фото без подписи → кнопки выбора действия (Redis: pending_image)
      │
      ├── Одиночная задача → enqueue_task() → агент
      │
      └── Цепочка → _plan_chain() → Claude API → JSON план
                        │
                        ▼
                   [🚀 Запустить?] — подтверждение
                        │
                        ▼
              Notion страница проекта (создаётся сразу)
                        │
                        ▼
       Agent[0] (напр. Дэн: create_repo → generate_image → design_system)
                        │ completed → _advance_chain()
                        ▼
       Agent[1] (напр. Кевин: использует ассеты Дэна → HTML → Pages)
                        │ completed → _advance_chain()
                        ▼
       Финал → резюме + кнопки [🌐 Открыть сайт] [📋 Notion]

Postgres Tasks Queue
  ├── queued → acknowledged → running → completed/failed/timeout
  ├── priority (0/10/20), correlation_id, retry_count, remind_at
  ├── chain_id, chain_index, chain_total, chain_plan (JSONB)
  ├── notion_page_id — прокидывается через всю цепочку
  └── SELECT FOR UPDATE SKIP LOCKED (без race conditions)

Worker Agents (asyncio, поллинг каждые 2 сек по agent_key)
      │
      ▼
Claude API → результат в tasks.result → уведомление пользователю

Фоновые задачи:
  ├── Макс — отзывы WB+Ozon: 09:00 / 14:00 / 20:00 МСК
  ├── Макс — мониторинг негативных отзывов: каждые 15 мин
  ├── Макс — синхронизация рекламы: 03:00 UTC
  ├── Питер — еженедельный аудит: понедельник 10:00 МСК
  ├── Ева — email-дайджест + сортировка почты: 09:30 МСК (нужен EVA_BOT_TOKEN + EMAIL_APP_PASS)
  ├── Ева — дайджест каналов: 09:30 МСК (заблокировано: ждёт TELETHON_SESSION)
  ├── Тина — дайджест тендеров: 08:00 МСК (ждёт деплой)
  └── Марта — статус офиса в Notion: каждую минуту
```

---

## Что работает

### ✅ Phase 1 — Core

- 10 агентов в одном asyncio event loop (PTB 22.7)
- Postgres task queue, worker loop, retry, таймауты
- `_on_polling_error` → `os._exit(0)` при Conflict
- Redis история диалогов с автосжатием (Haiku summary при ≥15 сообщений)

### ✅ Phase 2 — Observability

- `correlation_id` сквозной через всю цепочку
- Structured logging (loguru)
- `/status`, `/history`, `/cancel`, кнопки у Марты

### ✅ Phase 3 — Коммуникация и память

- Приоритеты 0/10/20, `_detect_priority()`
- `remind_at`, ntfy.sh → iOS push, `/testpush`
- Groq Whisper → транскрипция голосовых
- Claude Vision: структура из референса, кнопки без подписи, Redis pending_image

### ✅ Phase 4 — Оптимизация токенов

- `is_task=True` — история не грузится при задачах (−40–60%)
- Лимиты: payload 4000, история 1000, контекст 2000 символов
- Логирование: `tokens | agent=X | input=Y | output=Z`

### ✅ Phase 5 — Межагентная коммуникация (цепочки)

- JSON план, кнопка подтверждения со списком шагов
- Контекст через `tasks.result` в Postgres
- Финал: резюме + кнопки [Открыть сайт] [Notion]
- Защита от циклов через `from_agent`

### ✅ Phase 6 — Notion + Projects

- Реестр проектов: таблица `projects`, `/projects`, "продолжи проект X"
- Статус офиса в Notion — каждую минуту
- Notion интеграция:

| База | Кто пишет | Когда |
|------|-----------|-------|
| Research | Каспер | Ответ > 2000 символов |
| Research | Питер | Каждый бизнес-анализ |
| Research | Дэн | Дизайн-система проекта |
| Content | Элина | Каждая задача |
| Tasks | Алекс | Каждая задача + напоминания |
| Projects | Марта | Создание страницы проекта |
| Страница проекта | Все агенты цепочки | Toggle H2 секции |
| Статус офиса | Фоновая задача | Каждую минуту |

### ✅ Phase 6.5 — Новые агенты (дизайн и медиа)

- [x] Дэн — дизайнер (Pollinations.ai, цепочки Дэн→Кевин, Каспер→Дэн→Кевин)
- [ ] Ева — дайджест Telegram каналов (ждёт `TELETHON_SESSION`)
- [ ] Тина — тендерный агент 44-ФЗ (ждёт деплой + `TINA_BOT_TOKEN`)

### ✅ Phase 6.6 — Маркетплейсы и форматирование (июнь 2026)

- [x] Макс — полная интеграция WB+Ozon (отзывы, заказы, остатки, реклама)
- [x] Питер — реальные данные из PostgreSQL (рентабельность, ДРР, SWOT)
- [x] HTML-форматирование во всех агентах (миграция с Markdown)
- [x] /help-команды для всех агентов, регистрация в Telegram UI

---

## Дорожная карта

### 🔵 Phase 6 — Незакрытое

- [x] **Execution Trace** — таблица `task_events` в Postgres.
  Логировать: TASK_CREATED, TASK_STARTED, TOOL_CALLED, TOOL_FAILED, CHAIN_ADVANCED, TASK_COMPLETED, TASK_FAILED.
  Хелпер `log_event(task_id, event_type, payload)` в `base_agent.py`.
  Даёт: таймлайн цепочки, дебаг где сломалось, база для анализа промптов.
  Вдохновлено Hermes Agent и OpenAI Agents SDK Tracing — без DSPy/GEPA.

- [x] **Cost Tracking** — `estimated_cost` и `latency_ms` в БД.
  Сейчас только логирование токенов — стоимость не считается.
  Отчёт от Питера по запросу.

- [ ] **Деплой Евы (email-дайджест + сортировка)** — добавить `EVA_BOT_TOKEN`, `EMAIL_USER`, `EMAIL_APP_PASS` → `/email_digest 1d` и `/sort_emails 1d` работают без Telethon; автозапуск 09:30 МСК
- [ ] **Деплой Евы (Telegram-каналы)** — заблокировано: my.telegram.org выдаёт ERROR при создании API-приложения; нужен `TELEGRAM_API_ID` + `TELEGRAM_API_HASH` → `TELETHON_SESSION`

- [ ] **Деплой Тины** — добавить `TINA_BOT_TOKEN` в Railway, проверить `/tenders`.

### ⚪ Phase 7 — AI Operating System

- [ ] Persistent company context — профиль компании в system prompt всех агентов
- [ ] Механизм уточнений — агент задаёт вопрос, ждёт ответа, продолжает
- [ ] Approval Gates по уровням риска:
  - 0 (исследование, тексты, изображения) — без подтверждения
  - 1 (создание репо) — опционально
  - 2 (push в main, деплой) — обязательно
  - 3 (внешние API с расходами) — обязательно
- [ ] UI таймлайна цепочки (на базе task_events из Phase 6)
- [ ] Autonomous workflow: задача → план → код → тесты → PR → деплой
- [ ] Typed task payloads — структурированный JSON вместо свободного текста
- [ ] QA агент, AI PM функции у Марты
- [ ] **Ежедневный дайджест Марты** — фоновая задача каждый вечер (21:00 МСК).
  Марта читает `get_recent_tasks(limit=50)` за сегодня, формирует краткий отчёт
  в Notion: сколько задач выполнено / кто работал / что сделали / ошибки.
  Вдохновлено OpenClaw MEMORY.md паттерном — агент ведёт дневник своей работы.
- [ ] State machine для цепочек — когда появятся параллельные ветки
- [ ] CI/CD для Кевина — автотриггер деплоя после мёрджа PR (нужен только при регулярных деплоях)

---

## Планируемые таблицы (Phase 6)

### task_events — execution trace

```sql
task_events
├── id          BIGSERIAL PRIMARY KEY
├── task_id     BIGINT          -- ссылка на tasks.id
├── chain_id    TEXT            -- группировка событий цепочки
├── agent_key   TEXT
├── event_type  TEXT            -- TASK_CREATED / TASK_STARTED / TOOL_CALLED /
│                               -- TOOL_FAILED / CHAIN_ADVANCED /
│                               -- TASK_COMPLETED / TASK_FAILED
├── payload     JSONB           -- инструмент, ошибка, длина результата и т.д.
└── created_at  TIMESTAMPTZ DEFAULT NOW()

INDEX: idx_task_events_chain ON task_events(chain_id, created_at)
INDEX: idx_task_events_task  ON task_events(task_id, created_at)
```

---

## Стек технологий

| Компонент | Версия |
|-----------|--------|
| Python | 3.11 |
| python-telegram-bot | 22.7 |
| anthropic (AsyncAnthropic) | 0.104.1 |
| Claude модель (основная) | claude-sonnet-4-6 |
| Claude Opus (планирование цепочек) | claude-opus-4-8 |
| Claude Haiku (summary) | claude-haiku-4-5-20251001 |
| asyncpg | 0.29.0 |
| redis (asyncio) | 5.0.1 |
| tavily-python | 0.7.24 |
| aiohttp | 3.13.5 |
| loguru | 0.7.3 |
| groq | latest |
| python-dotenv | 1.2.2 |
| ntfy.sh | HTTP API |
| Pollinations.ai | HTTP API (бесплатно) |
| ГосПлан API | v2 (бесплатно до 01.08.2026) |

---

## Структура проекта

```
ai-office/
├── CLAUDE.md               # Навигация для Claude Code сессий
├── ROADMAP.md              # Этот файл — стратегический документ
├── main.py                 # Точка входа, asyncio, фоновые задачи
├── config.py               # env переменные + CONSTANTS
├── task_queue.py           # enqueue_task, enqueue_chain_task, get_next_task, mark_*
├── agents/
│   ├── base_agent.py       # think, _worker_loop, _notify_user, _advance_chain
│   ├── marta.py            # _process_text, _plan_chain, handle_photo, chain confirm
│   ├── kevin.py            # GITHUB_TOOLS, agentic loop, max_tokens=16000
│   ├── dan.py              # DESIGN_TOOLS, Pollinations, GitHub images
│   ├── kasper.py           # Tavily, Notion Research
│   ├── peter.py            # PostgreSQL данные, рентабельность, ДРР
│   ├── elina.py            # Notion Content
│   ├── alex.py             # Notion Tasks, ntfy.sh
│   ├── eva.py              # Telethon, digest_channels, Notion Content
│   ├── max.py              # WB+Ozon API, отзывы, заказы, реклама
│   └── tina.py             # ГосПлан API, tender_opportunities
├── tools/
│   ├── search.py           # Tavily
│   ├── notion.py           # API, _markdown_to_blocks, update_status_page
│   ├── github.py           # create_repo, create_file, create_branch, create_pr, enable_pages
│   ├── ntfy.py             # send_push
│   └── gosplan_api.py      # ГосПлан API клиент
├── utils/
│   └── tg_format.py        # HTML-форматирование для Telegram
├── docs/
│   └── index.html          # Интерактивная визуализация архитектуры
├── plans/                  # Технические планы фич
├── retrospectives/         # Рефлексии сессий
├── ai-clone/feedback/      # Накопленные правила работы
├── .claude/skills/         # SKILL.md для Claude Code
├── requirements.txt
├── Dockerfile
└── railway.toml
```

---

## Известные проблемы

> Честная фиксация — что сырое или работает с оговорками.

### ⚠️ Conflict при деплое — это нормально

При rolling restart Railway старый инстанс ещё жив, новый уже стартовал:
```
telegram.error.Conflict: terminated by other getUpdates request
```
Обработано через `_on_polling_error` → `os._exit(0)`. Само проходит за 30–60 сек. Не паниковать.

### ⚠️ WB /adv/v1/promotion/adverts → 404

Эндпоинт недоступен с октября 2025. Названия рекламных кампаний вносить вручную в таблицу `wb_campaigns`. Автосинхронизация названий не работает.

### ⚠️ Notion Unclosed connection

Периодически в логах появляется `Unclosed connection` от Notion клиента. Некритично, мониторим. Задачи и данные не теряются.

### ⚠️ Дэн (Pollinations.ai) — медленный

Генерация одного изображения 30–120 сек. Лендинг с 6 картинками = 5–7 минут. `timeout_seconds = 600`.
Альтернатива если станет критично — Replicate/Flux (~$0.003/img), замена URL в `_generate_image`.

### ⚠️ Кевин и max_tokens

`max_tokens = 16000` — потолок sonnet-4. Очень большой лендинг может обрезаться. Симптом: `stop_reason='max_tokens'` в логах. Решение: разбивать на несколько `create_file`.

### ⚠️ Ева ждёт TELETHON_SESSION

Агент зарегистрирован и запускается, но дайджест не работает без сессии Telethon. Нужен второй номер телефона для авторизации.

### ⚠️ Тина ждёт деплой

Код готов, нужно добавить `TINA_BOT_TOKEN` в Railway и проверить `/tenders` через Telegram.

---

## Антипаттерны (не делать)

- ❌ LangGraph / CrewAI / MetaGPT — оверинжиниринг для соло
- ❌ Celery / RabbitMQ / Kafka — не нужно на этом масштабе
- ❌ DSPy / GEPA для авто-улучшения промптов — для исследователей, не для соло
- ❌ Kubernetes / микросервисы — преждевременно
- ❌ Notion как transactional backend
- ❌ Переписывать с нуля — только эволюция
- ❌ DAG-style dependencies — цепочки линейные
- ❌ DALL-E 3 для Дэна — Pollinations.ai бесплатно, для прототипов достаточно
- ❌ Ева/Тина в отдельные Railway сервисы — лишние расходы
- ❌ Дробить Кевина на классы — преждевременная абстракция
- ❌ git add . или git add -A — только поимённо
- ❌ railway up без явной команды Бориса — продакшн с живыми заказами

---

## Источники вдохновения

**Hermes Agent (Nous Research)** — self-improving агент с learning loop и скиллами.
Взята идея execution traces для отладки цепочек.
Код не переиспользуется — разная философия (один агент vs команда ролей).

**OpenClaw** — паттерн MEMORY.md: агент сам ведёт ежедневный дневник своей работы.
В нашем случае — Марта пишет вечерний дайджест в Notion (Phase 6, не реализовано).

**OpenAI Agents SDK** — встроенный Tracing подтверждает правильность нашего подхода
к execution traces через task_events.

**Pocket Flow** — 100-строчный LLM фреймворк. Доказывает что вся оркестрация агентов
не требует тысяч строк. Наша архитектура лаконичнее большинства фреймворков.

---

## Переменные окружения

```env
ANTHROPIC_API_KEY=

# Telegram токены агентов
MARTA_BOT_TOKEN=
KEVIN_BOT_TOKEN=
KASPER_BOT_TOKEN=
PETER_BOT_TOKEN=
ELINA_BOT_TOKEN=
ALEX_BOT_TOKEN=
DEN_BOT_TOKEN=
EVA_BOT_TOKEN=
MAX_BOT_TOKEN=
TINA_BOT_TOKEN=          # добавить в Railway для активации Тины

OFFICE_GROUP_ID=
PARTNERS_GROUP_ID=        # группа для сводок Макса

DATABASE_URL=             # Railway: Add Plugin → PostgreSQL
REDIS_URL=                # Railway: Add Plugin → Redis

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
NOTION_STATUS_PAGE_ID=   # ID страницы "Статус офиса" (фиксированный)

NTFY_TOPIC=

# WB + Ozon (Макс)
WB_API_TOKEN=
OZON_API_KEY=
OZON_CLIENT_ID=

# Ева — Telethon (дайджест каналов)
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELETHON_SESSION=         # добавить для активации Евы

# Тина — тендеры
GOSPLAN_API_KEY=          # опционально до 01.08.2026
TENDER_REGION_CODE=       # напр. 23 для Краснодарского края
TENDER_MIN_NMCK=
TENDER_MAX_NMCK=
TENDER_SCAN_HOUR_UTC=     # час дайджеста тендеров по UTC

CLAUDE_MODEL=claude-sonnet-4-6
CLAUDE_OPUS_MODEL=claude-opus-4-8
CLAUDE_HAIKU_MODEL=claude-haiku-4-5-20251001
PORT=8080
```
