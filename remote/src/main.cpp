// ========================================================
//  X68-Stackchan リモコン送信機ファーム
//  StickC-Plus → ESP-NOW → StackChan (CoreS3 SE)
//
//  送信パケット (6 bytes, StackChan 側と共通定義):
//    magic[2] = {0x53, 0xC5}
//    joy_x  int8_t   0固定 (ジョイスティック未使用)
//    joy_y  int8_t   0固定 (ジョイスティック未使用)
//    buttons uint8_t bit0=BtnA(録音PTT), bit1=BtnB
// ========================================================
#include <Arduino.h>
#include <M5Unified.h>
#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>

#include "config.h"

struct __attribute__((packed)) Packet {
    uint8_t magic[2] = {0x53, 0xC5};
    int8_t  joy_x    = 0;
    int8_t  joy_y    = 0;
    uint8_t buttons  = 0;
};

static esp_now_peer_info_t g_peer;
static uint32_t            g_last_tx_ms  = 0;
static uint32_t            g_last_draw_ms = 0;
static bool                g_espnow_ok   = false;
static bool                g_wifi_ok     = false;
static uint8_t             g_last_buttons = 0;
static uint32_t            g_last_wifi_retry_ms = 0;
static bool                g_channel_scan = false;
static uint8_t             g_scan_channel = 1;

static void startChannelScan() {
    if (g_channel_scan) return;
    WiFi.setAutoReconnect(false);
    WiFi.disconnect(false, false);
    g_channel_scan = true;
    g_scan_channel = 1;
    Serial.println("ESP-NOW channel scan fallback");
}

static void maintainWifi() {
    if (g_channel_scan) return;
    const bool connected = WiFi.status() == WL_CONNECTED;
    if (connected != g_wifi_ok) {
        g_wifi_ok = connected;
        if (connected) {
            Serial.printf("WiFi reconnected ch=%d\n", WiFi.channel());
        } else {
            Serial.println("WiFi lost");
        }
    }
    if (connected) return;

    const uint32_t now = millis();
    if (now - g_last_wifi_retry_ms < 5000) return;
    g_last_wifi_retry_ms = now;
    // APが見つからない状態でreconnectを重ねるとSTA接続処理が競合する。
    // 一定時間でESP-NOWチャンネル巡回へ切り替える。
    startChannelScan();
}

static void drawStatus(const Packet& pkt) {
    M5.Display.setTextSize(2);
    M5.Display.setCursor(4, 28);
    M5.Display.print("PTT Remote ");
    M5.Display.setCursor(4, 50);
    M5.Display.printf("BTN:%02X     ", (int)pkt.buttons);
    M5.Display.setCursor(4, 72);
    M5.Display.print(pkt.buttons & 0x01 ? "PTT ON  " : "PTT OFF ");

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

static void onSent(const wifi_tx_info_t* info, esp_now_send_status_t status) {
    (void)info;
    (void)status;
}

void setup() {
    auto cfg = M5.config();
    M5.begin(cfg);
    Serial.begin(115200);

    M5.Display.fillScreen(0x0000);
    M5.Display.setTextColor(0xFFFF, 0x0000);
    M5.Display.setTextSize(1);
    M5.Display.setCursor(4, 4);
    M5.Display.print("PTT Remote");

    // Wi-Fi STA モードで接続 (ESP-NOW チャンネル同期)
    WiFi.mode(WIFI_STA);
    WiFi.persistent(false);
    WiFi.setAutoReconnect(true);
    WiFi.setSleep(false);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    const uint32_t t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) {
        delay(200);
    }
    g_wifi_ok = (WiFi.status() == WL_CONNECTED);
    esp_wifi_set_ps(WIFI_PS_NONE);
    if (g_wifi_ok) {
        Serial.printf("WiFi OK  ch=%d  MAC=%s\n",
                      WiFi.channel(), WiFi.macAddress().c_str());
    } else {
        Serial.println("WiFi FAILED — ESP-NOW channel may mismatch");
        startChannelScan();
    }

    // ESP-NOW 初期化
    if (esp_now_init() == ESP_OK) {
        memset(&g_peer, 0, sizeof(g_peer));
        memcpy(g_peer.peer_addr, TARGET_MAC, 6);
        // 0 はSTAの現在チャンネルを使う指定。AP再開時にチャンネルが
        // 変わってもpeer情報を書き直さず追従できる。
        g_peer.channel = 0;
        g_peer.encrypt = false;
        esp_now_add_peer(&g_peer);
        esp_now_register_send_cb(onSent);
        g_espnow_ok = true;
        Serial.println("ESP-NOW ready");
    } else {
        Serial.println("ESP-NOW init failed");
    }
}

void loop() {
    M5.update();
    maintainWifi();

    const uint32_t now = millis();
    // AP未接続時は25msごとに1chずつ移動し、約325msで1〜13chを一巡。
    const uint32_t tx_interval = g_channel_scan ? 25 : TX_INTERVAL_MS;
    if (now - g_last_tx_ms < tx_interval) return;
    g_last_tx_ms = now;

    Packet pkt;
    pkt.buttons = 0;
    // BtnA (StickC-Plus 側) → PTT
    if (M5.BtnA.isPressed()) pkt.buttons |= 0x01;
    if (M5.BtnB.isPressed())              pkt.buttons |= 0x02;

    if (g_espnow_ok) {
        if (g_channel_scan) {
            esp_wifi_set_channel(g_scan_channel, WIFI_SECOND_CHAN_NONE);
            g_scan_channel = g_scan_channel >= 13 ? 1 : g_scan_channel + 1;
        }
        esp_now_send(TARGET_MAC, (const uint8_t*)&pkt, sizeof(pkt));
    }

    if (pkt.buttons != g_last_buttons) {
        g_last_buttons = pkt.buttons;
        Serial.printf("[BTN] %02X\n", pkt.buttons);
    }
    if (now - g_last_draw_ms >= 250) {
        g_last_draw_ms = now;
        drawStatus(pkt);
    }
}
