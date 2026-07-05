"""
Тест на BaseAgent._format_chain_completion_message — вынесено из _advance_chain
при рефакторинге (плановая Фаза 1: убрать форматирование из оркестрации).
"""
import os

for _k in ("MARTA_BOT_TOKEN", "KASPER_BOT_TOKEN", "PETER_BOT_TOKEN", "ELINA_BOT_TOKEN",
           "ALEX_BOT_TOKEN", "MAX_BOT_TOKEN", "TINA_BOT_TOKEN", "ANTHROPIC_API_KEY",
           "GITHUB_TOKEN", "GITHUB_USERNAME", "DATABASE_URL"):
    os.environ.setdefault(_k, "x")

from agents.base_agent import BaseAgent  # noqa: E402


def test_no_results_gives_generic_message_no_keyboard():
    msg, kb = BaseAgent._format_chain_completion_message([])
    assert "завершила работу" in msg
    assert kb is None


def test_results_formatted_per_agent_with_emoji_and_name():
    results = [
        {"assigned_agent": "kasper", "result": "Нашёл 5 конкурентов"},
        {"assigned_agent": "elina", "result": "Написала текст поста"},
    ]
    msg, kb = BaseAgent._format_chain_completion_message(results)
    assert "🔍 **Каспер:** Нашёл 5 конкурентов" in msg
    assert "✍️ **Элина:** Написала текст поста" in msg
    assert kb is None


def test_unknown_agent_falls_back_to_robot_emoji_and_raw_key():
    results = [{"assigned_agent": "unknown_agent", "result": "что-то сделал"}]
    msg, _ = BaseAgent._format_chain_completion_message(results)
    assert "🤖 **unknown_agent:** что-то сделал" in msg


def test_long_result_truncated_with_ellipsis():
    long_text = "a" * 250
    results = [{"assigned_agent": "peter", "result": long_text}]
    msg, _ = BaseAgent._format_chain_completion_message(results)
    assert ("a" * 200 + "…") in msg
    assert ("a" * 201) not in msg


def test_html_tags_stripped_from_excerpt():
    results = [{"assigned_agent": "peter", "result": "<b>жирный</b> текст"}]
    msg, _ = BaseAgent._format_chain_completion_message(results)
    assert "<b>" not in msg
    assert "жирный текст" in msg


def test_github_pages_url_produces_button_and_link():
    # trailing "/" обрезается rstrip'ом — так же, как в исходном коде
    results = [{"assigned_agent": "kevin", "result": "Готово: https://myuser.github.io/myproject/ смотри"}]
    msg, kb = BaseAgent._format_chain_completion_message(results)
    assert "🌐 [Открыть сайт](https://myuser.github.io/myproject)" in msg
    assert kb is not None
    assert kb.inline_keyboard[0][0].url == "https://myuser.github.io/myproject"


def test_repo_url_used_as_fallback_when_no_pages():
    results = [{"assigned_agent": "kevin", "result": "PR тут: https://github.com/myuser/myrepo готово"}]
    msg, kb = BaseAgent._format_chain_completion_message(results)
    assert "📦 [Репозиторий](https://github.com/myuser/myrepo)" in msg
    assert kb is None  # кнопка только для Pages, не для голого репо


def test_pages_url_preferred_over_repo_url_when_both_present():
    results = [{
        "assigned_agent": "kevin",
        "result": "Репо: https://github.com/myuser/myrepo Сайт: https://myuser.github.io/myrepo/",
    }]
    msg, kb = BaseAgent._format_chain_completion_message(results)
    assert "🌐 [Открыть сайт]" in msg
    assert "📦 [Репозиторий]" not in msg
    assert kb is not None
