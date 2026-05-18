// ========================================================
//  X68000 風起動チャイム & ack beep
//
//  本物の X68000 起動音 (PCM サンプル) はメーカー著作物なので
//  使わず、ここでは「メジャー系アルペジオ + 余韻」で雰囲気を作る。
//  M5Unified::Speaker::tone() のシンプルなトーン生成だけで完結する。
// ========================================================
#pragma once

#include <M5Unified.h>
#include <cstdint>

namespace stackchan {

namespace detail {
struct Note { float hz; uint32_t ms; };

inline void playSequence(const Note* seq, size_t n, uint8_t vol = 160) {
    M5.Speaker.setVolume(vol);
    for (size_t i = 0; i < n; ++i) {
        // stop_current_sound=true で前の音をきっちり切り替え、
        // duration が終わるまで delay で待つ。
        M5.Speaker.tone(seq[i].hz, seq[i].ms, /*channel=*/0,
                        /*stop_current_sound=*/true);
        delay(seq[i].ms + 10);
    }
    // 余韻のために少しだけ間を空ける
    delay(40);
}
} // namespace detail

// 電源投入時。 A メジャーの上昇アルペジオ +  最後だけ少し長め。
// 「ピロリロロ〜ン」と聞こえる長さ感を狙う。
inline void playBootChime() {
    static const detail::Note kBootSeq[] = {
        { 440.00f, 90 },   // A4
        { 554.37f, 90 },   // C#5
        { 659.25f, 90 },   // E5
        { 880.00f, 240 },  // A5 (余韻)
    };
    detail::playSequence(kBootSeq, sizeof(kBootSeq)/sizeof(kBootSeq[0]));
}

// 応答音声を流す直前。短い "ピッ" を入れて喋り始めることを予告する。
inline void playAckBeep() {
    static const detail::Note kAck[] = {
        { 1318.5f, 50 },   // E6
    };
    detail::playSequence(kAck, 1, /*vol=*/130);
}

// エラー時。下降 2 音で「失敗」を表現する。
inline void playErrorBeep() {
    static const detail::Note kErr[] = {
        { 392.0f, 90 },    // G4
        { 261.6f, 180 },   // C4
    };
    detail::playSequence(kErr, 2, /*vol=*/140);
}

} // namespace stackchan
