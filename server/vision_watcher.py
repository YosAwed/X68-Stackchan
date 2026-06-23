"""Camera watcher that feeds visual observations into the utterance queue."""

from __future__ import annotations

import asyncio
import base64
import importlib
import logging
import re
import time
from collections import deque
from pathlib import Path
from typing import Callable

import httpx

from settings import settings
from utterance_queue import Utterance, UtteranceQueue

log = logging.getLogger("stackchan.vision")

_VISION_SYSTEM_PROMPT = (
    "あなたは画像の観察だけを行う。"
    "キャラクター、ペルソナ、プロンプト、指示、制約、推論過程は説明しない。"
    "画像の中で確実に見える具体物、人の動き、光や配置の変化を1つだけ述べる。"
    "あいまいなら、色、明るさ、配置、動きのどれかだけを短く述べる。"
    "毎回同じ言い回しにしない。"
    "見えないこと、ユーザーの意図、物語、感情、比喩は書かない。"
    "日本語の短い一文だけ。箇条書き、引用符、前置きは禁止。"
)

_VISION_USER_PROMPT = (
    "この画像で、机、画面、手、紙、キーボード、ケーブル、明るさ、色、動きなど、確実に見えるものを1つだけ、日本語8〜40字で述べて。"
    "悪い例: ユーザーはペケ子になりきるよう求めている。"
    "悪い例: キャラクター設定はX68000から生まれたロボット。"
    "悪い例: 画像を解析すると物体があります。"
    "悪い例: 何かがある。"
    "悪い例: そこに気配がある。"
    "良い例: 机の上にキーボードがある。"
    "良い例: 手元が少し動いている。"
    "良い例: 明るい画面が見える。"
    "良い例: 黒いケーブルが机にある。"
    "良い例: 右側が少し暗い。"
)

_VISION_BAD_TERMS = (
    "画像",
    "写真",
    "静止画",
    "カメラ",
    "フレーム",
    "検出",
    "解析",
    "物体",
    "推論",
    "AI",
    "ユーザー",
    "ペケ子",
    "ぺけ子",
    "キャラクター",
    "ペルソナ",
    "プロンプト",
    "指示",
    "制約",
    "設定",
    "名前",
    "視点",
    "応答",
    "生成",
    "X68000",
    "ロボット",
    "なりき",
    "微笑",
    "語る",
    "心躍",
)

_VISION_GENERIC_FRAGMENTS = (
    "何か見えた",
    "何かが見えた",
    "何かを見つけた",
    "目の前の様子",
    "気づいた感じ",
    "見えている",
    "見えます",
    "何かがある",
    "何かある",
    "なんだか存在感",
    "存在感",
    "気配",
    "作業中かな",
    "気になる",
    "ちゃんと見てた",
    "見てた",
    "目の前が少し変わった",
)

_VISION_FALLBACKS = (
    "机の明るさ、少し変わったね。",
    "端のほう、さっきより明るいかも。",
    "机まわり、少し並びが変わったね。",
    "近くの影がちょっと動いたよ。",
    "画面の光、今日は少しまぶしいね。",
    "手元のあたり、少しにぎやかだね。",
    "白っぽいところが少し増えたね。",
    "暗いところがゆっくり動いたよ。",
    "机の上、さっきより落ち着いたね。",
    "細かい輪郭が増えた感じだよ。",
    "明るい面がふっと広がったね。",
    "端っこの色、少し変わったみたい。",
)

_VISION_MOTION_FALLBACKS = (
    "手元の動き、今ちょっと見えたよ。",
    "影がすっと横に流れたね。",
    "机の端で小さく動いたよ。",
    "明るいところが一瞬ゆれたね。",
    "画面の前、少し動きがあったよ。",
    "輪郭がさっと変わった感じだよ。",
    "手元が少し忙しそうだね。",
    "端のほうがすっと動いたよ。",
)

_VISION_TONES = (
    "見えた名詞をそのまま拾う",
    "明るさの変化に触れる",
    "配置の変化に触れる",
    "動きを短く受け止める",
    "作業中の相手を邪魔しない",
    "少しだけいたずらっぽい",
)


