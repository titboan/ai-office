# AI Office — Project Status

> Последнее обновление: 2026-05-27

---

## Текущее состояние

**Статус:** 🟢 Активная разработка — MVP готов к деплою

| Параметр | Значение |
|----------|----------|
| Репозиторий | https://github.com/titboan/ai-office |
| Ветка | `main` |
| Последний коммит | `5490a57` — fix: real delegation cycle |
| Деплой | Railway (готов, не задеплоен) |
| Python | 3.11 |
| LLM | Claude Sonnet 4.6 (`claude-sonnet-4-6`) |

---

## Что работает сейчас

### ✅ Ядро
- **6 Telegram-агентов** в одном процессе через `asyncio` (PTB 22.7 non-blocking API)
- **Claude API** (`anthropic==0.104.1`, `AsyncAnthropic`) — нативный async
- **Запуск:** `python main.py` — все агенты, `python main.py --agent marta` — один
- **Локальная разработка:** `start_all.bat` — 6 отдельных окон терминала

### ✅ Память агентов (Redis)
- История диалогов хранится в Redis, ключ: `history:{agent_name}:{chat_id}`
- Последние 20 сообщений, TTL 7 дней
- **Fallback:** если `REDIS_URL` не задан — хранение в `dict` в памяти процесса
- При ошибке Redis — автоматически падает на dict (агент не падает)
- `/reset` — очищает историю из Redis или dict

### ✅ Веб-поиск у Каспера (Tavily)
- `tools/search.py` — `async search_web(query)` через `AsyncTavilyClient`
- Топ-3 результата: заголовок + URL + контент (до 400 символов)
- Краткий синтез Tavily (`include_answer=True`, `search_depth="advanced"`)
- Каспер делает поиск **перед каждым** ответом — результаты инжектируются в промпт
- **Fallback:** если `TAVILY_API_KEY` не задан — возвращает сообщение об ошибке (не падает)

### ✅ Общая офисная группа
- `post_to_group()` в BaseAgent — отправка от имени агента в `OFFICE_GROUP_ID`
- Worker-режим: standalone `async with Bot(token=...)` когда `self.app = None`
- `run_task()` — обёртка с уведомлениями о начале и завершении задачи
- Марта уведомляет группу при делегировании (оба пути: `handle_message` и `handle_task`)

### ✅ Делегирование Марта → Агент → Пользователь
- Системный промпт Марты инструктирует Клода вставлять структурированный блок:
  ```
  ##DELEGATE##
  agent: kasper
  task: исследуй Python 3.13
  ##END##
  ```
- `_parse_delegation()` парсит блок регекспом
- `_get_agent()` lazy-создаёт агента и кэширует в `_agent_pool`
- Полный цикл:
  1. Пользователь → Марта
  2. Марта думает → определяет исполнителя
  3. Пользователю: преамбула + "Агент принял задачу…"
  4. Агент выполняет `handle_task()` (с поиском у Каспера)
  5. Пользователю: результат агента
- Длинные ответы (> 4096 символов) автоматически разбиваются на части

### ✅ Railway-деплой (подготовлен)
- `Dockerfile` — `python main.py`, `AGENT_NAME` управляет режимом
- `railway.toml` — без `healthcheckPath` (polling-бот не слушает HTTP)
- `.dockerignore` — `.env`, `.git`, `__pycache__` исключены
- Один сервис = все 6 агентов / либо 6 отдельных сервисов с `AGENT_NAME`

---

## Команда агентов

| Агент | Роль | Бот-токен | Особенности |
|-------|------|-----------|-------------|
| 👩‍💼 **Марта** | Координатор | `MARTA_BOT_TOKEN` | Делегирование с парсингом |
| 👨‍💻 **Кевин** | Разработчик | `KEVIN_BOT_TOKEN` | `/code` |
| 🔍 **Каспер** | Исследователь | `KASPER_BOT_TOKEN` | Tavily поиск + `/research` |
| 📊 **Питер** | Аналитик | `PETER_BOT_TOKEN` | `/analyze` |
| ✍️ **Элина** | Копирайтер | `ELINA_BOT_TOKEN` | `/write`, `/post` |
| 🗓️ **Алекс** | Планировщик | `ALEX_BOT_TOKEN` | `/plan`, `/roadmap` |

---

## Стек технологий

| Компонент | Технология | Версия |
|-----------|-----------|--------|
| LLM | Anthropic Claude Sonnet | `claude-sonnet-4-6` |
| Telegram SDK | python-telegram-bot | 22.7 |
| Claude SDK | anthropic | 0.104.1 |
| Веб-поиск | tavily-python | 0.7.24 |
| Память | redis | 5.0.1 |
| HTTP/async | aiohttp | 3.13.5 |
| Окружение | python-dotenv | 1.2.2 |
| Логи | loguru | 0.7.3 |
| Runtime | Python | 3.11 |
| Контейнер | Docker | — |
| Хостинг | Railway | — |

---

## История версий

### v0.5.0 — 2026-05-27
**Общая офисная группа — агенты пишут в Telegram-группу**
- `post_to_group()` в `BaseAgent` — отправка в `OFFICE_GROUP_ID` от имени агента
- Два режима: через `self.app.bot` (основной бот) или standalone `Bot` (worker без app)
- `post_to_office()` — алиас для обратной совместимости
- `run_task()` — публичная обёртка над `handle_task()` с уведомлениями:
  - `📥 Принял задачу от X: ...` — перед выполнением
  - `✅ Задача выполнена: ...` — после выполнения
