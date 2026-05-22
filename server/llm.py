"""Ollama HTTP API を叩く軽量クライアント。

会話履歴は session_id ごとに保持する。デフォルトは in-memory deque だが、
``history_db`` を渡せば SQLite に永続化される (uvicorn 再起動で消えない)。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime
from typing import Deque, Dict, List

import httpx
from history_store import HistoryStore
from persona import SYSTEM_PROMPT

_WEEKDAY_JA = ("月", "火", "水", "木", "金", "土", "日")


def _format_jp_duration(seconds: float) -> str:
    """秒を「3 時間 12 分前」のような短い和文へ。"""
    s = max(0.0, seconds)
    if s < 60:
        return "ついさっき"
    minutes = int(s // 60)
    if minutes < 60:
        return f"{minutes} 分前"
    hours = minutes // 60
    rem_min = minutes % 60
    if hours < 24:
        if rem_min == 0:
            return f"{hours} 時間前"
        return f"{hours} 時間 {rem_min} 分前"
    days = hours // 24
    rem_hr = hours % 24
    if rem_hr == 0:
        return f"{days} 日前"
    return f"{days} 日 {rem_hr} 時間前"


def _build_time_context(last_ts: float | None) -> str:
    """LLM に渡す time-context 文字列を組み立てる。

    例: `[現在時刻: 2026-05-22 (木) 14:30  前回の会話: 3 時間 12 分前]`
    """
    now = datetime.now()
    weekday = _WEEKDAY_JA[now.weekday()]
    parts = [f"現在時刻: {now.strftime('%Y-%m-%d')} ({weekday}) {now.strftime('%H:%M')}"]
    if last_ts is not None:
        elapsed = max(0.0, time.time() - last_ts)
        parts.append(f"前回の会話: {_format_jp_duration(elapsed)}")
    return "[" + "  ".join(parts) + "]"

log = logging.getLogger(__name__)


class LLM:
    def __init__(
        self,
        host: str,
        model: str,
        history_turns: int = 6,
        timeout_s: float = 60.0,
        temperature: float = 0.7,
        num_predict: int = 200,
        max_sessions: int = 16,
        history_db: str | None = None,
    ):
        self.host = host.rstrip("/")
        self.model = model
        self.history_turns = history_turns
        self.temperature = temperature
        self.num_predict = num_predict
        self.max_sessions = max(1, max_sessions)
        self._client = httpx.Client(base_url=self.host, timeout=timeout_s)
        # {sid: deque[(role, content)]}  role in {"user", "assistant"}
        self._history: Dict[str, Deque[tuple[str, str]]] = OrderedDict()
        self._history_lock = threading.RLock()
        # 永続化が有効な場合のみ SQLite ストアを開き、起動時に既存履歴をハイドレート。
        self._store: HistoryStore | None = None
        if history_db:
            self._store = HistoryStore(history_db)
            self._store.trim_to_max_sessions(self.max_sessions)
            for sid in reversed(self._store.known_sids()):
                # known_sids() は新→旧。OrderedDict は古い順に append したいので reverse。
                rows = self._store.load(sid, self.history_turns)
                if not rows:
                    continue
                hist: Deque[tuple[str, str]] = deque(maxlen=self.history_turns * 2)
                hist.extend(rows)
                self._history[sid] = hist
            log.info("LLM hydrated %d session(s) from %s",
                     len(self._history), history_db)

    def chat(self, session_id: str, user_text: str) -> str:
        with self._history_lock:
            hist = self._history.get(session_id)
            if hist is None:
                hist = deque(maxlen=self.history_turns * 2)
                self._history[session_id] = hist
            elif isinstance(self._history, OrderedDict):
                self._history.move_to_end(session_id)

            history_snapshot = list(hist)

        # 時間文脈を 2 つめの system message として注入。SYSTEM_PROMPT は不変
        # のままなので、Ollama 側のプロンプトキャッシュは効いたままになる。
        last_ts = (self._store.last_ts(session_id)
                   if self._store is not None else None)
        time_ctx = _build_time_context(last_ts)
        messages: List[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": time_ctx},
        ]
        for role, content in history_snapshot:
            messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_text})

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.num_predict,    # スタックちゃんは短く返す
            },
        }
        log.info("LLM ▶ %r", user_text)
        r = self._client.post("/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()
        bot_text = data["message"]["content"].strip()
        log.info("LLM ◀ %r", bot_text)

        dropped_sids: list[str] = []
        with self._history_lock:
            hist = self._history.get(session_id)
            if hist is None:
                hist = deque(maxlen=self.history_turns * 2)
                self._history[session_id] = hist
            elif isinstance(self._history, OrderedDict):
                self._history.move_to_end(session_id)
            hist.append(("user", user_text))
            hist.append(("assistant", bot_text))
            while len(self._history) > self.max_sessions:
                dropped, _ = self._history.popitem(last=False)
                dropped_sids.append(dropped)
                log.info("LLM dropped old session history: %s", dropped)

        if self._store is not None:
            try:
                self._store.append_turn(session_id, user_text, bot_text)
                for dropped in dropped_sids:
                    self._store.reset(dropped)
            except Exception:
                # 永続化失敗は致命的でない (in-memory 履歴は更新済み)
                log.exception("history_store append failed (sid=%s)", session_id)
        return bot_text

    def status(self) -> dict:
        try:
            r = self._client.get("/api/tags", timeout=2.0)
            r.raise_for_status()
            models = r.json().get("models", [])
            names = {m.get("name") for m in models}
            return {
                "ok": self.model in names,
                "host": self.host,
                "model": self.model,
                "temperature": self.temperature,
                "num_predict": self.num_predict,
                "sessions": self._session_count(),
                "max_sessions": self.max_sessions,
                "persistent": self._store is not None,
                "available_models": sorted(n for n in names if n),
            }
        except Exception as e:
            return {
                "ok": False,
                "host": self.host,
                "model": self.model,
                "sessions": self._session_count(),
                "max_sessions": self.max_sessions,
                "persistent": self._store is not None,
                "error": str(e),
            }

    def reset(self, session_id: str) -> None:
        with self._history_lock:
            self._history.pop(session_id, None)
        if self._store is not None:
            self._store.reset(session_id)

    def _session_count(self) -> int:
        with self._history_lock:
            return len(self._history)
