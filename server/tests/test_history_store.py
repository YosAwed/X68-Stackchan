"""HistoryStore (SQLite 永続化) のテスト。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from history_store import HistoryStore


def test_append_and_load_roundtrip(tmp_path: Path):
    db = tmp_path / "h.sqlite"
    s = HistoryStore(db)
    s.append("alice", "user", "こんにちは")
    s.append("alice", "assistant", "やあ")
    s.append("alice", "user", "X68 好き")
    s.append("alice", "assistant", "あたしも大好き")
    rows = s.load("alice", max_turns=4)
    assert rows == [
        ("user", "こんにちは"),
        ("assistant", "やあ"),
        ("user", "X68 好き"),
        ("assistant", "あたしも大好き"),
    ]
    s.close()


def test_load_respects_max_turns(tmp_path: Path):
    s = HistoryStore(tmp_path / "h.sqlite")
    for i in range(6):
        s.append("bob", "user", f"u{i}")
        s.append("bob", "assistant", f"a{i}")
    # 直近 2 ターン (4 メッセージ) だけ取れる
    rows = s.load("bob", max_turns=2)
    assert rows == [
        ("user", "u4"), ("assistant", "a4"),
        ("user", "u5"), ("assistant", "a5"),
    ]


def test_persistence_across_instances(tmp_path: Path):
    db = tmp_path / "h.sqlite"
    s1 = HistoryStore(db)
    s1.append("carol", "user", "覚えてる?")
    s1.append("carol", "assistant", "うん、覚えてる")
    s1.close()
    # 別インスタンス (再起動相当) で同じ DB を開く
    s2 = HistoryStore(db)
    rows = s2.load("carol", max_turns=4)
    assert rows == [("user", "覚えてる?"), ("assistant", "うん、覚えてる")]
    s2.close()


def test_unknown_sid_returns_empty(tmp_path: Path):
    s = HistoryStore(tmp_path / "h.sqlite")
    assert s.load("nobody", max_turns=4) == []


def test_reject_invalid_role(tmp_path: Path):
    s = HistoryStore(tmp_path / "h.sqlite")
    with pytest.raises(ValueError):
        s.append("x", "system", "should not be stored here")


def test_trim_to_max_sessions(tmp_path: Path):
    s = HistoryStore(tmp_path / "h.sqlite")
    t0 = time.time()
    # 4 つの sid を、明確に異なるタイムスタンプで作る
    for i, sid in enumerate(("oldest", "older", "newer", "newest")):
        s.append(sid, "user", "hi", ts=t0 + i)
    assert len(s.known_sids()) == 4
    dropped = s.trim_to_max_sessions(2)
    assert dropped == 2
    remaining = set(s.known_sids())
    # 残るのは新しい 2 つ
    assert remaining == {"newer", "newest"}


def test_reset_specific_sid(tmp_path: Path):
    s = HistoryStore(tmp_path / "h.sqlite")
    s.append("alice", "user", "hi")
    s.append("bob", "user", "yo")
    s.reset("alice")
    assert s.load("alice", 4) == []
    # bob の履歴は無傷
    assert s.load("bob", 4) == [("user", "yo")]


def test_in_memory_db_works():
    s = HistoryStore(":memory:")
    s.append("alice", "user", "hi")
    rows = s.load("alice", max_turns=1)
    assert rows == [("user", "hi")]
    s.close()
