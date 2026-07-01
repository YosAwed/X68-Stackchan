// ========================================================
//  Wi-Fi 接続ライフサイクル管理
//
//  ESP32 の Wi-Fi ドライバは接続中に WiFi.begin() を重ねると
//  xQueueSemaphoreTake assert で落ちることがある。
//  ここで STA 初期化を 1 回に限定し、接続試行も直列化する。
// ========================================================
#pragma once

#include <Arduino.h>
#include <WiFi.h>

#include "config.h"

namespace stackchan {

namespace wifi_detail {

inline bool& initialized() {
    static bool v = false;
    return v;
}

inline bool& connecting() {
    static bool v = false;
    return v;
}

inline uint32_t& nextAttemptMs() {
    static uint32_t v = 0;
    return v;
}

inline uint32_t& backoffMs() {
    static uint32_t v = 2000;
    return v;
}

inline bool& reconnectEvent() {
    static bool v = false;
    return v;
}

inline bool isConnected() {
    return WiFi.status() == WL_CONNECTED;
}

inline bool isConnectInProgress(wl_status_t st) {
    return st == WL_IDLE_STATUS || st == WL_SCAN_COMPLETED;
}

inline void initStaOnce() {
    if (initialized()) return;
    WiFi.mode(WIFI_STA);
    WiFi.persistent(false);
    WiFi.setAutoReconnect(true);
    WiFi.setSleep(false);
    initialized() = true;
    Serial.println("[WiFi] STA initialized");
}

inline void startConnectAttempt() {
    initStaOnce();
    if (isConnected()) {
        connecting() = false;
        return;
    }

    const wl_status_t st = WiFi.status();
    if (connecting() && isConnectInProgress(st)) {
        // 既に接続処理中。begin() を重ねない。
        return;
    }

    if (st == WL_DISCONNECTED || st == WL_CONNECTION_LOST ||
        st == WL_CONNECT_FAILED || st == WL_NO_SSID_AVAIL) {
        WiFi.disconnect(false, false);
        delay(50);
    }

    connecting() = true;
    WiFi.begin(WIFI_SSID, WIFI_PASS);
}

}  // namespace wifi_detail

class WiFiManager {
public:
    static void initSta() { wifi_detail::initStaOnce(); }

    static bool isConnected() { return wifi_detail::isConnected(); }

    static bool consumeReconnectEvent() {
        if (!wifi_detail::reconnectEvent()) return false;
        wifi_detail::reconnectEvent() = false;
        return true;
    }

    // 起動時など、接続完了まで待つ。
    static bool connectBlocking(uint32_t timeout_ms = 15000) {
        wifi_detail::initStaOnce();
        if (wifi_detail::isConnected()) return true;

        wifi_detail::startConnectAttempt();
        const uint32_t t0 = millis();
        while (!wifi_detail::isConnected()) {
            delay(100);
            if (millis() - t0 > timeout_ms) {
                wifi_detail::connecting() = false;
                if (!wifi_detail::isConnected()) {
                    WiFi.disconnect(false, false);
                }
                Serial.println("[WiFi] connect timeout");
                return false;
            }
        }

        wifi_detail::connecting() = false;
        Serial.printf("[WiFi] connected: %s\n",
                      WiFi.localIP().toString().c_str());
        return true;
    }

    // Idle ループから呼ぶランタイム回復 (指数バックオフ付き)。
    static bool ensureConnected() {
        if (wifi_detail::isConnected()) return true;

        const uint32_t now = millis();
        if (now < wifi_detail::nextAttemptMs()) return false;

        const bool ok = connectBlocking(4000);
        if (ok) {
            Serial.println("[WiFi] reconnected");
            wifi_detail::backoffMs() = 2000;
            wifi_detail::nextAttemptMs() = 0;
            wifi_detail::reconnectEvent() = true;
            return true;
        }

        if (wifi_detail::backoffMs() < 30000) {
            wifi_detail::backoffMs() *= 2;
        }
        wifi_detail::nextAttemptMs() = now + wifi_detail::backoffMs();
        return false;
    }
};

}  // namespace stackchan
