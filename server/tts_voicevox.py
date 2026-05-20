"""VOICEVOX engine の HTTP API を 2 段 (audio_query → synthesis) で叩く。

Mac mini など CUDA を使えない / 使いたくない環境向けのバックエンド。
docs/setup-macmini.md 参照。
"""

from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger(__name__)


class TTS:
    def __init__(self, timeout_s: float = 30.0):
        host = os.getenv("VOICEVOX_HOST", "http://127.0.0.1:50021")
        speaker = int(os.getenv("VOICEVOX_SPEAKER", "3"))

        self.host = host.rstrip("/")
        self.speaker = speaker
        self._client = httpx.Client(base_url=self.host, timeout=timeout_s)
        log.info("VOICEVOX backend ready (host=%s, speaker=%d)", self.host, self.speaker)

    def status(self) -> dict:
        try:
            r = self._client.get("/version", timeout=2.0)
            r.raise_for_status()
            return {
                "ok": True,
                "backend": "voicevox",
                "host": self.host,
                "speaker": self.speaker,
                "version": r.text.strip().strip('"'),
            }
        except Exception as e:
            return {
                "ok": False,
                "backend": "voicevox",
                "host": self.host,
                "speaker": self.speaker,
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
        # 速さを少しだけ上げる (好みで)
        query["speedScale"] = 1.05

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
