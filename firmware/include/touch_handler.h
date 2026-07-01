// ========================================================
//  Si12T 3ゾーン静電タッチ — スワイプ / なでなで検出
//  StackChan BSP が読んだ Si12T の 3 ゾーン強度からイベントを判定する。
//
//  スワイプ: 異なるゾーンへの遷移 (SWIPE_WINDOW_MS 以内)
//  なでなで: 同一ゾーンを HOLD_MS 以上保持
//  スワイプを優先するため SWIPE_WINDOW_MS < HOLD_MS に設定。
// ========================================================
#pragma once
#include <Arduino.h>
#include <array>

namespace stackchan {

class TouchHandler {
public:
    static constexpr uint32_t SWIPE_WINDOW_MS = 600;   // スワイプ判定ウィンドウ
    static constexpr uint32_t HOLD_MS         = 800;   // なでなで判定閾値 (> SWIPE_WINDOW)
    static constexpr uint32_t COOL_MS         = 2500;  // 反応後のクールダウン
    static constexpr uint8_t  SI12T_ADDR      = 0x68;
    static constexpr uint8_t  REG_STATUS      = 0x00;

    enum class Zone  : uint8_t { None=0, Left, Center, Right };
    enum class Event : uint8_t { None=0, Pet, Swipe };

    // None/Pet/Swipe を返す。Swipe はスワイプ、Pet はなでなで検出。
    Event update(const std::array<uint8_t, 3>& intensities) {
        const uint32_t now  = millis();
        if (now - last_event_ms_ < COOL_MS) return Event::None;

        const Zone zone = readZone(intensities);

        switch (state_) {
            case S::Idle:
                if (zone != Zone::None) {
                    first_zone_ = zone;
                    first_ms_   = now;
                    state_      = S::FirstTouch;
                }
                break;

            case S::FirstTouch:
                if (zone == Zone::None) {
                    // ホールド未達・スワイプ未達で離した → キャンセル
                    state_ = S::Idle;
                } else if (zone != first_zone_) {
                    // 別ゾーンへ遷移 → スワイプ確定
                    state_         = S::Idle;
                    last_event_ms_ = now;
                    return Event::Swipe;
                } else if (now - first_ms_ >= HOLD_MS) {
                    // 同ゾーン HOLD_MS 以上保持 → なでなで確定
                    state_         = S::Idle;
                    last_event_ms_ = now;
                    return Event::Pet;
                }
                break;
        }
        return Event::None;
    }

private:
    enum class S : uint8_t { Idle, FirstTouch };
    S        state_         = S::Idle;
    Zone     first_zone_    = Zone::None;
    uint32_t first_ms_      = 0;
    uint32_t last_event_ms_ = 0;

    static Zone readZone(const std::array<uint8_t, 3>& intensities) {
        if (intensities[0] > 0) return Zone::Left;
        if (intensities[1] > 0) return Zone::Center;
        if (intensities[2] > 0) return Zone::Right;
        return Zone::None;
    }
};

} // namespace stackchan
