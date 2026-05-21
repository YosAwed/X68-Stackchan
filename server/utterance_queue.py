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
    emote: str = "neutral"  # CoreS3 側の口パク表情切替ヒント (emote.classify の出力)


class QueueReservation:
    def __init__(self, queue: "UtteranceQueue"):
        self._queue = queue
        self._active = True

    def commit(self, u: Utterance) -> bool:
        if not self._active:
            return False
        self._active = False
        self._queue._release_reserved_slot()
        return self._queue.push_nowait(u)

    def release(self) -> None:
        if not self._active:
            return
        self._active = False
        self._queue._release_reserved_slot()


class UtteranceQueue:
    def __init__(self, max_size: int = 16):
        self._q: asyncio.Queue[Utterance] = asyncio.Queue(maxsize=max_size)
        self._max_size = max_size
        self._reserved = 0

    def size(self) -> int:
        return self._q.qsize()

    def reserved(self) -> int:
        return self._reserved

    def has_capacity(self) -> bool:
        return self._max_size <= 0 or self._q.qsize() + self._reserved < self._max_size

    def reserve_nowait(self) -> QueueReservation | None:
        """重い LLM/TTS の前に 1 件分の空きを確保する。満杯なら None。"""
        if not self.has_capacity():
            log.warning(
                "queue full, cannot reserve slot (qsize=%d reserved=%d)",
                self._q.qsize(),
                self._reserved,
            )
            return None
        self._reserved += 1
        return QueueReservation(self)

    def _release_reserved_slot(self) -> None:
        if self._reserved > 0:
            self._reserved -= 1

    def push_nowait(self, u: Utterance) -> bool:
        """満杯なら False を返してログ。例外は投げない (スケジューラが死なないように)"""
        if not self.has_capacity():
            log.warning(
                "queue full, dropping %s (qsize=%d reserved=%d)",
                u.source,
                self._q.qsize(),
                self._reserved,
            )
            return False
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
