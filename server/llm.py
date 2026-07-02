"""Ollama HTTP API を叩く軽量クライアント。

会話履歴は session_id ごとに保持する。デフォルトは in-memory deque だが、
``history_db`` を渡せば SQLite に永続化される (uvicorn 再起動で消えない)。
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime
from typing import Deque, Dict, List

import httpx
from history_store import HistoryStore
from persona import SYSTEM_PROMPT
from settings import settings

_WEEKDAY_JA = ("月", "火", "水", "木", "金", "土", "日")

_BANNED_TRAILING_FOLLOWUP_RE = re.compile(
    r'(?:[!！?？。]*\s*(?:["”』）)\]]\s*)?;?\s*)?'
    r'(?:もっと聞きたい|もっとききたい|もっと聞かせて)'
    r'[!！?？。、\s]*$'
)
_TRAILING_BROKEN_RE = re.compile(r"[、,\s]+$")
_NATURAL_SENTENCE_END_RE = re.compile(r"(。|だよ|だね|かな|です|ます)$")
_SPEECH_TEST_RE = re.compile(r"(しゃべ|喋|話し|声.*出|音.*出|テスト)")
_STATUS_PROMPT_RE = re.compile(r"(調子|元気|げんき|具合)")
_TODAY_PROMPT_RE = re.compile(r"(今日|きょう).*(何|なに).*?(した|してた|してる)")
_X68000_TOPIC_RE = re.compile(r"(X68000|X68|エックス|レトロ\s*PC|Human68k|X-BASIC|SX-Window)", re.IGNORECASE)
_X68000_LIKE_RE = re.compile(r"(X68000|X68|エックス).*(好き|すき)|(?:好き|すき).*(X68000|X68|エックス)", re.IGNORECASE)
_POETIC_FRAGMENT_RE = re.compile(r"(きらきら|色鮮やか|光|音色|心を|小さな手|思い出す)")


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


def _sentences(text: str) -> list[str]:
    found = re.findall(r"[^。！？!?]+[。！？!?]?", text)
    return [s.strip() for s in found if s.strip()]


def _clean_bot_text(text: str, user_text: str = "") -> str:
    """音声向けに、禁止した定型誘導と未完の末尾だけを整える。"""
    cleaned = text.strip()
    while True:
        next_text = _BANNED_TRAILING_FOLLOWUP_RE.sub("", cleaned).rstrip()
        if next_text == cleaned:
            break
        cleaned = next_text.rstrip('!！?？。;；"”』）)] ')
    cleaned = " ".join(cleaned.split())
    cleaned = cleaned.replace("！", "。").replace("!", "。")
    cleaned = cleaned.replace("？", "。").replace("?", "。")
    cleaned = re.sub(r"X6800(?!0)", "X68000", cleaned)
    if _SPEECH_TEST_RE.search(user_text):
        cleaned = "うん、聞こえてるよ。あたしはぺけ子だよ。"
    elif _STATUS_PROMPT_RE.search(user_text):
        cleaned = "元気だよ。声をかけてくれてうれしいな。"
    elif _TODAY_PROMPT_RE.search(user_text):
        cleaned = "今日はサーバーのそばで待ってたよ。"
    elif _X68000_LIKE_RE.search(user_text):
        cleaned = "あたしはX68000が大好きだよ。"
    elif user_text and not _X68000_TOPIC_RE.search(user_text):
        kept = [
            s for s in _sentences(cleaned)
            if not _X68000_TOPIC_RE.search(s) and not _POETIC_FRAGMENT_RE.search(s)
        ]
        if kept:
            cleaned = "".join(kept)
    cleaned = _TRAILING_BROKEN_RE.sub("", cleaned)
    if cleaned and not _NATURAL_SENTENCE_END_RE.search(cleaned):
        cleaned += "。"
    return cleaned


# 時間帯ごとの「気分のフレーバ」。空文字なら系列を省略する。
# 「[気分のヒント: ...]」として system message に渡し、LLM に乗せる。
# 細かいセリフは指示しない — 雰囲気だけ示してキャラに任せる方が破綻しにくい。
_TIME_OF_DAY_FLAVORS: list[tuple[int, int, str]] = [
    (0,  5,  "深夜なので小声で、少し眠そう、ぼそぼそ気味の雰囲気"),
    (5,  9,  "早朝で、まだ目覚めきれてない感じ。あくび混じりに短く"),
    (9,  12, "午前で元気めの調子。テンポ良く、軽快に"),
    (12, 14, "お昼時。お腹空いてる感を少し滲ませても OK"),
    (14, 17, "午後の眠気がじわっと来る時間。ちょっとぼんやり目"),
    (17, 21, "夕方〜夜の入り口。落ち着いた声色、リラックスしてる雰囲気"),
    (21, 24, "夜遅め。ふんわり眠そう、しっとり目の雰囲気"),
]


def _time_of_day_hint(now: datetime | None = None) -> str:
    """現在時刻の時間帯フレーバ文字列。当てはまらない場合は空文字。"""
    if now is None:
        now = datetime.now()
    h = now.hour
    for lo, hi, flavor in _TIME_OF_DAY_FLAVORS:
        if lo <= h < hi:
            return f"[気分のヒント: {flavor}]"
    return ""


def _recent_reply_hint(history_snapshot: list[tuple[str, str]]) -> str:
    replies: list[str] = []
    for role, content in history_snapshot:
        if role != "assistant":
            continue
        normalized = " ".join(str(content or "").split())
        if normalized and normalized not in replies:
            replies.append(normalized)

    replies = replies[-3:]
    if not replies:
        return ""

    joined = " / ".join(replies)
    return (
        "[返答のヒント: 直近と同じ文や同じ慰め方を避ける。"
        "ユーザー発話の具体語を一つ拾って返す。"
        f"直近の返答: {joined}]"
    )


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
        # 同一 sid への並行 chat() を直列化するロック。これが無いと両者が
        # 同じ履歴スナップショットで Ollama を叩き、履歴が lost update する。
        self._session_locks: Dict[str, threading.Lock] = {}
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

    def _session_lock(self, session_id: str) -> threading.Lock:
        with self._history_lock:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = threading.Lock()
                self._session_locks[session_id] = lock
            return lock

    def chat(self, session_id: str, user_text: str, *, remember: bool = True) -> str:
        # スナップショット取得 → Ollama 呼び出し → 履歴 append までを
        # セッション単位で直列化する (別セッション同士は並行できる)。
        # remember=False は履歴に残さない one-shot 呼び出し (vision 用など)。
        with self._session_lock(session_id):
            return self._chat_locked(session_id, user_text, remember=remember)

    def _chat_locked(self, session_id: str, user_text: str, *, remember: bool) -> str:
        with self._history_lock:
            hist = self._history.get(session_id)
            if hist is not None and remember and isinstance(self._history, OrderedDict):
                self._history.move_to_end(session_id)
            history_snapshot = list(hist) if hist is not None else []

        # 時間文脈を 2 つめの system message として注入。SYSTEM_PROMPT は不変
        # のままなので、Ollama 側のプロンプトキャッシュは効いたままになる。
        last_ts = (self._store.last_ts(session_id)
                   if self._store is not None else None)
        time_ctx = _build_time_context(last_ts)
        messages: List[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": time_ctx},
        ]
        # 3 つ目: 時間帯ごとの気分フレーバ。空文字なら付けない (LLM への
        # ノイズを増やさない)。env で無効化したい場合 LLM_TIME_FLAVOR=0。
        if settings.is_time_flavor_enabled():
            flavor = _time_of_day_hint()
            if flavor:
                messages.append({"role": "system", "content": flavor})
        reply_hint = _recent_reply_hint(history_snapshot)
        if reply_hint:
            messages.append({"role": "system", "content": reply_hint})
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
        bot_text = _clean_bot_text(data["message"]["content"], user_text)
        log.info("LLM ◀ %r", bot_text)

        if not remember:
            # one-shot: in-memory 履歴にも SQLite にも残さない。
            return bot_text

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
                self._session_locks.pop(dropped, None)
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

    def close(self) -> None:
        """HTTP コネクションと履歴 DB を明示的に閉じる (lifespan shutdown 用)。"""
        try:
            self._client.close()
        except Exception:
            log.exception("httpx client close failed")
        if self._store is not None:
            self._store.close()

    def reset(self, session_id: str) -> None:
        with self._history_lock:
            self._history.pop(session_id, None)
            self._session_locks.pop(session_id, None)
        if self._store is not None:
            self._store.reset(session_id)

    def _session_count(self) -> int:
        with self._history_lock:
            return len(self._history)
