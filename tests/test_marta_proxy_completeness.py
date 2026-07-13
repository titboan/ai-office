"""
Регрессия для Фазы 2 плана "единственная точка входа — Марта":

Для каждого агента из main.py::AGENTS (кроме самой Марты) собираем множество
команд (CommandHandler) и callback-паттернов (CallbackQueryHandler), которые
он регистрирует на СВОЁМ боте, и сверяем с тем, что зарегистрировано на боте
Марты. Если у агента появилась новая команда/кнопка, а Марта её не зеркалит —
пользователь, который живёт только в чате Марты, не сможет ей воспользоваться.
Красный тест здесь = кто-то добавил команду агенту и забыл прокси на Марте.

build_app() (agents/base_agent.py) только регистрирует PTB-хендлеры — сети не
трогает (не делает getMe(), не открывает HTTP-сессию), поэтому дергать его
безопасно с фиктивными токенами и без .initialize().
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
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

import main as main_module

# ── Исключения ────────────────────────────────────────────────────────────
#
# Команды, которые намеренно НЕ проксируются Мартой — у неё своя реализация
# с тем же именем (start/help/reset — обязательный базовый набор BaseAgent,
# у каждого агента свой обработчик), либо это собственное UI-меню агента
# (у Марты есть свой /menu → mmenu:-callback'и, это не то же самое меню).
ALLOWED_AGENT_ONLY_COMMANDS: set[str] = {
    "start",   # базовая команда BaseAgent — у Марты свой cmd_start
    "help",    # базовая команда BaseAgent — у Марты свой cmd_help (+ proxy /help остаётся у Макса тоже)
    "reset",   # базовая команда BaseAgent — у Марты свой cmd_reset
    "menu",    # у Макса и Питера свой /menu (inline "menu_"), у Марты отдельное /menu → mmenu:
    # Известный пре-существующий разрыв поведения (не отсутствие прокси):
    # Max.cmd_cancel сбрасывает мастер /add и /cost (catalog_add/catalog_cost).
    # Marta.cmd_cancel отменяет задачу из очереди по task_id — команда "cancel"
    # уже присутствует на Марте (просто с другой семантикой), поэтому
    # set-based проверка ниже её не ловит; фиксируем здесь как заметку.
    # См. отчёт по задаче — Task A прокидывает *ввод текста* мастеров, но
    # /cancel во время мастера через Марту сегодня попадёт в cmd_cancel, а не
    # в сброс catalog_add/catalog_cost. Не чиним в рамках этой задачи.
}

# Callback-паттерны, которые намеренно не зеркалятся — привязаны к
# собственному inline-меню агента (Max.cmd_menu / "menu_"), а не к прокси-командам.
ALLOWED_AGENT_ONLY_CALLBACK_PATTERNS: set[str] = {
    r"^menu_",  # инлайн-меню /menu Макса (и Питера, у которых своя команда menu, но без callback)
}

# Агенты, которых main.py::AGENTS вообще не регистрирует (заморожены) —
# нет смысла сравнивать: у Марты для них есть только заглушки "заморожен".
FROZEN_AGENTS_NOT_IN_REGISTRY = {"kevin", "dan", "eva"}


def _build_app(agent_cls) -> Application:
    """Строит Application агента без сети: build_app() только регистрирует
    PTB-хендлеры (см. agents/base_agent.py::build_app), initialize()/start()
    не вызываются."""
    agent = agent_cls()
    return agent.build_app()


def _collect_commands(app: Application) -> set[str]:
    names: set[str] = set()
    for handlers in app.handlers.values():
        for h in handlers:
            if isinstance(h, CommandHandler):
                names |= set(h.commands)
    return names


def _collect_callback_patterns(app: Application) -> set[str]:
    patterns: set[str] = set()
    for handlers in app.handlers.values():
        for h in handlers:
            if isinstance(h, CallbackQueryHandler) and h.pattern is not None:
                p = h.pattern
                patterns.add(p.pattern if hasattr(p, "pattern") else str(p))
    return patterns


@pytest.fixture(scope="module")
def marta_app() -> Application:
    from agents.marta import MartaAgent
    return _build_app(MartaAgent)


@pytest.fixture(scope="module")
def marta_commands(marta_app) -> set[str]:
    return _collect_commands(marta_app)


@pytest.fixture(scope="module")
def marta_callback_patterns(marta_app) -> set[str]:
    return _collect_callback_patterns(marta_app)


_OTHER_AGENT_KEYS = sorted(k for k in main_module.AGENTS if k != "marta")


def test_registry_has_expected_non_frozen_agents():
    """Sanity: убеждаемся что main.AGENTS не содержит замороженных агентов
    (иначе сравнение ниже молчаливо ничего бы не проверяло)."""
    assert set(main_module.AGENTS) & FROZEN_AGENTS_NOT_IN_REGISTRY == set()
    assert "marta" in main_module.AGENTS
    assert len(_OTHER_AGENT_KEYS) >= 1


@pytest.mark.parametrize("agent_key", _OTHER_AGENT_KEYS)
def test_agent_commands_are_proxied_on_marta(agent_key, marta_commands):
    agent_cls, _ = main_module.AGENTS[agent_key]
    app = _build_app(agent_cls)
    agent_commands = _collect_commands(app) - ALLOWED_AGENT_ONLY_COMMANDS
    missing = agent_commands - marta_commands
    assert not missing, (
        f"Агент {agent_key!r} зарегистрировал команды без зеркала у Марты: "
        f"{sorted(missing)} — добавь CommandHandler в "
        f"MartaAgent._register_extra_handlers или занеси в ALLOWED_AGENT_ONLY_COMMANDS."
    )


@pytest.mark.parametrize("agent_key", _OTHER_AGENT_KEYS)
def test_agent_callback_patterns_are_proxied_on_marta(agent_key, marta_callback_patterns):
    agent_cls, _ = main_module.AGENTS[agent_key]
    app = _build_app(agent_cls)
    agent_patterns = _collect_callback_patterns(app) - ALLOWED_AGENT_ONLY_CALLBACK_PATTERNS
    missing = agent_patterns - marta_callback_patterns
    assert not missing, (
        f"Агент {agent_key!r} зарегистрировал callback-паттерны без зеркала у Марты: "
        f"{sorted(missing)} — добавь CallbackQueryHandler в "
        f"MartaAgent._register_extra_handlers или занеси в ALLOWED_AGENT_ONLY_CALLBACK_PATTERNS."
    )
