"""
Регрессия для Фазы 1 плана "единственная точка входа — Марта":
_notify_user по умолчанию (без явного bot_token=) отвечает пользователю через
собственный бот агента (self.bot_token) — это нужно для нативных интерактивных
флоу (например, онбординг-мастер Макса). Маршрутизация через бот Марты
происходит только там, где вызывающий код явно передаёт bot_token= (worker
loop на завершении задачи, продвижение цепочки, напоминания, дайджесты).
"""
import os

for _k, _v in (
    ("MARTA_BOT_TOKEN", "marta-token"),
    ("KASPER_BOT_TOKEN", "kasper-token"),
    ("PETER_BOT_TOKEN", "peter-token"),
    ("ELINA_BOT_TOKEN", "elina-token"),
    ("ALEX_BOT_TOKEN", "alex-token"),
    ("MAX_BOT_TOKEN", "max-token"),
    ("TINA_BOT_TOKEN", "tina-token"),
    ("ANTHROPIC_API_KEY", "x"),
    ("GITHUB_TOKEN", "x"),
    ("GITHUB_USERNAME", "x"),
    ("DATABASE_URL", "x"),
):
    os.environ.setdefault(_k, _v)

import pytest

import agents.base_agent as base_agent_module
from agents.base_agent import BaseAgent


class _DummyAgent(BaseAgent):
    def __init__(self, bot_token: str, agent_key: str, name: str = "Тест", emoji: str = "🤖"):
        self.bot_token = bot_token
        self.agent_key = agent_key
        self.name = name
        self.emoji = emoji
        self.app = None

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        return "dummy"


@pytest.mark.asyncio
async def test_notify_user_defaults_to_own_bot_when_not_overridden(monkeypatch):
    """Без явного bot_token= _notify_user должен слать через собственный бот агента
    (нужно для нативных флоу вроде онбординг-мастера Макса на его собственном боте)."""
    monkeypatch.setattr(base_agent_module.config, "MARTA_BOT_TOKEN", "marta-token", raising=False)
    agent = _DummyAgent(bot_token="max-token", agent_key="max")

    captured = {}

    async def _fake_send_rich(token, chat_id, text, reply_markup_dict=None, reply_to_message_id=None):
        captured["token"] = token
        return True

    monkeypatch.setattr(base_agent_module, "_send_rich", _fake_send_rich)
    ok = await agent._notify_user(12345, "привет")

    assert ok is True
    assert captured["token"] == "max-token"
    assert captured["token"] == agent.bot_token


@pytest.mark.asyncio
async def test_notify_user_respects_explicit_bot_token_override(monkeypatch):
    """Если bot_token передан явно — используем именно его (например, при прокси через Марту с её же токеном)."""
    monkeypatch.setattr(base_agent_module.config, "MARTA_BOT_TOKEN", "marta-token", raising=False)
    agent = _DummyAgent(bot_token="max-token", agent_key="max")

    captured = {}

    async def _fake_send_rich(token, chat_id, text, reply_markup_dict=None, reply_to_message_id=None):
        captured["token"] = token
        return True

    monkeypatch.setattr(base_agent_module, "_send_rich", _fake_send_rich)
    ok = await agent._notify_user(12345, "привет", bot_token="explicit-token")

    assert ok is True
    assert captured["token"] == "explicit-token"
