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


# ---- classify_reaction (照れ反応) ----


import pytest as _pytest  # noqa: E402
from emote import classify_reaction, is_praise  # noqa: E402


@_pytest.mark.parametrize("user_text,expected_praise", [
    ("かわいいね", True),
    ("えらい!", True),
    ("すごいよぺけ子ちゃん", True),
    ("好きだよ", True),
    ("ありがとう、助かった", True),
    ("天才じゃない?", True),
    # 普通の質問は褒めではない
    ("今日の天気は?", False),
    ("X68 のこと教えて", False),
    ("", False),
])
def test_is_praise_detection(user_text: str, expected_praise: bool):
    assert is_praise(user_text) is expected_praise


def test_classify_reaction_returns_embarrassed_when_user_praises():
    # bot 応答が joy 系でも、user の褒め言葉が優先される
    assert classify_reaction("かわいいね!", "やったー、嬉しい") == "embarrassed"


def test_classify_reaction_falls_through_when_no_praise():
    # 褒めがない時は通常通り bot_text を分類
    assert classify_reaction("今日のごはん", "やったー、楽しいよ") == "joy"
    assert classify_reaction("どう?", "ごめん、わからない") == "embarrassed"  # bot 側の謝罪
    assert classify_reaction("", "そうだね") == "neutral"


def test_classify_reaction_empty_user_text_safe():
    # user_text=None でも例外を投げない (main.py から空文字で渡される)
    assert classify_reaction("", "やった") == "joy"
