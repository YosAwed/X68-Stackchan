"""LLM の会話履歴を SQLite に永続化する小さなストア。

LLM 側は (role, content) のペアを deque で保持していたが、これだと
uvicorn を再起動するたびに履歴が消えてしまい、ぺけ子ちゃんが「初対面」
状態になる。本モジュールは同じ (sid, role, content, ts) を SQLite に
append-only で書き出し、起動時にハイドレートする。

設計メモ:
    - スキーマは 1 テーブル messages のみで十分。インデックスは sid+ts
      のみ。retention は (sid 数, sid あたり turns) を読み出し時にトリム。
    - LLM.chat は asyncio.to_thread など複数スレッドから呼ばれ得るため、
      SQLite connection は check_same_thread=False で開き、各操作を RLock
      で直列化する。
    - in-memory モード (path=":memory:") もサポート — テスト用。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    sid     TEXT    NOT NULL,
    ts      REAL    NOT NULL,
    role    TEXT    NOT NULL CHECK(role IN ('user','assistant')),
    content TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_sid_ts ON messages(sid, ts);
"""


class HistoryStore:
    """SQLite を裏に持つ会話履歴の追記/読み出しストア。"""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._lock = threading.RLock()
        # path=":memory:" を許容するため check_same_thread=False。
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        log.info("HistoryStore opened: %s", self.path)

    def close(self) -> None:
        try:
            with self._lock:
                self._conn.close()
        except Exception:
            pass

    def append(self, sid: str, role: str, content: str, ts: float | None = None) -> None:
        if role not in ("user", "assistant"):
            raise ValueError(f"unknown role: {role!r}")
        if ts is None:
            ts = time.time()
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO messages(sid, ts, role, content) VALUES(?,?,?,?)",
                    (sid, ts, role, content),
                )

    def append_turn(
        self,
        sid: str,
        user_text: str,
        assistant_text: str,
        ts: float | None = None,
    ) -> None:
        """1 往復分の履歴を同一 transaction で追記する。"""
        if ts is None:
            ts = time.time()
        rows = [
            (sid, ts, "user", user_text),
            (sid, ts, "assistant", assistant_text),
        ]
        with self._lock:
            with self._conn:
                self._conn.executemany(
                    "INSERT INTO messages(sid, ts, role, content) VALUES(?,?,?,?)",
                    rows,
                )

    def load(self, sid: str, max_turns: int) -> list[tuple[str, str]]:
        """sid の直近 (max_turns * 2) メッセージを古い → 新しい順で返す。

        max_turns は user/assistant ペアの数。実 SQL の LIMIT は ×2。
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT role, content FROM messages WHERE sid=? "
                "ORDER BY ts DESC, id DESC LIMIT ?",
                (sid, max_turns * 2),
            )
            rows = cur.fetchall()
        # DESC で取り出したので reverse して時系列順に
        return [(r, c) for r, c in reversed(rows)]

    def known_sids(self) -> list[str]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT sid FROM messages GROUP BY sid ORDER BY MAX(ts) DESC")
            return [row[0] for row in cur.fetchall()]

    def last_ts(self, sid: str) -> float | None:
        """sid の直近メッセージの epoch 秒。履歴ゼロなら None。"""
        with self._lock:
            cur = self._conn.execute(
                "SELECT MAX(ts) FROM messages WHERE sid=?", (sid,))
            row = cur.fetchone()
        if row is None or row[0] is None:
            return None
        return float(row[0])

    def trim_to_max_sessions(self, max_sessions: int) -> int:
        """最終発話時刻の古い sid を捨てて max_sessions に揃える。
        戻り値は削除した sid 数。"""
        sids = self.known_sids()
        if len(sids) <= max_sessions:
            return 0
        # known_sids() は新→旧 (最新発話順) なので、後ろが古い
        to_drop = sids[max_sessions:]
        with self._lock:
            with self._conn:
                for sid in to_drop:
                    self._conn.execute("DELETE FROM messages WHERE sid=?", (sid,))
        log.info("HistoryStore trimmed %d old session(s)", len(to_drop))
        return len(to_drop)

    def reset(self, sid: str) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute("DELETE FROM messages WHERE sid=?", (sid,))
