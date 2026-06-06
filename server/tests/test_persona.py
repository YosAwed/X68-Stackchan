"""persona.load_persona の単体テスト。"""

from __future__ import annotations

from pathlib import Path

from persona import DEFAULT_SYSTEM_PROMPT, load_persona


def test_returns_default_when_env_not_set(monkeypatch):
    monkeypatch.delenv("PERSONA_FILE", raising=False)
    assert load_persona() == DEFAULT_SYSTEM_PROMPT


def test_default_persona_does_not_request_fixed_followup():
    assert "必要なら最後に「もっと聞きたい」" not in DEFAULT_SYSTEM_PROMPT
    assert "毎回付けない" in DEFAULT_SYSTEM_PROMPT


def test_loads_text_from_file(monkeypatch, tmp_path: Path):
    p = tmp_path / "p.txt"
    p.write_text("あなたは別人格です", encoding="utf-8")
    monkeypatch.setenv("PERSONA_FILE", str(p))
    assert load_persona() == "あなたは別人格です"


def test_falls_back_when_file_missing(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("PERSONA_FILE", str(tmp_path / "absent.txt"))
    assert load_persona() == DEFAULT_SYSTEM_PROMPT


def test_falls_back_when_file_empty(monkeypatch, tmp_path: Path):
    p = tmp_path / "empty.txt"
    p.write_text("   \n", encoding="utf-8")
    monkeypatch.setenv("PERSONA_FILE", str(p))
    assert load_persona() == DEFAULT_SYSTEM_PROMPT
