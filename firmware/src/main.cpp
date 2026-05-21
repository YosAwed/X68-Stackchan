// ========================================================
//  X68-Stackchan (ぺけ子ちゃん版) ファームウェア (CoreS3 SE)
//
//  Push-to-talk → 母艦 PC に WAV を POST → 応答 WAV を再生
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
#include "servo_controller.h"

using namespace stackchan;

static PekekoFace     g_face;
static AudioRecorder  g_rec;
static State          g_state = State::Boot;
static bool           g_wait_release_after_auto_send = false;
static uint32_t       g_last_mic_log_ms = 0;
static uint32_t       g_last_pull_ms    = 0;

// 定期発話 / 外部 push をサーバから取りに行く間隔 (Idle 中のみ)
constexpr uint32_t PULL_INTERVAL_MS = 30000;

#if SERVO_ENABLED
static ServoController g_servo;
#endif

static void clearSideStatus() {
    const int dx = (M5.Display.width() - PekekoFace::kSize) / 2;
    if (dx <= 0) return;
    M5.Display.fillRect(0, 0, dx, M5.Display.height(), X68_BG);
    M5.Display.fillRect(dx + PekekoFace::kSize, 0,
                        M5.Display.width() - dx - PekekoFace::kSize,
                        M5.Display.height(), X68_BG);
}

static void setState(State s, int face_id = -1) {
    g_state = s;
    if (face_id > 0) g_face.show(face_id);
    clearSideStatus();
#if SERVO_ENABLED
    switch (s) {
        case State::Idle:      g_servo.goIdle();      break;
        case State::Listening: g_servo.goListening(); break;
        case State::Thinking:  g_servo.goThinking();  break;
        case State::Speaking:  g_servo.goSpeaking();  break;
        default: break;
    }
#endif
}

static void drawMicLevel(uint16_t peak, uint16_t rms) {
    const int dx = (M5.Display.width() - PekekoFace::kSize) / 2;
    if (dx < 28) return;

    constexpr int x = 8;
    constexpr int y = 24;
    constexpr int w = 18;
    constexpr int h = 188;
    constexpr uint16_t frame = 0x7BEF;
    constexpr uint16_t peak_color = 0xFBE0;
    constexpr uint16_t rms_color = 0x07E0;

    const int rms_h = (int)((uint32_t)rms * h / 12000);
    const int peak_h = (int)((uint32_t)peak * h / 18000);
    const int rh = rms_h > h ? h : rms_h;
    const int ph = peak_h > h ? h : peak_h;

    M5.Display.fillRect(x - 2, y - 2, w + 4, h + 4, X68_BG);
    M5.Display.drawRect(x, y, w, h, frame);
    if (ph > 0) M5.Display.fillRect(x + 2, y + h - ph, w - 4, ph, peak_color);
    if (rh > 0) M5.Display.fillRect(x + 5, y + h - rh, w - 10, rh, rms_color);

    const uint32_t now = millis();
    if (now - g_last_mic_log_ms >= 500) {
        g_last_mic_log_ms = now;
        Serial.printf("[MIC ] peak=%u rms=%u\n", (unsigned)peak, (unsigned)rms);
    }
}

