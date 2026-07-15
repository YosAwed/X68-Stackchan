"""Kokoro ONNX Japanese TTS backend.

Kokoro generates 24 kHz float audio.  This adapter performs Japanese G2P,
resamples to the CoreS3 contract (16 kHz mono), and returns PCM16 WAV bytes.
The optional dependencies are imported only when this backend is selected so
VOICEVOX/Irodori installations are unaffected.
"""

from __future__ import annotations

import io
import logging
import threading
from pathlib import Path
from typing import Any

from settings import settings

log = logging.getLogger(__name__)

SERVER_DIR = Path(__file__).resolve().parent
OUTPUT_SAMPLE_RATE = 16_000


def _resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else SERVER_DIR / path


class TTS:
    def __init__(
        self,
        model_path: str | None = None,
        voices_path: str | None = None,
        voice: str | None = None,
        speed: float | None = None,
        vocab_config: str | None = None,
    ):
        self.model_path = _resolve_path(model_path or settings.KOKORO_MODEL)
        self.voices_path = _resolve_path(voices_path or settings.KOKORO_VOICES)
        configured_vocab = (
            vocab_config if vocab_config is not None else settings.KOKORO_VOCAB_CONFIG
        )
        self.vocab_config = _resolve_path(configured_vocab) if configured_vocab else None
        self.voice = voice or settings.KOKORO_VOICE
        self.speed = speed if speed is not None else settings.KOKORO_SPEED
        self._lock = threading.Lock()

        missing = [
            str(path)
            for path in (self.model_path, self.voices_path, self.vocab_config)
            if path is not None and not path.is_file()
        ]
        if missing:
            raise FileNotFoundError("Kokoro model file(s) not found: " + ", ".join(missing))

        (
            self._kokoro,
            self._g2p,
            self._numpy,
            self._soundfile,
            self._soxr,
        ) = self._load_runtime(
            self.model_path,
            self.voices_path,
            self.vocab_config,
        )
        log.info(
            "Kokoro backend ready (voice=%s, speed=%.2f, model=%s)",
            self.voice,
            self.speed,
            self.model_path.name,
        )

    @staticmethod
    def _load_runtime(
        model_path: Path,
        voices_path: Path,
        vocab_config: Path | None,
    ) -> tuple[Any, Any, Any, Any, Any]:
        try:
            import numpy as np
            import soundfile as sf
            import soxr
            from kokoro_onnx import Kokoro
            from misaki import ja
        except ImportError as exc:
            raise RuntimeError(
                "Kokoro backend dependencies are missing; install "
                "requirements-macmini.txt"
            ) from exc

        kokoro = Kokoro(
            str(model_path),
            str(voices_path),
            vocab_config=str(vocab_config) if vocab_config else None,
        )
        return kokoro, ja.JAG2P(), np, sf, soxr

    def status(self) -> dict:
        return {
            "ok": True,
            "backend": "kokoro",
            "voice": self.voice,
            "speed": self.speed,
            "model": str(self.model_path),
            "voices": str(self.voices_path),
            "source_sample_rate": 24000,
            "output_sample_rate": OUTPUT_SAMPLE_RATE,
        }

    def synthesize(self, text: str) -> bytes:
        if not text.strip():
            raise ValueError("Kokoro cannot synthesize empty text")

        # MeCab/JAG2P and one shared ONNX session are serialized. FastAPI may
        # call synthesize() concurrently through asyncio.to_thread().
        with self._lock:
            phonemes, _ = self._g2p(text)
            if not phonemes.strip():
                raise RuntimeError(f"Kokoro Japanese G2P returned no phonemes for {text!r}")
            samples, sample_rate = self._kokoro.create(
                phonemes,
                voice=self.voice,
                speed=self.speed,
                is_phonemes=True,
            )

        audio = self._numpy.asarray(samples, dtype=self._numpy.float32).reshape(-1)
        if sample_rate != OUTPUT_SAMPLE_RATE:
            audio = self._soxr.resample(
                audio,
                sample_rate,
                OUTPUT_SAMPLE_RATE,
                quality="HQ",
            )

        output = io.BytesIO()
        self._soundfile.write(
            output,
            audio,
            OUTPUT_SAMPLE_RATE,
            format="WAV",
            subtype="PCM_16",
        )
        wav = output.getvalue()
        log.info(
            "TTS ◀ %d bytes (kokoro voice=%s text=%r)",
            len(wav),
            self.voice,
            text,
        )
        return wav
