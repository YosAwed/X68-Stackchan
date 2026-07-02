// ========================================================
//  電源管理 (バッテリ駆動時の安全策 + アイドル時Sleep)
//
//  - クリティカル (≤5%): 即 powerOff() で安全停止
//  - 低電池 (≤15%): 警告フラグを立てる (batteryLow() で参照可。
//    現状の UART サーボ (SCS0009) は SG90 ほど突入電流問題が無いので
//    主動的なサーボ抑止はしていないが、将来 SG90 系を併用するときの
//    フックは残してある)
//  - Idle が 30 分以上続いたら main.cpp 側でSleepへ移行
//
//  残量表示はやらない方針なので、状態は serial と動作 (入眠 / 停止)
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
            Serial.printf("[PWR ] Battery critical (%d%%), powering off\n", pct);
            delay(50);
            M5.Power.powerOff();
            return;
        }
        if (pct <= BATT_WARN_PCT) {
            if (!low_warning_active_) {
                Serial.printf("[PWR ] Battery low (%d%%)\n", pct);
                low_warning_active_ = true;
            }
        } else if (pct > BATT_WARN_PCT + 5) {   // ヒステリシスで戻し
            low_warning_active_ = false;
        }
    }

    bool batteryLow() const { return low_warning_active_; }

    // Idle 中の経過段階。main.cpp 側で表情を切り替えるのに使う。
    //   Active   : Idle に入ったばかり ( < 3 分)
    //   Bored    : 退屈そう (3 分超)
    //   Yawn     : あくび (4 分超)
    //   Sleeping : Zzz 表情 (5 分超、30 分でSleep)
    enum class IdleStage { Active, Bored, Yawn, Sleeping };

    IdleStage idleStage(State state) const {
        if (state != State::Idle) return IdleStage::Active;
        const uint32_t e = millis() - last_activity_ms_;
        if (e >= IDLE_ZZZ_MS)   return IdleStage::Sleeping;
        if (e >= IDLE_YAWN_MS)  return IdleStage::Yawn;
        if (e >= IDLE_BORED_MS) return IdleStage::Bored;
        return IdleStage::Active;
    }

    bool shouldSleep(State state) const {
        return state == State::Idle && millis() - last_activity_ms_ >= IDLE_SLEEP_MS;
    }

    // 設定でハード deep sleep を使う場合だけ呼ぶ。戻ってこない。
    void enterDeepSleep() {
        Serial.println("[PWR ] Idle timeout, deep sleep (press POWER button to wake)");
        delay(50);
        M5.Power.deepSleep();
    }

private:
    static constexpr int      BATT_WARN_PCT     = 15;
    static constexpr int      BATT_CRITICAL_PCT = 5;
#ifndef POWER_IDLE_BORED_MS
#define POWER_IDLE_BORED_MS (3UL * 60 * 1000)
#endif
#ifndef POWER_IDLE_YAWN_MS
#define POWER_IDLE_YAWN_MS (4UL * 60 * 1000)
#endif
#ifndef POWER_IDLE_ZZZ_MS
#define POWER_IDLE_ZZZ_MS (5UL * 60 * 1000)
#endif
#ifndef POWER_IDLE_SLEEP_MS
#define POWER_IDLE_SLEEP_MS (30UL * 60 * 1000)
#endif

    static constexpr uint32_t IDLE_BORED_MS     = POWER_IDLE_BORED_MS;
    static constexpr uint32_t IDLE_YAWN_MS      = POWER_IDLE_YAWN_MS;
    static constexpr uint32_t IDLE_ZZZ_MS       = POWER_IDLE_ZZZ_MS;
    static constexpr uint32_t IDLE_SLEEP_MS     = POWER_IDLE_SLEEP_MS;
    static constexpr uint32_t BATT_POLL_MS      = 5000;             // 5 秒ごと

    uint32_t last_activity_ms_   = 0;
    uint32_t last_batt_check_ms_ = 0;
    bool     low_warning_active_ = false;
};

} // namespace stackchan
