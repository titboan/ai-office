import os
import sys
from dotenv import load_dotenv

load_dotenv()

# Кириллица в логах на Windows — принудительно UTF-8
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


class Config:
    # Claude models
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    CLAUDE_HAIKU_MODEL: str = "claude-haiku-4-5-20251001"
    CLAUDE_OPUS_MODEL: str = "claude-opus-4-8"

    # Telegram токены агентов
    MARTA_BOT_TOKEN: str = os.getenv("MARTA_BOT_TOKEN", "")
    KEVIN_BOT_TOKEN: str = os.getenv("KEVIN_BOT_TOKEN", "")
    KASPER_BOT_TOKEN: str = os.getenv("KASPER_BOT_TOKEN", "")
    PETER_BOT_TOKEN: str = os.getenv("PETER_BOT_TOKEN", "")
    ELINA_BOT_TOKEN: str = os.getenv("ELINA_BOT_TOKEN", "")
    ALEX_BOT_TOKEN: str = os.getenv("ALEX_BOT_TOKEN", "")
    DEN_BOT_TOKEN: str = os.getenv("DEN_BOT_TOKEN", "")

    # Общая группа офиса
    OFFICE_GROUP_ID: int = int(os.getenv("OFFICE_GROUP_ID", "0"))

    # Веб-поиск (Каспер)
    TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")

    # Голосовые сообщения — Groq Whisper API (бесплатно: https://console.groq.com)
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")

    # База данных (task queue)
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")

    # Память агентов (Redis)
    # Если не задан — агенты хранят историю в памяти процесса (fallback dict)
    REDIS_URL: str = os.getenv("REDIS_URL", "")

    # Webhook (Railway)
    WEBHOOK_BASE_URL: str = os.getenv("WEBHOOK_BASE_URL", "")
    PORT: int = int(os.getenv("PORT", "8080"))

    # Notion Integration
    NOTION_TOKEN: str = os.getenv("NOTION_TOKEN", "")
    NOTION_PARENT_PAGE_ID: str = os.getenv("NOTION_PARENT_PAGE_ID", "")
    NOTION_PROJECTS_DB: str = os.getenv("NOTION_PROJECTS_DB", "")
    NOTION_TASKS_DB: str = os.getenv("NOTION_TASKS_DB", "")
    NOTION_IDEAS_DB: str = os.getenv("NOTION_IDEAS_DB", "")
    NOTION_RESEARCH_DB: str = os.getenv("NOTION_RESEARCH_DB", "")
    NOTION_CONTENT_DB: str = os.getenv("NOTION_CONTENT_DB", "")

    # Push-уведомления (ntfy.sh)
    NTFY_TOPIC: str = os.getenv("NTFY_TOPIC", "").strip()  # напр. "ai-office-tba"

    # GitHub (Кевин)
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
    GITHUB_USERNAME: str = os.getenv("GITHUB_USERNAME", "")

    # Макс — отзывы на маркетплейсах
    MAX_BOT_TOKEN: str = os.getenv("MAX_BOT_TOKEN", "")
    PARTNERS_GROUP_ID: int = int(os.getenv("PARTNERS_GROUP_ID", "0"))

    # Ева — Telethon MTProto + бот
    EVA_BOT_TOKEN: str = os.getenv("EVA_BOT_TOKEN", "")
    TELEGRAM_API_ID: str = os.getenv("TELEGRAM_API_ID", "")
    TELEGRAM_API_HASH: str = os.getenv("TELEGRAM_API_HASH", "")
    TELETHON_SESSION: str = os.getenv("TELETHON_SESSION", "")

    # Тина — тендерный агент (44-ФЗ, Краснодарский край)
    TINA_BOT_TOKEN: str = os.getenv("TINA_BOT_TOKEN", "")
    GOSPLAN_API_KEY: str = os.getenv("GOSPLAN_API_KEY", "")  # обязателен с 01.08.2026

    # Лимиты
    MAX_TOKENS: int = 2048
    TEMPERATURE: float = 0.7

    @classmethod
    def validate(cls) -> None:
        # Единственная переменная, без которой не работает ни один агент
        if not cls.ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY не задан — без него агенты не смогут думать")


config = Config()

# ─── CONSTANTS ────────────────────────────────────────────────────────────────
# Тендеры (44-ФЗ, Краснодарский край)
# Не хардкодить — менять только здесь

config.TENDER_REGION_CODE        = "23"          # ОКТМО Краснодарского края
config.TENDER_MIN_NMCK           = 100_000        # мин. НМЦК для поиска (руб)
config.TENDER_MAX_NMCK           = 5_000_000      # макс. НМЦК для поиска (руб)
config.TENDER_AVG_PRICE_REDUCTION = 0.28          # средний демпинг по 44-ФЗ (~28%)
config.TENDER_SCAN_HOUR_UTC      = 5             # 08:00 МСК = 05:00 UTC
config.TENDER_KEYWORDS           = [             # ключевые слова для ежедневного поиска
    "матрасы",
    "постельное белье",
    "мебель",
    "текстиль",
]

# Дашборд (Telegram Mini App)
config.DASHBOARD_URL = os.getenv("DASHBOARD_URL", "")   # Vercel URL фронтенда (CORS origin)
