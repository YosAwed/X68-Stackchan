"""Irodori-TTS-Lite (CUDA) のラッパ。

upstream の `infer.main()` は毎回 `InferenceRuntime.from_key()` でモデルを
再ロードする CLI ラッパのため、ここでは `irodori_tts.inference_runtime` を
直接叩いてシングルトンキャッシュ (`get_cached_runtime`) を使う:

    1. irodori_tts_lite.configure() + patch() でランタイムをパッチ
    2. resolve_checkpoint() でモデルパスを確定
    3. RuntimeKey を 1 回組み立て、get_cached_runtime() で InferenceRuntime を共有
    4. runtime.synthesize(SamplingRequest(...)) → メモリ上の WAV bytes
    5. 16 kHz / mono / PCM16 に整えて CoreS3 へ返す

並行性:
    InferenceRuntime.synthesize() は内部 `_infer_lock` で直列化される。
    `_to_16k_mono()` の torchaudio resample はロック外で並行実行できる。
"""

from __future__ import annotations

import io
import logging
import threading
import time
import wave
from dataclasses import dataclass

import numpy as np
import torch
import torchaudio.functional as AF
from settings import settings

log = logging.getLogger(__name__)

OUTPUT_SR = 16000  # CoreS3 の I2S 入力に揃える

# infer.main() の argparse 既定値 (YosAwed/Irodori-TTS infer.py と揃える)
_INFER_NUM_STEPS = 40
_INFER_CFG_GUIDANCE_MODE = "independent"
_INFER_CFG_SCALE_TEXT = 3.0
_INFER_CFG_SCALE_CAPTION = 3.0
_INFER_CFG_SCALE_SPEAKER = 5.0


