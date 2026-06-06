"""main.py の /pull, /enqueue, /scheduler/status エンドポイントの結合テスト。

faster-whisper / Irodori / Ollama を CI で起動できないため、
import 前に stt / llm / tts モジュールを sys.modules で差し替え、
main.py が同じ名前空間で動くようにしてから FastAPI TestClient で叩く。
"""

from __future__ import annotations

import importlib
import sys
import types
from urllib.parse import unquote

import pytest

TEST_ENQUEUE_TOKEN = "test-token-xyz"


@pytest.fixture(scope="module")
def app_with_fakes(monkeypatch_module):
    """main.py を import する前に重い依存をフェイクモジュールに差し替える。"""
    # CI / テストでは pre-warm を抑制 (FakeTTS は動くが、無用なログ抑止)
    monkeypatch_module.setenv("TTS_PREWARM", "0")
    # /enqueue は ENQUEUE_TOKEN env が必要なのでテスト用トークンを仕込む
    monkeypatch_module.setenv("ENQUEUE_TOKEN", TEST_ENQUEUE_TOKEN)

    fake_stt = types.ModuleType("stt")

    class _FakeSTT:
        def __init__(self, *a, **kw):
            pass

        def transcribe(self, wav: bytes) -> str:
            return "user said something"

        def status(self):
            return {"ok": True}

    fake_stt.STT = _FakeSTT
    sys.modules["stt"] = fake_stt

    fake_llm = types.ModuleType("llm")

    class _FakeLLM:
        def __init__(self, *a, **kw):
            pass

        def chat(self, sid: str, text: str) -> str:
            return f"echo:{text}"

        def status(self):
            return {"ok": True}

    fake_llm.LLM = _FakeLLM
    sys.modules["llm"] = fake_llm

    fake_tts = types.ModuleType("tts")

    class _FakeTTS:
        backend = "fake"

        def __init__(self, *a, **kw):
            pass

        def synthesize(self, text: str) -> bytes:
            return b"RIFF\x00\x00\x00\x00WAVE" + text.encode("utf-8")

        def status(self):
            return {"ok": True, "backend": "fake"}

    fake_tts.TTS = _FakeTTS
    sys.modules["tts"] = fake_tts

    # スケジューラは無効化して main を import (既存の DEFAULT は SCHEDULE_ENABLED=0)
    if "main" in sys.modules:
        del sys.modules["main"]
    main = importlib.import_module("main")
    return main


def test_limit_spoken_text_prefers_complete_sentence(app_with_fakes, monkeypatch):
    monkeypatch.setenv("MAX_SPEAK_CHARS", "28")
    text = "あたしはX68000を大好きだよ。 レトロな音と光に心を奪われるね。"
    assert app_with_fakes._limit_spoken_text(text) == "あたしはX68000を大好きだよ。"


def test_limit_spoken_text_avoids_comma_fragment(app_with_fakes, monkeypatch):
    monkeypatch.setenv("MAX_SPEAK_CHARS", "18")
    text = "今日はサーバーを見ていたよ、音声も確認したよ。"
    assert app_with_fakes._limit_spoken_text(text) == "今日はサーバーを見ていたよ。"


@pytest.fixture()
def client(app_with_fakes):
    from fastapi.testclient import TestClient
    with TestClient(app_with_fakes.app) as c:
        yield c


# ---------------- /pull ----------------


def test_pull_returns_204_when_queue_empty(client, app_with_fakes):
    # キューが空の状態で wait=0 を投げると 204 になるはず
    # 念のため前のテストの残骸を排除
    while app_with_fakes.queue.size() > 0:
        app_with_fakes.queue._q.get_nowait()
    r = client.get("/pull?wait=0")
    assert r.status_code == 204


def test_pull_returns_wav_and_headers_when_queued(client, app_with_fakes):
    from utterance_queue import Utterance
    app_with_fakes.queue.push_nowait(Utterance(
        wav=b"RIFFWAVbody",
        bot_text="こんにちは",
        source="sched:test",
        emote="joy",
    ))
    r = client.get("/pull?wait=0")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/wav")
    assert r.content == b"RIFFWAVbody"
    assert unquote(r.headers["x-stackchan-bot-text"]) == "こんにちは"
    assert r.headers["x-stackchan-source"] == "sched:test"
    assert r.headers["x-stackchan-emote"] == "joy"


def test_pull_clamps_wait_into_range(client, app_with_fakes):
    # wait=-1 でもエラーにならず、即時に 204 (空キュー) を返す
    while app_with_fakes.queue.size() > 0:
        app_with_fakes.queue._q.get_nowait()
    r = client.get("/pull?wait=-1")
    assert r.status_code == 204


# ---------------- /enqueue ----------------


def test_enqueue_without_via_llm_uses_text_directly(client, app_with_fakes):
    # キューを空にしてから enqueue
    while app_with_fakes.queue.size() > 0:
        app_with_fakes.queue._q.get_nowait()
    r = client.post("/enqueue", data={"text": "やったー、X68 最高だね", "via_llm": "false"}, headers={"X-Stackchan-Token": TEST_ENQUEUE_TOKEN})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["bot_text"] == "やったー、X68 最高だね"
    assert body["emote"] == "joy"
    assert body["queue_size"] >= 1
    # 続けて /pull で取り出せて、ヘッダにも emote が乗る
    pull = client.get("/pull?wait=0")
    assert pull.status_code == 200
    assert b"RIFF" in pull.content  # FakeTTS は RIFF を返す
    assert pull.headers["x-stackchan-emote"] == "joy"


