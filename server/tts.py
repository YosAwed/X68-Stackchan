"""VOICEVOX engine の HTTP API を 2 段 (audio_query → synthesis) で叩く"""

from __future__ import annotations

import logging
import httpx

log = logging.getLogger(__name__)


class TTS:
    def __init__(self, host: str, speaker: int, timeout_s: float = 30.0):
        self.host = host.rstrip("/")
        self.speaker = speaker
        self._client = httpx.Client(base_url=self.host, timeout=timeout_s)

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