- Марта пишет в группу при каждом делегировании: `🔀 Делегирую → Агент: задача`
- `handle_task()` и `handle_message()` используют `post_to_group()` / `run_task()`
- `OFFICE_GROUP_ID` — в config, `.env.example`, документации

### v0.4.0 — 2026-05-27 `5490a57`
**Делегирование Марта → Агент → Пользователь**
- Полный цикл делегирования: Марта парсит ответ Клода, вызывает нужного агента, возвращает результат пользователю
- Структурированный формат `##DELEGATE##` в системном промпте
- `_parse_delegation()`, `_strip_delegate_block()`, `_get_agent()` — lazy pool агентов
- `handle_task()` тоже умеет делегировать (для вызовов агент→агент)
- Разбивка ответов > 4096 символов

### v0.3.0 — 2026-05-27 `65d0100`
**Веб-поиск для Каспера (Tavily)**
- `tools/search.py` — `async search_web()` через `AsyncTavilyClient`
- Топ-3 результата с синтезом, глубокий поиск (`advanced`)
- Каспер переопределяет `handle_message()` — поиск перед каждым Claude-вызовом
- Fallback без ключа — агент не падает
- `TAVILY_API_KEY` в config и `.env.example`

### v0.2.0 — 2026-05-27 `ad8525b`
**Память агентов через Redis**
- `redis.asyncio` — нативный async-клиент
- Ключи `history:{agent_name}:{chat_id}`, TTL 7 дней, лимит 20 сообщений
- Fallback на in-memory dict если Redis недоступен
- `/reset` удаляет из Redis + dict
- `_close_redis()` в `stop_async()` — чистый shutdown

### v0.1.1 — 2026-05-27 `3e19845`
**Все 6 агентов в одном процессе**
- PTB 22.x non-blocking API: `initialize()` + `start()` + `updater.start_polling()`
- `start_polling_async()` + `stop_async()` в BaseAgent
- Graceful shutdown: SIGTERM (Linux) + KeyboardInterrupt (Windows)
- `AGENT_NAME=all` (дефолт) → все агенты; задан → один агент

### v0.1.0 — 2026-05-27 `464e982`
**Первый релиз — базовая структура**
- 6 агентов на Claude API + python-telegram-bot 22.7
- `BaseAgent`: `think()` с AsyncAnthropic, `post_to_office()`, глобальный error handler
- Логирование входящих сообщений (`logger.info`)
- `Dockerfile`, `railway.toml`, `.dockerignore`, `.gitignore`
- `start_all.bat` для Windows
- Деплой на Railway (подготовлен)

---

## Дорожная карта

### ✅ Выполнено
- [x] Структура проекта — 6 агентов, BaseAgent, config
- [x] Python-telegram-bot 22.x совместимость
- [x] Все агенты в одном asyncio event loop
- [x] Логирование входящих сообщений и ответов
- [x] Глобальный PTB error handler (ошибки не теряются молча)
- [x] Redis-память: история диалогов с TTL и fallback
- [x] Веб-поиск Tavily для Каспера с fallback
- [x] Рабочий цикл делегирования Марта → Агент → Пользователь
- [x] Railway-деплой (Dockerfile, railway.toml, .dockerignore)
- [x] Git-репозиторий на GitHub
- [x] Общая Telegram-группа: уведомления о задачах и результатах

### 🔲 В планах
- [ ] Межагентное общение — агенты могут вызывать друг друга, не только через Марту
- [ ] Веб-поиск для других агентов (Питер, Кевин)
- [ ] Инструменты для Кевина — выполнение кода через Code Interpreter / sandbox
- [ ] Инструменты для Питера — парсинг CSV/Excel, построение таблиц
- [ ] Dashboard — веб-интерфейс со статусом агентов и историей задач
- [ ] Rate limiting — очередь задач при пиковой нагрузке
- [ ] Юнит-тесты (pytest) для каждого агента
- [ ] CI/CD — GitHub Actions → Railway auto-deploy
- [ ] Мониторинг — Sentry для ошибок, Prometheus для метрик

---

## Переменные окружения

```env
# Обязательные
ANTHROPIC_API_KEY=       # claude API
MARTA_BOT_TOKEN=         # @BotFather
KEVIN_BOT_TOKEN=
KASPER_BOT_TOKEN=
PETER_BOT_TOKEN=
ELINA_BOT_TOKEN=
ALEX_BOT_TOKEN=
OFFICE_GROUP_ID=         # ID группового чата (отрицательное число)

# Опциональные
CLAUDE_MODEL=claude-sonnet-4-6
TAVILY_API_KEY=          # веб-поиск Каспера (tavily.com)
REDIS_URL=               # память агентов (Railway: добавь плагин Redis)
WEBHOOK_BASE_URL=        # только для webhook-режима
PORT=8080
```

---

## Запуск

```bash
# Локально — все агенты
python main.py

# Локально — один агент
python main.py --agent marta

# Windows — все агенты в отдельных окнах
start_all.bat

# Railway (через Dockerfile)
# CMD: python main.py
# AGENT_NAME не задан → все 6 агентов
```
