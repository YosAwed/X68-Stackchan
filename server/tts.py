"""Irodori-TTS-Lite (CUDA) のラッパ。

upstream の `example/run_tts.py` が CLI shape (sys.argv を組んで infer.main()
を呼び、出力 WAV をファイルに書く形) で、純粋な Python API としての
synthesize 関数は export されていない。実推論は `irodori_tts.inference_runtime`
側にある (parent package。Irodori-TTS-Lite はその int4 量子化パッチ層)。

ここでは run_tts.py と同じ流れを in-process で再現する:
    1. irodori_tts_lite.configure() + patch() でランタイムをパッチ
    2. resolve_checkpoint() でモデルパスを確定
    3. tempfile に WAV を書かせるために sys.argv を組んで infer.main() を呼ぶ
    4. 書き出された WAV を読み戻して 16kHz / mono / PCM16 に整える

注意 (TODO):
    infer.main() の内部で InferenceRuntime が毎回再構築されると、毎呼び出しで
    モデルロードが走り /chat の応答が秒オーダーで遅くなる。fork 側で
        irodori_tts.inference_runtime.InferenceRuntime
    のインスタンスを 1 回だけ作って `synthesize(text) -> waveform` を露出させた
    ら、本ファイルの `synthesize()` 内で sys.argv を弄っている部分を直接呼び出
    しに差し替えること (4 行ほどの修正で済む)。
"""

from __future__ import annotations

import io
import logging
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np
import torch
import torchaudio.functional as AF

log = logging.getLogger(__name__)

OUTPUT_SR = 16000  # CoreS3 の I2S 入力に揃える


class TTS:
    def __init__(
        self,
        ref_wav: str | None = None,
        device: str = "cuda",
        force_fp16: bool = True,
        use_fused: bool = True,
        checkpoint: str | None = None,
    ):
        import irodori_tts_lite

        irodori_tts_lite.configure(
            use_fused=use_fused,
            force_fp16=force_fp16,
        )
        irodori_tts_lite.patch()

        self._checkpoint = irodori_tts_lite.resolve_checkpoint(checkpoint)
        self._ref_wav = ref_wav
        self._device = device
        log.info(
            "Irodori-TTS-Lite ready (device=%s, fp16=%s, ckpt=%s, ref=%s)",
            device, force_fp16, self._checkpoint, ref_wav or "<none/--no-ref>",
        )

    def synthesize(self, text: str) -> bytes:
        seconds = self._estimate_seconds(text)

        # infer.main() は --output-wav にファイルを書くので一時ファイルを用意
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        try:
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

            saved = sys.argv
            sys.argv = argv
            try:
                infer.main()
            finally:
                sys.argv = saved

            raw = Path(tmp_path).read_bytes()
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        wav_bytes = self._to_16k_mono(raw)
        log.info("TTS ◀ %d bytes (text=%r)", len(wav_bytes), text)
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