class VisionWatcher:
    def __init__(
        self,
        *,
        llm,
        queue: UtteranceQueue,
        make_utterance: Callable[[str, str], Utterance],
        limit_text: Callable[[str], str],
    ) -> None:
        self.llm = llm
        self.queue = queue
        self.make_utterance = make_utterance
        self.limit_text = limit_text
        self._task: asyncio.Task | None = None
        self._cap = None
        self._running = False
        self._last_motion_at = 0.0
        self._last_capture_at = 0.0
        self._last_spoken_at = 0.0
        self._last_spoken_monotonic = 0.0
        self._last_text = ""
        self._recent_texts = deque(maxlen=8)
        self._vision_turn = 0
        self._last_error = ""
        self._motion_score = 0.0
        self._dropped = 0
        self._camera_read_failures = 0
        self._last_image_mtime = 0.0

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name="vision-watcher")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._release_camera()

    def status(self) -> dict:
        return {
            "enabled": bool(settings.VISION_ENABLED),
            "running": self._running,
            "mode": settings.VISION_MODE,
            "camera_index": settings.VISION_CAMERA_INDEX,
            "image_path": settings.VISION_IMAGE_PATH or None,
            "vision_provider": settings.VISION_PROVIDER,
            "vision_host": self._vision_host(),
            "vision_model": self._vision_model() or None,
            "poll_interval_s": settings.VISION_POLL_INTERVAL_S,
            "snapshot_interval_s": settings.VISION_SNAPSHOT_INTERVAL_S,
            "cooldown_s": settings.VISION_COOLDOWN_S,
            "motion_score": round(self._motion_score, 4),
            "last_motion_at": self._last_motion_at or None,
            "last_capture_at": self._last_capture_at or None,
            "last_spoken_at": self._last_spoken_at or None,
            "last_text": self._last_text,
            "last_error": self._last_error,
            "dropped": self._dropped,
        }

    async def _run(self) -> None:
        if settings.VISION_IMAGE_PATH:
            await self._run_image_file_loop()
            return

        try:
            cv2 = importlib.import_module("cv2")
        except Exception as exc:
            self._last_error = f"OpenCV import failed: {exc}"
            log.warning("Vision disabled: %s", self._last_error)
            return

        cap = cv2.VideoCapture(settings.VISION_CAMERA_INDEX)
        self._cap = cap
        if not cap.isOpened():
            self._last_error = f"camera {settings.VISION_CAMERA_INDEX} could not be opened"
            log.warning("Vision disabled: %s", self._last_error)
            self._release_camera()
            return

        self._running = True
        self._last_error = ""
        log.info(
            "Vision watcher started: camera=%s mode=%s provider=%s model=%s",
            settings.VISION_CAMERA_INDEX,
            settings.VISION_MODE,
            settings.VISION_PROVIDER,
            self._vision_model() or "local-summary",
        )

        try:
            if settings.VISION_MODE == "snapshot":
                await self._run_snapshot_loop(cv2, cap)
            else:
                await self._run_motion_loop(cv2, cap)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._last_error = f"vision loop failed: {exc}"
            log.exception("Vision watcher stopped by error")
        finally:
            self._running = False
            self._release_camera()

    async def _run_image_file_loop(self) -> None:
        path = Path(settings.VISION_IMAGE_PATH)
        self._running = True
        self._last_error = ""
        log.info(
            "Vision watcher started: image_path=%s mode=%s provider=%s model=%s",
            path,
            settings.VISION_MODE,
            settings.VISION_PROVIDER,
            self._vision_model() or "local-summary",
        )

        try:
            while True:
                if (time.monotonic() - self._last_spoken_monotonic) >= settings.VISION_COOLDOWN_S:
                    try:
                        stat = path.stat()
                    except FileNotFoundError:
                        self._last_error = f"image file not found: {path}"
                        await asyncio.sleep(min(10.0, settings.VISION_SNAPSHOT_INTERVAL_S))
                        continue

                    if stat.st_mtime > self._last_image_mtime:
                        jpeg = await asyncio.to_thread(path.read_bytes)
                        self._last_image_mtime = stat.st_mtime
                        self._last_capture_at = time.time()
                        self._last_error = ""
                        await self._react_to_jpeg(jpeg, None, None, None)

                await asyncio.sleep(min(10.0, settings.VISION_SNAPSHOT_INTERVAL_S))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._last_error = f"vision file loop failed: {exc}"
            log.exception("Vision watcher stopped by file-loop error")
        finally:
            self._running = False

    async def _run_snapshot_loop(self, cv2, cap) -> None:
        while True:
            if (time.monotonic() - self._last_spoken_monotonic) >= settings.VISION_COOLDOWN_S:
                ok, frame = cap.read()
                if not ok:
                    cap = await self._reopen_camera_after_read_failure(cv2, cap)
                    await asyncio.sleep(2.0)
                    continue
                else:
                    self._camera_read_failures = 0
                    self._last_error = ""
                    self._last_capture_at = time.time()
                    await self._react_to_frame(cv2, frame, None)

            await asyncio.sleep(settings.VISION_SNAPSHOT_INTERVAL_S)

    async def _run_motion_loop(self, cv2, cap) -> None:
        prev_gray = None
        while True:
            ok, frame = cap.read()
            if not ok:
                cap = await self._reopen_camera_after_read_failure(cv2, cap)
                await asyncio.sleep(settings.VISION_POLL_INTERVAL_S)
                continue
            self._camera_read_failures = 0

            gray = self._motion_frame(cv2, frame)
            if prev_gray is None:
                prev_gray = gray
                await asyncio.sleep(settings.VISION_POLL_INTERVAL_S)
                continue

            score, changed = self._motion_score_for(cv2, prev_gray, gray)
            prev_gray = gray
            self._motion_score = score

            if self._should_react(score, changed):
                self._last_motion_at = time.time()
                self._last_capture_at = self._last_motion_at
                await self._react_to_frame(cv2, frame, score)

            await asyncio.sleep(settings.VISION_POLL_INTERVAL_S)

    def _release_camera(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

    async def _reopen_camera_after_read_failure(self, cv2, cap):
        self._camera_read_failures += 1
        self._last_error = f"camera read failed ({self._camera_read_failures})"
        log.warning("Camera read failed; reopening camera %s", settings.VISION_CAMERA_INDEX)
        try:
            cap.release()
        except Exception:
            pass
        await asyncio.sleep(0.5)
        cap = cv2.VideoCapture(settings.VISION_CAMERA_INDEX)
        self._cap = cap
        if not cap.isOpened():
            self._last_error = f"camera {settings.VISION_CAMERA_INDEX} could not be reopened"
        return cap

    @staticmethod
    def _motion_frame(cv2, frame):
        small = cv2.resize(frame, (320, 240))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(gray, (21, 21), 0)

    @staticmethod
    def _motion_score_for(cv2, prev_gray, gray) -> tuple[float, int]:
        delta = cv2.absdiff(prev_gray, gray)
        thresh = cv2.threshold(delta, 25, 255, cv2.THRESH_BINARY)[1]
        changed = int(cv2.countNonZero(thresh))
        return changed / float(thresh.size), changed

    def _should_react(self, score: float, changed: int) -> bool:
        if changed < settings.VISION_MIN_CHANGED_PIXELS:
            return False
        if score < settings.VISION_MOTION_THRESHOLD:
            return False
        return (time.monotonic() - self._last_spoken_monotonic) >= settings.VISION_COOLDOWN_S

    async def _react_to_frame(self, cv2, frame, score: float | None) -> None:
        jpeg = self._encode_jpeg(cv2, frame)
        await self._react_to_jpeg(jpeg, cv2, frame, score)

    async def _react_to_jpeg(self, jpeg: bytes, cv2, frame, score: float | None) -> None:
        reservation = self.queue.reserve_nowait()
        if reservation is None:
            self._dropped += 1
            return

        try:
            raw_text = await self._describe_frame(cv2, frame, jpeg, score)
            observation = self._clean_vision_text(raw_text)
            if not observation and cv2 is not None and frame is not None:
                observation = self._clean_vision_text(self._local_scene_hint(cv2, frame))
            if observation:
                styled = await self._style_observation(observation)
                text = self._clean_vision_text(styled) or observation
            else:
                text = self._fallback_reaction(score)
            text = self.limit_text(text)
            utterance = await asyncio.to_thread(self.make_utterance, text, "vision")
            if not reservation.commit(utterance):
                self._dropped += 1
                return
            self._last_text = text
            self._remember_vision_text(text)
            self._last_spoken_at = time.time()
            self._last_spoken_monotonic = time.monotonic()
        except Exception as exc:
            reservation.release()
            self._last_error = f"vision reaction failed: {exc}"
            log.exception("Vision reaction failed")

    @staticmethod
    def _encode_jpeg(cv2, frame) -> bytes:
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        if not ok:
            raise RuntimeError("JPEG encode failed")
        return bytes(encoded)

    def _clean_vision_text(self, text: str) -> str:
        text = str(text or "").strip()
        if not text:
            return ""

        text = text.replace("\r", "\n")
        text = re.sub(r"```(?:[a-zA-Z0-9_-]+)?", "", text)
        text = text.replace("```", "")
        text = re.sub(
            r"^\s*(?:[-*・]\s*)?(?:ペケ子|回答|返答|一言|セリフ|発話)\s*[:：]\s*",
            "",
            text,
        )
        text = text.strip("「」『』\"'` \t\n")
        text = re.sub(r"\s+", " ", text)

        candidates = re.findall(r"[^。！？!?\n]+[。！？!?]?", text)
        if not candidates:
            candidates = [text.split("\n", 1)[0]]

        for candidate in candidates:
            candidate = candidate.strip("「」『』\"'` \t\n")
            if not candidate:
                continue
            if candidate[-1] not in "。！？!?":
                candidate = f"{candidate}。"
            if any(term in candidate for term in _VISION_BAD_TERMS):
                continue
            if any(fragment in candidate for fragment in _VISION_GENERIC_FRAGMENTS):
                continue
            if self._is_recent_vision_text(candidate):
                continue
            return candidate
        return ""

    def _fallback_reaction(self, score: float | None) -> str:
        options = list(_VISION_FALLBACKS)
        if score is not None and score >= max(settings.VISION_MOTION_THRESHOLD, 0.03):
            options = list(_VISION_MOTION_FALLBACKS)

        start = (self._vision_turn + int(time.time() // 7)) % len(options)
        for offset in range(len(options)):
            candidate = options[(start + offset) % len(options)]
            if not self._is_recent_vision_text(candidate):
                return candidate
        return options[start]

    def _remember_vision_text(self, text: str) -> None:
        normalized = self._normalize_vision_text(text)
        if normalized:
            self._recent_texts.append(normalized)
        self._vision_turn += 1

    def _is_recent_vision_text(self, text: str) -> bool:
        normalized = self._normalize_vision_text(text)
        if not normalized:
            return False
        if normalized == self._normalize_vision_text(self._last_text):
            return True
        return normalized in self._recent_texts

    @staticmethod
    def _normalize_vision_text(text: str) -> str:
        text = str(text or "")
        text = re.sub(r"[。！？!?、,. \t\r\n「」『』\"'`]", "", text)
        return text

    def _vision_user_prompt(self) -> str:
        return _VISION_USER_PROMPT

    async def _style_observation(self, observation: str) -> str:
        tone = _VISION_TONES[self._vision_turn % len(_VISION_TONES)]
        prompt = (
            "以下の観察だけを材料に、ペケ子の自然な一言へ直して。"
            "観察にないものは足さない。"
            "必ず観察中の名詞を1つ残す。"
            "画像、写真、カメラ、検出、解析、物体とは言わない。"
            "「何か」「気配」「存在感」「気になる」だけで終えない。"
            "日本語14〜38字の一文だけ。"
            "見えたものに対して軽く反応する。"
            "質問で終えすぎない。"
            f"今回の口調: {tone}。"
            f"{self._recent_text_prompt()}"
            f"観察: {observation}"
        )
        return await asyncio.to_thread(self.llm.chat, settings.VISION_SID, prompt)

    def _recent_text_prompt(self) -> str:
        recent = [text for text in list(self._recent_texts)[-4:] if text]
        if not recent:
            return "直近と似た定型文にしない。"
        joined = "、".join(recent)
        return f"直近のセリフと同じ言い回しは禁止: {joined}。"

    async def _describe_frame(self, cv2, frame, jpeg: bytes, score: float | None) -> str:
        vision_model = self._vision_model()
        if vision_model:
            try:
                if settings.VISION_PROVIDER in ("openai", "lmstudio"):
                    return await self._describe_frame_with_openai(jpeg)
                return await self._describe_frame_with_ollama(jpeg)
            except Exception as exc:
                self._last_error = f"vision model failed: {exc}"
                log.warning("Vision model failed, falling back to local prompt: %s", exc)

        if settings.VISION_MODE == "snapshot":
            if cv2 is None or frame is None:
                prompt = (
                    "明るさか配置が少し変わった。"
                    "ペケ子の自然な話し言葉で短く反応して。"
                    "具体物がない時は、明るさや配置の変化だけに触れる。"
                    "「何か」「気配」だけで終えない。"
                    "日本語14〜38字の一文だけ。画像、写真、カメラ、解析とは言わない。"
                    f"{self._recent_text_prompt()}"
                )
                return await asyncio.to_thread(self.llm.chat, settings.VISION_SID, prompt)

            hint = self._local_scene_hint(cv2, frame)
            prompt = (
                "見た目のヒントだけを使って、ペケ子の自然な話し言葉で短く反応して。"
                f"見た目のヒント: {hint}。"
                "明るさ、色、輪郭のうち1つを拾う。"
                "日本語14〜38字の一文だけ。画像、写真、カメラ、解析とは言わない。"
                f"{self._recent_text_prompt()}"
            )
            return await asyncio.to_thread(self.llm.chat, settings.VISION_SID, prompt)

        prompt = (
            "目の前で少し動きがあった。"
            "ペケ子の自然な話し言葉で、動きに短く反応して。"
            "手元、影、明るさなど動いた部分を1つ入れる。"
            "日本語14〜38字の一文だけ。画像、写真、カメラ、数値とは言わない。"
            f"動きの強さの参考: {(score or 0.0):.2%}。"
            f"{self._recent_text_prompt()}"
        )
        return await asyncio.to_thread(self.llm.chat, settings.VISION_SID, prompt)

    @staticmethod
    def _local_scene_hint(cv2, frame) -> str:
        small = cv2.resize(frame, (160, 120))
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        mean_h = float(hsv[:, :, 0].mean())
        mean_s = float(hsv[:, :, 1].mean())
        mean_v = float(hsv[:, :, 2].mean())

        if mean_v < 60:
            brightness = "暗め"
        elif mean_v > 180:
            brightness = "明るめ"
        else:
            brightness = "ほどよい明るさ"

        if mean_s < 35:
            color = "色味はひかえめ"
        elif mean_h < 10 or mean_h >= 170:
            color = "赤っぽい"
        elif mean_h < 25:
            color = "オレンジっぽい"
        elif mean_h < 40:
            color = "黄色っぽい"
        elif mean_h < 85:
            color = "緑っぽい"
        elif mean_h < 130:
            color = "青っぽい"
        else:
            color = "紫っぽい"

        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 80, 160)
        edge_score = cv2.countNonZero(edges) / float(edges.size)
        detail = "細かい輪郭が多い" if edge_score > 0.08 else "すっきりしている"
        return f"{brightness}、{color}、{detail}"

    def _vision_host(self) -> str:
        if settings.VISION_PROVIDER in ("openai", "lmstudio"):
            return settings.VISION_OPENAI_HOST or "http://127.0.0.1:1234"
        return settings.VISION_OLLAMA_HOST or settings.OLLAMA_HOST

    def _vision_model(self) -> str:
        if settings.VISION_PROVIDER in ("openai", "lmstudio"):
            return settings.VISION_OPENAI_MODEL
        return settings.VISION_OLLAMA_MODEL

    async def _describe_frame_with_ollama(self, jpeg: bytes) -> str:
        image_b64 = base64.b64encode(jpeg).decode("ascii")
        payload = {
            "model": settings.VISION_OLLAMA_MODEL,
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": _VISION_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": self._vision_user_prompt(),
                    "images": [image_b64],
                },
            ],
            "options": {"temperature": 0.6, "num_predict": 64},
        }
        async with httpx.AsyncClient(timeout=settings.VISION_OLLAMA_TIMEOUT_S) as client:
            host = self._vision_host()
            response = await client.post(f"{host.rstrip('/')}/api/chat", json=payload)
            response.raise_for_status()
        data = response.json()
        return str(data.get("message", {}).get("content", "")).strip()

    async def _describe_frame_with_openai(self, jpeg: bytes) -> str:
        image_b64 = base64.b64encode(jpeg).decode("ascii")
        payload = {
            "model": settings.VISION_OPENAI_MODEL,
            "stream": False,
            "temperature": 0.6,
            "max_tokens": 64,
            "messages": [
                {
                    "role": "system",
                    "content": _VISION_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": self._vision_user_prompt(),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_b64}",
                            },
                        },
                    ],
                },
            ],
        }
        headers = {}
        if settings.VISION_OPENAI_API_KEY:
            headers["Authorization"] = f"Bearer {settings.VISION_OPENAI_API_KEY}"

        async with httpx.AsyncClient(timeout=settings.VISION_OLLAMA_TIMEOUT_S) as client:
            response = await client.post(
                f"{self._vision_host().rstrip('/')}/v1/chat/completions",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        content = str(message.get("content", "") or "").strip()
        if not content and message.get("reasoning_content"):
            log.info("Vision model returned reasoning_content without visible content; ignoring it")
            return ""
        return content
