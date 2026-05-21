"""定期発話 / 外部 push の WAV を貯めて、CoreS3 からの long-poll で取り出すキュー。

シンプルな asyncio.Queue ラッパ。CoreS3 側は GET /pull?wait=N で取りに来る。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class Utterance:
    wav: bytes        # 合成済み WAV
    bot_text: str     # 表示用テキスト
    source: str       # "sched:<name>" / "ext:<sid>" など発信元タグ


class UtteranceQueue:
    def __init__(self, max_size: int = 16):
        self._q: asyncio.Queue[Utterance] = asyncio.Queue(maxsize=max_size)

    def size(self) -> int:
        return self._q.qsize()

    def push_nowait(self, u: Utterance) -> bool:
        """満杯なら False を返してログ。例外は投げない (スケジューラが死なないように)"""
        try:
            self._q.put_nowait(u)
            log.info("queue ◀ %s (%d bytes, qsize=%d)", u.source, len(u.wav), self._q.qsize())
            return True
        except asyncio.QueueFull:
            log.warning("queue full, dropping %s (qsize=%d)", u.source, self._q.qsize())
            return False

    async def pull(self, timeout_s: float) -> Utterance | None:
        """timeout_s=0 は即時 (キュー空なら None)。それ以外は long-poll。"""
        if timeout_s <= 0:
            try:
                return self._q.get_nowait()
            except asyncio.QueueEmpty:
                return None
        try:
            return await asyncio.wait_for(self._q.get(), timeout=timeout_s)
        except asyncio.TimeoutError:
            return None
