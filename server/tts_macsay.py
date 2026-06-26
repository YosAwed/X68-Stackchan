"""macOS built-in TTS backend using say + afconvert.

This is a zero-service local backend for hardware smoke tests on a Mac.  It
does not try to match the character voice; it only guarantees that the server
can return CoreS3-friendly WAV (16 kHz / mono / PCM16) without VOICEVOX or
Irodori running.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from settings import settings

log = logging.getLogger(__name__)


class TTS:
    def __init__(self, timeout_s: float = 30.0):
        self.voice = settings.MACSAY_VOICE
        self.rate = settings.MACSAY_RATE
        self.timeout_s = timeout_s
        self._say = shutil.which("say")
        self._afconvert = shutil.which("afconvert")
        log.info(
            "macsay backend ready (voice=%s, rate=%d, say=%s, afconvert=%s)",
            self.voice,
            self.rate,
            self._say or "<missing>",
            self._afconvert or "<missing>",
        )

    def status(self) -> dict:
        ok = bool(self._say and self._afconvert)
        return {
            "ok": ok,
            "backend": "macsay",
            "voice": self.voice,
            "rate": self.rate,
            "say": self._say,
            "afconvert": self._afconvert,
            "error": None if ok else "say or afconvert not found",
        }

    def synthesize(self, text: str) -> bytes:
        if not self._say or not self._afconvert:
            raise RuntimeError("macsay backend requires /usr/bin/say and /usr/bin/afconvert")

        with tempfile.TemporaryDirectory(prefix="stackchan-macsay-") as tmp:
            aiff = Path(tmp) / "speech.aiff"
            wav = Path(tmp) / "speech.wav"

            subprocess.run(
                [
                    self._say,
                    "-v",
                    self.voice,
                    "-r",
                    str(self.rate),
                    "-o",
                    str(aiff),
                    text,
                ],
                check=True,
                timeout=self.timeout_s,
            )
            subprocess.run(
                [
                    self._afconvert,
                    "-f",
                    "WAVE",
                    "-d",
                    "LEI16@16000",
                    "-c",
                    "1",
                    str(aiff),
                    str(wav),
                ],
                check=True,
                timeout=self.timeout_s,
            )
            data = wav.read_bytes()

        log.info("TTS ◀ %d bytes (macsay text=%r)", len(data), text)
        return data
