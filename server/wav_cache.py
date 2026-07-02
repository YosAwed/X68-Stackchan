"""定型 TTS 出力のディスクキャッシュ。

同じテキストを毎回 Irodori で合成するのは無駄なので、
``TTS_CACHE_DIR`` を設定してあれば WAV bytes を sha256(text) で
ファイルキャッシュする。Scheduler の kind="fixed" や /enqueue の
非 LLM 経路から使う。

キーは text のみのハッシュなので、声 (IRODORI_REF_WAV, VOICEVOX_SPEAKER)
を変えた場合は ``TTS_CACHE_DIR`` を消すか、別ディレクトリに切り替える。
細かい無効化が要るなら ``TTS_CACHE_VERSION`` を bump するという運用も
できる (キーに混ぜる)。
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)


class WavCache:
    """text → WAV bytes の content-addressed cache。

    dir=None なら全操作が no-op (キャッシュ無効)。
    """

    def __init__(self, dir: str | Path | None, version: str = "v1"):
        self.dir: Path | None
        if dir is None or dir == "":
            self.dir = None
        else:
            self.dir = Path(dir)
            self.dir.mkdir(parents=True, exist_ok=True)
        self.version = version

    def enabled(self) -> bool:
        return self.dir is not None

    def _key(self, text: str) -> str:
        h = hashlib.sha256()
        h.update(self.version.encode("utf-8"))
        h.update(b"\x00")
        h.update(text.encode("utf-8"))
        return h.hexdigest()

    def _path(self, text: str) -> Path | None:
        if self.dir is None:
            return None
        return self.dir / (self._key(text) + ".wav")

    def get(self, text: str) -> bytes | None:
        p = self._path(text)
        if p is None or not p.exists():
            return None
        try:
            return p.read_bytes()
        except Exception:
            log.exception("wav_cache read failed: %s", p)
            return None

    def put(self, text: str, wav: bytes) -> None:
        p = self._path(text)
        if p is None:
            return
        # atomic write: tmp に書いてから rename。
        # tmp 名にはスレッド ID を混ぜ、同一テキストの並行 put (asyncio.to_thread
        # から同時に呼ばれるケース) でも tmp ファイルが衝突しないようにする。
        # rename 自体はアトミックなので、読み手が部分ファイルを見ることはない。
        tmp = p.with_suffix(f".wav.{os.getpid()}-{threading.get_ident()}.tmp")
        try:
            tmp.write_bytes(wav)
            tmp.replace(p)
        except Exception:
            log.exception("wav_cache write failed: %s", p)
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

    def size(self) -> int:
        """キャッシュ済みエントリ数。dir=None なら 0。"""
        if self.dir is None or not self.dir.exists():
            return 0
        return sum(1 for p in self.dir.iterdir()
                   if p.suffix == ".wav" and p.is_file())
