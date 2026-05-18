// ========================================================
//  X68-Stackchan (ぺけ子ちゃん版) ファームウェア (CoreS3 SE)
//
//  Push-to-talk → Mac mini に WAV を POST → 応答 WAV を再生
//  会話中はぺけ子ちゃんの表情 (face_NN.jpg in LittleFS) を切り替える
//  発話中は PCM の RMS で「口閉/口開」の 2 フレーム口パク
// ========================================================
#include <Arduino.h>
#include <WiFi.h>
#include <M5Unified.h>
#include <cmath>
#include <cstdint>
#include <cstring>

#include "config.h"
#include "avatar_state.h"
#include "audio_recorder.h"
#include "http_client.h"
#include "pekeko_theme.h"
#include "pekeko_face.h"
#include "face_map.h"
#include "chime.h"

using namespace stackchan;

static PekekoFace     g_face;
static AudioRecorder  g_rec;
static State          g_state = State::Boot;

static void setState(State s, int face_id = -1) {
    g_state = s;
    if (face_id > 0) g_face.show(face_id);
}

static bool connectWiFi() {
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    const uint32_t t0 = millis();
    while (WiFi.status() != WL_CONNECTED) {
        delay(200);
        if (millis() - t0 > 15000) return false;
    }
    Serial.printf("WiFi connected: %s\n", WiFi.localIP().toString().c_str());
    return true;
}

// 応答 WAV を再生しながら、PCM の RMS で口パク (口閉/口開 2 フレーム)
static void playWavWithLipsync(const uint8_t* wav, size_t size) {
    if (!wav || size < 44) return;

    // WAV ヘッダから data チャンクを探す (簡易: 標準的な配置を仮定)
    const uint8_t* pcm = wav + 44;
    size_t pcm_bytes = size - 44;
    // 安全策: "data" タグを探して位置を上書き
    for (size_t i = 12; i + 8 < size && i < 256; ++i) {
        if (memcmp(wav + i, "data", 4) == 0) {
            uint32_t n; memcpy(&n, wav + i + 4, 4);
            pcm = wav + i + 8;
            pcm_bytes = n;
            break;
        }
    }
    const size_t samples = pcm_bytes / 2;       // 16-bit PCM mono 想定
    const uint32_t sr    = MIC_SAMPLE_RATE;     // TTS 側で 16k に統一済み

    M5.Speaker.setVolume(SPK_VOLUME);
    const uint32_t t0 = millis();
    M5.Speaker.playWav(wav, size);

    constexpr int RMS_THRESH = 2200;            // 経験値: 大きすぎたら下げる
    int  last_face = -1;
    uint32_t last_update = 0;

    while (M5.Speaker.isPlaying()) {
        const uint32_t now = millis();
        if (now - last_update >= 40) {           // 25 fps 相当で更新
            last_update = now;
            const uint32_t elapsed_ms = now - t0;
            const size_t pos = (size_t)elapsed_ms * sr / 1000;
            // 前後 ±256 サンプルで RMS
            const size_t lo = pos > 256 ? pos - 256 : 0;
            const size_t hi = pos + 256 < samples ? pos + 256 : samples;
            int64_t sumsq = 0;
            for (size_t i = lo; i < hi; ++i) {
                int16_t s; std::memcpy(&s, pcm + i * 2, 2);
                sumsq += (int32_t)s * s;
            }
            const int rms = (hi > lo) ? (int)std::sqrt((double)sumsq / (hi - lo)) : 0;
            const int next = (rms > RMS_THRESH) ? faces::FACE_SPEAK_OPEN
                                                : faces::FACE_SPEAK_CLOSED;
            if (next != last_face) {
                g_face.show(next);
                last_face = next;
            }
        }
        delay(4);
    }
    // 締めは口閉じ
    g_face.show(faces::FACE_SPEAK_CLOSED);
}

void setup() {
    auto cfg = M5.config();
    M5.begin(cfg);
    Serial.begin(115200);

    // (1) Human68k 風スプラッシュ + 起動チャイム (画面と音は同時進行)
    playBootChime();
    showBootSplash();

    // (2) LittleFS マウント & ぺけ子ちゃん初期化
    if (!g_face.begin()) {
        M5.Display.fillScreen(X68_BG);
        M5.Display.setCursor(20, 100);
        M5.Display.setTextColor(0xF800, X68_BG);
        M5.Display.print("LittleFS init failed");
        return;
    }
    g_face.show(faces::FACE_BOOT_DONE);

    if (!g_rec.begin()) {
        g_face.show(faces::FACE_ERR_GENERIC);
        return;
    }
    if (!connectWiFi()) {
        g_face.show(faces::FACE_ERR_WIFI);
        return;
    }

    // 短いウェーブの後、Idle 表情へ
    delay(700);
    setState(State::Idle, faces::FACE_IDLE);
}

void loop() {
    M5.update();

    const bool pressed = M5.BtnA.isPressed();

    switch (g_state) {
        case State::Idle:
            if (pressed) {
                g_rec.start();
                setState(State::Listening, faces::FACE_LISTENING);
            }
            break;

        case State::Listening: {
            g_rec.poll();
            if (!pressed) {
                const size_t n = g_rec.stop();
                setState(State::Thinking, faces::FACE_THINKING);
                ChatResponse r = ChatClient::send(g_rec.data(), n);
                if (r.ok) {
                    Serial.printf("[USER] %s\n", r.user_text.c_str());
                    Serial.printf("[BOT ] %s\n", r.bot_text.c_str());
                    g_state = State::Speaking;
                    playAckBeep();
                    playWavWithLipsync(r.body, r.body_size);
                    free(r.body);
                } else {
                    Serial.printf("HTTP failed: status=%d\n", r.http_status);
                    setState(State::Error, faces::FACE_ERR_HTTP);
                    playErrorBeep();
                    delay(1200);
                }
                setState(State::Idle, faces::FACE_IDLE);
            }
            break;
        }

        default:
            break;
    }

    delay(5);
}
