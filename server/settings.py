"""Centralized, validated application settings using pydantic-settings.

All environment variables are declared here with types and defaults that match
the previous scattered os.getenv calls + .env.example.

Benefits:
- Validation at import / startup time (e.g. negative HISTORY_TURNS will raise).
- Single source of truth + nice repr / .model_dump() for debugging.
- Existing .env files continue to work unchanged.
- python-dotenv is no longer required for runtime loading (pydantic-settings handles .env),
  but remains in requirements for test monkeypatching convenience.

Usage:
    from settings import settings
    print(settings.WHISPER_MODEL)
    if settings.is_scheduler_enabled(): ...
"""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ---- TTS backend selection ----
    # irodori  = CUDA in-process (Irodori-TTS-Lite)
    # voicevox = HTTP (Mac mini etc.)
    TTS_BACKEND: str = "irodori"

    # ---- Whisper (STT) ----
    WHISPER_MODEL: str = "small"
    WHISPER_DEVICE: str = "auto"          # cuda / cpu / auto
    WHISPER_LANGUAGE: str = "ja"

    # ---- Ollama (LLM) ----
    OLLAMA_HOST: str = "http://127.0.0.1:11434"
    OLLAMA_MODEL: str = "qwen2.5:7b"
    OLLAMA_TIMEOUT_S: float = 60.0
    OLLAMA_TEMPERATURE: float = 0.7
    OLLAMA_NUM_PREDICT: int = 200

    # Conversation history
    HISTORY_TURNS: int = Field(default=6, gt=0)
    MAX_SESSIONS: int = Field(default=16, gt=0)
    LLM_HISTORY_DB: str | None = None
    LLM_TIME_FLAVOR: int = Field(default=1, ge=0, le=1)  # 1=ON, 0=OFF (kept as int for backward .env)

    # ---- Irodori-TTS-Lite (when TTS_BACKEND=irodori) ----
    IRODORI_DEVICE: str = "cuda"
    IRODORI_REF_WAV: str | None = None
    IRODORI_FORCE_FP16: int = Field(default=1, ge=0, le=1)
    IRODORI_CHECKPOINT: str | None = None

    # ---- VOICEVOX (when TTS_BACKEND=voicevox) ----
    VOICEVOX_HOST: str = "http://127.0.0.1:50021"
    VOICEVOX_SPEAKER: int = 3

    # ---- Server / HTTP ----
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8000
    LOG_LEVEL: str = "info"
    MAX_AUDIO_BYTES: int = 2 * 1024 * 1024

    # ---- Scheduler / external push ----
    SCHEDULE_ENABLED: int = Field(default=0, ge=0, le=1)
    SCHEDULE_FILE: str = "schedule.json"
    QUEUE_MAX_SIZE: int = Field(default=16, gt=0)
    ENQUEUE_TOKEN: str = ""

    # ---- TTS pre-warm & cache ----
    TTS_PREWARM: int = Field(default=1, ge=0, le=1)
    TTS_PREWARM_TEXT: str = "あ"
    TTS_CACHE_DIR: str | None = None
    TTS_CACHE_VERSION: str = "v1"

    # ---- Persona override ----
    PERSONA_FILE: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",   # allow future keys without breaking
        case_sensitive=True,
    )

    @field_validator("LOG_LEVEL")
    @classmethod
    def _upper_log_level(cls, v: str) -> str:
        return v.upper()

    # ---- Convenience helpers (used by main.py and scheduler init) ----
    # NOTE: These helpers re-instantiate Settings() so that pytest monkeypatch.setenv
    # calls in tests are reflected even when the module-level singleton was created
    # before the patch. The operation is cheap (pure env parsing).
    def is_scheduler_enabled(self) -> bool:
        return bool(Settings().SCHEDULE_ENABLED)

    def is_tts_prewarm_enabled(self) -> bool:
        return bool(Settings().TTS_PREWARM)

    def is_time_flavor_enabled(self) -> bool:
        return bool(Settings().LLM_TIME_FLAVOR)

    def get_log_level(self) -> str:
        return Settings().LOG_LEVEL


# Singleton instantiated at import time.
# This triggers .env loading + validation exactly once.
settings = Settings()