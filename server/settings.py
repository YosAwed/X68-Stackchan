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
    # kokoro   = in-process ONNX (Mac mini / CPU)
    # macsay   = macOS built-in say + afconvert (local smoke-test backend)
    TTS_BACKEND: str = "irodori"

    # ---- Whisper (STT) ----
    WHISPER_MODEL: str = "small"
    WHISPER_DEVICE: str = "auto"          # cuda / cpu / auto
    WHISPER_LANGUAGE: str = "ja"
    WHISPER_VAD_FILTER: int = Field(default=0, ge=0, le=1)
    WHISPER_BEAM_SIZE: int = Field(default=1, gt=0)
    WHISPER_PREWARM: int = Field(default=1, ge=0, le=1)

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
    IRODORI_SEED: int | None = 68000
    IRODORI_EMOJI_STYLE: int = Field(default=1, ge=0, le=1)

    # ---- VOICEVOX (when TTS_BACKEND=voicevox) ----
    VOICEVOX_HOST: str = "http://127.0.0.1:50021"
    VOICEVOX_SPEAKER: int = 3
    VOICEVOX_SPEED_SCALE: float = Field(default=1.0, gt=0.0)
    VOICEVOX_PITCH_SCALE: float = Field(default=0.0, ge=-0.15, le=0.15)
    VOICEVOX_INTONATION_SCALE: float = Field(default=1.0, ge=0.0)
    VOICEVOX_VOLUME_SCALE: float = Field(default=1.0, gt=0.0)

    # ---- Kokoro ONNX (when TTS_BACKEND=kokoro) ----
    KOKORO_MODEL: str = "models/kokoro/kokoro-v1.0.onnx"
    KOKORO_VOICES: str = "models/kokoro/voices-v1.0.bin"
    KOKORO_VOCAB_CONFIG: str | None = None
    KOKORO_VOICE: str = "jf_alpha"
    KOKORO_SPEED: float = Field(default=1.0, gt=0.0)

    # ---- macOS say (when TTS_BACKEND=macsay) ----
    MACSAY_VOICE: str = "Kyoko"
    MACSAY_RATE: int = Field(default=185, gt=0)

    # ---- Server / HTTP ----
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 8000
    LOG_LEVEL: str = "info"
    MAX_AUDIO_BYTES: int = 2 * 1024 * 1024
    MAX_SPEAK_CHARS: int = 70
    WAKE_WORDS: str = "ぺけ子,ペケ子,ぺけこ,ペケコ,スタックちゃん"

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

    # ---- Vision / camera watcher ----
    VISION_ENABLED: int = Field(default=0, ge=0, le=1)
    VISION_MODE: str = "motion"
    VISION_CAMERA_INDEX: int = Field(default=0, ge=0)
    VISION_IMAGE_PATH: str = ""
    VISION_POLL_INTERVAL_S: float = Field(default=0.5, gt=0)
    VISION_SNAPSHOT_INTERVAL_S: float = Field(default=180.0, ge=10.0)
    VISION_COOLDOWN_S: float = Field(default=45.0, ge=1.0)
    VISION_MOTION_THRESHOLD: float = Field(default=0.02, gt=0.0)
    VISION_MIN_CHANGED_PIXELS: int = Field(default=500, gt=0)
    VISION_SID: str = "vision"
    VISION_PROVIDER: str = "ollama"
    VISION_OLLAMA_HOST: str | None = None
    VISION_OLLAMA_MODEL: str = ""
    VISION_OLLAMA_TIMEOUT_S: float = Field(default=45.0, gt=0.0)
    VISION_OPENAI_HOST: str | None = None
    VISION_OPENAI_MODEL: str = ""
    VISION_OPENAI_API_KEY: str = ""

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

    @field_validator("VISION_MODE")
    @classmethod
    def _vision_mode(cls, v: str) -> str:
        value = v.strip().lower()
        allowed = {"motion", "snapshot"}
        if value not in allowed:
            raise ValueError(f"VISION_MODE must be one of {sorted(allowed)}")
        return value

    @field_validator("VISION_PROVIDER")
    @classmethod
    def _vision_provider(cls, v: str) -> str:
        value = v.strip().lower()
        allowed = {"ollama", "openai", "lmstudio"}
        if value not in allowed:
            raise ValueError(f"VISION_PROVIDER must be one of {sorted(allowed)}")
        return value

    @field_validator("IRODORI_SEED", mode="before")
    @classmethod
    def _empty_seed_means_random(cls, v):
        if v is None:
            return None
        if isinstance(v, str) and v.strip().lower() in ("", "none", "null", "random"):
            return None
        return v

    # ---- Convenience helpers (used by main.py and scheduler init) ----
    # NOTE: These helpers re-instantiate Settings() so that pytest monkeypatch.setenv
    # calls in tests are reflected even when the module-level singleton was created
    # before the patch. The operation is cheap (pure env parsing).
    def is_scheduler_enabled(self) -> bool:
        return bool(Settings().SCHEDULE_ENABLED)

    def is_tts_prewarm_enabled(self) -> bool:
        return bool(Settings().TTS_PREWARM)

    def is_whisper_prewarm_enabled(self) -> bool:
        return bool(Settings().WHISPER_PREWARM)

    def is_time_flavor_enabled(self) -> bool:
        return bool(Settings().LLM_TIME_FLAVOR)

    def is_vision_enabled(self) -> bool:
        return bool(Settings().VISION_ENABLED)

    def get_log_level(self) -> str:
        return Settings().LOG_LEVEL

    def get_max_speak_chars(self) -> int:
        return Settings().MAX_SPEAK_CHARS


# Singleton instantiated at import time.
# This triggers .env loading + validation exactly once.
settings = Settings()
