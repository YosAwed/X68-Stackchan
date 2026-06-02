// ========================================================
//  X68-Stackchan (ぺけ子ちゃん版) ファームウェア (CoreS3 SE)
//
//  Push-to-talk → 母艦 PC に WAV を POST → 応答 WAV を再生
//  会話中はぺけ子ちゃんの表情 (face_NN.jpg in LittleFS) を切り替える
//  発話中は PCM の RMS で「口閉/口開」の 2 フレーム口パク
// ========================================================
#include <Arduino.h>
#include <WiFi.h>
// M5Unified より先に LittleFS.h を引いておく。M5GFX (common.hpp) は
// _LITTLEFS_H_ が定義された時点で DataWrapperT<fs::LittleFSFS> 特殊化を
// 有効化する。順番が逆だと drawJpgFile(LittleFS, ...) が pure-virtual
// な抽象クラスを実体化しようとして main.cpp.o が落ちる。
#include <LittleFS.h>
#include <M5Unified.h>
// 頭頂タッチセンサー (Si12T, I2C) を扱う公式 BSP。M5StackChan.begin() で
// I/O expander + RGB + TouchSensor をまとめて初期化、loop で update() を
// 呼ぶと M5StackChan.TouchSensor が Button_Class 互換で wasPressed() などを
// 提供する。
#include <M5StackChan.h>
#include <esp_random.h>
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
#include "rgb_controller.h"
#include "touch_handler.h"
#include "imu_handler.h"
#include "remote_handler.h"

using namespace stackchan;

static PekekoFace     g_face;
static AudioRecorder  g_rec;
static State          g_state = State::Boot;
static bool           g_wait_release_after_auto_send = false;
static uint32_t       g_headpat_start_ms = 0;   // Headpat 状態に入った時刻
static uint32_t       g_headpat_last_press_ms = 0;  // 直近で isPressed=true だった時刻
static uint32_t       g_last_mic_log_ms = 0;
static uint32_t       g_last_pull_ms    = 0;

// ---- Idle 中のまばたき ----
// 4〜8 秒のランダム間隔で目を閉じた表情 (FACE_BLINK) を 150 ms 表示する。
// Speaking / Listening / Thinking 中は動かない (state が Idle の時だけ走る)。
static constexpr uint32_t BLINK_HOLD_MS = 150;
static constexpr uint32_t BLINK_MIN_MS  = 4000;
static constexpr uint32_t BLINK_MAX_MS  = 8000;
static uint32_t g_next_blink_ms    = 0;   // 次のまばたき開始予定 (millis())
static uint32_t g_blink_started_ms = 0;   // 0 = 表示してない、>0 = 表示中の開始時刻

static inline void scheduleNextBlink() {
    const uint32_t span = BLINK_MAX_MS - BLINK_MIN_MS;
    g_next_blink_ms    = millis() + BLINK_MIN_MS + (uint32_t)(rand() % span);
    g_blink_started_ms = 0;
}

// ---- Idle 中のマイクロ表情 (気分のゆらぎ) ----
// 8〜15 秒のランダム間隔で 5 種の表情を 800 ms 表示する。
// blink と排他: 片方が動いている間は他方をトリガしない。
static constexpr uint32_t MICRO_HOLD_MS = 800;
static constexpr uint32_t MICRO_MIN_MS  = 8000;
static constexpr uint32_t MICRO_MAX_MS  = 15000;
static uint32_t g_next_micro_ms    = 0;
static uint32_t g_micro_started_ms = 0;

static inline void scheduleNextMicro() {
    const uint32_t span = MICRO_MAX_MS - MICRO_MIN_MS;
    g_next_micro_ms    = millis() + MICRO_MIN_MS + (uint32_t)(rand() % span);
    g_micro_started_ms = 0;
}

static inline void updateIdleBlink() {
    const uint32_t now = millis();
    if (g_blink_started_ms != 0) {
        if (now - g_blink_started_ms >= BLINK_HOLD_MS) {
            g_face.show(faces::FACE_IDLE);
            scheduleNextBlink();
        }
        return;
    }
    if (g_micro_started_ms != 0) return;   // マイクロ表情中はトリガしない
    if (now >= g_next_blink_ms) {
        g_face.show(faces::FACE_BLINK);
        g_blink_started_ms = now;
    }
}

