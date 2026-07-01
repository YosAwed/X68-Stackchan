"""tts_irodori.TTS のユニットテスト (Irodori 本体はモック)。"""

from __future__ import annotations

import importlib
import sys
import types

import pytest
import torch


@pytest.fixture()
def irodori_tts(monkeypatch):
    """irodori_tts_lite / irodori_tts を差し替えて TTS を import する。"""
    fake_lite = types.ModuleType("irodori_tts_lite")
    fake_lite.configure = lambda **kw: None
    fake_lite.patch = lambda: None
    fake_lite.resolve_checkpoint = lambda ckpt: ckpt or "/tmp/fake_ckpt.safetensors"
    monkeypatch.setitem(sys.modules, "irodori_tts_lite", fake_lite)

    load_calls: list[object] = []

    class _ModelCfg:
        use_speaker_condition = False
        use_caption_condition = False

    class _FakeRuntime:
        model_cfg = _ModelCfg()

        def synthesize(self, req, log_fn=None):
            return types.SimpleNamespace(
                audio=torch.zeros(1, 24000),
                sample_rate=24000,
            )

    runtime = _FakeRuntime()

    def fake_get_cached(key):
        load_calls.append(key)
        return runtime, len(load_calls) == 1

    def fake_resolve_cfg_scales(**kw):
        return 3.0, 3.0, 5.0, []

    fake_runtime_mod = types.ModuleType("irodori_tts.inference_runtime")
    fake_runtime_mod.RuntimeKey = lambda **kw: types.SimpleNamespace(**kw)
    fake_runtime_mod.default_runtime_device = lambda: "cpu"
    fake_runtime_mod.get_cached_runtime = fake_get_cached
    fake_runtime_mod.SamplingRequest = lambda **kw: types.SimpleNamespace(**kw)
    fake_runtime_mod.resolve_cfg_scales = fake_resolve_cfg_scales
    fake_irodori_pkg = types.ModuleType("irodori_tts")
    fake_irodori_model = types.ModuleType("irodori_tts.model")

    class _ReferenceLatentEncoder:
        def forward(self, latent, mask):
            return latent

    fake_irodori_model.ReferenceLatentEncoder = _ReferenceLatentEncoder
    monkeypatch.setitem(sys.modules, "irodori_tts.inference_runtime", fake_runtime_mod)
    monkeypatch.setitem(sys.modules, "irodori_tts.model", fake_irodori_model)
    monkeypatch.setitem(sys.modules, "irodori_tts", fake_irodori_pkg)

    monkeypatch.setenv("TTS_BACKEND", "irodori")
    monkeypatch.setenv("IRODORI_DEVICE", "cpu")
    monkeypatch.setenv("IRODORI_FORCE_FP16", "0")

    if "tts_irodori" in sys.modules:
        del sys.modules["tts_irodori"]
    import settings as settings_mod
    settings_mod.settings = settings_mod.Settings()

    mod = importlib.import_module("tts_irodori")
    return mod, load_calls


def test_runtime_singleton_reuses_cached_runtime(irodori_tts):
    mod, load_calls = irodori_tts
    tts = mod.TTS()

    wav1 = tts.synthesize("こんにちは")
    wav2 = tts.synthesize("またね")

    assert wav1.startswith(b"RIFF")
    assert wav2.startswith(b"RIFF")
    assert len(load_calls) == 1
    assert tts.status()["runtime_loads"] == 1
    assert tts.status()["calls"] == 2


def test_synthesize_uses_phoneme_seconds(irodori_tts, monkeypatch):
    mod, _ = irodori_tts
    captured: dict[str, float] = {}

    class _FakeRuntime:
        model_cfg = type("MC", (), {"use_speaker_condition": False, "use_caption_condition": False})()

        def synthesize(self, req, log_fn=None):
            captured["seconds"] = req.seconds
            return types.SimpleNamespace(
                audio=torch.zeros(1, 24000),
                sample_rate=24000,
            )

    runtime = _FakeRuntime()

    def fake_get_cached(key):
        return runtime, True

    sys.modules["irodori_tts.inference_runtime"].get_cached_runtime = fake_get_cached

    monkeypatch.setattr(
        mod.TTS,
        "_estimate_seconds",
        staticmethod(lambda text: 3.5),
    )

    tts = mod.TTS()
    tts.synthesize("テスト")
    assert captured["seconds"] == 3.5
