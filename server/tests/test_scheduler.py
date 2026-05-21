"""Scheduler の単体テスト。

LLM / TTS は FakeLLM / FakeTTS で置き換え、実際の Ollama や Irodori を
触らないでロジックだけ検証する。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from scheduler import ScheduledTrigger, Scheduler
from utterance_queue import UtteranceQueue


class FakeLLM:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def chat(self, sid: str, prompt: str) -> str:
        self.calls.append((sid, prompt))
        return f"reply:{prompt[:20]}"


class FakeTTS:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def synthesize(self, text: str) -> bytes:
        self.calls.append(text)
        return b"RIFF\x00\x00\x00\x00WAVE" + text.encode("utf-8")


# ---------------------- trigger validation ----------------------


def test_llm_kind_requires_prompt():
    with pytest.raises(ValueError, match="kind=llm requires prompt"):
        ScheduledTrigger(name="bad", cron="* * * * *", kind="llm")


def test_fixed_kind_requires_text():
    with pytest.raises(ValueError, match="kind=fixed requires text"):
        ScheduledTrigger(name="bad", cron="* * * * *", kind="fixed")


def test_unknown_kind_rejected():
    with pytest.raises(ValueError, match="unknown kind"):
        ScheduledTrigger(name="bad", cron="* * * * *", kind="weird", text="x")


def test_invalid_cron_rejected():
    # croniter は明らかに壊れた cron 式で例外を投げる
    with pytest.raises(Exception):  # noqa: B017
        ScheduledTrigger(name="bad", cron="not a cron", kind="fixed", text="x")


def test_obviously_bad_cron_is_caught_by_is_valid():
    # フィールド数が足りない cron はバリデーションで弾かれる
    with pytest.raises(Exception):  # noqa: B017
        ScheduledTrigger(name="bad", cron="0 0", kind="fixed", text="x")


# ---------------------- from_file loader ----------------------


def test_from_file_loads_valid_triggers(tmp_path: Path):
    schedule = tmp_path / "schedule.json"
    schedule.write_text(json.dumps({
        "triggers": [
            {"name": "fixed_one", "cron": "0 8 * * *", "kind": "fixed",
             "text": "ohayou"},
            {"name": "llm_one", "cron": "0 22 * * *", "kind": "llm",
             "prompt": "say good night", "sid": "scheduled"},
        ]
    }), encoding="utf-8")

    s = Scheduler.from_file(schedule, FakeLLM(), FakeTTS(), UtteranceQueue(8))
    assert len(s.triggers) == 2
    names = {t.name for t in s.triggers}
    assert names == {"fixed_one", "llm_one"}


def test_from_file_skips_invalid_triggers_without_crashing(tmp_path: Path):
    schedule = tmp_path / "schedule.json"
    schedule.write_text(json.dumps({
        "triggers": [
            # この 1 件は llm なのに prompt が無い → スキップされるはず
            {"name": "broken", "cron": "0 8 * * *", "kind": "llm"},
            # こちらは正常
            {"name": "ok", "cron": "0 9 * * *", "kind": "fixed",
             "text": "hello"},
        ]
    }), encoding="utf-8")

    s = Scheduler.from_file(schedule, FakeLLM(), FakeTTS(), UtteranceQueue(8))
    assert [t.name for t in s.triggers] == ["ok"]


def test_from_file_missing_returns_empty(tmp_path: Path):
    s = Scheduler.from_file(
        tmp_path / "absent.json", FakeLLM(), FakeTTS(), UtteranceQueue(8))
    assert s.triggers == []


# ---------------------- _fire dispatch ----------------------


@pytest.mark.asyncio
async def test_fire_fixed_pushes_to_queue():
    llm, tts, q = FakeLLM(), FakeTTS(), UtteranceQueue(4)
    s = Scheduler(llm, tts, q)
    t = ScheduledTrigger(name="fx", cron="* * * * *",
                         kind="fixed", text="やったー、できた")
    await s._fire(t)
    assert tts.calls == ["やったー、できた"]
    u = await q.pull(0)
    assert u is not None
    assert u.bot_text == "やったー、できた"
    assert u.source == "sched:fx"
    # 喜び系キーワードを含むので emote=joy になる
    assert u.emote == "joy"


@pytest.mark.asyncio
async def test_fire_llm_calls_llm_then_tts():
    llm, tts, q = FakeLLM(), FakeTTS(), UtteranceQueue(4)
    s = Scheduler(llm, tts, q)
    t = ScheduledTrigger(name="ai", cron="* * * * *",
                         kind="llm", prompt="greet me")
    await s._fire(t)
    assert llm.calls == [("scheduled", "greet me")]
    assert len(tts.calls) == 1
    u = await q.pull(0)
    assert u is not None
    assert u.source == "sched:ai"


@pytest.mark.asyncio
async def test_fire_skips_tts_when_llm_returns_empty():
    class EmptyLLM(FakeLLM):
        def chat(self, sid: str, prompt: str) -> str:
            return ""

    tts, q = FakeTTS(), UtteranceQueue(4)
    s = Scheduler(EmptyLLM(), tts, q)
    t = ScheduledTrigger(name="empty", cron="* * * * *",
                         kind="llm", prompt="nothing")
    await s._fire(t)
    # 空文字なら TTS は走らずキューにも積まれない
    assert tts.calls == []
    assert q.size() == 0
    # 空応答は発火カウントには載せない
    assert t.fire_count == 0
    assert t.last_fire is None


@pytest.mark.asyncio
async def test_fire_increments_fire_count_and_last_fire():
    llm, tts, q = FakeLLM(), FakeTTS(), UtteranceQueue(4)
    s = Scheduler(llm, tts, q)
    t = ScheduledTrigger(name="counter", cron="* * * * *",
                         kind="fixed", text="hi")
    assert t.fire_count == 0
    await s._fire(t)
    await s._fire(t)
    assert t.fire_count == 2
    assert t.last_fire is not None
    assert t.last_error is None


def test_record_error_sets_last_error():
    t = ScheduledTrigger(name="x", cron="* * * * *",
                         kind="fixed", text="hi")
    t.record_error(RuntimeError("boom"))
    assert t.last_error == "RuntimeError: boom"


@pytest.mark.asyncio
async def test_start_pre_synthesizes_fixed_triggers_once():
    """fixed トリガは start() 時に 1 度だけ TTS を呼び、以後発火では再合成しない。"""
    llm, tts, q = FakeLLM(), FakeTTS(), UtteranceQueue(8)
    s = Scheduler(llm, tts, q)
    fx = ScheduledTrigger(name="fx", cron="0 4 * 1 *",  # 遠未来
                          kind="fixed", text="お昼だよ")
    s.triggers.append(fx)

    await s.start()
    assert tts.calls == ["お昼だよ"]  # pre-synth で 1 回
    assert fx._cached_wav is not None
    cached = fx._cached_wav

    # 発火を 2 回手動で起こしても TTS 呼び出し回数は増えない
    await s._fire(fx)
    await s._fire(fx)
    assert tts.calls == ["お昼だよ"]

    # キューには毎回キャッシュした bytes がそのまま積まれる
    u1 = await q.pull(0)
    u2 = await q.pull(0)
    assert u1 is not None and u2 is not None
    assert u1.wav is cached
    assert u2.wav is cached

    await s.stop()


@pytest.mark.asyncio
async def test_llm_triggers_are_not_pre_synthesized():
    llm, tts, q = FakeLLM(), FakeTTS(), UtteranceQueue(4)
    s = Scheduler(llm, tts, q)
    t = ScheduledTrigger(name="ai", cron="0 4 * 1 *",
                         kind="llm", prompt="hi")
    s.triggers.append(t)
    await s.start()
    # LLM トリガは応答が毎回違うのでキャッシュしない
    assert tts.calls == []
    assert t._cached_wav is None
    await s.stop()


# ---------------------- due() boundary ----------------------


def test_due_returns_true_when_past_next_and_advances():
    t = ScheduledTrigger(name="x", cron="* * * * *",
                         kind="fixed", text="hi")
    # _next を強制的に過去にする
    past = datetime.now() - timedelta(seconds=10)
    t._next = past
    now = datetime.now()
    assert t.due(now) is True
    # 一度発火したら _next が前進していて、同じ now では二度発火しない
    assert t._next > now
    assert t.due(now) is False


# ---------------------- status() shape ----------------------


def test_status_before_start_reports_not_running():
    s = Scheduler(FakeLLM(), FakeTTS(), UtteranceQueue(4))
    s.triggers.append(ScheduledTrigger(name="x", cron="* * * * *",
                                       kind="fixed", text="hi"))
    st = s.status()
    assert st["running"] is False
    assert st["queue_size"] == 0
    assert len(st["triggers"]) == 1
    t0 = st["triggers"][0]
    assert t0["name"] == "x"
    assert t0["cron"] == "* * * * *"
    assert t0["kind"] == "fixed"
    # 発火履歴フィールド (D 対応)
    assert t0["fire_count"] == 0
    assert t0["last_fire"] is None
    assert t0["last_error"] is None


@pytest.mark.asyncio
async def test_start_without_triggers_is_noop():
    s = Scheduler(FakeLLM(), FakeTTS(), UtteranceQueue(4))
    await s.start()
    assert s._task is None  # ループは立ち上がらない
    await s.stop()  # 何もしないが例外も投げない


@pytest.mark.asyncio
async def test_stop_cancels_running_loop():
    s = Scheduler(FakeLLM(), FakeTTS(), UtteranceQueue(4))
    # 遠未来トリガを 1 件入れて start させる
    s.triggers.append(ScheduledTrigger(name="far", cron="0 4 * 1 *",
                                       kind="fixed", text="hi"))
    await s.start()
    assert s._task is not None
    # すぐ stop してもデッドロックしない
    await asyncio.wait_for(s.stop(), timeout=2.0)
    assert s._task is None
