// ========================================================
//  ぺけ子ちゃんの首振りサーボ (SG90 ×2)
//
//  yaw / pitch を State 遷移ごとにスナップで動かす最小実装。
//  サーボが物理的に未接続でも begin() 失敗時は no-op で済むので、
//  会話パイプラインだけ動かしたい場合も安全に通る。
// ========================================================
#pragma once

#include <Arduino.h>
#include <ESP32Servo.h>

#include "config.h"

namespace stackchan {

class ServoMotion {
public:
    bool begin() {
        // ESP32Servo の LEDC タイマを 2 本確保 (yaw / pitch)
        ESP32PWM::allocateTimer(0);
        ESP32PWM::allocateTimer(1);
        yaw_.setPeriodHertz(50);
        pitch_.setPeriodHertz(50);
        if (!yaw_.attach(SERVO_YAW_PIN, 500, 2400))     return false;
        if (!pitch_.attach(SERVO_PITCH_PIN, 500, 2400)) return false;
        ok_ = true;
        setIdle();
        return true;
    }

    bool ok() const { return ok_; }

    // 低電池時に呼ぶ。enabled=false の間は write() / RMS 更新を抑止する
    // (SG90 の突入電流による本体リセット回避)
    void setEnabled(bool enabled) { enabled_ = enabled; }

    void setIdle()      { write(YAW_CENTER,      PITCH_NEUTRAL);     }
    void setListening() { write(YAW_CENTER,      PITCH_FORWARD);     }
    void setThinking()  { write(YAW_CENTER - 12, PITCH_NEUTRAL + 6); }
    void setSpeaking()  { write(YAW_CENTER,      PITCH_NEUTRAL - 4); }

    // 起動直後の挨拶: yaw を左右に振って手振り風 (face_36 のバイバイと同期)
    void bootGreet() {
        if (!ok_) { delay(700); return; }   // サーボ無し時もタイミングを揃える
        for (int i = 0; i < 3; ++i) {
            write(YAW_CENTER - 18, PITCH_NEUTRAL); delay(160);
            write(YAW_CENTER + 18, PITCH_NEUTRAL); delay(160);
        }
        write(YAW_CENTER, PITCH_NEUTRAL);
    }

    // 発話中: 口パクの RMS と同じ 25 fps tick から呼ぶ。
    // 口が開いた瞬間に pitch を少し上げて「うなずきながら喋る」風に。
    void updateSpeakingByRms(int rms, int rms_thresh) {
        if (!enabled_) return;
        const int target = (rms > rms_thresh)
            ? PITCH_NEUTRAL - 8   // 顔上げ
            : PITCH_NEUTRAL - 2;  // ほぼ中立
        if (target != last_pitch_) {
            if (ok_) pitch_.write(target);
            last_pitch_ = target;
        }
    }

private:
    static constexpr int YAW_CENTER    = 90;   // 正面
    static constexpr int PITCH_NEUTRAL = 88;   // 直立
    static constexpr int PITCH_FORWARD = 100;  // ちょっとお辞儀

    void write(int yaw_deg, int pitch_deg) {
        if (!ok_ || !enabled_) return;
        yaw_.write(yaw_deg);
        pitch_.write(pitch_deg);
        last_yaw_   = yaw_deg;
        last_pitch_ = pitch_deg;
    }

    Servo yaw_, pitch_;
    bool ok_      = false;
    bool enabled_ = true;
    int  last_yaw_   = -1;
    int  last_pitch_ = -1;
};

} // namespace stackchan
