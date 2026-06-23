"""LLM クラスの履歴永続化テスト (実 Ollama を叩かない)。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from history_store import HistoryStore
from llm import LLM, _clean_bot_text


@pytest.fixture()
def fake_ollama_response():
    """httpx.Client.post を差し替えて Ollama の応答を返すモック。"""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"message": {"content": "ぼっとの応答だよ"}}
    return mock_resp


def _make_llm(tmp_path: Path, *, persistent: bool, hydrate_only: bool = False):
    """テスト用 LLM。実 HTTP は飛ばさない (post をモック)。"""
    db = str(tmp_path / "h.sqlite") if persistent else None
    llm = LLM(
        host="http://localhost:11434",
        model="dummy",
        history_turns=4,
        max_sessions=8,
        history_db=db,
    )
    return llm


def test_in_memory_mode_history_not_persistent(tmp_path: Path, fake_ollama_response):
    llm1 = _make_llm(tmp_path, persistent=False)
    with patch.object(llm1._client, "post", return_value=fake_ollama_response):
        llm1.chat("alice", "ねえ X68 のこと")
    assert len(llm1._history["alice"]) == 2  # user + assistant

    # 別インスタンスを作る (再起動相当)。in-memory なので履歴は無い。
    llm2 = _make_llm(tmp_path, persistent=False)
    assert "alice" not in llm2._history
    assert llm2.status()["persistent"] is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("こんにちは。もっと聞きたい", "こんにちは。"),
        ("おはよう！\"); もっと聞きたい", "おはよう。"),
        ("楽しいね!もっと聞きたい!\"); もっと聞きたい", "楽しいね。"),
        ("それは聞いてみたい話だね。", "それは聞いてみたい話だね。"),
        ("X68000の話が好きで、 ", "X68000の話が好きで。"),
        ("X6800の話だよ", "X68000の話だよ"),
        ("うん、好きだよ。 レトロPCもいいね", "うん、好きだよ。 レトロPCもいいね。"),
    ],
)
def test_clean_bot_text_removes_only_banned_trailing_followup(raw: str, expected: str):
    assert _clean_bot_text(raw) == expected


def test_clean_bot_text_normalizes_marks_for_speech():
    assert _clean_bot_text("元気だよ！ぺけ子だよ?") == "元気だよ。ぺけ子だよ。"


def test_clean_bot_text_removes_off_topic_x68000_sentence():
    raw = "あたし、今日はぽかぽかしてるよ！X68000の話、聞いてくれてありがとう。"
    assert _clean_bot_text(raw, "今日なにしてた？") == "今日はサーバーのそばで待ってたよ。"


def test_clean_bot_text_handles_speech_test_prompt():
    raw = "X68000の音色、好きだよ！ ぺけ子ちゃんが、きらきらと語るよ。"
    assert _clean_bot_text(raw, "しゃべってみて") == "うん、聞こえてるよ。あたしはぺけ子だよ。"


def test_clean_bot_text_handles_status_prompt():
    raw = "元気だよ！ぺけ子ちゃんが元気だよ。"
    assert _clean_bot_text(raw, "こんにちは、調子はどう？") == "元気だよ。声をかけてくれてうれしいな。"


def test_clean_bot_text_handles_today_prompt():
    raw = "今日もレトロな音に包まれてるよ。"
    assert _clean_bot_text(raw, "今日なにしてた？") == "今日はサーバーのそばで待ってたよ。"


def test_clean_bot_text_handles_x68000_like_prompt():
    raw = "はい！X68000の色鮮やかな世界が大好きだよ。"
    assert _clean_bot_text(raw, "X68000って好き？") == "あたしはX68000が大好きだよ。"


def test_persistent_mode_survives_restart(tmp_path: Path, fake_ollama_response):
    llm1 = _make_llm(tmp_path, persistent=True)
    with patch.object(llm1._client, "post", return_value=fake_ollama_response):
        llm1.chat("alice", "覚えててね")
        llm1.chat("alice", "もう一回")
    assert len(llm1._history["alice"]) == 4

    # 別インスタンスを作っても同じ DB を見るので履歴が戻る
    llm2 = _make_llm(tmp_path, persistent=True)
    assert "alice" in llm2._history
    msgs = list(llm2._history["alice"])
    assert msgs[0] == ("user", "覚えててね")
    assert msgs[-1] == ("assistant", "ぼっとの応答だよ")
    assert llm2.status()["persistent"] is True


def test_persistent_mode_respects_history_turns_on_hydrate(tmp_path: Path):
    db = str(tmp_path / "h.sqlite")
    # history_turns=4 → 直近 8 メッセージだけハイドレートされる
    s = HistoryStore(db)
    for i in range(10):
        s.append("bob", "user", f"u{i}")
        s.append("bob", "assistant", f"a{i}")
    s.close()

    llm = LLM(host="http://x", model="m", history_turns=4, history_db=db)
    rows = list(llm._history["bob"])
    assert len(rows) == 8  # 4 turns * 2
    assert rows[0] == ("user", "u6")
    assert rows[-1] == ("assistant", "a9")


def test_reset_clears_both_memory_and_disk(tmp_path: Path, fake_ollama_response):
    llm = _make_llm(tmp_path, persistent=True)
    with patch.object(llm._client, "post", return_value=fake_ollama_response):
        llm.chat("alice", "hi")
    assert "alice" in llm._history
    llm.reset("alice")
    assert "alice" not in llm._history

    # 別インスタンスでも見えないことを確認
    llm2 = _make_llm(tmp_path, persistent=True)
    assert "alice" not in llm2._history


def test_chat_injects_time_context_as_second_system_message(
    tmp_path: Path, fake_ollama_response
):
    """LLM.chat() で 2 つ目の system message に [現在時刻: ...] が乗ること。"""
    llm = _make_llm(tmp_path, persistent=False)
    captured: dict = {}

    def fake_post(url, json):
        captured["payload"] = json
        return fake_ollama_response

    with patch.object(llm._client, "post", side_effect=fake_post):
        llm.chat("alice", "やあ")

    msgs = captured["payload"]["messages"]
    assert msgs[0]["role"] == "system"  # persona (SYSTEM_PROMPT)
    assert msgs[1]["role"] == "system"  # 時間文脈
    ctx = msgs[1]["content"]
    assert "現在時刻:" in ctx
    # フォーマット例: [現在時刻: 2026-05-22 (木) 14:30]
    assert ctx.startswith("[") and ctx.endswith("]")
    # 永続化 OFF なので「前回の会話」行は出ない
    assert "前回の会話" not in ctx


def test_chat_includes_time_of_day_flavor_by_default(
    tmp_path: Path, fake_ollama_response, monkeypatch
):
    """LLM_TIME_FLAVOR がデフォルト (=1) なら 3 つ目の system message にヒントが乗る。"""
    monkeypatch.delenv("LLM_TIME_FLAVOR", raising=False)
    llm = _make_llm(tmp_path, persistent=False)
    captured: dict = {}

    def fake_post(url, json):
        captured["payload"] = json
        return fake_ollama_response

    with patch.object(llm._client, "post", side_effect=fake_post):
        llm.chat("alice", "やあ")

    msgs = captured["payload"]["messages"]
    # 0: persona, 1: time context, 2: time-of-day flavor (時刻による)
    # 時間帯フレーバは 0-24 時の全帯域でカバーされているはずなので必ず付く
    assert len(msgs) >= 3
    assert msgs[2]["role"] == "system"
    assert msgs[2]["content"].startswith("[気分のヒント:")


def test_chat_skips_time_of_day_flavor_when_disabled(
    tmp_path: Path, fake_ollama_response, monkeypatch
):
    """LLM_TIME_FLAVOR=0 でフレーバは付かず、最初のユーザ msg がすぐ来る。"""
    monkeypatch.setenv("LLM_TIME_FLAVOR", "0")
    llm = _make_llm(tmp_path, persistent=False)
    captured: dict = {}

    def fake_post(url, json):
        captured["payload"] = json
        return fake_ollama_response

    with patch.object(llm._client, "post", side_effect=fake_post):
        llm.chat("alice", "やあ")

    msgs = captured["payload"]["messages"]
    # フレーバが抑制されると msgs = [persona, time, user] の 3 件で済む
    assert len(msgs) == 3
    assert msgs[2]["role"] == "user"
    assert "気分のヒント" not in msgs[1]["content"]


def test_chat_adds_recent_reply_hint_when_history_exists(
    tmp_path: Path, fake_ollama_response, monkeypatch
):
    monkeypatch.setenv("LLM_TIME_FLAVOR", "0")
    llm = _make_llm(tmp_path, persistent=False)

    with patch.object(llm._client, "post", return_value=fake_ollama_response):
        llm.chat("alice", "こんにちは")

    captured: dict = {}

    def fake_post(url, json):
        captured["payload"] = json
        return fake_ollama_response

    with patch.object(llm._client, "post", side_effect=fake_post):
        llm.chat("alice", "今日は天気が悪かった")

    msgs = captured["payload"]["messages"]
    assert msgs[2]["role"] == "system"
    assert "返答のヒント" in msgs[2]["content"]
    assert "ぼっとの応答だよ" in msgs[2]["content"]
    assert msgs[3]["role"] == "user"
    assert msgs[-1]["content"] == "今日は天気が悪かった"


def test_chat_includes_last_interaction_when_persistent(
    tmp_path: Path, fake_ollama_response
):
    """永続化 ON で過去発話があれば「前回の会話: ...前」が time-context に乗る。"""
    import time as _time

    llm = _make_llm(tmp_path, persistent=True)
    assert llm._store is not None
    # 1 時間前の会話を仕込む
    llm._store.append("alice", "user", "前の話", ts=_time.time() - 3600)
    llm._store.append("alice", "assistant", "ふむ", ts=_time.time() - 3600)

    captured: dict = {}

    def fake_post(url, json):
        captured["payload"] = json
        return fake_ollama_response

    with patch.object(llm._client, "post", side_effect=fake_post):
        llm.chat("alice", "また来たよ")

    ctx = captured["payload"]["messages"][1]["content"]
    assert "前回の会話:" in ctx
    # ~1 時間前なので "時間" の単位が入っているはず
    assert "時間" in ctx


def test_max_sessions_drops_oldest_on_overflow(tmp_path: Path, fake_ollama_response):
    db = str(tmp_path / "h.sqlite")
    llm = LLM(host="http://x", model="m",
              history_turns=2, max_sessions=2, history_db=db)
    with patch.object(llm._client, "post", return_value=fake_ollama_response):
        llm.chat("sid_old", "1")
        llm.chat("sid_mid", "2")
        llm.chat("sid_new", "3")
    # max_sessions=2 を超えたので sid_old が捨てられているはず
    assert "sid_old" not in llm._history
    assert "sid_mid" in llm._history
    assert "sid_new" in llm._history
    # DB からも消えている (drop されたら reset が走る)
    llm2 = LLM(host="http://x", model="m",
               history_turns=2, max_sessions=8, history_db=db)
    assert "sid_old" not in llm2._history
