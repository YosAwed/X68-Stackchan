// ========================================================
//  SG90 x2 パン・チルト制御 (ESP32-S3 LEDC 使用)
//
//  ESP-IDF 5.x / arduino-esp32 3.x 対応 API:
//    ledcAttach(pin, freq, bits)
//    ledcWrite(pin, duty)
// ========================================================
#pragma once
#include <Arduino.h>
#include <cmath>

namespace stackchan {

class ServoController {
public:
    // SG90 パラメータ
    static constexpr uint32_t PWM_FREQ     = 50;      // 50 Hz
    static constexpr uint8_t  PWM_BITS     = 16;      // 16-bit resolution
    static constexpr float    PULSE_CTR_US = 1450.0f; // 中点
    static constexpr float    PULSE_HLF_US = 950.0f;  // ±幅 (500..2400μs)
    static constexpr float    PERIOD_US    = 20000.0f;

    // 機構上の可動範囲 (中点からの角度)
    static constexpr float YAW_MAX_DEG   = 45.0f;
    static constexpr float PITCH_MAX_DEG = 20.0f;

    // 補間速度 (update() 1 回あたりの進捗率)
    static constexpr float LERP_FAST = 0.10f; // 素早い遷移 (~200 ms)
    static constexpr float LERP_SLOW = 0.05f; // なめらかな遷移 (~400 ms)

    // --------------------------------------------------------
    //  初期化
    // --------------------------------------------------------
    bool begin(int yaw_pin, int pitch_pin) {
        yaw_pin_   = yaw_pin;
        pitch_pin_ = pitch_pin;

        if (!ledcAttach(yaw_pin_,   PWM_FREQ, PWM_BITS)) return false;
        if (!ledcAttach(pitch_pin_, PWM_FREQ, PWM_BITS)) return false;

        writeDeg(yaw_pin_,   0.0f, YAW_MAX_DEG);
        writeDeg(pitch_pin_, 0.0f, PITCH_MAX_DEG);
        last_idle_ms_ = millis();
        return true;
    }

    // --------------------------------------------------------
    //  ターゲット設定 (normalized: -1.0..+1.0)
    // --------------------------------------------------------
    void setTarget(float yaw, float pitch, float speed = LERP_FAST) {
        target_yaw_   = constrain(yaw,   -1.0f, 1.0f);
        target_pitch_ = constrain(pitch, -1.0f, 1.0f);
        lerp_speed_   = speed;
        in_idle_      = false;
    }

    // --------------------------------------------------------
    //  状態別プリセット
    // --------------------------------------------------------
    void goIdle() {
        target_yaw_   = 0.0f;
        target_pitch_ = 0.0f;
        lerp_speed_   = LERP_SLOW;
        in_idle_      = true;
        last_idle_ms_ = millis();
        idle_interval_ms_ = 2000 + (uint32_t)(rand() % 3000);
    }

    void goListening() {
        setTarget(0.0f, 0.12f, LERP_FAST);  // 少し前傾き
    }

    void goThinking() {
        setTarget(0.2f, -0.1f, LERP_SLOW);  // 右に傾いて考え込む
    }

    void goSpeaking() {
        setTarget(0.0f, 0.08f, LERP_FAST);  // 正面やや上向き
        speak_base_pitch_ = 0.08f;
    }

    // 発話中に RMS を渡して微小な頷き動作 (0..1)
    void setSpeakLipWeight(float w) {
        if (!in_idle_) {
            target_pitch_ = speak_base_pitch_ + w * 0.08f;
        }
    }

    // --------------------------------------------------------
    //  毎ループ呼び出し (約 10 ms 周期を想定)
    // --------------------------------------------------------
    void update() {
        const uint32_t now = millis();

        // Idle 時: ゆっくりランダムにさ迷う
        if (in_idle_ && now - last_idle_ms_ > idle_interval_ms_) {
            last_idle_ms_     = now;
            idle_interval_ms_ = 2000 + (uint32_t)(rand() % 3000);
            target_yaw_   = (float)(rand() % 7 - 3) * 0.10f;  // -0.3..+0.3
            target_pitch_ = (float)(rand() % 5 - 2) * 0.07f;  // -0.14..+0.14
        }

        // Lerp で現在位置を目標に近づける
        current_yaw_   += (target_yaw_   - current_yaw_)   * lerp_speed_;
        current_pitch_ += (target_pitch_ - current_pitch_) * lerp_speed_;

        writeDeg(yaw_pin_,   current_yaw_,   YAW_MAX_DEG);
        writeDeg(pitch_pin_, current_pitch_, PITCH_MAX_DEG);
    }

    // --------------------------------------------------------
    //  センタリング (電源オフ前など)
    // --------------------------------------------------------
    void center() {
        setTarget(0.0f, 0.0f, LERP_FAST);
    }

private:
    int yaw_pin_   = -1;
    int pitch_pin_ = -1;

    float current_yaw_   = 0.0f;
    float current_pitch_ = 0.0f;
    float target_yaw_    = 0.0f;
    float target_pitch_  = 0.0f;
    float lerp_speed_    = LERP_FAST;
    float speak_base_pitch_ = 0.0f;

    bool     in_idle_         = false;
    uint32_t last_idle_ms_    = 0;
    uint32_t idle_interval_ms_ = 3000;

    // normalized (-1..+1) → PWM duty
    void writeDeg(int pin, float norm, float max_deg) {
        float deg  = norm * max_deg;
        float us   = PULSE_CTR_US + deg * (PULSE_HLF_US / 90.0f);
        us = constrain(us, 500.0f, 2400.0f);
        uint32_t duty = (uint32_t)(us / PERIOD_US * ((1u << PWM_BITS) - 1));
        ledcWrite(pin, duty);
    }
};

} // namespace stackchan
