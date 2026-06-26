"""TTS backend dispatcher.

env `TTS_BACKEND` で実体を選ぶ (instantiate 時点で判定するので、
load_dotenv() の前後どちらで import しても OK):

    irodori (default) — tts_irodori.TTS (CUDA / WSL2 用)
    voicevox          — tts_voicevox.TTS (Mac mini など非 CUDA 用)
    macsay            — tts_macsay.TTS (macOS 標準 say / afconvert)

main.py からは `from tts import TTS; tts = TTS()` で透過的に切替わる。
各バックエンドは __init__ 内で自分の env 変数を読む。
"""

from __future__ import annotations

import logging

from settings import settings

log = logging.getLogger(__name__)


def TTS(*args, **kwargs):
    backend = settings.TTS_BACKEND.lower()
    log.info("TTS backend = %s", backend)
    if backend == "irodori":
        from tts_irodori import TTS as _Real
    elif backend == "voicevox":
        from tts_voicevox import TTS as _Real
    elif backend == "macsay":
        from tts_macsay import TTS as _Real
    else:
        raise ValueError(
            f"unknown TTS_BACKEND: {backend!r} (expected 'irodori', 'voicevox', or 'macsay')"
        )
    return _Real(*args, **kwargs)