static inline void updateIdleMicro() {
    const uint32_t now = millis();
    if (g_micro_started_ms != 0) {
        if (now - g_micro_started_ms >= MICRO_HOLD_MS) {
            g_face.show(faces::FACE_IDLE);
            scheduleNextMicro();
        }
        return;
    }
    if (g_blink_started_ms != 0) return;   // まばたき中はトリガしない
    if (now >= g_next_micro_ms) {
        static const int kMicroFaces[] = {
            faces::F_SOFT_SMILE,
            faces::F_SPARKLE_EYES,
            faces::F_BASHFUL,
            faces::F_BORED,
            faces::F_YAWN_SMALL,
        };
        constexpr int kN = sizeof(kMicroFaces) / sizeof(kMicroFaces[0]);
        g_face.show(kMicroFaces[rand() % kN]);
        g_micro_started_ms = now;
    }
}

// 定期発話 / 外部 push をサーバから取りに行く間隔 (Idle 中のみ)
constexpr uint32_t PULL_INTERVAL_MS = 30000;

// 一時リアクション (なでなで / シェイク) の終了時刻
static uint32_t g_reaction_end_ms = 0;

#if SERVO_ENABLED
static ServoController g_servo;
#endif
#if RGB_ENABLED
static RgbController   g_rgb;
#endif
static TouchHandler    g_touch;
static ImuHandler      g_imu;
static RemoteHandler   g_remote;

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
    // Idle に入る時にまばたき/マイクロ表情タイマを初期化、Idle 以外に出る時は停止。
    if (s == State::Idle) {
        scheduleNextBlink();
        scheduleNextMicro();
    } else {
        g_blink_started_ms = 0;
        g_micro_started_ms = 0;
    }
#if SERVO_ENABLED
    switch (s) {
        case State::Idle:      g_servo.goIdle();      break;
        case State::Listening: g_servo.goListening(); break;
        case State::Thinking:  g_servo.goThinking();  break;
        case State::Speaking:  g_servo.goSpeaking();  break;
        default: break;
    }
#endif
#if RGB_ENABLED
    g_rgb.onState(s);
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

// 応答 WAV を再生しながら、PCM の RMS で口パク (口閉/口開/大開 3 段階)
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

    // 3 段階閾値: <LOW=口閉じ、<HIGH=口開け、>=HIGH=大開け (climax 音節)
    // RMS_THRESH=2200 の旧運用と同等の頻度で open が出る帯域に挟む。
    constexpr int RMS_LOW  = 1400;
    constexpr int RMS_HIGH = 3500;
    int  last_face = -1;
    uint32_t last_update = 0;

    // 発話中の (口閉, 口開, 大開) を emote タグに応じて決める。
    // 未知 / 空 / nullptr なら neutral 既定 (closed=SMILE, open=DETERMINED, wide=JOY)。
    int face_close = faces::FACE_SPEAK_CLOSED;
    int face_open  = faces::FACE_SPEAK_OPEN;
    int face_wide  = faces::FACE_SPEAK_WIDE;
    faces::resolve_speak_triple(emote, face_close, face_open, face_wide);

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
            const int next = (rms >= RMS_HIGH) ? face_wide
                            : (rms >= RMS_LOW)  ? face_open
                                                : face_close;
            if (next != last_face) {
                g_face.show(next);
                last_face = next;
            }
