"""Camera watcher that feeds visual observations into the utterance queue."""

from __future__ import annotations

import asyncio
import base64
import importlib
import logging
import time
from pathlib import Path
from typing import Callable

import httpx

from settings import settings
from utterance_queue import Utterance, UtteranceQueue

log = logging.getLogger("stackchan.vision")


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
            text = await self._describe_frame(cv2, frame, jpeg, score)
            text = self.limit_text(text.strip() or "いま、目の前の様子が少し変わった気がする。")
            utterance = await asyncio.to_thread(self.make_utterance, text, "vision")
            if not reservation.commit(utterance):
                self._dropped += 1
                return
            self._last_text = text
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
                    "カメラで静止画を1枚見たよ。"
                    "画像モデルの説明は取れなかった。"
                    "ペケ子として、目の前に何かを見つけた感じで日本語20〜45字で一言だけ反応して。"
                )
                return await asyncio.to_thread(self.llm.chat, settings.VISION_SID, prompt)

            hint = self._local_scene_hint(cv2, frame)
            prompt = (
                "カメラで静止画を1枚見たよ。"
                f"軽量解析では「{hint}」に見える。"
                "ペケ子として、目の前の様子に気づいた感じで日本語20〜45字で一言だけ反応して。"
            )
            return await asyncio.to_thread(self.llm.chat, settings.VISION_SID, prompt)

        prompt = (
            "カメラの前で何かが動いたよ。"
            "ペケ子として、見えたものに気づいた感じで日本語20〜45字で一言だけ反応して。"
            f"動きの大きさは{(score or 0.0):.2%}くらい。"
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
                    "content": (
                        "あなたは小さなロボットのペケ子です。"
                        "カメラ画像で目立つ物体や動きを1つだけ選び、"
                        "親しみのある日本語で短く話します。"
                    ),
                },
                {
                    "role": "user",
                    "content": "画像を見て、何に気づいたか20〜45字で一言だけ返して。",
                    "images": [image_b64],
                },
            ],
            "options": {"temperature": 0.7, "num_predict": 80},
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
            "temperature": 0.7,
            "max_tokens": 80,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "あなたは小さなロボットのペケ子です。"
                        "カメラ画像で目立つ物体を1つだけ選び、"
                        "親しみのある日本語で短く話します。"
                        "推論過程や説明は書かず、最終回答の一文だけ返します。"
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "画像を見て、何に気づいたか20〜45字で一言だけ返して。推論過程は不要です。",
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
            reasoning = str(message.get("reasoning_content") or "").strip()
            prompt = (
                "以下は画像モデルから得た観察メモです。"
                "推論過程には触れず、見えたものへのペケ子の反応だけを"
                "日本語20〜45字の一文で返して。\n"
                f"観察メモ: {reasoning[:800]}"
            )
            return await asyncio.to_thread(self.llm.chat, settings.VISION_SID, prompt)
        return content
