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
    "You only describe what is visible. "
    "Do not explain your role, prompts, reasoning, camera, image analysis, or hidden context. "
    "Name one concrete visible thing, motion, light, color, or layout detail. "
    "If unsure, mention only brightness, color, or layout. "
    "Return one short plain sentence. No bullets, quotes, labels, or preface."
)

_VISION_USER_PROMPT = (
    "Describe exactly one clearly visible thing in this scene in 3 to 10 simple English words. "
    "Prefer objects such as a desk, screen, hand, paper, keyboard, cable, wall, face, or light. "
    "Do not mention image, photo, camera, analysis, detection, user, character, robot, prompt, or AI. "
    "Do not use a list. Do not invent anything. "
    "Good examples: A keyboard on a desk. A bright screen. A hand near the desk. A dark cable."
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
    "image",
    "photo",
    "camera",
    "analysis",
    "detection",
    "object",
    "user",
    "character",
    "robot",
    "prompt",
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

_VISION_STYLED_BAD_FRAGMENTS = (
    "あ、あれ",
    "あれ",
    "その人は",
    "この人は",
    "人物は",
    "見える",
    "写って",
    "感じているよう",
    "しているよう",
    "ようだ",
    "そうだ",
    "そうな",
    "そうに",
    "そうだな",
    "らしい",
    "何か",
    "雰囲気",
)

_VISION_FALLBACKS = (
    "ん、今日は少し集中したい気分だな。",
    "よし、もう少しだけ進めてみよう。",
    "ちょっと考えを整理したいところだね。",
    "今は静かに続きを見ていたいな。",
    "ふう、少しだけ気持ちを整えよう。",
    "このまま、もう一歩だけ進めたいな。",
    "急がず、今できるところからだね。",
    "少し迷うけど、手は止めたくないな。",
)

_VISION_MOTION_FALLBACKS = (
    "よし、今のうちに片づけてしまおう。",
    "あ、次はこっちを見ればよさそうだ。",
    "少し急いでるけど、落ち着いていこう。",
    "この流れなら、もう少し進められそう。",
    "手を動かしてると、考えもまとまるね。",
    "さて、次の一手を決めたいところだな。",
)

_VISION_TONES = (
    "本人の小さな独り言",
    "集中している人の内心",
    "少し疲れたけど前向き",
    "次にやることを考えている",
    "静かに気持ちを整えている",
    "作業中の自然なつぶやき",
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
        # OpenCV VideoCapture は並行アクセス不可。バックグラウンドループと
        # /vision/capture (capture_once) のカメラ I/O をこのロックで直列化する。
        self._camera_lock = asyncio.Lock()
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

    async def capture_once(self, source: str = "vision:manual") -> Utterance:
        """Capture one still image now and return a synthesized utterance."""
        if settings.VISION_IMAGE_PATH:
            path = Path(settings.VISION_IMAGE_PATH)
            jpeg = await asyncio.to_thread(path.read_bytes)
            self._last_image_mtime = path.stat().st_mtime
            self._last_capture_at = time.time()
            self._last_error = ""
            return await self._utterance_from_jpeg(jpeg, None, None, None, source)

        try:
            cv2 = importlib.import_module("cv2")
        except Exception as exc:
            self._last_error = f"OpenCV import failed: {exc}"
            raise RuntimeError(self._last_error) from exc

        own_cap = False
        try:
            # カメラのオープン〜読み取りはバックグラウンドループと排他。
            # (合成などの重い処理はロック外で行う)
            async with self._camera_lock:
                cap = self._cap
                if cap is None:
                    cap = cv2.VideoCapture(settings.VISION_CAMERA_INDEX)
                    own_cap = True
                if not cap.isOpened():
                    raise RuntimeError(f"camera {settings.VISION_CAMERA_INDEX} could not be opened")
                frame = None
                for _ in range(3):
                    ok, frame = cap.read()
                    if not ok:
                        frame = None
                    await asyncio.sleep(0.03)
                if frame is None:
                    raise RuntimeError("camera read failed")
            self._last_capture_at = time.time()
            self._last_error = ""
            jpeg = self._encode_jpeg(cv2, frame)
            return await self._utterance_from_jpeg(jpeg, cv2, frame, None, source)
        finally:
            if own_cap:
                cap.release()

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
                async with self._camera_lock:
                    ok, frame = cap.read()
                if not ok:
                    async with self._camera_lock:
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
            async with self._camera_lock:
                ok, frame = cap.read()
            if not ok:
                async with self._camera_lock:
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
            utterance = await self._utterance_from_jpeg(jpeg, cv2, frame, score, "vision")
            if not reservation.commit(utterance):
                self._dropped += 1
                return
        except Exception as exc:
            reservation.release()
            self._last_error = f"vision reaction failed: {exc}"
            log.exception("Vision reaction failed")

    async def _utterance_from_jpeg(
        self,
        jpeg: bytes,
        cv2,
        frame,
        score: float | None,
        source: str,
    ) -> Utterance:
        raw_text = await self._describe_frame(cv2, frame, jpeg, score)
        observation = self._clean_vision_text(raw_text)
        if not observation and cv2 is not None and frame is not None:
            observation = self._clean_vision_text(self._local_scene_hint(cv2, frame))
        if observation:
            styled = await self._style_observation(observation)
            text = self._clean_styled_text(styled, observation) or self._thought_fallback(observation, score)
        else:
            text = self._fallback_reaction(score)
        text = self.limit_text(text)
        utterance = await asyncio.to_thread(self.make_utterance, text, source)
        self._last_text = text
        self._remember_vision_text(text)
        self._last_spoken_at = time.time()
        self._last_spoken_monotonic = time.monotonic()
        return utterance

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
        text = re.sub(r"(?:^|\s)[*•]\s+", "\n", text)
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
            candidate = re.sub(r"^\s*\d+[.)．、]\s*", "", candidate)
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

    def _thought_fallback(self, observation: str, score: float | None) -> str:
        obs = str(observation or "").lower()
        if ("above" in obs or "overhead" in obs) and ("look" in obs or "looking" in obs):
            options = (
                "上のほう、少し確認しておきたいな。",
                "あれ、上の様子をもう少し見たいな。",
                "上を見ながら、少し考えを整理しよう。",
            )
        elif "resting" in obs and ("chin" in obs or "hand" in obs):
            options = (
                "うーん、ちょっと考え込んじゃうな。",
                "ここは少し立ち止まって考えたいな。",
                "焦らず、もう少し考えてから動こう。",
            )
        elif "looking down" in obs or "look down" in obs:
            options = (
                "うーん、手元を見ながら整理しよう。",
                "少し下を向いて、考えをまとめたいな。",
                "今は静かに考えていたいところだ。",
            )
        elif "holding" in obs and any(word in obs for word in ("phone", "device", "smartphone", "black device")):
            options = (
                "この端末、どこまで見たっけ。",
                "端末を見ながら、少し考えを整理しよう。",
                "この端末の続き、落ち着いて確認しよう。",
            )
        elif any(word in obs for word in ("phone", "device", "smartphone", "black device")):
            options = (
                "この端末、もう少しだけ確認しよう。",
                "端末の続き、落ち着いて見ていこう。",
                "スマホの内容、少し整理したいな。",
            )
        elif any(word in obs for word in ("screen", "monitor", "display")):
            options = (
                "この画面、もう少し集中して見よう。",
                "画面の続き、落ち着いて確認したいな。",
                "よし、画面を見ながら考えをまとめよう。",
            )
        elif any(word in obs for word in ("keyboard", "desk", "paper")):
            options = (
                "机の上、少し整理してから進めよう。",
                "キーボードに戻って、少し進めたいな。",
                "この紙、あとでちゃんと確認しよう。",
                "机まわり、落ち着いて片づけたいな。",
            )
        elif "glasses" in obs or "eyeglasses" in obs:
            options = (
                "少し目が疲れたし、休みたいな。",
                "目元が重いから、少し整えよう。",
                "メガネ、あとで少し直そうかな。",
            )
        elif "lanyard" in obs or "strap" in obs or "badge" in obs:
            options = (
                "首元のストラップ、あとで整えよう。",
                "このストラップ、少し気になるな。",
                "ストラップを直して、気持ちも整えよう。",
            )
        elif "hand" in obs:
            options = (
                "手元、落ち着いて進めていこう。",
                "この手元の続き、もう少しやろう。",
                "手を動かしながら考えをまとめたいな。",
            )
        elif any(word in obs for word in ("man", "woman", "person", "face")):
            options = (
                "顔まわり、少し疲れてるかもな。",
                "うーん、顔を上げて少し考えよう。",
                "今は顔を伏せて、少し集中したいな。",
            )
        elif "明る" in obs or "brightness" in obs or "light" in obs:
            options = (
                "この明るさなら、少し落ち着けそうだ。",
                "明るさがちょうどいいし、集中しよう。",
                "この明るさなら、もう少し考えられそう。",
            )
        elif "黄色" in obs or "yellow" in obs:
            options = (
                "少し黄色っぽい光で、落ち着くな。",
                "この色味、なんだか集中しやすいな。",
                "黄色っぽい明かりで、少し考えよう。",
            )
        elif "輪郭" in obs or "edge" in obs or "detail" in obs:
            options = (
                "細かいところまで、もう少し見ておこう。",
                "輪郭が多いし、少し丁寧に確認しよう。",
                "細かい部分、落ち着いて見たいな。",
            )
        else:
            options = tuple(_VISION_MOTION_FALLBACKS if score else _VISION_FALLBACKS)

        start = (self._vision_turn + int(time.time() // 7)) % len(options)
        for offset in range(len(options)):
            candidate = options[(start + offset) % len(options)]
            if not self._is_recent_vision_text(candidate):
                return candidate
        return options[start]

    def _clean_styled_text(self, text: str, observation: str = "") -> str:
        candidate = self._clean_vision_text(text)
        if not candidate:
            return ""
        if len(self._normalize_vision_text(candidate)) <= 4:
            return ""
        if any(fragment in candidate for fragment in _VISION_STYLED_BAD_FRAGMENTS):
            return ""
        anchors = self._expected_anchor_terms(observation)
        if anchors and not any(anchor in candidate for anchor in anchors):
            return ""
        return candidate

    @staticmethod
    def _expected_anchor_terms(observation: str) -> tuple[str, ...]:
        obs = str(observation or "").lower()
        if ("above" in obs or "overhead" in obs) and ("look" in obs or "looking" in obs):
            return ("上", "見上", "確認")
        if "resting" in obs and ("chin" in obs or "hand" in obs):
            return ("考え", "迷", "整理", "立ち止")
        if "holding" in obs and any(word in obs for word in ("phone", "device", "smartphone", "black device")):
            return ("端末", "スマホ", "確認", "見")
        if any(word in obs for word in ("phone", "device", "smartphone", "black device")):
            return ("端末", "スマホ", "確認")
        if any(word in obs for word in ("screen", "monitor", "display")):
            return ("画面", "確認", "集中")
        if any(word in obs for word in ("keyboard", "desk", "paper")):
            return ("机", "キーボード", "紙", "整理", "確認")
        if "glasses" in obs or "eyeglasses" in obs:
            return ("目", "メガネ", "休", "整")
        if "lanyard" in obs or "strap" in obs or "badge" in obs:
            return ("ストラップ", "首元", "整")
        if "hand" in obs:
            return ("手", "手元", "進め", "考え")
        if "明る" in obs or "brightness" in obs or "light" in obs:
            return ("明る", "光", "集中", "落ち着")
        if "黄色" in obs or "yellow" in obs:
            return ("黄色", "色味", "明かり")
        if "輪郭" in obs or "edge" in obs or "detail" in obs:
            return ("輪郭", "細か", "確認")
        return ()

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
            "以下の観察を材料に、状況説明ではなく、そこに写っている本人が言いそうな短い会話や想いへ直して。"
            "人が写っているなら、その人の内心か独り言として自然にする。"
            "人がいない観察なら、持ち主がその場で言いそうな気持ちにする。"
            "観察にない具体的な事実は足さないが、気持ちは自然に補ってよい。"
            "断定しすぎず、ありそうな一言にする。"
            "机、画面、手、顔、スマホなど観察中の名詞を必要なら1つだけ残す。"
            "画像、写真、カメラ、検出、解析、物体とは言わない。"
            "見えたものを説明する文で終えない。"
            "「その人は」「この人は」「見える」「写っている」「ようだ」「ように見える」「そうだ」「そうだな」は禁止。"
            "「何か」「気配」「存在感」「気になる」「雰囲気」は禁止。"
            "日本語12〜34字の一文だけ。"
            "必ず一人称か独り言にする。"
            "例: よし、もう少しだけ集中しよう。"
            "例: うーん、少し考えを整理したいな。"
            "例: ここは落ち着いて確認しておこう。"
            "例: 少し寂しいけど、もう少し頑張ろう。"
            "質問で終えない。"
            f"今回の口調: {tone}。"
            f"{self._recent_text_prompt()}"
            f"観察: {observation}"
        )
        # remember=False: 長大な vision プロンプトを会話履歴 (in-memory / SQLite)
        # に蓄積させない。直近セリフの重複回避は _recent_texts 側で行っている。
        return await asyncio.to_thread(
            self.llm.chat, settings.VISION_SID, prompt, remember=False
        )

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
                return await asyncio.to_thread(
                    self.llm.chat, settings.VISION_SID, prompt, remember=False
                )

            hint = self._local_scene_hint(cv2, frame)
            prompt = (
                "見た目のヒントだけを使って、ペケ子の自然な話し言葉で短く反応して。"
                f"見た目のヒント: {hint}。"
                "明るさ、色、輪郭のうち1つを拾う。"
                "日本語14〜38字の一文だけ。画像、写真、カメラ、解析とは言わない。"
                f"{self._recent_text_prompt()}"
            )
            return await asyncio.to_thread(
                self.llm.chat, settings.VISION_SID, prompt, remember=False
            )

        prompt = (
            "目の前で少し動きがあった。"
            "ペケ子の自然な話し言葉で、動きに短く反応して。"
            "手元、影、明るさなど動いた部分を1つ入れる。"
            "日本語14〜38字の一文だけ。画像、写真、カメラ、数値とは言わない。"
            f"動きの強さの参考: {(score or 0.0):.2%}。"
            f"{self._recent_text_prompt()}"
        )
        return await asyncio.to_thread(
            self.llm.chat, settings.VISION_SID, prompt, remember=False
        )

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
