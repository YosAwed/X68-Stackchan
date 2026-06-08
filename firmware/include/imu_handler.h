// ========================================================
//  動き検出 (M5Unified IMU)
//  加速度の大きさ (G) で動きを 2 段階に判定する:
//    mag > SHAKE_G            → Shake (激しく振られた → 目回し)
//    LIFT_G < mag <= SHAKE_G  → Lift  (持ち上げ / 小突きなど中程度の動き → 驚き)
//  それぞれ独立クールダウンで連続発火を防ぐ。
//  ※ IMU 非搭載の機体 (getAccel が false) では常に None を返す no-op。
// ========================================================
#pragma once
#include <Arduino.h>
#include <M5Unified.h>
#include <cmath>

namespace stackchan {

class ImuHandler {
public:
    enum class Event : uint8_t { None, Shake, Lift };

    static constexpr float    SHAKE_G    = 2.8f;   // 重力加速度の倍率 (1G = 通常静止)
    static constexpr float    LIFT_G     = 1.4f;   // 持ち上げ/小突き判定の下限
    static constexpr uint32_t SHAKE_COOL = 2500;   // シェイクのクールダウン (ms)
    static constexpr uint32_t LIFT_COOL  = 1500;   // 持ち上げのクールダウン (ms)

    // None 以外が返った瞬間がイベント検出。
    Event update() {
        float ax, ay, az;
        if (!M5.Imu.getAccel(&ax, &ay, &az)) return Event::None;

        const float    mag = sqrtf(ax * ax + ay * ay + az * az);
        const uint32_t now = millis();

        // 強い動きを先に判定。シェイクのクールダウン中は Lift に漏らさず None。
        if (mag > SHAKE_G) {
            if (now - last_shake_ms_ < SHAKE_COOL) return Event::None;
            last_shake_ms_ = now;
            return Event::Shake;
        }
        if (mag > LIFT_G) {
            if (now - last_lift_ms_ < LIFT_COOL) return Event::None;
            last_lift_ms_ = now;
            return Event::Lift;
        }
        return Event::None;
    }

private:
    uint32_t last_shake_ms_ = 0;
    uint32_t last_lift_ms_  = 0;
};

} // namespace stackchan
