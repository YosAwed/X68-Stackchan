// ========================================================
//  X68-Stackchan リモコン送信機ファーム
//  StickC-Plus + Hat Mini JoyC → ESP-NOW → StackChan (CoreS3 SE)
//
//  Hat Mini JoyC (I2C 0x38):
//    Reg 0x00: joy_x uint8_t  (0-255, 中央≈128)
//    Reg 0x01: joy_y uint8_t  (0-255, 中央≈128)
//    Reg 0x02: button uint8_t (bit0=スティック押し込み)
//
//  送信パケット (6 bytes, StackChan 側と共通定義):
//    magic[2] = {0x53, 0xC5}
//    joy_x  int8_t   -100..+100 (左右パン)
//    joy_y  int8_t   -100..+100 (上下チルト)
//    buttons uint8_t bit0=BtnA(録音PTT), bit1=BtnB
// ========================================================
#include <Arduino.h>
#include <M5Unified.h>
#include <WiFi.h>
#include <esp_now.h>
#include <Wire.h>

#include "config.h"

static constexpr uint8_t JOYC_ADDR = 0x38;  // Hat Mini JoyC I2C address

struct __attribute__((packed)) Packet {
    uint8_t magic[2] = {0x53, 0xC5};
    int8_t  joy_x    = 0;
    int8_t  joy_y    = 0;
    uint8_t buttons  = 0;
};

static esp_now_peer_info_t g_peer;
static uint32_t            g_last_tx_ms  = 0;
static bool                g_espnow_ok   = false;
static bool                g_wifi_ok     = false;

static int8_t mapJoy(uint8_t raw) {
    int v = ((int)raw - 128) * 100 / 128;
    if (v >  100) v =  100;
    if (v < -100) v = -100;
    return (int8_t)v;
}

static bool readJoyC(int8_t& x, int8_t& y, bool& btn_stick) {
    Wire.beginTransmission(JOYC_ADDR);
    Wire.write(0x00);
    if (Wire.endTransmission(false) != 0) return false;
    if (Wire.requestFrom(JOYC_ADDR, (uint8_t)3) != 3) return false;
    x         = mapJoy(Wire.read());
    y         = mapJoy(Wire.read());
    btn_stick = (Wire.read() & 0x01) != 0;
    return true;
}

static void drawStatus(const Packet& pkt) {
    M5.Display.setTextSize(2);
    M5.Display.setCursor(4, 28);
    M5.Display.printf("X:%4d  ", (int)pkt.joy_x);
    M5.Display.setCursor(4, 50);
    M5.Display.printf("Y:%4d  ", (int)pkt.joy_y);
    M5.Display.setCursor(4, 72);
    M5.Display.printf("BTN:%02X ", (int)pkt.buttons);

    M5.Display.setTextSize(1);
    M5.Display.setCursor(4, 100);
    if (g_espnow_ok) {
        M5.Display.setTextColor(0x07E0, 0x0000);
        M5.Display.print("ESP-NOW OK ");
    } else {
        M5.Display.setTextColor(0xF800, 0x0000);
        M5.Display.print("ESP-NOW ERR");
    }
    M5.Display.setTextColor(0xFFFF, 0x0000);
}

void setup() {
    auto cfg = M5.config();
    M5.begin(cfg);
    Serial.begin(115200);

    M5.Display.fillScreen(0x0000);
    M5.Display.setTextColor(0xFFFF, 0x0000);
    M5.Display.setTextSize(1);
    M5.Display.setCursor(4, 4);
    M5.Display.print("JoyC Remote");

    // Wi-Fi STA モードで接続 (ESP-NOW チャンネル同期)
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    const uint32_t t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) {
        delay(200);
    }
    g_wifi_ok = (WiFi.status() == WL_CONNECTED);
    if (g_wifi_ok) {
        Serial.printf("WiFi OK  ch=%d  MAC=%s\n",
                      WiFi.channel(), WiFi.macAddress().c_str());
    } else {
        Serial.println("WiFi FAILED — ESP-NOW channel may mismatch");
    }

    // ESP-NOW 初期化
    if (esp_now_init() == ESP_OK) {
        memset(&g_peer, 0, sizeof(g_peer));
        memcpy(g_peer.peer_addr, TARGET_MAC, 6);
        g_peer.channel = 0;      // 0 = 現在のチャンネル
        g_peer.encrypt = false;
        esp_now_add_peer(&g_peer);
        g_espnow_ok = true;
        Serial.println("ESP-NOW ready");
    } else {
        Serial.println("ESP-NOW init failed");
    }
}

void loop() {
    M5.update();

    const uint32_t now = millis();
    if (now - g_last_tx_ms < TX_INTERVAL_MS) return;
    g_last_tx_ms = now;

    Packet pkt;
    bool btn_stick = false;
    if (!readJoyC(pkt.joy_x, pkt.joy_y, btn_stick)) {
        // JoyC 読み取り失敗: ゼロ送信
        pkt.joy_x = 0;
        pkt.joy_y = 0;
    }

    pkt.buttons = 0;
    // BtnA (StickC-Plus 側) またはスティック押し込み → PTT
    if (M5.BtnA.isPressed() || btn_stick) pkt.buttons |= 0x01;
    if (M5.BtnB.isPressed())              pkt.buttons |= 0x02;

    if (g_espnow_ok) {
        esp_now_send(TARGET_MAC, (const uint8_t*)&pkt, sizeof(pkt));
    }

    drawStatus(pkt);
    Serial.printf("[TX] x=%4d y=%4d btn=%02X\n",
                  (int)pkt.joy_x, (int)pkt.joy_y, (int)pkt.buttons);
}
