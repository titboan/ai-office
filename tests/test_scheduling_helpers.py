"""
Тесты на общие хелперы расчёта времени для фоновых циклов main.py
(вынесены при рефакторинге ~18 циклов в run_scheduled_loop — regression-тест
на то, что расчёт "сколько ждать до следующего запуска" не сломался).
"""
import os
from datetime import datetime, timezone
from unittest.mock import patch

for _k in ("MARTA_BOT_TOKEN", "KASPER_BOT_TOKEN", "PETER_BOT_TOKEN", "ELINA_BOT_TOKEN",
           "ALEX_BOT_TOKEN", "MAX_BOT_TOKEN", "TINA_BOT_TOKEN", "ANTHROPIC_API_KEY",
           "GITHUB_TOKEN", "GITHUB_USERNAME", "DATABASE_URL"):
    os.environ.setdefault(_k, "x")

import main  # noqa: E402


def _at(iso: str):
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)


class _FixedDatetime(datetime):
    _now = None

    @classmethod
    def now(cls, tz=None):
        return cls._now


def _frozen(iso: str):
    _FixedDatetime._now = _at(iso)
    return patch("main.datetime", _FixedDatetime)


def test_wait_interval_returns_fixed_seconds():
    wait_fn = main._wait_interval(900)
    assert wait_fn() == 900
    assert wait_fn() == 900  # вызывается заново каждую итерацию — должно быть стабильно


def test_wait_daily_utc_later_today():
    with _frozen("2026-07-05T01:00:00"):
        wait_fn = main._wait_daily_utc(3, 0)
        assert wait_fn() == 2 * 3600  # 01:00 -> 03:00 сегодня


def test_wait_daily_utc_already_passed_rolls_to_tomorrow():
    with _frozen("2026-07-05T05:00:00"):
        wait_fn = main._wait_daily_utc(3, 0)
        assert wait_fn() == 22 * 3600  # 05:00 -> 03:00 завтра


def test_wait_daily_utc_exact_target_rolls_to_tomorrow():
    # target <= now — «уже наступило», ждём следующие сутки (не 0 секунд)
    with _frozen("2026-07-05T03:00:00"):
        wait_fn = main._wait_daily_utc(3, 0)
        assert wait_fn() == 24 * 3600


def test_wait_weekly_utc_same_day_before_hour():
    # 2026-07-06 — понедельник
    with _frozen("2026-07-06T05:00:00"):
        wait_fn = main._wait_weekly_utc(0, 7, 0)  # понедельник 07:00
        assert wait_fn() == 2 * 3600


def test_wait_weekly_utc_same_day_after_hour_rolls_to_next_week():
    with _frozen("2026-07-06T08:00:00"):
        wait_fn = main._wait_weekly_utc(0, 7, 0)
        assert wait_fn() == 7 * 24 * 3600 - 3600  # 08:00 сегодня -> 07:00 через неделю = 167ч


def test_wait_weekly_utc_different_day():
    # 2026-07-08 — среда, следующая суббота (weekday=5) через 3 дня
    with _frozen("2026-07-08T10:00:00"):
        wait_fn = main._wait_weekly_utc(5, 2, 0)
        expected = 3 * 24 * 3600 - 10 * 3600 + 2 * 3600
        assert wait_fn() == expected


def test_wait_weekly_utc_fin_sync_minute_boundary():
    # regression: воскресенье 01:29 — ещё не наступило 01:30, ждём в тот же день
    with _frozen("2026-07-05T01:29:00"):
        wait_fn = main._wait_weekly_utc(6, 1, 30)  # воскресенье 01:30
        assert wait_fn() == 60

    # воскресенье 01:30 ровно — уже наступило, ждём следующее воскресенье
    with _frozen("2026-07-05T01:30:00"):
        wait_fn = main._wait_weekly_utc(6, 1, 30)
        assert wait_fn() == 7 * 24 * 3600
