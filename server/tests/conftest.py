"""pytest 共通設定。

server/ を sys.path に追加して、tests/ から utterance_queue 等を
そのまま import できるようにする。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))


@pytest.fixture(scope="module")
def monkeypatch_module():
    """module スコープで使える monkeypatch (pytest 標準は function スコープのみ)。"""
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    yield mp
    mp.undo()
