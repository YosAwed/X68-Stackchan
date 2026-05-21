"""emote.classify の単体テスト。"""

from __future__ import annotations

import pytest

from emote import VALID_CATEGORIES, classify


@pytest.mark.parametrize("text,expected", [
    # neutral
    ("", "neutral"),
    ("そうだね", "neutral"),
    # joy
    ("やったー、できたよ", "joy"),
    ("X68000 のことは大好き", "joy"),
    ("X68 ならこうだったのに", "joy"),
    ("Human68k 懐かしいね", "joy"),
    # surprised
    ("えっ、本当に動いたの", "surprised"),
    ("まじですごい", "surprised"),
    # sad
    ("ちょっと悲しい気分", "sad"),
    ("つらいことがあった", "sad"),
    # embarrassed
    ("ごめん、忘れてた", "embarrassed"),
    ("申し訳ないけど、わからない", "embarrassed"),  # 申し訳 が先勝ち
    # confused
    ("うーん、わからないな", "confused"),
    ("えっと、それはどうだろう", "confused"),
    # sleepy
    ("もう眠いよ", "sleepy"),
    ("あくびが出ちゃう", "sleepy"),
    # confident
    ("まかせて、ばっちり", "confident"),  # まかせて を先勝ち
    ("大丈夫、なんとかなる", "confident"),
])
def test_classify_keyword_mapping(text: str, expected: str):
    assert classify(text) == expected


def test_classify_returns_valid_category_for_any_input():
    samples = [
        "完全に未知の文字列です",
        "数字 12345 のみ",
        "🚀 unicode emoji",
        "純粋な English text without any keyword",
    ]
    for s in samples:
        assert classify(s) in VALID_CATEGORIES


def test_classify_is_case_insensitive_for_ascii():
    # 大文字でも joy 扱い
    assert classify("X68000 万歳") == "joy"
    assert classify("x68000 万歳") == "joy"
    assert classify("HUMAN68K rules") == "joy"


def test_valid_categories_includes_neutral():
    assert "neutral" in VALID_CATEGORIES
