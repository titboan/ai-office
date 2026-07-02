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

    # Ева — Email дайджест (ящик 1 Gmail, ящик 2 Яндекс)
    EMAIL_IMAP_HOST:   str = os.getenv("EMAIL_IMAP_HOST",   "imap.gmail.com")
    EMAIL_USER:        str = os.getenv("EMAIL_USER",        "")
    EMAIL_APP_PASS:    str = os.getenv("EMAIL_APP_PASS",    "")
    EMAIL_IMAP_HOST_2: str = os.getenv("EMAIL_IMAP_HOST_2", "imap.yandex.ru")
    EMAIL_USER_2:      str = os.getenv("EMAIL_USER_2",      "")
    EMAIL_APP_PASS_2:  str = os.getenv("EMAIL_APP_PASS_2",  "")
    # IMAP proxy (Fly.io) — Railway не может подключиться к IMAP напрямую (порт 993)
    # Локально: оставь пустым — будет прямое IMAP-подключение
    IMAP_PROXY_URL:    str = os.getenv("IMAP_PROXY_URL",    "")
    IMAP_PROXY_SECRET: str = os.getenv("IMAP_PROXY_SECRET", "")

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
config.OWNER_CHAT_ID = int(os.getenv("OWNER_CHAT_ID", "0"))   # Telegram user_id владельца (чьи данные показывать)
config.DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "")     # секретный токен для доступа коллег по ссылке

# Алерты остатков
config.STOCK_ALERT_DAYS_THRESHOLD = 21   # слать алерт если остаток < 21 дня продаж
config.STOCK_ALERT_HOUR_UTC       = 10   # 10:00 UTC = 13:00 МСК
config.SUPPLY_LEAD_TIME_DAYS      = 21   # дней от заказа производителю до поставки на склад МП
config.SUPPLY_SAFETY_STOCK_DAYS   = 14   # страховой буфер после поставки (на непредвиденное)

# Статусы поставок (marketplace_supply_orders), которые считаются "уже в процессе"
# и вычитаются из рекомендуемого количества дозаказа (supply_committed).
# Единый источник для всех расчётов остатков/дозаказа — db.get_supply_pipeline().
config.SUPPLY_COMMITTED_STATUSES_WB   = {
    "Запланировано", "Отгрузка разрешена", "В пути", "Транзит", "Идёт приёмка",
}
config.SUPPLY_COMMITTED_STATUSES_OZON = {
    "Новая", "Готова к отгрузке", "В пути", "Принята на склад",
}

config.WB_FOOD_CATEGORIES = ["лакомства", "корм"]   # категории товаров с ограниченными WB-складами

# Аналитика рентабельности
config.NET_MARGIN_TAX_RATE = 0.08   # налог УСН "доходы" (повышенная ставка), % от выплаты МП
config.TARGET_NET_MARGIN_PCT = 50.0 # целевая NET-маржа, % — порог для рекомендованной цены

# Ежедневный дайджест Питера
config.DAILY_DIGEST_HOUR_UTC = 18   # 18:00 UTC = 21:00 МСК

# SEO-алерты позиций (WB search keywords)
config.SEO_POSITION_DROP_THRESHOLD = 10  # алерт если позиция упала на ≥10 мест

# Авто-управление рекламными кампаниями Ozon
config.DRR_PAUSE_THRESHOLD_OZON    = 40  # % ДРР — предложить паузу кампании Ozon
config.DRR_ALERT_THRESHOLD         = 25  # % ДРР — алерт без действия (оба маркетплейса)
config.DRR_MIN_SPEND_FOR_ACTION    = 200 # ₽ минимальный расход за 7д для предложения
config.OZON_CAMPAIGN_INITIAL_BID   = 30  # ₽ начальная ставка per-SKU для новых кампаний
config.OZON_CAMPAIGN_DEFAULT_BUDGET = 500 # ₽/день бюджет по умолчанию для новой кампании

# Мониторинг цен конкурентов (WB публичный поиск, еженедельно)
config.COMPETITOR_SCAN_HOUR_UTC = 6  # 06:00 UTC = 09:00 МСК, каждый понедельник
config.COMPETITOR_KEYWORDS = [       # ниша DoggyDog — ключи для снапшота топ-100 WB
    "лакомства для собак говяжье лёгкое",
    "бычий корень для собак",
    "трахея говяжья для собак",
    "корм холистик для собак говядина",
    "корм холистик для собак индейка",
]

# Контекст компании — добавляется в system prompt всех агентов
config.COMPANY_CONTEXT = """--- Контекст компании ---
Бренд: DoggyDog
Маркетплейсы: Wildberries (WB) и Ozon
Схема работы: FBO (хранение и отгрузка силами маркетплейса), упаковка и перепродажа
Регион: Краснодарский край, отгрузка по всей России

Товары:
- Лакомства для собак (сушёные): говяжье лёгкое, бычий корень, трахея говяжья
- Корм холистик для собак: говядина, индейка

При анализе данных, написании текстов и ответах на вопросы учитывай этот контекст.
Не нужно объяснять, что ты знаешь о компании — просто используй эти данные в работе."""
