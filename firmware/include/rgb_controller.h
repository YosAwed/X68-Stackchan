// ========================================================
//  WS2812C x12 RGB LED 感情演出
//  StackChan基板のRGBはPY32 IO Expander (I2C 0x6F) 経由で制御する。
//
//  Idle     : 青紫のブリージング (6秒周期) + マイクロ表情連動フラッシュ
//  Listening: 緑のパルス (1.2秒周期)
//  Thinking : 琥珀色の追いかけアニメ
//  Speaking : emote に応じた色味で RMS 連動の輝度 (14種)
//  Pet      : ピンクのポワッと点灯 (2秒)
//  Shaken   : 赤の点滅
//  Error    : 赤フラッシュ
//  Praise   : 褒められた時のピンクパルス (1.5秒)
//  Sleep    : 寝顔時の呼吸灯 (青〜シアン, 4秒周期)
//  PraiseEnd: 褒め反応の終了フェード (500ms)
//  AmbientIdle: 3分放置後のランダム色アンビエント (20秒)
//  Swipe    : スワイプ撫での黄色バースト
// ========================================================
#pragma once
#include <Arduino.h>
#include <FastLED.h>
#include <M5Unified.h>
#include <cmath>
#include "avatar_state.h"
#include "config.h"

namespace stackchan {

enum class RgbScene {
    Idle, Listening, Thinking, Speaking, Pet, Shaken, Error, Off,
    AmbientIdle,  // 放置中の低頻度アンビエント演出
    Swipe,        // スワイプ撫での反応
    Praise,       // 褒められた時のピンクパルス
    Sleep,        // 寝顔時の呼吸灯
    PraiseEnd,    // 褒め反応の終了フェード
};

class RgbController {
public:
    static constexpr uint8_t MAX_BRIGHT = 80;  // 0-255 (熱・消費電力を抑制)

    bool begin() {
        if (!beginPy32()) {
            Serial.println("[RGB ] PY32 IO expander not found");
            return false;
        }
        Serial.println("[RGB ] PY32 IO expander ready");
        // 起動時レインボースイープ
        for (int i = 0; i < RGB_NUM_LEDS * 3; i++) {
            for (int j = 0; j < RGB_NUM_LEDS; j++) {
                leds_[j] = CHSV(((i + j) * 21) & 0xFF, 240, 180);
            }
            show();
            delay(20);
        }
        fill_solid(leds_, RGB_NUM_LEDS, CRGB::Black);
        show();
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
            case State::Sleep:     setScene(RgbScene::Sleep);     break;
            case State::Error:     setScene(RgbScene::Error);     break;
            default: break;
        }
    }

    // 発話 RMS (0..1) — playWavWithLipsync から毎フレーム渡す
    void setSpeakRms(float w) { speak_rms_ = constrain(w, 0.0f, 1.0f); }

    // 発話中の emote タグ — animSpeaking で色を切り替える
    void setSpeakEmote(const char* emote) {
        if (!emote || !*emote) { speak_emote_[0] = '\0'; return; }
        strncpy(speak_emote_, emote, sizeof(speak_emote_) - 1);
        speak_emote_[sizeof(speak_emote_) - 1] = '\0';
    }

    // マイクロ表情連動: Idle 中に一瞬だけ色を変える
    void flashMicroExpression() {
        if (scene_ != RgbScene::Idle) return;
        micro_flash_ms_ = millis();
        micro_flash_active_ = true;
    }

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
            case RgbScene::AmbientIdle: animAmbientIdle(dt); break;
            case RgbScene::Swipe:       animSwipe(dt);       break;
            case RgbScene::Praise:      animPraise(dt);      break;
            case RgbScene::Sleep:       animSleep(dt);       break;
            case RgbScene::PraiseEnd:   animPraiseEnd(dt);   break;
            case RgbScene::Off:
                fill_solid(leds_, RGB_NUM_LEDS, CRGB::Black);
                break;
        }
        show();
    }

