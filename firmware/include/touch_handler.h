// ========================================================
//  Si12T 3ゾーン静電タッチ検出 (StackChan 天面タッチパネル)
//  Silicon Labs Si12T, I2C 0x68 (Wire / Port.A バス)
//  3 電極: CS0=左, CS1=中央, CS2=右
//  HOLD_MS 以上のタッチでイベント発火。COOL_MS クールダウン付き。
// ========================================================
#pragma once
#include <Arduino.h>
#include <Wire.h>

namespace stackchan {

class TouchHandler {
public:
    static constexpr uint32_t HOLD_MS    = 500;   // 触れ続ける閾値 (ms)
    static constexpr uint32_t COOL_MS    = 3000;  // 反応後のクールダウン (ms)
    static constexpr uint8_t  SI12T_ADDR = 0x68;
    static constexpr uint8_t  REG_STATUS = 0x00;

    enum class Zone : uint8_t { None = 0, Left = 1, Center = 2, Right = 3 };

    // true が返った瞬間が「なでなで検出」。lastZone() で触れた場所を取得。
    bool update() {
        const uint32_t now = millis();
        if (now - last_pet_ms_ < COOL_MS) return false;

        const Zone z = readZone();
        if (z != Zone::None) {
            if (touch_start_ms_ == 0) touch_start_ms_ = now;
            if (now - touch_start_ms_ >= HOLD_MS) {
                last_zone_      = z;
                last_pet_ms_    = now;
                touch_start_ms_ = 0;
                return true;
            }
        } else {
            touch_start_ms_ = 0;
        }
        return false;
    }

    Zone lastZone() const { return last_zone_; }

private:
    uint32_t touch_start_ms_ = 0;
    uint32_t last_pet_ms_    = 0;
    Zone     last_zone_      = Zone::None;

    static Zone readZone() {
        Wire.beginTransmission(SI12T_ADDR);
        Wire.write(REG_STATUS);
        if (Wire.endTransmission(false) != 0) return Zone::None;
        if (Wire.requestFrom(SI12T_ADDR, (uint8_t)1) != 1) return Zone::None;
        const uint8_t s = Wire.read();
        // bit0=CS0(左), bit1=CS1(中央), bit2=CS2(右)
        if (s & 0x01) return Zone::Left;
        if (s & 0x02) return Zone::Center;
        if (s & 0x04) return Zone::Right;
        return Zone::None;
    }
};

} // namespace stackchan
