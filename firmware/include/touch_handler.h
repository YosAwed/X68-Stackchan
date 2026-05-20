// ========================================================
//  なでなで検出 (CoreS3 SE タッチパネル)
//  HOLD_MS 以上タッチし続けると pet イベントを発火。
//  COOL_MS のクールダウンで連続発火を防ぐ。
// ========================================================
#pragma once
#include <Arduino.h>
#include <M5Unified.h>

namespace stackchan {

class TouchHandler {
public:
    static constexpr uint32_t HOLD_MS = 600;   // 触れ続ける閾値 (ms)
    static constexpr uint32_t COOL_MS = 3000;  // 反応後のクールダウン (ms)

    // true が返った瞬間が「なでなで検出」
    bool update() {
        const uint32_t now = millis();
        if (now - last_pet_ms_ < COOL_MS) return false;

        const bool touched = M5.Touch.getCount() > 0;
        if (touched) {
            if (touch_start_ms_ == 0) touch_start_ms_ = now;
            if (now - touch_start_ms_ >= HOLD_MS) {
                last_pet_ms_    = now;
                touch_start_ms_ = 0;
                return true;
            }
        } else {
            touch_start_ms_ = 0;
        }
        return false;
    }

private:
    uint32_t touch_start_ms_ = 0;
    uint32_t last_pet_ms_    = 0;
};

} // namespace stackchan