private:
    static constexpr uint8_t PY32_ADDR = 0x6F;
    static constexpr uint32_t PY32_I2C_FREQ = 100000;
    static constexpr uint8_t REG_GPIO_M_L = 0x03;
    static constexpr uint8_t REG_GPIO_M_H = 0x04;
    static constexpr uint8_t REG_GPIO_PU_L = 0x09;
    static constexpr uint8_t REG_GPIO_PU_H = 0x0A;
    static constexpr uint8_t REG_GPIO_PD_L = 0x0B;
    static constexpr uint8_t REG_GPIO_PD_H = 0x0C;
    static constexpr uint8_t REG_GPIO_DRV_L = 0x13;
    static constexpr uint8_t REG_GPIO_DRV_H = 0x14;
    static constexpr uint8_t REG_LED_CFG = 0x24;
    static constexpr uint8_t REG_LED_RAM_START = 0x30;

    CRGB     leds_[RGB_NUM_LEDS];
    RgbScene scene_      = RgbScene::Off;
    uint32_t scene_ms_   = 0;
    float    speak_rms_  = 0.0f;
    char     speak_emote_[16] = {0};  // 発話中の emote タグ
    uint8_t  ambient_hue_ = 80;  // 現在のアンビエント色相 (非赤系, 30-199)
    bool     py32_ready_  = false;
    uint32_t micro_flash_ms_ = 0;     // マイクロ表情フラッシュ開始時刻
    bool     micro_flash_active_ = false;  // フラッシュ中フラグ

    static constexpr uint32_t AMBIENT_INTERVAL_MS = 180000; // 3 分放置でアンビエント開始
    static constexpr uint32_t AMBIENT_DURATION_MS  = 20000; // アンビエント継続時間 20 秒

    // ---- アニメーション ----------------------------------------

    bool beginPy32() {
        const uint32_t start = millis();
        while (millis() - start < 1200) {
            if (M5.In_I2C.scanID(PY32_ADDR, PY32_I2C_FREQ)) {
                py32_ready_ = true;
                break;
            }
            delay(100);
        }
        if (!py32_ready_) return false;

        // 公式StackChanと同じく、PY32 pin 13をRGB出力用に設定する。
        writeBit(REG_GPIO_M_L, REG_GPIO_M_H, 13, true);
        writeBit(REG_GPIO_PU_L, REG_GPIO_PU_H, 13, true);
        writeBit(REG_GPIO_PD_L, REG_GPIO_PD_H, 13, false);
        writeBit(REG_GPIO_DRV_L, REG_GPIO_DRV_H, 13, false);
        M5.In_I2C.writeRegister8(PY32_ADDR, REG_LED_CFG, RGB_NUM_LEDS & 0x3F, PY32_I2C_FREQ);
        delay(50);
        return true;
    }

    void writeBit(uint8_t reg_l, uint8_t reg_h, uint8_t pin, bool value) {
        const uint8_t reg = pin < 8 ? reg_l : reg_h;
        const uint8_t bit = pin < 8 ? pin : pin - 8;
        uint8_t current = M5.In_I2C.readRegister8(PY32_ADDR, reg, PY32_I2C_FREQ);
        if (value) current |= (1 << bit);
        else current &= ~(1 << bit);
        M5.In_I2C.writeRegister8(PY32_ADDR, reg, current, PY32_I2C_FREQ);
    }

    void show() {
        if (!py32_ready_) return;
        uint8_t data[RGB_NUM_LEDS * 2];
        for (int i = 0; i < RGB_NUM_LEDS; i++) {
            const uint8_t r = (uint16_t)leds_[i].r * MAX_BRIGHT / 255;
            const uint8_t g = (uint16_t)leds_[i].g * MAX_BRIGHT / 255;
            const uint8_t b = (uint16_t)leds_[i].b * MAX_BRIGHT / 255;
            const uint16_t rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3);
            data[i * 2] = rgb565 & 0xFF;
            data[i * 2 + 1] = (rgb565 >> 8) & 0xFF;
        }
        M5.In_I2C.writeRegister(PY32_ADDR, REG_LED_RAM_START, data, sizeof(data), PY32_I2C_FREQ);
        const uint8_t cfg = M5.In_I2C.readRegister8(PY32_ADDR, REG_LED_CFG, PY32_I2C_FREQ);
        M5.In_I2C.writeRegister8(PY32_ADDR, REG_LED_CFG, cfg | (1 << 6), PY32_I2C_FREQ);
    }

    void animIdle(uint32_t dt) {
        // マイクロ表情フラッシュ: 200ms の間だけ色を変える
        if (micro_flash_active_) {
            const uint32_t flash_dt = millis() - micro_flash_ms_;
            if (flash_dt < 200) {
                // フラッシュ中: ピンク〜ラベンダーで一瞬光る
                float ratio = 1.0f - (float)flash_dt / 200.0f;
                uint8_t v = (uint8_t)(ratio * 140.0f);
                fill_solid(leds_, RGB_NUM_LEDS, CHSV(200, 180, v));
                return;
            }
            micro_flash_active_ = false;
        }

        // 3 分放置でランダム非赤色アンビエントへ遷移
        if (dt >= AMBIENT_INTERVAL_MS) {
            ambient_hue_ = (uint8_t)(30 + rand() % 170);  // 30-199: 黄〜青紫 (赤除外)
            scene_    = RgbScene::AmbientIdle;
            scene_ms_ = millis();
            return;
        }
        // 青紫ブリージング: 6秒周期。暗くなる側を長めにして、ぱさっと消えないようにする。
        float t = (float)(dt % 6000) / 6000.0f;
        float wave;
        if (t < 0.35f) {
            wave = t / 0.35f;                    // 2.1秒でふわっと明るく
        } else {
            wave = 1.0f - (t - 0.35f) / 0.65f;   // 3.9秒かけてゆっくり暗く
        }
        wave = constrain(wave, 0.0f, 1.0f);
        wave = wave * wave * (3.0f - 2.0f * wave);  // smoothstep
        uint8_t v = (uint8_t)(6.0f + wave * 112.0f);
        uint8_t h = (uint8_t)(170 + (int)(sinf(t * M_PI) * 8));
        fill_solid(leds_, RGB_NUM_LEDS, CHSV(h, 210, v));
    }

    void animAmbientIdle(uint32_t dt) {
        // 20 秒経過したら通常 Idle に戻す
        if (dt >= AMBIENT_DURATION_MS) {
            scene_    = RgbScene::Idle;
            scene_ms_ = millis();
            return;
        }
        // ランダム色でゆっくり明滅 (3 秒周期)
        float t   = (float)(dt % 3000) / 3000.0f;
        uint8_t v = (uint8_t)(sinf(t * 2.0f * (float)M_PI) * 55.0f + 65.0f);
        fill_solid(leds_, RGB_NUM_LEDS, CHSV(ambient_hue_, 220, v));
    }

    void animSwipe(uint32_t dt) {
        // 黄色バースト → 1.5 秒でフェードアウト
        uint8_t v;
        if (dt < 150) {
            v = 220;  // 最初の 150ms は全開輝度
        } else {
            float ratio = 1.0f - constrain((float)(dt - 150) / 1350.0f, 0.0f, 1.0f);
            v = (uint8_t)(ratio * 220.0f);
        }
        fill_solid(leds_, RGB_NUM_LEDS, CHSV(52, 200, v));
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
        // emote に応じた色味で RMS 連動の輝度変化
        uint8_t h, s;
        if (speak_emote_[0] == '\0' || strcmp(speak_emote_, "neutral") == 0) {
            // 既定: 暖色 (琥珀)
            h = 38; s = 180;
        } else if (strcmp(speak_emote_, "joy") == 0) {
            // 嬉しい: 明るい黄色〜オレンジ
            h = 42; s = 200;
        } else if (strcmp(speak_emote_, "sad") == 0) {
            // 悲しい: 水色〜青
            h = 150; s = 180;
        } else if (strcmp(speak_emote_, "shy") == 0 || strcmp(speak_emote_, "embarrassed") == 0) {
            // 照れ・はにかみ: ピンク
            h = 220; s = 160;
        } else if (strcmp(speak_emote_, "surprised") == 0) {
            // 驚き: キラキラ白〜水色
            h = 160; s = 120;
        } else if (strcmp(speak_emote_, "angry") == 0) {
            // 怒り: 赤
            h = 0; s = 240;
        } else if (strcmp(speak_emote_, "sleepy") == 0) {
            // 眠い: 紫
            h = 190; s = 160;
        } else if (strcmp(speak_emote_, "confident") == 0) {
            // 自信: 緑
            h = 96; s = 180;
        } else if (strcmp(speak_emote_, "mischief") == 0) {
            // いたずら: 黄緑
            h = 72; s = 200;
        } else if (strcmp(speak_emote_, "relieved") == 0) {
            // 安堵: 薄緑
            h = 100; s = 140;
        } else if (strcmp(speak_emote_, "cold") == 0) {
            // 冷淡: グレーがかった青
            h = 160; s = 60;
        } else if (strcmp(speak_emote_, "panic") == 0) {
            // 慌て: 赤紫
            h = 240; s = 200;
        } else if (strcmp(speak_emote_, "confused") == 0) {
            // 困惑: 黄色
            h = 50; s = 180;
        } else {
            h = 38; s = 180;
        }
        uint8_t v = (uint8_t)(50.0f + speak_rms_ * 160.0f);
        fill_solid(leds_, RGB_NUM_LEDS, CHSV(h, s, v));
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
        fill_solid(leds_, RGB_NUM_LEDS, on ? CRGB(CHSV(0, 255, 180)) : CRGB::Black);
    }

    void animError(uint32_t dt) {
        // 赤フラッシュして消える
        uint8_t v = dt < 400 ? (uint8_t)(200 - dt / 2) : 0;
        fill_solid(leds_, RGB_NUM_LEDS, CHSV(0, 255, v));
    }

    void animPraise(uint32_t dt) {
        // 褒められた時のピンクパルス: ふわっと明るくなってゆっくり消える (1.5秒)
        float ratio;
        uint8_t h;
        if (dt < 300) {
            // 0-300ms: ふわっと明るく (ピンク)
            ratio = (float)dt / 300.0f;
            h = 220;  // ピンク
        } else if (dt < 1500) {
            // 300-1500ms: ゆっくり消える (色相をラベンダーへシフト)
            float fade = 1.0f - (float)(dt - 300) / 1200.0f;
            ratio = fade * fade;  // 2次減衰でふわっと消える
            h = 200 + (uint8_t)(20.0f * (1.0f - fade));  // 200→220 ラベンダー
        } else {
            // 終了 → Idle に戻る
            scene_ = RgbScene::Idle;
            scene_ms_ = millis();
            return;
        }
        uint8_t v = (uint8_t)(ratio * 180.0f);
        fill_solid(leds_, RGB_NUM_LEDS, CHSV(h, 160, v));
    }

    void animPraiseEnd(uint32_t dt) {
        // 褒め反応の終了フェード: 500ms かけてゆっくり消える
        float ratio = 1.0f - constrain((float)dt / 500.0f, 0.0f, 1.0f);
        ratio = ratio * ratio;  // 2次減衰
        uint8_t v = (uint8_t)(ratio * 120.0f);
        fill_solid(leds_, RGB_NUM_LEDS, CHSV(210, 140, v));
        if (dt >= 500) {
            scene_ = RgbScene::Idle;
            scene_ms_ = millis();
        }
    }

    void animSleep(uint32_t dt) {
        // 寝顔時の呼吸灯: 青〜シアンでゆっくり明滅 (4秒周期)
        float t = (float)(dt % 4000) / 4000.0f;
        // ゆっくり吸って、もっとゆっくり吐く (非対称波形)
        float wave;
        if (t < 0.4f) {
            wave = t / 0.4f;                    // 1.6秒で明るく
        } else {
            wave = 1.0f - (t - 0.4f) / 0.6f;   // 2.4秒かけて暗く
        }
        wave = constrain(wave, 0.0f, 1.0f);
        wave = wave * wave * (3.0f - 2.0f * wave);  // smoothstep
        // 青〜シアンの間で色相がゆらぐ
        uint8_t h = (uint8_t)(150 + (int)(sinf(t * M_PI * 0.5f) * 20));
        uint8_t v = (uint8_t)(4.0f + wave * 60.0f);  // 暗め (眠い雰囲気)
        fill_solid(leds_, RGB_NUM_LEDS, CHSV(h, 180, v));
    }
};

} // namespace stackchan
