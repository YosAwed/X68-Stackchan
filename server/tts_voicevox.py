"""VOICEVOX engine の HTTP API を 2 段 (audio_query → synthesis) で叩く。

Mac mini など CUDA を使えない / 使いたくない環境向けのバックエンド。
docs/setup-macmini.md 参照。
"""

from __future__ import annotations

import logging

import httpx
from settings import settings

log = logging.getLogger(__name__)


class TTS:
    def __init__(self, timeout_s: float = 30.0):
        host = settings.VOICEVOX_HOST
        speaker = settings.VOICEVOX_SPEAKER

        self.host = host.rstrip("/")
        self.speaker = speaker
        self.speed_scale = settings.VOICEVOX_SPEED_SCALE
        self.pitch_scale = settings.VOICEVOX_PITCH_SCALE
        self.intonation_scale = settings.VOICEVOX_INTONATION_SCALE
        self.volume_scale = settings.VOICEVOX_VOLUME_SCALE
        self._client = httpx.Client(base_url=self.host, timeout=timeout_s)
        log.info(
            "VOICEVOX backend ready (host=%s, speaker=%d, speed=%.2f, pitch=%.2f, intonation=%.2f, volume=%.2f)",
            self.host,
            self.speaker,
            self.speed_scale,
            self.pitch_scale,
            self.intonation_scale,
            self.volume_scale,
        )

    def status(self) -> dict:
        try:
            r = self._client.get("/version", timeout=2.0)
            r.raise_for_status()
            return {
                "ok": True,
                "backend": "voicevox",
                "host": self.host,
                "speaker": self.speaker,
                "speed_scale": self.speed_scale,
                "pitch_scale": self.pitch_scale,
                "intonation_scale": self.intonation_scale,
                "volume_scale": self.volume_scale,
                "version": r.text.strip().strip('"'),
            }
        except Exception as e:
            return {
                "ok": False,
                "backend": "voicevox",
                "host": self.host,
                "speaker": self.speaker,
                "speed_scale": self.speed_scale,
                "pitch_scale": self.pitch_scale,
                "intonation_scale": self.intonation_scale,
                "volume_scale": self.volume_scale,
                "error": str(e),
            }

    def synthesize(self, text: str) -> bytes:
        # 1) クエリを作る
        q = self._client.post(
            "/audio_query",
            params={"text": text, "speaker": self.speaker},
        )
        q.raise_for_status()
        query = q.json()

        # CoreS3 側の I2S 設定に合わせて 16kHz mono に揃える
        query["outputSamplingRate"] = 16000
        query["outputStereo"] = False
        query["speedScale"] = self.speed_scale
        query["pitchScale"] = self.pitch_scale
        query["intonationScale"] = self.intonation_scale
        query["volumeScale"] = self.volume_scale

        # 2) 合成
        r = self._client.post(
            "/synthesis",
            params={"speaker": self.speaker},
            json=query,
            headers={"Accept": "audio/wav"},
        )
        r.raise_for_status()
        wav = r.content
        log.info("TTS ◀ %d bytes (text=%r)", len(wav), text)
        return wav
