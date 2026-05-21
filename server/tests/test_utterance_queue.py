"""UtteranceQueue の単体テスト。"""

from __future__ import annotations

import asyncio

import pytest

from utterance_queue import Utterance, UtteranceQueue


@pytest.mark.asyncio
async def test_empty_pull_returns_none_immediately():
    q = UtteranceQueue(max_size=2)
    assert q.size() == 0
    assert await q.pull(timeout_s=0) is None


@pytest.mark.asyncio
async def test_push_then_pull_roundtrip():
    q = UtteranceQueue(max_size=2)
    u = Utterance(wav=b"RIFFDATA", bot_text="hello", source="test:1")
    assert q.push_nowait(u)
    assert q.size() == 1

    got = await q.pull(timeout_s=0)
    assert got is not None
    assert got.wav == b"RIFFDATA"
    assert got.bot_text == "hello"
    assert got.source == "test:1"
    assert q.size() == 0


@pytest.mark.asyncio
async def test_long_poll_times_out_when_empty():
    q = UtteranceQueue(max_size=2)
    # short timeout to keep the test fast but still exercise wait_for path
    got = await asyncio.wait_for(q.pull(timeout_s=0.2), timeout=1.0)
    assert got is None


@pytest.mark.asyncio
async def test_long_poll_wakes_on_push():
    q = UtteranceQueue(max_size=2)
    u = Utterance(wav=b"X", bot_text="t", source="test:2")

    async def push_later():
        await asyncio.sleep(0.05)
        q.push_nowait(u)

    pusher = asyncio.create_task(push_later())
    got = await asyncio.wait_for(q.pull(timeout_s=2.0), timeout=3.0)
    await pusher
    assert got is u


@pytest.mark.asyncio
async def test_push_to_full_queue_is_dropped_safely():
    q = UtteranceQueue(max_size=1)
    assert q.push_nowait(Utterance(b"a", "t", "x"))
    # 満杯なら False を返し、例外は投げない (Scheduler が死なないように)
    assert q.push_nowait(Utterance(b"b", "t", "y")) is False
    assert q.size() == 1
