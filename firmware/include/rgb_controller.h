// ========================================================
//  WS2812C x12 RGB LED 感情演出
//  データピンは config.h の RGB_DATA_PIN で指定 (要 #define)
//
//  Idle     : 青紫のブリージング (4秒周期)
//  Listening: 緑のパルス (1秒周期)
//  Thinking : 琥珀色の追いかけアニメ
//  Speaking : 暖色、RMS 連動の輝度
//  Pet      : ピンクのポワッと点灯
//  Shaken   : 赤の点滅
//  Error    : 赤フラッシュ
// ========================================================
#pragma once
#include <Arduino.h>
#include <FastLED.h>
#include <cmath>
#include "avatar_state.h"
#include "config.h"

namespace stackchan {

enum class RgbScene {
    Idle, Listening, Thinking, Speaking, Pet, Shaken, Error, Off
};

class RgbController {
public:
    static constexpr uint8_t MAX_BRIGHT = 80;  // 0-255 (熱・消費電力を抑制)

    bool begin() {
        FastLED.addLeds<WS2812, RGB_DATA_PIN, GRB>(leds_, RGB_NUM_LEDS);
        FastLED.setBrightness(MAX_BRIGHT);
        // 起動時レインボースイープ
        for (int i = 0; i < RGB_NUM_LEDS * 3; i++) {
            for (int j = 0; j < RGB_NUM_LEDS; j++) {
                leds_[j] = CHSV(((i + j) * 21) & 0xFF, 240, 180);
            }
            FastLED.show();
            delay(20);
        }
        fill_solid(leds_, RGB_NUM_LEDS, CRGB::Black);
        FastLED.show();
        scene_ms_ = millis();
        return true;
    }

    void setScene(RgbScene s) {
        if (scene_ == s) return;
        scene_   = s;
        scene_ms_ = millis();
    }

    void onState(State s) {
        switch (s) {
            case State::Idle:      setScene(RgbScene::Idle);      break;
            case State::Listening: setScene(RgbScene::Listening); break;
            case State::Thinking:  setScene(RgbScene::Thinking);  break;
            case State::Speaking:  setScene(RgbScene::Speaking);  break;
            case State::Error:     setScene(RgbScene::Error);     break;
            default: break;
        }
    }

    // 発話 RMS (0..1) — playWavWithLipsync から毎フレーム渡す
    void setSpeakRms(float w) { speak_rms_ = constrain(w, 0.0f, 1.0f); }

    // loop() / lipsync ループから毎回呼ぶ
    void update() {
        const uint32_t dt = millis() - scene_ms_;
        switch (scene_) {
            case RgbScene::Idle:      animIdle(dt);      break;
            case RgbScene::Listening: animListening(dt); break;
            case RgbScene::Thinking:  animThinking(dt);  break;
            case RgbScene::Speaking:  animSpeaking();    break;
            case RgbScene::Pet:       animPet(dt);       break;
            case RgbScene::Shaken:    animShaken(dt);    break;
            case RgbScene::Error:     animError(dt);     break;
            case RgbScene::Off:
                fill_solid(leds_, RGB_NUM_LEDS, CRGB::Black);
                break;
        }
        FastLED.show();
    }

private:
    CRGB     leds_[RGB_NUM_LEDS];
    RgbScene scene_    = RgbScene::Off;
    uint32_t scene_ms_ = 0;
    float    speak_rms_ = 0.0f;

    // ---- アニメーション ----------------------------------------

    void animIdle(uint32_t dt) {
        // 青紫ブリージング: 4秒周期
        float t   = (float)(dt % 4000) / 4000.0f;
        uint8_t v = (uint8_t)(sinf(t * 2.0f * M_PI) * 50.0f + 70.0f);
        uint8_t h = (uint8_t)(170 + (int)(sinf(t * M_PI) * 15));
        fill_solid(leds_, RGB_NUM_LEDS, CHSV(h, 210, v));
    }

    void animListening(uint32_t dt) {
        // 緑パルス: 1.2秒周期
        float t   = (float)(dt % 1200) / 1200.0f;
        uint8_t v = (uint8_t)(sinf(t * 2.0f * M_PI) * 45.0f + 65.0f);
        fill_solid(leds_, RGB_NUM_LEDS, CHSV(96, 230, v));
    }

    void animThinking(uint32_t dt) {
        // 琥珀色コメット追いかけ
        fill_solid(leds_, RGB_NUM_LEDS, CRGB::Black);
        int pos = (dt / 80) % RGB_NUM_LEDS;
        leds_[pos]                           = CHSV(30, 255, 200);
        leds_[(pos + 1) % RGB_NUM_LEDS]      = CHSV(30, 255,  90);
        leds_[(pos + 2) % RGB_NUM_LEDS]      = CHSV(30, 255,  30);
    }

    void animSpeaking() {
        // 暖色、RMS で輝度変化
        uint8_t v = (uint8_t)(50.0f + speak_rms_ * 160.0f);
        fill_solid(leds_, RGB_NUM_LEDS, CHSV(38, 180, v));
    }

    void animPet(uint32_t dt) {
        // ピンクがふわっと点いてゆっくり消える (2秒)
        float ratio = 1.0f - constrain((float)dt / 2000.0f, 0.0f, 1.0f);
        uint8_t v = (uint8_t)(ratio * 200.0f);
        fill_solid(leds_, RGB_NUM_LEDS, CHSV(220, 180, v));
    }

    void animShaken(uint32_t dt) {
        // 赤点滅 (200ms オン/オフ)
        bool on = (dt / 200) % 2 == 0;
        fill_solid(leds_, RGB_NUM_LEDS, on ? CHSV(0, 255, 180) : CRGB::Black);
    }

    void animError(uint32_t dt) {
        // 赤フラッシュして消える
        uint8_t v = dt < 400 ? (uint8_t)(200 - dt / 2) : 0;
        fill_solid(leds_, RGB_NUM_LEDS, CHSV(0, 255, v));
    }
};

} // namespace stackchan
