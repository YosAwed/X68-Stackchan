"""Ollama HTTP API を叩く軽量クライアント。
会話履歴は session_id ごとにメモリで保持する。
"""

from __future__ import annotations

import logging
from collections import OrderedDict, deque
from typing import Deque, Dict, List

import httpx

from persona import SYSTEM_PROMPT

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

    def chat(self, session_id: str, user_text: str) -> str:
        hist = self._history.get(session_id)
        if hist is None:
            hist = deque(maxlen=self.history_turns * 2)
            self._history[session_id] = hist
        elif isinstance(self._history, OrderedDict):
            self._history.move_to_end(session_id)
        while len(self._history) > self.max_sessions:
            dropped, _ = self._history.popitem(last=False)
            log.info("LLM dropped old session history: %s", dropped)

        messages: List[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for role, content in hist:
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

        hist.append(("user", user_text))
        hist.append(("assistant", bot_text))
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
                "sessions": len(self._history),
                "max_sessions": self.max_sessions,
                "available_models": sorted(n for n in names if n),
            }
        except Exception as e:
            return {
                "ok": False,
                "host": self.host,
                "model": self.model,
                "sessions": len(self._history),
                "max_sessions": self.max_sessions,
                "error": str(e),
            }

    def reset(self, session_id: str) -> None:
        self._history.pop(session_id, None)
