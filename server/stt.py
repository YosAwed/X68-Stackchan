"""faster-whisper による STT ラッパ"""

from __future__ import annotations

import io
import logging
import threading

from faster_whisper import WhisperModel

log = logging.getLogger(__name__)


class STT:
    def __init__(self, model_name: str, device: str = "auto", language: str = "ja"):
        # CUDA なら float16 が速い。CPU フォールバックは int8。
        # device="auto" は faster-whisper 側で CUDA を優先検出するので float16 を選ぶ。
        compute_type = "int8" if device == "cpu" else "float16"
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.model = None
        self._lock = threading.Lock()

    def _model(self) -> WhisperModel:
        if self.model is not None:
            return self.model
        with self._lock:
            if self.model is None:
                log.info("Loading whisper model=%s device=%s compute=%s",
                         self.model_name, self.device, self.compute_type)
                self.model = WhisperModel(
                    self.model_name,
                    device=self.device,
                    compute_type=self.compute_type,
                )
        return self.model

    def status(self) -> dict:
        return {
            "ok": True,
            "model": self.model_name,
            "device": self.device,
            "compute_type": self.compute_type,
            "language": self.language,
            "loaded": self.model is not None,
        }

    def warmup(self) -> None:
        """モデルだけをロードする。実際の音声認識は行わない。"""
        self._model()

    def transcribe(self, wav_bytes: bytes) -> str:
        """WAV (RIFF) のバイト列を渡してテキストを返す"""
        # faster-whisper は file path / file-like / numpy を受ける
        buf = io.BytesIO(wav_bytes)
        segments, info = self._model().transcribe(
            buf,
            language=self.language,
            beam_size=1,            # ロボット会話なので速度優先
            vad_filter=True,        # 無音をカット
            condition_on_previous_text=False,
        )
        text = "".join(seg.text for seg in segments).strip()
        log.info("STT (%.2fs): %s", info.duration, text)
        return text
