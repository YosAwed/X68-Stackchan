"""定期発話スケジューラ。

JSON で cron 式 + プロンプト (LLM 駆動) または固定文を定義しておき、
時刻が来たら LLM (任意) → TTS → utterance_queue へ enqueue する。
CoreS3 側は GET /pull で受け取って喋る。

スケジューラ自体は asyncio タスク 1 本で、30 秒ごとに全トリガを点検する
(cron の最小単位は 1 分なのでこの粒度で十分)。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from croniter import croniter

from utterance_queue import Utterance, UtteranceQueue

log = logging.getLogger(__name__)


@dataclass
class ScheduledTrigger:
    name: str
    cron: str
    kind: str          # "llm" or "fixed"
    sid: str = "scheduled"
    prompt: str | None = None    # kind="llm" のみ
    text: str | None = None      # kind="fixed" のみ

    def __post_init__(self):
        if self.kind == "llm" and not self.prompt:
            raise ValueError(f"trigger {self.name!r}: kind=llm requires prompt")
        if self.kind == "fixed" and not self.text:
            raise ValueError(f"trigger {self.name!r}: kind=fixed requires text")
        if self.kind not in ("llm", "fixed"):
            raise ValueError(f"trigger {self.name!r}: unknown kind={self.kind!r}")
        # 次回発火予定時刻を内部状態として持つ
        self._iter = croniter(self.cron, datetime.now())
        self._next: datetime = self._iter.get_next(datetime)

    def due(self, now: datetime) -> bool:
        if now >= self._next:
            # 次回をすぐ更新 (同じ分内で 2 回発火しないように)
            self._next = self._iter.get_next(datetime)
            return True
        return False


class Scheduler:
    """LLM / TTS / UtteranceQueue を組み合わせて定期発火する。

    使い方:
        sched = Scheduler.from_file(Path("schedule.json"), llm, tts, queue)
        await sched.start()
        ...
        await sched.stop()
    """

    POLL_INTERVAL_S = 30

    def __init__(self, llm, tts, queue: UtteranceQueue):
        self.llm = llm
        self.tts = tts
        self.queue = queue
        self.triggers: list[ScheduledTrigger] = []
        self._task: asyncio.Task | None = None

    @classmethod
    def from_file(cls, path: Path, llm, tts, queue: UtteranceQueue) -> "Scheduler":
        s = cls(llm, tts, queue)
        if not path.exists():
            log.warning("schedule file not found: %s (scheduler will idle)", path)
            return s
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            log.exception("failed to parse schedule file %s", path)
            return s
        for raw in data.get("triggers", []):
            try:
                s.triggers.append(ScheduledTrigger(
                    name=raw["name"],
                    cron=raw["cron"],
                    kind=raw.get("kind", raw.get("type", "llm")),
                    sid=raw.get("sid", "scheduled"),
                    prompt=raw.get("prompt"),
                    text=raw.get("text"),
                ))
            except Exception:
                log.exception("skipping invalid trigger: %r", raw)
        log.info("scheduler loaded %d trigger(s) from %s", len(s.triggers), path)
        return s

    async def start(self):
        if self._task is not None:
            return
        if not self.triggers:
            log.info("scheduler has no triggers, not starting loop")
            return
        self._task = asyncio.create_task(self._loop(), name="utterance-scheduler")

    async def stop(self):
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self):
        log.info("scheduler loop starting (poll=%ds)", self.POLL_INTERVAL_S)
        try:
            while True:
                now = datetime.now()
                for trig in self.triggers:
                    if trig.due(now):
                        try:
                            await self._fire(trig)
                        except Exception:
                            log.exception("trigger %s fire failed", trig.name)
                await asyncio.sleep(self.POLL_INTERVAL_S)
        except asyncio.CancelledError:
            log.info("scheduler loop cancelled")
            raise

    async def _fire(self, trig: ScheduledTrigger):
        log.info("scheduler ▶ %s (kind=%s)", trig.name, trig.kind)
        if trig.kind == "llm":
            # LLM 呼び出しはブロッキングなので別スレッドへ
            bot_text = await asyncio.to_thread(self.llm.chat, trig.sid, trig.prompt)
        else:
            bot_text = trig.text or ""
        if not bot_text:
            log.warning("trigger %s produced empty text, skipping TTS", trig.name)
            return
        wav = await asyncio.to_thread(self.tts.synthesize, bot_text)
        self.queue.push_nowait(Utterance(
            wav=wav,
            bot_text=bot_text,
            source=f"sched:{trig.name}",
        ))

    def status(self) -> dict[str, Any]:
        return {
            "running": self._task is not None and not self._task.done(),
            "triggers": [
                {
                    "name": t.name,
                    "cron": t.cron,
                    "kind": t.kind,
                    "next": t._next.isoformat(timespec="seconds"),
                }
                for t in self.triggers
            ],
            "queue_size": self.queue.size(),
        }
