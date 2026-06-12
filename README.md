# 🏢 AI Office

Команда из 10 AI-агентов на базе **Claude API**, управляемых через Telegram. Автоматизирует маркетплейс-бизнес (WB + Ozon): отвечает на отзывы, строит аналитику, пишет тексты, создаёт сайты, мониторит тендеры. Один Python процесс на Railway.

→ Полная документация, roadmap и антипаттерны: **[ROADMAP.md](ROADMAP.md)**
→ Интерактивная схема архитектуры: **[docs/index.html](docs/index.html)**

---

## Архитектура

```mermaid
graph TD
    TG[📱 Telegram] --> MARTA

    subgraph Агенты
        MARTA[👩‍💼 Марта\nКоординатор]
        KEVIN[👨‍💻 Кевин\nРазработчик]
        KASPER[🔍 Каспер\nИсследователь]
        PETER[📊 Питер\nАналитик]
        ELINA[✍️ Элина\nКопирайтер]
        ALEX[🗓️ Алекс\nПланировщик]
        DAN[🎨 Дэн\nДизайнер]
        EVA[📰 Ева\nДайджест]
        MAX[🛒 Макс\nМаркетплейсы]
        TINA[🏛️ Тина\nТендеры]
    end

    MARTA -->|делегирует| KEVIN
    MARTA -->|делегирует| KASPER
    MARTA -->|делегирует| PETER
    MARTA -->|делегирует| ELINA
    MARTA -->|делегирует| ALEX
    MARTA -->|делегирует| DAN
    MARTA -->|делегирует| EVA
    MARTA -->|делегирует| MAX
    MARTA -->|делегирует| TINA

    KASPER -->|цепочка| DAN
    DAN -->|цепочка| KEVIN

    subgraph Хранилище
        PG[(PostgreSQL\nTask Queue)]
        REDIS[(Redis\nИстория)]
    end

    MARTA --- PG
    KEVIN --- PG
    KASPER --- PG
    PETER --- PG
    ELINA --- PG
    ALEX --- PG
    DAN --- PG
    EVA --- PG
    MAX --- PG
    TINA --- PG

    subgraph Интеграции
        GH[GitHub API]
        NOTION[Notion]
        WB[Wildberries API]
        OZON[Ozon API]
        TAVILY[Tavily Search]
        POLLS[Pollinations.ai]
        GOSPLAN[ГосПлан API]
        NTFY[ntfy.sh]
    end

    KEVIN --> GH
    KASPER --> TAVILY
    KASPER --> NOTION
    PETER --> NOTION
    ELINA --> NOTION
    ALEX --> NOTION
    ALEX --> NTFY
    DAN --> POLLS
    DAN --> GH
    EVA --> NOTION
    MAX --> WB
    MAX --> OZON
    TINA --> GOSPLAN
    TINA --> TAVILY
    MARTA --> NOTION
```

---

## Быстрый старт

```bash
git clone https://github.com/titboan/ai-office.git && cd ai-office
pip install -r requirements.txt
cp .env.example .env   # вставь токены
python main.py         # запускает все агенты
```

Деплой: Railway — один сервис, все переменные из `.env`, `railway up`.
