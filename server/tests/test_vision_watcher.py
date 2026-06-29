"""VisionWatcher response cleanup tests."""

from __future__ import annotations

from vision_watcher import VisionWatcher


def _watcher() -> VisionWatcher:
    return VisionWatcher(
        llm=None,
        queue=None,
        make_utterance=lambda text, source: None,
        limit_text=lambda text: text,
    )


def test_clean_vision_text_skips_meta_sentence_and_uses_natural_line():
    watcher = _watcher()

    text = watcher._clean_vision_text(
        "画像には机があります。机の上、今日はちょっとにぎやかだね。"
    )

    assert text == "机の上、今日はちょっとにぎやかだね。"


def test_clean_vision_text_rejects_generic_camera_line():
    watcher = _watcher()

    assert watcher._clean_vision_text("カメラで何か見えたよ。") == ""


def test_clean_vision_text_rejects_prompt_persona_leak():
    watcher = _watcher()

    assert (
        watcher._clean_vision_text(
            "ユーザーは私（ペケ子）になりきって応答を生成するように求めている。"
        )
        == ""
    )
    assert (
        watcher._clean_vision_text(
            "キャラクター設定・制約: 名前はペケ子。視点はユーザーの机です。"
        )
        == ""
    )


def test_clean_vision_text_rejects_persona_narration():
    watcher = _watcher()

    assert (
        watcher._clean_vision_text(
            "X68000の光に心躍らせる。ぺけ子は微笑み、優しく語る。"
        )
        == ""
    )


def test_clean_vision_text_rejects_repeated_line():
    watcher = _watcher()
    watcher._last_text = "手が動いた、なにか作業中かな。"

    assert watcher._clean_vision_text("手が動いた、なにか作業中かな。") == ""


def test_clean_vision_text_rejects_recent_line():
    watcher = _watcher()
    watcher._remember_vision_text("机の上、今日はちょっとにぎやかだね。")

    assert watcher._clean_vision_text("机の上、今日はちょっとにぎやかだね。") == ""


def test_fallback_reaction_avoids_recent_lines():
    watcher = _watcher()
    for text in (
        "ん、そこちょっと動いた気がする。",
        "机の上、少しにぎやかになったね。",
        "今のちらっとした動き、気になるな。",
        "明るいところがふわっと変わったね。",
    ):
        watcher._remember_vision_text(text)

    text = watcher._fallback_reaction(score=None)

    assert text not in {
        "ん、そこちょっと動いた気がする。",
        "机の上、少しにぎやかになったね。",
        "今のちらっとした動き、気になるな。",
        "明るいところがふわっと変わったね。",
    }


def test_vision_user_prompt_requests_observation_only():
    watcher = _watcher()
    watcher._remember_vision_text("端っこの明るさ、さっきと違うね。")

    prompt = watcher._vision_user_prompt()

    assert "one clearly visible thing" in prompt
    assert "Do not mention image" in prompt
    assert "端っこの明るささっきと違うね" not in prompt


def test_clean_styled_text_rejects_third_person_description():
    watcher = _watcher()

    assert watcher._clean_styled_text("その人は何か心地良い雰囲気を感じているようだ。") == ""
    assert watcher._clean_styled_text("うーん、少し考えを整理したいな。") == "うーん、少し考えを整理したいな。"
    assert (
        watcher._clean_styled_text(
            "うーん、ちょっと手伝って。",
            "A man with glasses looking at something above his head.",
        )
        == ""
    )
    assert (
        watcher._clean_styled_text(
            "上のほう、少し確認しておきたいな。",
            "A man with glasses looking at something above his head.",
        )
        == "上のほう、少し確認しておきたいな。"
    )


def test_thought_fallback_keeps_visible_anchor():
    watcher = _watcher()

    assert "端末" in watcher._thought_fallback("A man holding a black device.", None)
    assert "上" in watcher._thought_fallback(
        "A man with glasses looking at something above his head.", None
    )
    assert "考え" in watcher._thought_fallback(
        "A man with glasses is resting his chin in his hand.", None
    )
    assert "ストラップ" in watcher._thought_fallback("A blue lanyard around his neck.", None)
    assert "明る" in watcher._thought_fallback("ほどよい明るさ、黄色っぽい、細かい輪郭が多い。", None)
