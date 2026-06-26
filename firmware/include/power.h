// ========================================================
//  電源管理 (バッテリ駆動時の安全策 + アイドル時 deep sleep)
//
//  - 低電池 (≤15%): サーボを止める (突入電流で本体リセットを防ぐ)
//  - クリティカル (≤5%): 即 powerOff() で安全停止
//  - Idle が 5 分以上続いたら deep sleep。電源ボタンで復帰 (= リセット)
//
//  残量表示はやらない方針なので、状態は serial と動作 (サーボ抑止 / 入眠)
//  のみで間接的に分かる。
// ========================================================
#pragma once

#include <Arduino.h>
#include <M5Unified.h>

#include "avatar_state.h"

namespace stackchan {

class PowerManager {
public:
    void begin() {
        last_activity_ms_   = millis();
        last_batt_check_ms_ = 0;
        low_warning_active_ = false;
    }

    // State 遷移ごとに呼ぶ。idle タイマの起点を更新する。
    void noteActivity() { last_activity_ms_ = millis(); }

    // 毎ループ末尾で呼ぶ。クリティカルなら戻ってこない (powerOff)。
    void poll() {
        const uint32_t now = millis();
        if (now - last_batt_check_ms_ < BATT_POLL_MS) return;
        last_batt_check_ms_ = now;

        const int pct = M5.Power.getBatteryLevel();
        if (pct < 0) return;   // 取得失敗時はスキップ

        if (pct <= BATT_CRITICAL_PCT) {
            Serial.printf("Battery critical (%d%%), powering off\n", pct);
            delay(50);
            M5.Power.powerOff();
            return;
        }
        if (pct <= BATT_WARN_PCT) {
            if (!low_warning_active_) {
                Serial.printf("Battery low (%d%%), suppressing servo\n", pct);
                low_warning_active_ = true;
            }
        } else if (pct > BATT_WARN_PCT + 5) {   // ヒステリシスで戻し
            low_warning_active_ = false;
        }
    }

    bool batteryLow() const { return low_warning_active_; }

    bool shouldSleep(State state) const {
        return state == State::Idle &&
               (millis() - last_activity_ms_) >= IDLE_SLEEP_MS;
    }

    // Zzz 表情を出してから呼ぶこと。戻ってこない (復帰時はリセット → setup() 再走)。
    void enterDeepSleep() {
        Serial.println("Idle timeout, deep sleep (press POWER button to wake)");
        delay(50);
        M5.Power.deepSleep();
    }

private:
    static constexpr int      BATT_WARN_PCT     = 15;
    static constexpr int      BATT_CRITICAL_PCT = 5;
    static constexpr uint32_t IDLE_SLEEP_MS     = 5UL * 60 * 1000;  // 5 分
    static constexpr uint32_t BATT_POLL_MS      = 5000;             // 5 秒ごと

    uint32_t last_activity_ms_   = 0;
    uint32_t last_batt_check_ms_ = 0;
    bool     low_warning_active_ = false;
};

} // namespace stackchan
