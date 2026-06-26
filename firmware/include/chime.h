// ========================================================
//  X68000 風起動チャイム & ack beep
//
//  本物の X68000 起動音 (PCM サンプル) はメーカー著作物なので使わず、
//  ここでは X68 の FM 音源 (YM2151 / OPM) を思わせる金属的な倍音を
//  2-operator FM 合成でその場生成して鳴らす。
//    sample = env * sin(2π·fc·t + idx·sin(2π·fm·t))
//  キャリア fc = 音程, モジュレータ fm = fc·mod_ratio。
//  振幅 env とモジュレーション指数 idx を指数減衰させることで、
//  アタックは明るく倍音豊かに、余韻は純音に近づく「鐘・ベル」感を出す。
//  生成した PCM は M5Unified::Speaker::playRaw() で再生する。
// ========================================================
#pragma once

#include <M5Unified.h>
#include <cmath>
#include <cstddef>
#include <cstdint>

namespace stackchan {

namespace detail {
struct Note { float hz; uint32_t ms; };

// FM 合成のサンプルレートと、1 音あたりの最大長 (これを超える ms はクリップ)。
inline constexpr uint32_t kChimeSR    = 16000;
inline constexpr size_t   kMaxSamples = kChimeSR * 300 / 1000;  // 最長 300ms

// 1 音を 2-op FM で生成して再生 (再生完了まで block)。
inline void playFmNote(float hz, uint32_t ms,
                       float mod_ratio, float mod_index) {
    static int16_t buf[kMaxSamples];
    uint32_t n = (uint32_t)((uint64_t)kChimeSR * ms / 1000);
    if (n > kMaxSamples) n = kMaxSamples;
    if (n == 0) return;

    constexpr float kTwoPi = 6.28318530718f;
    const float dur_s = ms / 1000.0f;
    const float dt    = 1.0f / (float)kChimeSR;
    const float fc    = hz;
    const float fm    = hz * mod_ratio;
    const float atk_s = 0.005f;   // 5ms の線形アタック (発音時のクリック防止)

    for (uint32_t i = 0; i < n; ++i) {
        const float t = i * dt;
        float env = expf(-3.2f * t / dur_s);          // 振幅: 指数減衰
        if (t < atk_s) env *= t / atk_s;              // 立ち上がりだけ線形
        const float idx = mod_index * expf(-2.5f * t / dur_s);  // 倍音も減衰
        const float s   = env * sinf(kTwoPi * fc * t + idx * sinf(kTwoPi * fm * t));
        int v = (int)(s * 0.8f * 32767.0f);           // 0.8 ヘッドルーム
        if (v >  32767) v =  32767;
        if (v < -32768) v = -32768;
        buf[i] = (int16_t)v;
    }

    M5.Speaker.playRaw(buf, n, kChimeSR, /*stereo=*/false,
                       /*repeat=*/1, /*channel=*/0, /*stop_current_sound=*/true);
    while (M5.Speaker.isPlaying()) delay(2);
}

// 音量・FM パラメータを共有して音列を順に鳴らす。
//   mod_ratio: モジュレータ比 (整数比に近いほど協和、非整数で金属的)
//   mod_index: モジュレーション深さ (大きいほど倍音が派手)
inline void playSequence(const Note* seq, size_t n, uint8_t vol = 160,
                         float mod_ratio = 2.0f, float mod_index = 2.5f) {
    M5.Speaker.setVolume(vol);
    for (size_t i = 0; i < n; ++i) {
        playFmNote(seq[i].hz, seq[i].ms, mod_ratio, mod_index);
        delay(8);   // 音の粒立ちのための小休止
    }
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

// 頭をなでられた時。F6 → A6 → C7 の上昇 3 音で「うれしい」を表現する。
inline void playHeadpatChime() {
    static const detail::Note kHeadpat[] = {
        { 1396.9f, 70 },   // F6
        { 1760.0f, 70 },   // A6
        { 2093.0f, 120 },  // C7 (余韻)
    };
    detail::playSequence(kHeadpat, 3, /*vol=*/130);
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
        { 880.0f, 60 },    // A5 (ダブルパルス)
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

// シェイクされた時。ぐるぐる回るような不安定な音列で「目が回る」感を出す。
// 高めの mod_index でクラクラした金属的な倍音を強調。
inline void playDizzyChime() {
    static const detail::Note kDizzy[] = {
        { 784.0f, 70 },    // G5
        { 587.3f, 70 },    // D5
        { 698.5f, 70 },    // F5
        { 523.3f, 70 },    // C5
        { 440.0f, 160 },   // A4 (落ち着く)
    };
    detail::playSequence(kDizzy, 5, /*vol=*/140,
                         /*mod_ratio=*/1.5f, /*mod_index=*/4.0f);
}

// 持ち上げ / 小突きされた時。短い上昇 2 音で "わっ" と驚きを表す。
inline void playLiftBeep() {
    static const detail::Note kLift[] = {
        { 880.0f,  50 },   // A5
        { 1318.5f, 90 },   // E6
    };
    detail::playSequence(kLift, 2, /*vol=*/130,
                         /*mod_ratio=*/3.0f, /*mod_index=*/2.0f);
}

} // namespace stackchan
