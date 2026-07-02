"""Единый реестр агентов: имя, эмодзи, таймаут задачи по умолчанию.

Раньше эти данные были продублированы в base_agent.py, marta.py (несколько мест)
и tools/tg_status.py — с расхождениями между копиями (например, эмодзи Тины: 📋
в одних местах, 🏛️ в других; Ева/Макс отсутствовали в части копий). Теперь один
источник истины — добавление нового агента правится в одном месте.
"""

AGENTS: dict[str, dict] = {
    "marta":  {"name": "Марта",  "emoji": "👩‍💼", "timeout": 300},
    "kasper": {"name": "Каспер", "emoji": "🔍",  "timeout": 300},
    "kevin":  {"name": "Кевин",  "emoji": "👨‍💻", "timeout": 300},
    "peter":  {"name": "Питер",  "emoji": "📊",  "timeout": 300},
    "elina":  {"name": "Элина",  "emoji": "✍️",  "timeout": 300},
    "alex":   {"name": "Алекс",  "emoji": "🗓️",  "timeout": 300},
    "dan":    {"name": "Дэн",    "emoji": "🎨",  "timeout": 600},
    "eva":    {"name": "Ева",    "emoji": "📰",  "timeout": 300},
    "max":    {"name": "Макс",   "emoji": "🛒",  "timeout": 300},
    "tina":   {"name": "Тина",   "emoji": "📋",  "timeout": 300},
}

# Псевдо-агент для рендера строк дайджеста внутри результатов цепочки —
# не зарегистрирован как настоящий BaseAgent, но встречается в assigned_agent.
_DIGEST_PSEUDO_AGENT = {"name": "Дайджест", "emoji": "📰"}


def agent_emoji(agent_key: str) -> str:
    if agent_key == "digest":
        return _DIGEST_PSEUDO_AGENT["emoji"]
    return AGENTS.get(agent_key, {}).get("emoji", "🤖")


def agent_name(agent_key: str) -> str:
    if agent_key == "digest":
        return _DIGEST_PSEUDO_AGENT["name"]
    return AGENTS.get(agent_key, {}).get("name", agent_key)


def agent_timeout(agent_key: str) -> int:
    return AGENTS.get(agent_key, {}).get("timeout", 300)