#if SERVO_ENABLED
            {
                const float w = constrain((float)rms / 8000.0f, 0.0f, 1.0f);
                g_servo.setSpeakLipWeight(w);
                g_servo.update();
#if RGB_ENABLED
                g_rgb.setSpeakRms(w);
                g_rgb.update();
#endif
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
    // 公式 StackChan キット拡張 (I/O expander, RGB, 頭頂 Si12T タッチ) を初期化。
    // M5Unified 配下の I2C バスをそのまま借りるので順序的に M5.begin() の後で。
    M5StackChan.begin();
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
#if RGB_ENABLED
    g_rgb.begin();
#endif

    if (!g_rec.begin()) {
        g_face.show(faces::FACE_ERR_GENERIC);
        return;
    }
#if defined(OFFLINE_MODE) && OFFLINE_MODE
    Serial.println("[OFFLINE] skipping WiFi & server ready check (kawaii test mode)");
#else
    if (!connectWiFi()) {
        g_face.show(faces::FACE_ERR_WIFI);
        return;
    }
    g_remote.begin();

    ReadyResponse ready = ChatClient::ready();
    if (!ready.ok) {
        showReadyError(ready);
        return;
    }
    Serial.printf("[READY] server ok: %s\n", ready.body.c_str());
#endif

    // 短いウェーブの後、Idle 表情へ
    delay(700);
    const uint32_t seed = micros() ^ esp_random();
    randomSeed(seed);
    srand(seed);   // upstream の scheduleNextBlink が rand() を使う
    setState(State::Idle, faces::FACE_IDLE);
}

