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

// 応答音声を流す直前。"ピロン♪" の 2 音で喋り始めることを予告する。
inline void playAckBeep() {
    static const detail::Note kAck[] = {
        { 1174.7f, 40 },   // D6
        { 1567.9f, 60 },   // G6
    };
    detail::playSequence(kAck, 2, /*vol=*/130);
}

// エラー時。下降 2 音で「失敗」を表現する。
inline void playErrorBeep() {
    static const detail::Note kErr[] = {
        { 392.0f, 90 },    // G4
        { 261.6f, 180 },   // C4
    };
    detail::playSequence(kErr, 2, /*vol=*/140);
}

// 録音上限到達時。短い二連音で自動送信を知らせる。
inline void playOverflowBeep() {
    static const detail::Note kOverflow[] = {
        { 880.0f, 60 },    // A5
        { 880.0f, 60 },    // A5
    };
    detail::playSequence(kOverflow, 2, /*vol=*/130);
}

// 413 Payload Too Large: 録音が長すぎたことを下降音で知らせる。
inline void playTooLargeBeep() {
    static const detail::Note kTooLarge[] = {
        { 523.3f, 80 },    // C5
        { 440.0f, 80 },    // A4
        { 349.2f, 160 },   // F4
    };
    detail::playSequence(kTooLarge, 3, /*vol=*/140);
}

// 5xx Server Error: サーバ側の失敗を少し不規則な音で知らせる。
inline void playServerErrorBeep() {
    static const detail::Note kServer[] = {
        { 261.6f, 60 },    // C4
        { 392.0f, 60 },    // G4
        { 261.6f, 120 },   // C4
    };
    detail::playSequence(kServer, 3, /*vol=*/140);
}

} // namespace stackchan
