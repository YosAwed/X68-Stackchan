// ========================================================
//  ESP-NOW リモコン受信 (StackChan 側)
//
//  StickC-Plus + Hat Mini JoyC からパケットを受信する。
//  WiFi 接続後に begin() を呼ぶこと (チャンネル同期のため)。
//
//  パケット形式 (6 bytes):
//    magic[2] = {0x53, 0xC5}   識別子
//    joy_x    int8_t  -100..+100 (左右パン)
//    joy_y    int8_t  -100..+100 (上下チルト)
//    buttons  uint8_t bit0=BtnA, bit1=BtnB
// ========================================================
#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include <esp_now.h>

namespace stackchan {

class RemoteHandler {
public:
    static constexpr uint32_t TIMEOUT_MS  = 600;  // 受信途絶のタイムアウト
    static constexpr int8_t   DEADZONE    = 12;   // ジョイスティック不感帯

    struct State {
        int8_t  joy_x   = 0;
        int8_t  joy_y   = 0;
        uint8_t buttons = 0;
        uint32_t last_ms = 0;
    };

    bool begin() {
        if (esp_now_init() != ESP_OK) {
            Serial.println("[RMT ] ESP-NOW init failed");
            return false;
        }
        esp_now_register_recv_cb(onRecv);
        instance_ = this;
        Serial.println("[RMT ] ESP-NOW receiver ready");
        return true;
    }

    // リモコンが接続中 (最近パケットを受信した) か
    bool isConnected() const {
        return state_.last_ms > 0 &&
               millis() - state_.last_ms < TIMEOUT_MS;
    }

    // ジョイスティック値 (不感帯処理済み) を normalized -1..+1 で返す
    float yawNorm()   const { return applyDeadzone(state_.joy_x) / 100.0f; }
    float pitchNorm() const { return applyDeadzone(state_.joy_y) / 100.0f; }

    bool btnA() const { return (state_.buttons & 0x01) != 0; }
    bool btnB() const { return (state_.buttons & 0x02) != 0; }

    // ボタン A の立ち上がりエッジ検出 (毎 loop 呼ぶ)
    bool btnAEdge() {
        const bool cur = btnA();
        const bool edge = cur && !prev_btn_a_;
        prev_btn_a_ = cur;
        return edge;
    }

    const State& raw() const { return state_; }

private:
    static constexpr uint8_t MAGIC[2] = {0x53, 0xC5};

    struct __attribute__((packed)) Packet {
        uint8_t magic[2];
        int8_t  joy_x;
        int8_t  joy_y;
        uint8_t buttons;
    };

    State state_;
    bool  prev_btn_a_ = false;
    static RemoteHandler* instance_;

    static float applyDeadzone(int8_t v) {
        if (v > -DEADZONE && v < DEADZONE) return 0.0f;
        return (float)v;
    }

    static void onRecv(const esp_now_recv_info_t* info, const uint8_t* data, int len) {
        (void)info;
        if (!instance_ || len < (int)sizeof(Packet)) return;
        const Packet* pkt = reinterpret_cast<const Packet*>(data);
        if (pkt->magic[0] != MAGIC[0] || pkt->magic[1] != MAGIC[1]) return;
        instance_->state_.joy_x   = pkt->joy_x;
        instance_->state_.joy_y   = pkt->joy_y;
        instance_->state_.buttons = pkt->buttons;
        instance_->state_.last_ms = millis();
    }
};

RemoteHandler* RemoteHandler::instance_ = nullptr;

} // namespace stackchan
