// ========================================================
//  シェイク検出 (M5Unified IMU)
//  加速度の大きさが SHAKE_G を超えたら shake イベントを発火。
//  COOL_MS のクールダウンで連続発火を防ぐ。
// ========================================================
#pragma once
#include <Arduino.h>
#include <M5Unified.h>
#include <cmath>

namespace stackchan {

class ImuHandler {
public:
    static constexpr float    SHAKE_G  = 2.8f;   // 重力加速度の倍率 (1G = 通常静止)
    static constexpr uint32_t COOL_MS  = 2500;   // クールダウン (ms)

    // true が返った瞬間が「シェイク検出」
    bool update() {
        const uint32_t now = millis();
        if (now - last_shake_ms_ < COOL_MS) return false;

        float ax, ay, az;
        if (!M5.Imu.getAccel(&ax, &ay, &az)) return false;

        const float mag = sqrtf(ax * ax + ay * ay + az * az);
        if (mag > SHAKE_G) {
            last_shake_ms_ = now;
            return true;
        }
        return false;
    }

private:
    uint32_t last_shake_ms_ = 0;
};

} // namespace stackchan
