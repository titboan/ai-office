"""Единый реестр агентов: имя, эмодзи, таймаут задачи по умолчанию.

Единая точка правды для всех мест, где раньше были разбросаны инлайн-словари
{agent_key → эмодзи/имя} — base_agent.py, marta.py и др. Добавление нового
агента = одна правка здесь, а не поиск по всем файлам.
"""

AGENTS: dict[str, dict] = {
    "marta":  {"name": "Марта",  "emoji": "👩‍💼", "timeout": 300},
    "kevin":  {"name": "Кевин",  "emoji": "👨‍💻", "timeout": 300},
    "kasper": {"name": "Каспер", "emoji": "🔍",  "timeout": 300},
    "peter":  {"name": "Питер",  "emoji": "📊",  "timeout": 300},
    "elina":  {"name": "Элина",  "emoji": "✍️",  "timeout": 300},
    "alex":   {"name": "Алекс",  "emoji": "🗓️",  "timeout": 300},
    "dan":    {"name": "Дэн",    "emoji": "🎨",  "timeout": 600},
    "eva":    {"name": "Ева",    "emoji": "📰",  "timeout": 300},
    "max":    {"name": "Макс",   "emoji": "🛒",  "timeout": 300},
    "tina":   {"name": "Тина",   "emoji": "🏛️",  "timeout": 300},
}


def agent_emoji(key: str, default: str = "🤖") -> str:
    return AGENTS.get(key, {}).get("emoji", default)


def agent_name(key: str, default: str | None = None) -> str:
    return AGENTS.get(key, {}).get("name", default if default is not None else key)


def agent_label(key: str, default: str | None = None) -> str:
    a = AGENTS.get(key)
    if not a:
        return default if default is not None else key
    return f"{a['emoji']} {a['name']}"


def agent_timeout(key: str, default: int = 300) -> int:
    return AGENTS.get(key, {}).get("timeout", default)
