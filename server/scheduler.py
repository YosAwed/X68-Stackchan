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
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from croniter import croniter
from emote import classify as classify_emote
from emote import with_irodori_emoji
from history_store import HistoryStore
from settings import settings
from utterance_queue import Utterance, UtteranceQueue
from wav_cache import WavCache

log = logging.getLogger(__name__)


def _tts_input_text(text: str, emote: str) -> str:
    if settings.TTS_BACKEND == "irodori" and bool(settings.IRODORI_EMOJI_STYLE):
        return with_irodori_emoji(text, emote)
    return text


@dataclass
class ScheduledTrigger:
    name: str
    cron: str
    kind: str          # "llm" or "fixed"
    sid: str = "scheduled"
    prompt: str | None = None    # kind="llm" のみ
    text: str | None = None      # kind="fixed" のみ

    # 「ユーザが直近 N 分以上話しかけていない」場合だけ発火する条件。
    # None なら常に発火 (= 普通の cron トリガ)。
    silent_for_minutes: float | None = None
    # 沈黙判定の対象 sid (省略時は CoreS3 既定の "stackchan-01")。
    # /chat の sid と一致させる。trigger.sid は LLM の人格セッションなので別物。
    check_sid: str = "stackchan-01"

    # kind="fixed" の場合に事前合成した WAV を保持しておくキャッシュ。
    # text が不変なので Scheduler.start() で一度だけ合成し、以降の発火では
    # TTS を呼ばずにこの bytes をそのままキューへ。
    _cached_wav: bytes | None = None

    def __post_init__(self):
        if self.kind not in ("llm", "fixed"):
            raise ValueError(f"trigger {self.name!r}: unknown kind={self.kind!r}")
        if self.kind == "llm" and not self.prompt:
            raise ValueError(f"trigger {self.name!r}: kind=llm requires prompt")
        if self.kind == "fixed" and not self.text:
            raise ValueError(f"trigger {self.name!r}: kind=fixed requires text")
        # cron 式の妥当性は croniter に事前検査させる。
        # croniter.is_valid は古いバージョンでは無い可能性があるので、
        # 無ければ普通に croniter(...) を呼んで例外を拾う。
        is_valid = getattr(croniter, "is_valid", None)
        if callable(is_valid):
            if not is_valid(self.cron):
                raise ValueError(
                    f"trigger {self.name!r}: invalid cron expression {self.cron!r}")
        # 次回発火予定時刻を内部状態として持つ
        self._iter = croniter(self.cron, datetime.now())
        self._next: datetime = self._iter.get_next(datetime)
        # 発火履歴 (status() で参照)
        self.fire_count: int = 0
        self.last_fire: datetime | None = None
        self.last_error: str | None = None

    def due(self, now: datetime) -> bool:
        if now >= self._next:
            # 次回をすぐ更新 (同じ分内で 2 回発火しないように)
            self._next = self._iter.get_next(datetime)
            return True
        return False

    def record_fire(self, when: datetime) -> None:
        self.fire_count += 1
        self.last_fire = when
        self.last_error = None

    def record_error(self, err: BaseException) -> None:
        # 直近のエラーだけ保持。トレースは log.exception 側で出す。
        self.last_error = f"{type(err).__name__}: {err}"