void loop() {
    M5.update();
    M5StackChan.update();   // 頭頂タッチセンサー (Si12T) のポーリング

    // CoreS3 SE は物理ボタンが無く、M5Unified の virtual button マッピングも
    // デフォルトでは効かない (touch 検知はできるが BtnA/B/C は発火しない)。
    // M5.Touch を直接読んで「画面のどこかを触っていれば押下 = 録音」と扱う。
    bool pressed = false;
    if (M5.Touch.getCount() > 0) {
        const auto t = M5.Touch.getDetail(0);
        pressed = t.isPressed();
    }
    const bool remote_ptt_edge = g_remote.btnAEdge();  // 毎フレーム呼ぶ (エッジ追跡)

#if defined(OFFLINE_MODE) && OFFLINE_MODE
    {
        static bool prev = false;
        if (pressed != prev) {
            Serial.printf("[TOUCH] %s\n", pressed ? "PRESS" : "RELEASE");
            prev = pressed;
        }
    }
#endif

    switch (g_state) {
        case State::Idle: {
            if (!pressed && !g_remote.btnA()) {
                g_wait_release_after_auto_send = false;
            }
            // 頭頂タッチが優先 (LCD タッチより先にチェック)。
            if (M5StackChan.TouchSensor.wasPressed() && !g_wait_release_after_auto_send) {
                playHeadpatChime();
                const uint32_t now = millis();
                g_headpat_start_ms = now;
                g_headpat_last_press_ms = now;
                M5StackChan.showRgbColor(180, 60, 100);
                setState(State::Headpat, faces::F_BASHFUL);
                Serial.println("[HEADPAT] start");
                break;
            }
            // リモコン: ジョイスティックでサーボ手動操作
#if SERVO_ENABLED
            if (g_remote.isConnected()) {
                const float yaw   = g_remote.yawNorm();
                const float pitch = g_remote.pitchNorm();
                if (yaw != 0.0f || pitch != 0.0f) {
                    g_servo.setTarget(yaw, -pitch, ServoController::LERP_FAST);
                }
            }
#endif
            // LCD タッチ または リモコン BtnA で録音開始
            if ((pressed || remote_ptt_edge) && !g_wait_release_after_auto_send) {
                if (pressed) {
                    g_face.show(faces::F_SURPRISED);
                    delay(150);
                }
                g_rec.start();
                setState(State::Listening, faces::FACE_LISTENING);
                break;
            }
#if !defined(OFFLINE_MODE) || !OFFLINE_MODE
            // 待機中: スケジュール発話 / 外部 push を取りにいく (wait=0 即時応答)
            // OFFLINE_MODE では WiFi が未初期化のため pull を呼ぶと
            // HTTPClient が semaphore assert で crash → ループ再起動するので塞ぐ。
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
#endif
            // 何もない時はまばたき + マイクロ表情を刻む
            updateIdleBlink();
            updateIdleMicro();
            break;
        }

        case State::Listening: {
            g_rec.poll();
            drawMicLevel(g_rec.lastPeak(), g_rec.lastRms());
            const bool rec_overflow = g_rec.isFull();
            // ローカルボタンもリモコンボタンも離されたら送信
            if ((!pressed && !g_remote.btnA()) || rec_overflow) {
                if (rec_overflow) {
                    Serial.printf("[REC ] Buffer full (%us): auto-sending\n",
                                  (unsigned)MAX_REC_SECONDS);
                    g_wait_release_after_auto_send = true;
                    g_face.show(faces::FACE_REC_OVERFLOW);
                    playOverflowBeep();
                }
                const size_t n = g_rec.stop();
                setState(State::Thinking, faces::FACE_THINKING);
#if defined(OFFLINE_MODE) && OFFLINE_MODE
                // OFFLINE: HTTP 呼び出しせず、考えてるフリ → ack → 笑顔 → Idle
                Serial.printf("[OFFLINE] recorded %u bytes (would POST /chat)\n",
                              (unsigned)n);
                delay(800);
                playAckBeep();
                g_face.show(faces::F_SOFT_SMILE);
                delay(600);
#else
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
#endif
                setState(State::Idle, faces::FACE_IDLE);
            }
            break;
        }

        case State::Headpat: {
            // 「撫でる」動きで Si12T が一瞬 0 強度を返すことがあるので、
            // 500ms 以上タッチが途切れない限り Headpat を継続するヒステリシス。
            const uint32_t now = millis();
            if (M5StackChan.TouchSensor.isPressed()) {
                g_headpat_last_press_ms = now;
            }
            constexpr uint32_t HEADPAT_RELEASE_MS = 500;
            if (now - g_headpat_last_press_ms > HEADPAT_RELEASE_MS) {
                // 撫でられ終わり: LED 消灯 → やわらか笑顔 → Idle
                const uint32_t total = now - g_headpat_start_ms;
                Serial.printf("[HEADPAT] end (total=%ums)\n", (unsigned)total);
                M5StackChan.showRgbColor(0, 0, 0);
                g_face.show(faces::F_SOFT_SMILE);
                delay(600);
                setState(State::Idle, faces::FACE_IDLE);
                break;
            }
            // 撫でられ継続: 100ms 周期で表情 / RGB を更新。
            static uint32_t last_update = 0;
            if (now - last_update < 100) break;
            last_update = now;

            const uint32_t held_ms = now - g_headpat_start_ms;

            // 表情: 段階的に「とろけ」へ
            //   0〜1.5s: F_BASHFUL        (はにかみ)
            //   1.5〜3s: F_LAUGH_EYES_CLOSED (目閉じ大笑い = とろけ笑い)
            //   3s〜:   F_SLEEPING       (気持ちよくて眠っちゃった)
            int target_face;
            if (held_ms < 1500)      target_face = faces::F_BASHFUL;
            else if (held_ms < 3000) target_face = faces::F_LAUGH_EYES_CLOSED;
            else                     target_face = faces::F_SLEEPING;
            static int prev_face = -1;
            if (target_face != prev_face) {
                Serial.printf("[HEADPAT] stage @%ums -> face_%02d\n",
                              (unsigned)held_ms, target_face);
                prev_face = target_face;
            }
            g_face.show(target_face);   // show() は同 ID なら redraw を省く

            // RGB: 段階で色味、脈動 (sin 2.5Hz 相当) で「呼吸」感
            const float t_sec = held_ms / 1000.0f;
            const float pulse = 0.55f + 0.45f * sinf(t_sec * 2.5f);  // 0.10..1.00
            uint8_t r, g, b;
            if (held_ms < 1500) {
                // 薄いピンク
                r = (uint8_t)(180 * pulse);
                g = (uint8_t)( 60 * pulse);
                b = (uint8_t)(100 * pulse);
            } else if (held_ms < 3000) {
                // 暖かいオレンジ
                r = (uint8_t)(255 * pulse);
                g = (uint8_t)(120 * pulse);
                b = (uint8_t)( 40 * pulse);
            } else {
                // 紫マゼンタ (夢見心地)
                r = (uint8_t)(150 * pulse);
                g = (uint8_t)( 40 * pulse);
                b = (uint8_t)(200 * pulse);
            }
            M5StackChan.showRgbColor(r, g, b);
            break;
        }

        default:
            break;
    }

#if SERVO_ENABLED
    g_servo.update();
#endif
#if RGB_ENABLED
    g_rgb.update();
#endif

    delay(5);
}