static void showReadyError(const ReadyResponse& ready) {
    setState(State::Error, faces::FACE_ERR_SERVER);
    M5.Display.fillScreen(X68_BG);
    M5.Display.setTextColor(0xF800, X68_BG);
    M5.Display.setTextSize(1);
    M5.Display.setCursor(12, 70);
    M5.Display.println("SERVER NOT READY");
    M5.Display.setTextColor(0xFFFF, X68_BG);
    M5.Display.setCursor(12, 96);
    M5.Display.printf("HTTP: %d\n", ready.http_status);
    M5.Display.setCursor(12, 116);
    M5.Display.println("Check /ready, Ollama, STT, TTS");
    Serial.printf("[ERR ] /ready failed: HTTP %d\n", ready.http_status);
    if (ready.body.length() > 0) {
        Serial.printf("[READY] %s\n", ready.body.c_str());
    }
    playServerErrorBeep();
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
static void playWavWithLipsync(const uint8_t* wav, size_t size,
                               const char* emote = nullptr) {
    if (!wav || size < 44) return;

    // WAV ヘッダから data チャンクを探す (簡易: 標準的な配置を仮定)
    const uint8_t* pcm = wav + 44;
    size_t pcm_bytes = size - 44;
    uint32_t wav_sr = MIC_SAMPLE_RATE;
    uint16_t wav_channels = 1;
    uint16_t wav_bits = 16;
    uint16_t wav_block = 2;

    for (size_t i = 12; i + 24 <= size && i < 256; ++i) {
        if (memcmp(wav + i, "fmt ", 4) == 0) {
            uint32_t fmt_size; memcpy(&fmt_size, wav + i + 4, 4);
            if (fmt_size >= 16 && i + 8 + fmt_size <= size) {
                memcpy(&wav_channels, wav + i + 10, 2);
                memcpy(&wav_sr,       wav + i + 12, 4);
                memcpy(&wav_block,    wav + i + 20, 2);
                memcpy(&wav_bits,     wav + i + 22, 2);
            }
            break;
        }
    }

    // 安全策: "data" タグを探して位置を上書き
    for (size_t i = 12; i + 8 <= size && i < 256; ++i) {
        if (memcmp(wav + i, "data", 4) == 0) {
            uint32_t n; memcpy(&n, wav + i + 4, 4);
            pcm = wav + i + 8;
            const size_t available = size - (i + 8);
            pcm_bytes = (n < available) ? n : available;
            break;
        }
    }
    if (wav_bits != 16 || wav_channels == 0 || wav_block < 2) {
        M5.Speaker.setVolume(SPK_VOLUME);
        M5.Speaker.playWav(wav, size);
        while (M5.Speaker.isPlaying()) delay(4);
        return;
    }
    pcm_bytes -= pcm_bytes % wav_block;
    const size_t frames = pcm_bytes / wav_block;

    M5.Speaker.setVolume(SPK_VOLUME);
    const uint32_t t0 = millis();
    M5.Speaker.playWav(wav, size);

    constexpr int RMS_THRESH = 2200;            // 経験値: 大きすぎたら下げる
    int  last_face = -1;
    uint32_t last_update = 0;

    // 発話中の (口開, 口閉) を emote タグに応じて決める。
    // 未知 / 空 / nullptr なら既定の FACE_SPEAK_OPEN / FACE_SPEAK_CLOSED。
    int face_open  = faces::FACE_SPEAK_OPEN;
    int face_close = faces::FACE_SPEAK_CLOSED;
    faces::resolve_speak_pair(emote, face_open, face_close);

    while (M5.Speaker.isPlaying()) {
        const uint32_t now = millis();
        if (now - last_update >= 40) {           // 25 fps 相当で更新
            last_update = now;
            const uint32_t elapsed_ms = now - t0;
            const size_t pos = (size_t)elapsed_ms * wav_sr / 1000;
            // 前後 ±256 サンプルで RMS
            const size_t lo = pos > 256 ? pos - 256 : 0;
            const size_t hi = pos + 256 < frames ? pos + 256 : frames;
            int64_t sumsq = 0;
            for (size_t i = lo; i < hi; ++i) {
                int16_t s; std::memcpy(&s, pcm + i * wav_block, 2);
                sumsq += (int32_t)s * s;
            }
            const int rms = (hi > lo) ? (int)std::sqrt((double)sumsq / (hi - lo)) : 0;
            const int next = (rms > RMS_THRESH) ? face_open : face_close;
            if (next != last_face) {
                g_face.show(next);
                last_face = next;
            }
#if SERVO_ENABLED
            {
                const float w = constrain((float)rms / 8000.0f, 0.0f, 1.0f);
                g_servo.setSpeakLipWeight(w);
                g_servo.update();
            }
#endif
        }
        delay(4);
    }
    // 締めは口閉じ (emote ペアに合わせる)
    g_face.show(face_close);
}

static void handleHttpError(int status) {
    if (status == 0) {
        Serial.printf("[ERR ] HTTP timeout / connect failed\n");
        setState(State::Error, faces::FACE_ERR_TIMEOUT);
        playErrorBeep();
    } else if (status == 413) {
        Serial.printf("[ERR ] HTTP 413: audio too large\n");
        setState(State::Error, faces::FACE_ERR_TOO_LARGE);
        playTooLargeBeep();
    } else if (status >= 500) {
        Serial.printf("[ERR ] HTTP %d: server error\n", status);
        setState(State::Error, faces::FACE_ERR_SERVER);
        playServerErrorBeep();
    } else {
        Serial.printf("[ERR ] HTTP %d\n", status);
        setState(State::Error, faces::FACE_ERR_HTTP);
        playErrorBeep();
    }
    delay(1500);
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

#if SERVO_ENABLED
    if (!g_servo.begin()) {
        Serial.println("[WARN] Servo init failed — check wiring");
    }
#endif

    if (!g_rec.begin()) {
        g_face.show(faces::FACE_ERR_GENERIC);
        return;
    }
    if (!connectWiFi()) {
        g_face.show(faces::FACE_ERR_WIFI);
        return;
    }
    ReadyResponse ready = ChatClient::ready();
    if (!ready.ok) {
        showReadyError(ready);
        return;
    }
    Serial.printf("[READY] server ok: %s\n", ready.body.c_str());

    // 短いウェーブの後、Idle 表情へ
    delay(700);
    setState(State::Idle, faces::FACE_IDLE);
}

void loop() {
    M5.update();

    const bool pressed = M5.BtnA.isPressed();

    switch (g_state) {
        case State::Idle:
            if (!pressed) {
                g_wait_release_after_auto_send = false;
            }
            if (pressed && !g_wait_release_after_auto_send) {
                g_rec.start();
                setState(State::Listening, faces::FACE_LISTENING);
                break;
            }
            // 待機中: スケジュール発話 / 外部 push を取りにいく (wait=0 即時応答)
            if (millis() - g_last_pull_ms >= PULL_INTERVAL_MS) {
                g_last_pull_ms = millis();
                PullResponse pr = ChatClient::pull(0);
                if (pr.ok && pr.body && pr.body_size > 0) {
                    Serial.printf("[PUSH] %s (source=%s emote=%s)\n",
                                  pr.bot_text.c_str(), pr.source.c_str(),
                                  pr.emote.length() ? pr.emote.c_str() : "neutral");
                    setState(State::Speaking);     // 顔/サーボのみ。lipsync が表情を上書き
                    playAckBeep();
                    playWavWithLipsync(pr.body, pr.body_size, pr.emote.c_str());
                    free(pr.body);
                    setState(State::Idle, faces::FACE_IDLE);
                }
            }
            break;

        case State::Listening: {
            g_rec.poll();
            drawMicLevel(g_rec.lastPeak(), g_rec.lastRms());
            const bool rec_overflow = g_rec.isFull();
            if (!pressed || rec_overflow) {
                if (rec_overflow) {
                    Serial.printf("[REC ] Buffer full (%us): auto-sending\n",
                                  (unsigned)MAX_REC_SECONDS);
                    g_wait_release_after_auto_send = true;
                    g_face.show(faces::FACE_REC_OVERFLOW);
                    playOverflowBeep();
                }
                const size_t n = g_rec.stop();
                setState(State::Thinking, faces::FACE_THINKING);
                ChatResponse r = ChatClient::send(g_rec.data(), n);
                if (r.ok) {
                    Serial.printf("[USER] %s\n", r.user_text.c_str());
                    Serial.printf("[BOT ] %s\n", r.bot_text.c_str());
                    if (r.timing.length() > 0) {
                        Serial.printf("[TIME] %s\n", r.timing.c_str());
                    }
                    if (r.tts_backend.length() > 0) {
                        Serial.printf("[TTS ] %s\n", r.tts_backend.c_str());
                    }
                    if (r.emote.length() > 0) {
                        Serial.printf("[EMO ] %s\n", r.emote.c_str());
                    }
                    g_state = State::Speaking;
                    playAckBeep();
                    playWavWithLipsync(r.body, r.body_size, r.emote.c_str());
                    free(r.body);
                } else {
                    handleHttpError(r.http_status);
                }
                setState(State::Idle, faces::FACE_IDLE);
            }
            break;
        }

        default:
            break;
    }

#if SERVO_ENABLED
    g_servo.update();
#endif

    delay(5);
}
