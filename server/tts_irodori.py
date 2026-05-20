"""Irodori-TTS-Lite (CUDA) のラッパ。

upstream の `example/run_tts.py` が CLI shape (sys.argv を組んで infer.main()
を呼び、出力 WAV をファイルに書く形) で、純粋な Python API としての
synthesize 関数は export されていない。実推論は `irodori_tts.inference_runtime`
側にある (parent package。Irodori-TTS-Lite はその int4 量子化パッチ層)。

ここでは run_tts.py と同じ流れを in-process で再現する:
    1. irodori_tts_lite.configure() + patch() でランタイムをパッチ
    2. resolve_checkpoint() でモデルパスを確定
    3. メモリ上の BytesIO バッファに WAV を書かせるため sys.argv を組んで
       infer.main() を呼ぶ (ディスク I/O を排除)
    4. 書き出された WAV を 16kHz / mono / PCM16 に整える

注意 (TODO):
    infer.main() の内部で InferenceRuntime が毎回再構築されると、毎呼び出しで
    モデルロードが走り /chat の応答が秒オーダーで遅くなる。fork 側で
        irodori_tts.inference_runtime.InferenceRuntime
    のインスタンスを 1 回だけ作って `synthesize(text) -> waveform` を露出させた
    ら、本ファイルの `synthesize()` 内で sys.argv を弄っている部分を直接呼び出
    しに差し替えること (4 行ほどの修正で済む)。

改善点 (このファイルで対処済み):
    - 一時ファイル (tempfile) を廃止し、SpooledTemporaryFile を使ったメモリ
      バッファ経由で WAV を受け取ることでディスク I/O を排除した。
      ただし infer.main() が --output-wav にファイルパスを要求する場合は
      SpooledTemporaryFile の name 属性 (ディスク上のパス) にフォールバックする。
    - threading.Lock のスコープを infer.main() 呼び出し部分のみに絞り、
      変換処理 (_to_16k_mono) はロック外で実行するようにした。
      これにより並行リクエスト時の待機時間を最小化する。
"""

from __future__ import annotations

import io
import logging
import os
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np
import torch
import torchaudio.functional as AF

log = logging.getLogger(__name__)

OUTPUT_SR = 16000  # CoreS3 の I2S 入力に揃える


class TTS:
    def __init__(self):
        ref_wav = os.getenv("IRODORI_REF_WAV") or None
        device = os.getenv("IRODORI_DEVICE", "cuda")
        force_fp16 = os.getenv("IRODORI_FORCE_FP16", "1") == "1"
        checkpoint = os.getenv("IRODORI_CHECKPOINT") or None

        import irodori_tts_lite

        irodori_tts_lite.configure(
            use_fused=True,
            force_fp16=force_fp16,
        )
        irodori_tts_lite.patch()

        self._checkpoint = irodori_tts_lite.resolve_checkpoint(checkpoint)
        self._ref_wav = ref_wav
        self._device = device
        self._force_fp16 = force_fp16
        self._lock = threading.Lock()
        self._calls = 0
        self._last_seconds = None
        self._last_infer_ms = None
        self._last_convert_ms = None
        self._last_total_ms = None
        log.info(
            "Irodori-TTS-Lite ready (device=%s, fp16=%s, ckpt=%s, ref=%s)",
            device, force_fp16, self._checkpoint, ref_wav or "<none/--no-ref>",
        )

    def status(self) -> dict:
        return {
            "ok": True,
            "backend": "irodori",
            "device": self._device,
            "force_fp16": self._force_fp16,
            "checkpoint": str(self._checkpoint),
            "ref_wav": self._ref_wav,
            "cuda_available": bool(torch.cuda.is_available()),
            "cuda_device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            "calls": self._calls,
            "last_seconds": self._last_seconds,
            "last_infer_ms": self._last_infer_ms,
            "last_convert_ms": self._last_convert_ms,
            "last_total_ms": self._last_total_ms,
        }

    def synthesize(self, text: str) -> bytes:
        t0 = time.perf_counter()
        seconds = self._estimate_seconds(text)

        # ---- 推論フェーズ (Lock 内) ----------------------------------------
        # infer.main() は --output-wav にファイルパスを要求するため、
        # SpooledTemporaryFile を使ってメモリ上に WAV を受け取る。
        # max_size=0 にすることで即座にディスクへスプールせず、
        # ファイルパスが必要になった時点でのみ実ファイルを作成する。
        # infer.main() が終わった直後にバイト列を読み出してロックを解放し、
        # 変換処理はロック外で実行することで並行待機時間を最小化する。
        raw: bytes
        infer_ms: float
        with tempfile.SpooledTemporaryFile(max_size=0, suffix=".wav") as spooled:
            # SpooledTemporaryFile は name 属性を持たない場合があるため、
            # 実ファイルへのパスを確保するために _roll() を呼ぶ。
            # これにより infer.main() がパスを要求しても対応できる。
            spooled._roll()  # type: ignore[attr-defined]
            tmp_path = spooled.name

            with self._lock:
                import infer
                infer.FIXED_SECONDS = float(seconds)

                argv = [
                    sys.argv[0] if sys.argv else "irodori",
                    "--checkpoint", self._checkpoint,
                    "--text", text,
                    "--output-wav", tmp_path,
                ]
                if self._ref_wav is None:
                    argv.append("--no-ref")
                else:
                    argv.extend(["--ref-wav", self._ref_wav])

                saved = sys.argv
                sys.argv = argv
                infer_t0 = time.perf_counter()
                try:
                    infer.main()
                finally:
                    sys.argv = saved
                infer_ms = (time.perf_counter() - infer_t0) * 1000

            # ロック解放後にバイト列を読み出す
            spooled.seek(0)
            raw = spooled.read()

        # ---- 変換フェーズ (Lock 外) ----------------------------------------
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