class Scheduler:
    """LLM / TTS / UtteranceQueue を組み合わせて定期発火する。

    使い方:
        sched = Scheduler.from_file(Path("schedule.json"), llm, tts, queue)
        await sched.start()
        ...
        await sched.stop()
    """

    POLL_INTERVAL_S = 30

    def __init__(self, llm, tts, queue: UtteranceQueue,
                 wav_cache: WavCache | None = None,
                 history_store: HistoryStore | None = None):
        self.llm = llm
        self.tts = tts
        self.queue = queue
        self.wav_cache = wav_cache if wav_cache is not None else WavCache(dir=None)
        # silent_for_minutes 条件付きトリガの判定に使う (なくても OK、
        # その場合は silent_for_minutes は無効化されて常に発火)。
        self.history_store = history_store
        self.triggers: list[ScheduledTrigger] = []
        self._task: asyncio.Task | None = None

    @classmethod
    def from_file(cls, path: Path, llm, tts, queue: UtteranceQueue,
                  wav_cache: WavCache | None = None,
                  history_store: HistoryStore | None = None) -> "Scheduler":
        s = cls(llm, tts, queue, wav_cache=wav_cache, history_store=history_store)
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
                    silent_for_minutes=raw.get("silent_for_minutes"),
                    check_sid=raw.get("check_sid", "stackchan-01"),
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
        # kind="fixed" は text が不変なので、起動時に一度だけ TTS を回して
        # bytes をキャッシュしておく。以後の発火は TTS をスキップできる。
        await self._prewarm_fixed_triggers()
        self._task = asyncio.create_task(self._loop(), name="utterance-scheduler")

    async def _prewarm_fixed_triggers(self):
        fixed = [t for t in self.triggers if t.kind == "fixed"]
        if not fixed:
            return
        log.info("scheduler pre-synthesizing %d fixed trigger(s) ...", len(fixed))
        for t in fixed:
            text = t.text or ""
            emote = classify_emote(text)
            synth_text = _tts_input_text(text, emote)
            # まずディスクキャッシュを試す (前回起動時の合成結果が残っていれば即時)
            wav = self.wav_cache.get(synth_text)
            if wav is not None:
                t._cached_wav = wav
                log.info("  cached %s (%d bytes, from disk)", t.name, len(wav))
                continue
            try:
                wav = await asyncio.to_thread(self.tts.synthesize, synth_text)
                t._cached_wav = wav
                self.wav_cache.put(synth_text, wav)  # 次回起動用にディスクへ
                log.info("  cached %s (%d bytes, fresh)", t.name, len(wav))
            except Exception as e:
                # キャッシュ失敗は致命的でない (発火時に再試行される)
                log.exception("trigger %s pre-synth failed", t.name)
                t.record_error(e)

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
                        except Exception as e:
                            log.exception("trigger %s fire failed", trig.name)
                            trig.record_error(e)
                await asyncio.sleep(self.POLL_INTERVAL_S)
        except asyncio.CancelledError:
            log.info("scheduler loop cancelled")
            raise

    def _should_fire(self, trig: ScheduledTrigger) -> bool:
        """silent_for_minutes 条件を満たすか。条件無し or 履歴ストア無しなら常に True。"""
        if trig.silent_for_minutes is None:
            return True
        if self.history_store is None:
            # 永続化されていない環境では沈黙判定不能。conservative に「発火しない」よりは
            # 「常に発火」のほうが運用ミスに気付きやすい (絶対消えないトリガが残る)。
            return True
        threshold_s = trig.silent_for_minutes * 60.0
        last = self.history_store.last_ts(trig.check_sid)
        if last is None:
            return True  # 履歴ゼロ = 沈黙そのもの。発火する
        return (time.time() - last) >= threshold_s

    async def _fire(self, trig: ScheduledTrigger):
        if not self._should_fire(trig):
            log.info("scheduler ▷ %s skipped (sid=%s active within %.0f min)",
                     trig.name, trig.check_sid, float(trig.silent_for_minutes or 0))
            return
        log.info("scheduler ▶ %s (kind=%s)", trig.name, trig.kind)
        reservation = self.queue.reserve_nowait()
        if reservation is None:
            log.warning("trigger %s skipped because utterance queue is full", trig.name)
            return
        try:
            if trig.kind == "llm":
                # LLM 呼び出しはブロッキングなので別スレッドへ
                bot_text = await asyncio.to_thread(self.llm.chat, trig.sid, trig.prompt)
            else:
                bot_text = trig.text or ""
            if not bot_text:
                log.warning("trigger %s produced empty text, skipping TTS", trig.name)
                return
            # fixed トリガは事前合成済み bytes があればそれを再利用 (TTS 呼び出しを節約)
            if trig.kind == "fixed" and trig._cached_wav is not None:
                wav = trig._cached_wav
            else:
                emote = classify_emote(bot_text)
                wav = await asyncio.to_thread(
                    self.tts.synthesize,
                    _tts_input_text(bot_text, emote),
                )
                # 起動時に失敗していた fixed もここで遅延キャッシュ
                if trig.kind == "fixed":
                    trig._cached_wav = wav
            emote = classify_emote(bot_text)
            reservation.commit(Utterance(
                wav=wav,
                bot_text=bot_text,
                source=f"sched:{trig.name}",
                emote=emote,
            ))
            trig.record_fire(datetime.now())
        finally:
            reservation.release()

    def status(self) -> dict[str, Any]:
        return {
            "running": self._task is not None and not self._task.done(),
            "triggers": [
                {
                    "name": t.name,
                    "cron": t.cron,
                    "kind": t.kind,
                    "next": t._next.isoformat(timespec="seconds"),
                    "fire_count": t.fire_count,
                    "last_fire": (
                        t.last_fire.isoformat(timespec="seconds")
                        if t.last_fire else None
                    ),
                    "last_error": t.last_error,
                    "silent_for_minutes": t.silent_for_minutes,
                    "check_sid": (
                        t.check_sid if t.silent_for_minutes is not None else None
                    ),
                }
                for t in self.triggers
            ],
            "queue_size": self.queue.size(),
            "queue_reserved": self.queue.reserved(),
        }
