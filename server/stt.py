"""faster-whisper による STT ラッパ"""

from __future__ import annotations

import io
import logging
from typing import Optional

from faster_whisper import WhisperModel

log = logging.getLogger(__name__)


class STT:
    def __init__(self, model_name: str, device: str = "auto", language: str = "ja"):
        # Apple Silicon は compute_type="int8" で軽く動く
        # CUDA があるなら "float16"
        compute_type = "int8" if device in ("cpu", "auto") else "float16"
        log.info("Loading whisper model=%s device=%s compute=%s",
                 model_name, device, compute_type)
        self.model = WhisperModel(model_name, device=device, compute_type=compute_type)
        self.language = language

    def transcribe(self, wav_bytes: bytes) -> str:
        """WAV (RIFF) のバイト列を渡してテキストを返す"""
        # faster-whisper は file path / file-like / numpy を受ける
        buf = io.BytesIO(wav_bytes)
        segments, info = self.model.transcribe(
            buf,
            language=self.language,
            beam_size=1,            # ロボット会話なので速度優先
            vad_filter=True,        # 無音をカット
            condition_on_previous_text=False,
        )
        text = "".join(seg.text for seg in segments).strip()
        log.info("STT (%.2fs): %s", info.duration, text)
        return text
