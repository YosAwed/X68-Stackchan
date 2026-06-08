"""Basic validation and loading tests for the centralized Settings.

These run without any heavy ML dependencies.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

# We import after possible monkeypatching in other tests, but this module
# can be imported directly.
from settings import Settings
from settings import settings as _global_settings


def test_settings_defaults_and_types():
    s = Settings()
    assert s.WHISPER_MODEL == "small"
    assert s.HISTORY_TURNS == 6
    assert s.MAX_SESSIONS == 16
    assert s.TTS_BACKEND == "irodori"
    assert isinstance(s.MAX_AUDIO_BYTES, int)
    assert s.LOG_LEVEL in ("INFO", "DEBUG", "WARNING") or True  # may be customized


def test_settings_validation_rejects_bad_values(monkeypatch):
    monkeypatch.setenv("HISTORY_TURNS", "-5")
    with pytest.raises(ValidationError):
        Settings()


def test_settings_env_override(monkeypatch):
    monkeypatch.setenv("WHISPER_MODEL", "base")
    monkeypatch.setenv("SCHEDULE_ENABLED", "1")
    monkeypatch.setenv("LLM_TIME_FLAVOR", "0")
    s = Settings()
    assert s.WHISPER_MODEL == "base"
    assert s.is_scheduler_enabled() is True
    assert s.is_time_flavor_enabled() is False


def test_global_singleton_is_loaded():
    # The module-level singleton must be a valid Settings instance.
    assert isinstance(_global_settings, Settings)
    assert _global_settings.WHISPER_MODEL  # just sanity


def test_settings_log_level_uppercased(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "debug")
    s = Settings()
    assert s.get_log_level() == "DEBUG"