def test_enqueue_uses_wav_cache_when_enabled(tmp_path, app_with_fakes):
    """TTS_CACHE_DIR が有効な状態で同じ text を 2 回 enqueue したら、
    2 回目は TTS を呼ばずキャッシュから取れる。"""
    from fastapi.testclient import TestClient
    from wav_cache import WavCache

    # ランタイムでキャッシュ dir を差し替える
    original_cache = app_with_fakes.wav_cache
    app_with_fakes.wav_cache = WavCache(dir=tmp_path)

    # キューを空に
    while app_with_fakes.queue.size() > 0:
        app_with_fakes.queue._q.get_nowait()

    # synthesize の呼び出し回数を追跡するため、wrap して差し替える
    tts = app_with_fakes.tts
    original_synth = tts.synthesize
    counter = {"n": 0}

    def counting_synth(text: str) -> bytes:
        counter["n"] += 1
        return original_synth(text)

    tts.synthesize = counting_synth  # type: ignore[assignment]

    try:
        with TestClient(app_with_fakes.app) as c:
            r1 = c.post("/enqueue", data={"text": "同じこと", "via_llm": "false"}, headers={"X-Stackchan-Token": TEST_ENQUEUE_TOKEN})
            r2 = c.post("/enqueue", data={"text": "同じこと", "via_llm": "false"}, headers={"X-Stackchan-Token": TEST_ENQUEUE_TOKEN})
        assert r1.status_code == 200 and r2.status_code == 200
        assert counter["n"] == 1  # 2 回目はキャッシュヒットで TTS スキップ
    finally:
        tts.synthesize = original_synth  # type: ignore[assignment]
        app_with_fakes.wav_cache = original_cache


def test_chat_text_uses_wav_cache_when_enabled(tmp_path, app_with_fakes):
    from fastapi.testclient import TestClient
    from wav_cache import WavCache

    original_cache = app_with_fakes.wav_cache
    app_with_fakes.wav_cache = WavCache(dir=tmp_path)

    tts = app_with_fakes.tts
    original_synth = tts.synthesize
    counter = {"n": 0}

    def counting_synth(text: str) -> bytes:
        counter["n"] += 1
        return original_synth(text)

    tts.synthesize = counting_synth  # type: ignore[assignment]

    try:
        with TestClient(app_with_fakes.app) as c:
            r1 = c.post("/chat_text", data={"text": "同じ質問", "sid": "cache-test"})
            r2 = c.post("/chat_text", data={"text": "同じ質問", "sid": "cache-test"})
        assert r1.status_code == 200 and r2.status_code == 200
        assert counter["n"] == 1
    finally:
        tts.synthesize = original_synth  # type: ignore[assignment]
        app_with_fakes.wav_cache = original_cache


def test_enqueue_with_via_llm_routes_through_llm(client, app_with_fakes):
    while app_with_fakes.queue.size() > 0:
        app_with_fakes.queue._q.get_nowait()
    r = client.post("/enqueue", data={"text": "hi", "via_llm": "true"}, headers={"X-Stackchan-Token": TEST_ENQUEUE_TOKEN})
    assert r.status_code == 200
    # FakeLLM は "echo:hi" を返すので bot_text もそれになる
    assert r.json()["bot_text"] == "echo:hi"


def test_enqueue_rejects_empty_text(client):
    r = client.post("/enqueue", data={"text": "  "}, headers={"X-Stackchan-Token": TEST_ENQUEUE_TOKEN})
    assert r.status_code == 400
    assert "empty" in r.json().get("detail", "").lower()


def test_enqueue_returns_503_when_queue_full(client, app_with_fakes):
    # 既存キューを満たしてから一個多く投げる
    while app_with_fakes.queue.size() > 0:
        app_with_fakes.queue._q.get_nowait()
    cap = app_with_fakes.queue._q.maxsize
    for i in range(cap):
        ok = app_with_fakes.queue.push_nowait(
            __import__("utterance_queue").Utterance(b"a", f"t{i}", "x"))
        assert ok
    r = client.post("/enqueue", data={"text": "overflow"}, headers={"X-Stackchan-Token": TEST_ENQUEUE_TOKEN})
    assert r.status_code == 503


# ---------------- /chat_text emote reactions ----------------


def test_chat_text_returns_embarrassed_when_user_praises(client):
    """ユーザが「かわいい」と言ったら、bot_text の内容に関わらず
    X-Stackchan-Emote=embarrassed (はにかむ)。"""
    r = client.post("/chat_text", data={"text": "かわいいね、ぺけ子ちゃん"})
    assert r.status_code == 200
    assert r.headers["x-stackchan-emote"] == "embarrassed"


def test_chat_text_emote_follows_bot_text_when_no_praise(client):
    """褒めない普通の質問では bot_text の分類が反映される。"""
    # FakeLLM は "echo:..." を返すので、text が joy 系なら echo も joy
    r = client.post("/chat_text", data={"text": "X68 が好きなんだ"})
    assert r.status_code == 200
    # echo:X68 が好きなんだ → joy
    assert r.headers["x-stackchan-emote"] == "joy"


# ---------------- /scheduler/status ----------------


def test_scheduler_status_when_disabled(client):
    # SCHEDULE_ENABLED=0 (default) では enabled: False
    r = client.get("/scheduler/status")
    assert r.status_code == 200
    assert r.json() == {"enabled": False}


# ---------------- /admin ----------------


def test_admin_returns_html(client):
    r = client.get("/admin")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    assert "<title>Stack-chan admin</title>" in body
    # 主要なエンドポイントに JS で fetch している
    for fragment in ("/ready", "/scheduler/status", "/enqueue"):
        assert fragment in body