def _patch_reference_encoder_dtype() -> None:
    """Keep reference-audio latents in the same dtype as the int4/fp16 model."""
    try:
        from irodori_tts.model import ReferenceLatentEncoder
    except Exception:
        log.exception("failed to import ReferenceLatentEncoder for dtype patch")
        return

    if getattr(ReferenceLatentEncoder.forward, "_stackchan_dtype_patch", False):
        return

    original_forward = ReferenceLatentEncoder.forward

    def forward(self, latent: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        weight = getattr(getattr(self, "in_proj", None), "weight", None)
        if weight is not None and latent.dtype != weight.dtype:
            latent = latent.to(dtype=weight.dtype)
        return original_forward(self, latent, mask)

    forward._stackchan_dtype_patch = True  # type: ignore[attr-defined]
    ReferenceLatentEncoder.forward = forward  # type: ignore[method-assign]
    log.info("patched Irodori reference encoder dtype cast")


def _resolve_model_device() -> str:
    from irodori_tts.inference_runtime import default_runtime_device

    device = settings.IRODORI_DEVICE.strip().lower()
    if device in ("auto", ""):
        return default_runtime_device()
    return settings.IRODORI_DEVICE


def _build_runtime_key(checkpoint: str):
    from irodori_tts.inference_runtime import RuntimeKey, default_runtime_device

    device = _resolve_model_device()
    codec_device = default_runtime_device()
    return RuntimeKey(
        checkpoint=str(checkpoint),
        model_device=device,
        codec_repo="Aratako/Semantic-DACVAE-Japanese-32dim",
        model_precision="fp32",
        codec_device=codec_device,
        codec_precision="fp32",
        codec_deterministic_encode=True,
        codec_deterministic_decode=True,
        enable_watermark=False,
        compile_model=False,
        compile_dynamic=False,
    )


@dataclass(frozen=True)
class _InferDefaults:
    """SamplingRequest に渡す infer CLI 既定値の束。"""

    num_steps: int = _INFER_NUM_STEPS
    cfg_guidance_mode: str = _INFER_CFG_GUIDANCE_MODE
    cfg_scale_text: float = _INFER_CFG_SCALE_TEXT
    cfg_scale_caption: float = _INFER_CFG_SCALE_CAPTION
    cfg_scale_speaker: float = _INFER_CFG_SCALE_SPEAKER
    cfg_min_t: float = 0.5
    cfg_max_t: float = 1.0
    context_kv_cache: bool = True
    trim_tail: bool = True
    tail_window_size: int = 20
    tail_std_threshold: float = 0.05
    tail_mean_threshold: float = 0.1
    ref_normalize_db: float | None = -16.0
    ref_ensure_max: bool = True
    max_ref_seconds: float = 30.0
    num_candidates: int = 1
    decode_mode: str = "sequential"


def _tensor_to_wav_bytes(audio: torch.Tensor, sample_rate: int) -> bytes:
    """InferenceRuntime の波形テンソルを 16-bit PCM WAV bytes へ。"""
    audio_cpu = audio.detach().to(device="cpu", dtype=torch.float32)
    if audio_cpu.ndim == 2 and audio_cpu.shape[0] == 1:
        x = audio_cpu.squeeze(0).numpy()
    elif audio_cpu.ndim == 2:
        x = audio_cpu.mean(dim=0).numpy()
    else:
        x = audio_cpu.numpy()

    pcm = np.clip(x, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return out.getvalue()


class TTS:
    def __init__(self):
        ref_wav = settings.IRODORI_REF_WAV or None
        device = _resolve_model_device()
        force_fp16 = bool(settings.IRODORI_FORCE_FP16)
        checkpoint = settings.IRODORI_CHECKPOINT
        seed = settings.IRODORI_SEED

        import irodori_tts_lite

        irodori_tts_lite.configure(
            use_fused=True,
            force_fp16=force_fp16,
        )
        irodori_tts_lite.patch()
        _patch_reference_encoder_dtype()

        self._checkpoint = irodori_tts_lite.resolve_checkpoint(checkpoint)
        self._ref_wav = ref_wav
        self._seed = seed
        self._device = device
        self._force_fp16 = force_fp16
        self._runtime_key = _build_runtime_key(self._checkpoint)
        self._infer_defaults = _InferDefaults()
        self._runtime_lock = threading.Lock()
        self._runtime = None
        self._runtime_loads = 0
        self._calls = 0
        self._last_seconds = None
        self._last_infer_ms = None
        self._last_convert_ms = None
        self._last_total_ms = None
        log.info(
            "Irodori-TTS-Lite ready (device=%s, fp16=%s, ckpt=%s, ref=%s, seed=%s)",
            device, force_fp16, self._checkpoint, ref_wav or "<none/--no-ref>",
            seed if seed is not None else "<random>",
        )

    def status(self) -> dict:
        return {
            "ok": True,
            "backend": "irodori",
            "device": self._device,
            "force_fp16": self._force_fp16,
            "checkpoint": str(self._checkpoint),
            "ref_wav": self._ref_wav,
            "seed": self._seed,
            "cuda_available": bool(torch.cuda.is_available()),
            "cuda_device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            "runtime_loaded": self._runtime is not None,
            "runtime_loads": self._runtime_loads,
            "calls": self._calls,
            "last_seconds": self._last_seconds,
            "last_infer_ms": self._last_infer_ms,
            "last_convert_ms": self._last_convert_ms,
            "last_total_ms": self._last_total_ms,
        }

    def _get_runtime(self):
        from irodori_tts.inference_runtime import get_cached_runtime

        with self._runtime_lock:
            if self._runtime is not None:
                return self._runtime
            runtime, reloaded = get_cached_runtime(self._runtime_key)
            self._runtime = runtime
            if reloaded:
                self._runtime_loads += 1
                log.info(
                    "Irodori InferenceRuntime loaded (checkpoint=%s, device=%s)",
                    self._checkpoint,
                    self._device,
                )
            return runtime

    def _synthesize_raw(self, text: str, seconds: float) -> tuple[bytes, float]:
        from irodori_tts.inference_runtime import SamplingRequest, resolve_cfg_scales

        runtime = self._get_runtime()
        use_speaker = bool(
            runtime.model_cfg.use_speaker_condition and self._ref_wav is not None
        )
        cfg_scale_text, cfg_scale_caption, cfg_scale_speaker, _ = resolve_cfg_scales(
            cfg_guidance_mode=self._infer_defaults.cfg_guidance_mode,
            cfg_scale_text=self._infer_defaults.cfg_scale_text,
            cfg_scale_caption=self._infer_defaults.cfg_scale_caption,
            cfg_scale_speaker=self._infer_defaults.cfg_scale_speaker,
            cfg_scale=None,
            use_caption_condition=False,
            use_speaker_condition=use_speaker,
        )
        defaults = self._infer_defaults
        infer_t0 = time.perf_counter()
        result = runtime.synthesize(
            SamplingRequest(
                text=text,
                ref_wav=self._ref_wav,
                no_ref=self._ref_wav is None,
                ref_normalize_db=defaults.ref_normalize_db,
                ref_ensure_max=defaults.ref_ensure_max,
                num_candidates=defaults.num_candidates,
                decode_mode=defaults.decode_mode,
                seconds=float(seconds),
                max_ref_seconds=defaults.max_ref_seconds,
                num_steps=defaults.num_steps,
                cfg_scale_text=cfg_scale_text,
                cfg_scale_caption=cfg_scale_caption,
                cfg_scale_speaker=cfg_scale_speaker,
                cfg_guidance_mode=defaults.cfg_guidance_mode,
                cfg_scale=None,
                cfg_min_t=defaults.cfg_min_t,
                cfg_max_t=defaults.cfg_max_t,
                context_kv_cache=defaults.context_kv_cache,
                seed=self._seed,
                trim_tail=defaults.trim_tail,
                tail_window_size=defaults.tail_window_size,
                tail_std_threshold=defaults.tail_std_threshold,
                tail_mean_threshold=defaults.tail_mean_threshold,
            ),
            log_fn=None,
        )
        infer_ms = (time.perf_counter() - infer_t0) * 1000
        return _tensor_to_wav_bytes(result.audio, result.sample_rate), infer_ms

    def synthesize(self, text: str) -> bytes:
        t0 = time.perf_counter()
        seconds = self._estimate_seconds(text)

        raw, infer_ms = self._synthesize_raw(text, seconds)

        convert_t0 = time.perf_counter()
        wav_bytes = self._to_16k_mono(raw)
        convert_ms = (time.perf_counter() - convert_t0) * 1000

        total_ms = (time.perf_counter() - t0) * 1000
        self._calls += 1
        self._last_seconds = round(seconds, 3)
        self._last_infer_ms = round(infer_ms, 1)
        self._last_convert_ms = round(convert_ms, 1)
        self._last_total_ms = round(total_ms, 1)
        log.info(
            "TTS ◀ %d bytes in %.1fms (infer=%.1fms convert=%.1fms seconds=%.2f text=%r)",
            len(wav_bytes), total_ms, infer_ms, convert_ms, seconds, text,
        )
        return wav_bytes

    @staticmethod
    def _estimate_seconds(text: str) -> float:
        # run_tts.py の式: max(2.0, phonemes / 11.0 + 0.6)
        try:
            import pyopenjtalk
            phs = pyopenjtalk.g2p(text, kana=False).split()
            return max(2.0, len(phs) / 11.0 + 0.6)
        except Exception:
            log.warning("pyopenjtalk g2p failed, fallback seconds=4.0", exc_info=True)
            return 4.0

    @staticmethod
    def _to_16k_mono(raw: bytes) -> bytes:
        with wave.open(io.BytesIO(raw), "rb") as r:
            sr = r.getframerate()
            ch = r.getnchannels()
            sw = r.getsampwidth()
            frames = r.readframes(r.getnframes())

        if sw != 2:
            raise ValueError(f"unexpected sample width={sw} (expected 16-bit PCM)")

        x = np.frombuffer(frames, dtype="<i2").astype("float32") / 32768.0
        if ch > 1:
            x = x.reshape(-1, ch).mean(axis=1)

        if sr != OUTPUT_SR:
            t = torch.from_numpy(x).unsqueeze(0)
            t = AF.resample(t, sr, OUTPUT_SR)
            x = t.squeeze(0).cpu().numpy()

        pcm = np.clip(x, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype("<i2")

        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(OUTPUT_SR)
            w.writeframes(pcm.tobytes())
        return out.getvalue()
