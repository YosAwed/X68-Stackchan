"""WavCache のテスト。"""

from __future__ import annotations

from pathlib import Path

from wav_cache import WavCache


def test_disabled_cache_is_noop(tmp_path: Path):
    c = WavCache(dir=None)
    assert c.enabled() is False
    assert c.get("hello") is None
    c.put("hello", b"WAVDATA")  # 例外を投げないこと
    assert c.size() == 0


def test_put_and_get_roundtrip(tmp_path: Path):
    c = WavCache(dir=tmp_path)
    assert c.get("hello") is None
    c.put("hello", b"RIFFWAVDATA")
    assert c.get("hello") == b"RIFFWAVDATA"
    assert c.size() == 1


def test_keys_are_text_specific(tmp_path: Path):
    c = WavCache(dir=tmp_path)
    c.put("おはよう", b"A")
    c.put("お昼だよ", b"B")
    assert c.get("おはよう") == b"A"
    assert c.get("お昼だよ") == b"B"
    assert c.size() == 2


def test_version_isolates_keys(tmp_path: Path):
    c1 = WavCache(dir=tmp_path, version="v1")
    c1.put("hello", b"old voice")

    c2 = WavCache(dir=tmp_path, version="v2")
    # 同じ text でも version 違いだとミス
    assert c2.get("hello") is None
    c2.put("hello", b"new voice")
    assert c2.get("hello") == b"new voice"
    # v1 のエントリは無事
    assert c1.get("hello") == b"old voice"
    # 両 version のファイルが同じ dir に共存。size() はディレクトリ全体を数える。
    assert c1.size() == 2
    assert c2.size() == 2


def test_persistence_across_instances(tmp_path: Path):
    c1 = WavCache(dir=tmp_path)
    c1.put("ohayou", b"morning")
    # 別インスタンス (サーバ再起動相当)
    c2 = WavCache(dir=tmp_path)
    assert c2.get("ohayou") == b"morning"


def test_empty_string_dir_means_disabled(tmp_path: Path):
    c = WavCache(dir="")  # 環境変数が空文字のとき
    assert c.enabled() is False
    c.put("x", b"y")
    assert c.get("x") is None
