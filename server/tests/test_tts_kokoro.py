from __future__ import annotations

from pathlib import Path

import pytest

import tts_kokoro


class _FakeArray(list):
    def reshape(self, _shape):
        return self


class _FakeNumpy:
    float32 = object()

    @staticmethod
    def asarray(values, dtype=None):
        return _FakeArray(values)


class _FakeSoxr:
    calls = []

    @classmethod
    def resample(cls, audio, source_rate, target_rate, quality):
        cls.calls.append((source_rate, target_rate, quality))
        return audio


class _FakeSoundFile:
    calls = []

    @classmethod
    def write(cls, output, audio, rate, format, subtype):
        cls.calls.append((rate, format, subtype))
        output.write(b"RIFF-fake-wave")


class _FakeG2P:
    def __call__(self, text):
        return f"phonemes:{text}", []


class _FakeKokoro:
    def __init__(self):
        self.calls = []

    def create(self, phonemes, **kwargs):
        self.calls.append((phonemes, kwargs))
        return [0.0, 0.25, -0.25], 24000


def _runtime(fake_kokoro):
    return fake_kokoro, _FakeG2P(), _FakeNumpy, _FakeSoundFile, _FakeSoxr


def test_kokoro_synthesizes_16khz_pcm_wav(tmp_path, monkeypatch):
    model = tmp_path / "model.onnx"
    voices = tmp_path / "voices.bin"
    model.write_bytes(b"model")
    voices.write_bytes(b"voices")
    fake_kokoro = _FakeKokoro()
    _FakeSoxr.calls.clear()
    _FakeSoundFile.calls.clear()
    monkeypatch.setattr(
        tts_kokoro.TTS,
        "_load_runtime",
        staticmethod(lambda *_args: _runtime(fake_kokoro)),
    )

    tts = tts_kokoro.TTS(
        model_path=str(model),
        voices_path=str(voices),
        voice="jf_alpha",
        speed=1.1,
    )
    wav = tts.synthesize("こんにちは")

    assert wav.startswith(b"RIFF")
    assert _FakeSoxr.calls == [(24000, 16000, "HQ")]
    assert _FakeSoundFile.calls == [(16000, "WAV", "PCM_16")]
    assert fake_kokoro.calls == [
        (
            "phonemes:こんにちは",
            {"voice": "jf_alpha", "speed": 1.1, "is_phonemes": True},
        )
    ]
    assert tts.status()["backend"] == "kokoro"


def test_kokoro_rejects_missing_model(tmp_path):
    with pytest.raises(FileNotFoundError, match="Kokoro model"):
        tts_kokoro.TTS(
            model_path=str(tmp_path / "missing.onnx"),
            voices_path=str(tmp_path / "missing.bin"),
        )


def test_kokoro_rejects_empty_text(tmp_path, monkeypatch):
    model = tmp_path / "model.onnx"
    voices = tmp_path / "voices.bin"
    model.touch()
    voices.touch()
    monkeypatch.setattr(
        tts_kokoro.TTS,
        "_load_runtime",
        staticmethod(lambda *_args: _runtime(_FakeKokoro())),
    )
    tts = tts_kokoro.TTS(str(model), str(voices))
    with pytest.raises(ValueError, match="empty"):
        tts.synthesize("   ")
