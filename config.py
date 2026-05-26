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
    # Claude
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

    # Telegram токены агентов
    MARTA_BOT_TOKEN: str = os.getenv("MARTA_BOT_TOKEN", "")
    KEVIN_BOT_TOKEN: str = os.getenv("KEVIN_BOT_TOKEN", "")
    KASPER_BOT_TOKEN: str = os.getenv("KASPER_BOT_TOKEN", "")
    PETER_BOT_TOKEN: str = os.getenv("PETER_BOT_TOKEN", "")
    ELINA_BOT_TOKEN: str = os.getenv("ELINA_BOT_TOKEN", "")
    ALEX_BOT_TOKEN: str = os.getenv("ALEX_BOT_TOKEN", "")

    # Общая группа офиса
    OFFICE_GROUP_ID: int = int(os.getenv("OFFICE_GROUP_ID", "0"))

    # Webhook (Railway)
    WEBHOOK_BASE_URL: str = os.getenv("WEBHOOK_BASE_URL", "")
    PORT: int = int(os.getenv("PORT", "8080"))

    # Лимиты
    MAX_TOKENS: int = 2048
    TEMPERATURE: float = 0.7

    @classmethod
    def validate(cls) -> None:
        required = [
            ("ANTHROPIC_API_KEY", cls.ANTHROPIC_API_KEY),
            ("MARTA_BOT_TOKEN", cls.MARTA_BOT_TOKEN),
            ("OFFICE_GROUP_ID", cls.OFFICE_GROUP_ID),
        ]
        missing = [name for name, val in required if not val]
        if missing:
            raise ValueError(f"Отсутствуют обязательные переменные: {', '.join(missing)}")


config = Config()
