"""Ollama HTTP API を叩く軽量クライアント。
会話履歴は session_id ごとにメモリで保持する。
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
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
    ):
        self.host = host.rstrip("/")
        self.model = model
        self.history_turns = history_turns
        self._client = httpx.Client(base_url=self.host, timeout=timeout_s)
        # {sid: deque[(role, content)]}  role in {"user", "assistant"}
        self._history: Dict[str, Deque[tuple[str, str]]] = defaultdict(
            lambda: deque(maxlen=self.history_turns * 2)
        )

    def chat(self, session_id: str, user_text: str) -> str:
        hist = self._history[session_id]
        messages: List[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for role, content in hist:
            messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_text})

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "num_predict": 200,    # スタックちゃんは短く返す
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

    def reset(self, session_id: str) -> None:
        self._history.pop(session_id, None)
