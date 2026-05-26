# 🏢 AI Office — Команда ИИ-агентов в Telegram

Виртуальный офис из 6 AI-агентов на базе **Claude API**, каждый из которых — отдельный Telegram-бот. Марта координирует команду, делегирует задачи и следит за результатом.

---

## 👥 Команда

| Агент | Роль | Emoji | Специализация |
|-------|------|-------|---------------|
| **Марта** | Координатор | 👩‍💼 | Принимает задачи, делегирует команде |
| **Кевин** | Разработчик | 👨‍💻 | Python, JS, архитектура, DevOps |
| **Каспер** | Исследователь | 🔍 | Поиск информации, отчёты, тренды |
| **Питер** | Аналитик | 📊 | Данные, SWOT, бизнес-анализ |
| **Элина** | Копирайтер | ✍️ | Тексты, посты, email, SEO |
| **Алекс** | Планировщик | 🗓️ | Стратегия, roadmap, OKR |

---

## 🚀 Быстрый старт

### 1. Клонируй репозиторий

```bash
git clone https://github.com/your-repo/ai-office.git
cd ai-office
```

### 2. Установи зависимости

```bash
pip install -r requirements.txt
```

### 3. Настрой переменные окружения

```bash
cp .env.example .env
# Отредактируй .env — вставь токены и ключи
```

### 4. Создай Telegram-ботов

1. Открой [@BotFather](https://t.me/BotFather) в Telegram
2. Создай 6 ботов командой `/newbot`
3. Скопируй токены в `.env`

### 5. Запусти в режиме разработки

```bash
# Все агенты сразу
python main.py

# Только один агент
python main.py --agent marta
```

---

## ⚙️ Команды агентов

### Марта (координатор)
- `/start` — приветствие
- `/delegate <задача>` — делегировать задачу команде
- `/reset` — очистить историю

### Кевин (разработчик)
- `/code <что написать>` — написать код

### Каспер (исследователь)
- `/research <тема>` — исследовать тему

### Питер (аналитик)
- `/analyze <данные>` — проанализировать

### Элина (копирайтер)
- `/write <бриф>` — написать текст
- `/post <тема>` — создать Telegram-пост

### Алекс (планировщик)
- `/plan <цель>` — составить план
- `/roadmap <проект>` — дорожная карта

---

## 🚂 Деплой на Railway

### Архитектура на Railway

Каждый агент — **отдельный Railway Service** внутри одного проекта:

```
Railway Project: AI Office
├── Service: marta   (AGENT_NAME=marta)
├── Service: kevin   (AGENT_NAME=kevin)
├── Service: kasper  (AGENT_NAME=kasper)
├── Service: peter   (AGENT_NAME=peter)
├── Service: elina   (AGENT_NAME=elina)
└── Service: alex    (AGENT_NAME=alex)
```

### Шаги деплоя

1. **Создай проект на Railway** → [railway.app](https://railway.app)

2. **Добавь сервисы** — по одному на каждого агента:
   - Source: GitHub repo
   - Environment variables — добавь все из `.env`
   - Добавь `AGENT_NAME=marta` (и так для каждого)

3. **Переменные среды** (общие для всех сервисов):
```
ANTHROPIC_API_KEY=...
MARTA_BOT_TOKEN=...
KEVIN_BOT_TOKEN=...
KASPER_BOT_TOKEN=...
PETER_BOT_TOKEN=...
ELINA_BOT_TOKEN=...
ALEX_BOT_TOKEN=...
OFFICE_GROUP_ID=...
WEBHOOK_BASE_URL=https://<railway-domain>
```

4. **Railway автоматически** назначит `PORT` и `RAILWAY_PUBLIC_DOMAIN`.

---

## 📁 Структура проекта

```
ai-office/
├── main.py              # Точка входа, маршрутизация агентов
├── config.py            # Конфигурация из .env
├── agents/
│   ├── __init__.py
│   ├── base_agent.py    # Базовый класс (Claude + Telegram)
│   ├── marta.py         # 👩‍💼 Координатор
│   ├── kevin.py         # 👨‍💻 Разработчик
│   ├── kasper.py        # 🔍 Исследователь
│   ├── peter.py         # 📊 Аналитик
│   ├── elina.py         # ✍️ Копирайтер
│   └── alex.py          # 🗓️ Планировщик
├── requirements.txt
├── Dockerfile
├── railway.toml
├── .env.example
└── README.md
```

---

## 🔧 Технологии

| Компонент | Технология |
|-----------|-----------|
| LLM | Claude API (claude-sonnet-4-6) |
| Telegram | python-telegram-bot 20.7 |
| Среда выполнения | Python 3.11 |
| Контейнеризация | Docker |
| Хостинг | Railway |

---

## 📝 Лицензия

MIT
