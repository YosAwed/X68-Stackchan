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
#include "power.h"
#include "midi_player.h"
#include "midi_song_can_can_bunny_2.h"
#include "wifi_manager.h"

using namespace stackchan;

#ifndef MIDI_SAM2695_ENABLED
#define MIDI_SAM2695_ENABLED 0
#endif
#ifndef MIDI_UART_TX_PIN
#define MIDI_UART_TX_PIN -1
#endif
#ifndef MIDI_UART_RX_PIN
#define MIDI_UART_RX_PIN -1
#endif
#ifndef MIDI_VOLUME
#define MIDI_VOLUME 48
#endif
#ifndef MIDI_DANCE_ENABLED
#define MIDI_DANCE_ENABLED 1
#endif
#ifndef MIDI_DANCE_NOD_MEASURES
#define MIDI_DANCE_NOD_MEASURES 4
#endif
#ifndef POWER_IDLE_DEEP_SLEEP_ENABLED
#define POWER_IDLE_DEEP_SLEEP_ENABLED 0
#endif
#ifndef SLEEP_MURMUR_ENABLED
#define SLEEP_MURMUR_ENABLED 1
#endif
#ifndef SLEEP_MURMUR_INITIAL_MIN_MS
#define SLEEP_MURMUR_INITIAL_MIN_MS (45UL * 1000)
#endif
#ifndef SLEEP_MURMUR_INITIAL_MAX_MS
#define SLEEP_MURMUR_INITIAL_MAX_MS (150UL * 1000)
#endif
#ifndef SLEEP_MURMUR_MIN_MS
#define SLEEP_MURMUR_MIN_MS (2UL * 60 * 1000)
#endif
#ifndef SLEEP_MURMUR_MAX_MS
#define SLEEP_MURMUR_MAX_MS (6UL * 60 * 1000)
#endif
#ifndef WAKE_WORD_ENABLED
#define WAKE_WORD_ENABLED 0
#endif
#ifndef WAKE_LISTEN_MS
#define WAKE_LISTEN_MS 1800
#endif
#ifndef WAKE_POLL_INTERVAL_MS
#define WAKE_POLL_INTERVAL_MS 3500
#endif
// ウェイクワード検出時に beep の代わりに「なぁに?」等の短い返事を再生する。
#ifndef WAKE_REPLY_ENABLED
#define WAKE_REPLY_ENABLED 1
#endif
// Idle 中のレアイベント (鼻歌 / 伸び)。低頻度で勝手に何かしている感を出す。
#ifndef IDLE_EVENT_ENABLED
#define IDLE_EVENT_ENABLED 1
#endif
#ifndef IDLE_EVENT_MIN_MS
#define IDLE_EVENT_MIN_MS (2UL * 60 * 1000)
#endif
#ifndef IDLE_EVENT_MAX_MS
#define IDLE_EVENT_MAX_MS (5UL * 60 * 1000)
#endif

static PekekoFace     g_face;
static AudioRecorder  g_rec;
static PowerManager   g_pwr;
static MidiPlayer     g_midi;
static uint8_t        g_midi_volume = MIDI_VOLUME;
static uint32_t       g_midi_dance_step = 0xFFFFFFFFu;
static State          g_state = State::Boot;
static PowerManager::IdleStage g_idle_stage_last = PowerManager::IdleStage::Active;
static bool           g_wait_release_after_auto_send = false;
static uint32_t       g_listening_start_ms = 0;
static bool           g_auto_listening_after_wake = false;
static bool           g_auto_listening_heard_voice = false;
static uint32_t       g_auto_listening_last_voice_ms = 0;
static uint32_t       g_wake_listening_start_ms = 0;
static uint32_t       g_next_wake_listen_ms = 0;
static uint32_t       g_headpat_start_ms = 0;   // Headpat 状態に入った時刻
static uint32_t       g_headpat_last_press_ms = 0;  // 直近で isPressed=true だった時刻
static uint32_t       g_headpat_idle_press_ms = 0;  // Idle 中の頭頂タッチ開始時刻
static uint32_t       g_headpat_idle_cool_ms = 0;   // BSP fallback の再発火抑止
static uint32_t       g_sleep_enter_ms = 0;
static bool           g_sleep_head_released = false;
static uint32_t       g_sleep_touch_wake_start_ms = 0;
static uint32_t       g_sleep_remote_wake_start_ms = 0;
static uint32_t       g_sleep_head_wake_start_ms = 0;
static uint32_t       g_sleep_next_murmur_ms = 0;
static uint32_t       g_last_mic_log_ms = 0;
static bool           g_mic_overlay_visible = false;
static uint32_t       g_last_pull_ms    = 0;

// 診断ステータス最終出力時刻
static uint32_t g_last_status_ms = 0;

// 診断用定期ステータス出力間隔（シリアルでヒープ/WiFi/uptimeを確認しやすくする）
constexpr uint32_t STATUS_INTERVAL_MS = 15000;

// Wake word detected -> hands-free follow-up recording.
// Manual PTT still uses release-to-send; these thresholds only affect the
// automatic listening window after the short "はーい!" wake reply.
constexpr uint32_t AUTO_LISTEN_MAX_MS = 6500;
constexpr uint32_t AUTO_LISTEN_MIN_MS = 900;
constexpr uint32_t AUTO_LISTEN_SILENCE_MS = 1100;
constexpr uint16_t AUTO_LISTEN_RMS_THRESHOLD = 180;
constexpr uint16_t AUTO_LISTEN_PEAK_THRESHOLD = 1200;

// Sleep wake should be intentional. Touch sensors can blip briefly while the
// body settles, so require a short hold instead of waking on a single frame.
constexpr uint32_t SLEEP_WAKE_MIN_AGE_MS = 2500;
constexpr uint32_t SLEEP_TOUCH_WAKE_HOLD_MS = 650;
constexpr uint32_t SLEEP_REMOTE_WAKE_HOLD_MS = 450;
constexpr uint32_t SLEEP_HEAD_WAKE_HOLD_MS = 1200;

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

static inline void flashIdleMicroRgb();
static inline void scheduleNextSleepMurmur(bool initial = false);

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
        flashIdleMicroRgb();
    }
}

// ---- Idle 定期診断ステータス（シリアルログ強化） ----
// 15 秒おきに WiFi 状態・ヒープ使用量・稼働時間を出す。
// これにより WiFi 回復の成否やメモリ圧迫をデバッグしやすくなる。
static inline void updateIdleStatus() {
    const uint32_t now = millis();
    if (now - g_last_status_ms < STATUS_INTERVAL_MS) return;
    g_last_status_ms = now;

    const bool wifi_ok = WiFiManager::isConnected();
    const uint32_t free_heap  = ESP.getFreeHeap();
    const uint32_t free_psram = ESP.getFreePsram();

    Serial.printf("[STATUS] wifi=%d heap=%u psram=%u uptime=%lus\n",
                  wifi_ok ? 1 : 0,
                  (unsigned)free_heap,
                  (unsigned)free_psram,
                  (unsigned long)(now / 1000));
}

// 定期発話 / 外部 push をサーバから取りに行く間隔 (Idle 中のみ)
constexpr uint32_t PULL_INTERVAL_MS = 30000;

// setup で WiFi / 母艦 /ready に失敗した時の再試行間隔 (State::Error 中)
constexpr uint32_t ERROR_RETRY_INTERVAL_MS = 10000;
static uint32_t g_error_retry_ms = 0;
static bool     g_remote_begun = false;

// リモコン A: 1回押しは PTT、ダブルクリックは vision capture。
constexpr uint32_t REMOTE_DOUBLE_CLICK_MS = 350;
static bool     g_remote_a_pending_single = false;
static uint32_t g_remote_a_first_click_ms = 0;

// 一時リアクション (シェイク / 持ち上げ) の表示保持。終了時刻まで Idle の
// まばたき・マイクロ表情・pull を抑止し、表示が即上書きされないようにする。
static uint32_t g_reaction_end_ms = 0;
static bool     g_reaction_active = false;

#if SERVO_ENABLED
static ServoController g_servo;
#endif
#if RGB_ENABLED
static RgbController   g_rgb;
#endif
static TouchHandler    g_touch;
static ImuHandler      g_imu;
static RemoteHandler   g_remote;

static inline void flashIdleMicroRgb() {
#if RGB_ENABLED
    g_rgb.flashMicroExpression();
#endif
}

static inline uint32_t randomDelayMs(uint32_t min_ms, uint32_t max_ms) {
    if (max_ms <= min_ms) return min_ms;
    return min_ms + (uint32_t)(rand() % (max_ms - min_ms + 1));
}

// ---- Idle 中のレアイベント (鼻歌 / 伸び) ----
// 2〜5 分放置されると、たまに鼻歌をふんふん歌ったり、んーっと伸びをする。
static uint32_t g_next_idle_event_ms = 0;

static inline void scheduleNextIdleEvent() {
    g_next_idle_event_ms =
        millis() + randomDelayMs(IDLE_EVENT_MIN_MS, IDLE_EVENT_MAX_MS);
}

static void maybePlayIdleEvent() {
#if !IDLE_EVENT_ENABLED
    return;
#else
    const uint32_t now = millis();
    if (g_next_idle_event_ms == 0 || now < g_next_idle_event_ms) return;
    scheduleNextIdleEvent();
    // まばたき / マイクロ表情の最中なら今回は見送る
    if (g_blink_started_ms != 0 || g_micro_started_ms != 0) return;
#if MIDI_SAM2695_ENABLED
    if (g_midi.isPlaying()) return;
#endif
    if ((rand() & 1) == 0) {
        // 鼻歌: ご機嫌な様子で「ふんふふ〜ん♪」
        Serial.println("[IDLE] event: humming");
        g_face.show(faces::F_SOFT_SMILE);
        flashIdleMicroRgb();
        playHummingTune();   // 再生完了まで block (約 1 秒)
        g_reaction_active = true;
        g_reaction_end_ms = millis() + 800;
    } else {
        // 伸び: んーっと上を向いてゆっくり戻る
        Serial.println("[IDLE] event: stretch");
        g_face.show(faces::F_YAWN_SMALL);
#if SERVO_ENABLED
        g_servo.startStretch();
#endif
        playStretchChime();
        g_reaction_active = true;
        g_reaction_end_ms = millis() + 2600;
    }
#endif
}

static inline void scheduleNextSleepMurmur(bool initial) {
#if SLEEP_MURMUR_ENABLED
    g_sleep_next_murmur_ms = millis() + (
        initial
            ? randomDelayMs(SLEEP_MURMUR_INITIAL_MIN_MS, SLEEP_MURMUR_INITIAL_MAX_MS)
            : randomDelayMs(SLEEP_MURMUR_MIN_MS, SLEEP_MURMUR_MAX_MS));
#else
    (void)initial;
    g_sleep_next_murmur_ms = 0;
#endif
}

static void updateRemoteAButton(bool edge, bool& single_click, bool& double_click) {
    single_click = false;
    double_click = false;
    const uint32_t now = millis();

    if (edge) {
        if (g_remote_a_pending_single &&
            now - g_remote_a_first_click_ms <= REMOTE_DOUBLE_CLICK_MS) {
            g_remote_a_pending_single = false;
            double_click = true;
            return;
        }
        g_remote_a_pending_single = true;
        g_remote_a_first_click_ms = now;
    }

    if (g_remote_a_pending_single &&
        now - g_remote_a_first_click_ms > REMOTE_DOUBLE_CLICK_MS) {
        g_remote_a_pending_single = false;
        single_click = true;
    }
}

#if MIDI_SAM2695_ENABLED
static void setMidiVolume(uint8_t volume) {
    g_midi_volume = volume > 127 ? 127 : volume;
    g_midi.setVolume(g_midi_volume);
}

static void adjustMidiVolume(int delta) {
    int next = (int)g_midi_volume + delta;
    if (next < 0) next = 0;
    if (next > 127) next = 127;
    setMidiVolume((uint8_t)next);
}

static void stopMidiPlayback(const char* source) {
    if (!g_midi.isPlaying()) return;
    Serial.printf("[MIDI] stop requested by %s\n", source);
    g_midi.stop();
}

static void toggleMidiPlayback(const char* source) {
    if (g_midi.isPlaying()) {
        stopMidiPlayback(source);
        return;
    }
    if (!g_midi.isReady()) {
        Serial.println("[MIDI] not ready");
        return;
    }
    Serial.printf("[MIDI] play requested by %s\n", source);
    g_midi.play(
        songs::CAN_CAN_BUNNY_2_SUPERIOR_SELECT_SCENARIO_MID,
        songs::CAN_CAN_BUNNY_2_SUPERIOR_SELECT_SCENARIO_MID_LEN);
}

static void updateMidiDance() {
#if SERVO_ENABLED && MIDI_DANCE_ENABLED
    if (!g_midi.isPlaying() || g_state != State::Idle) {
        g_midi_dance_step = 0xFFFFFFFFu;
        return;
    }

    const uint16_t division = g_midi.division();
    if (division == 0) return;

    const uint32_t step_ticks = max<uint32_t>(1, division / 2);
    const uint32_t tick = g_midi.playbackTick();
    const uint32_t step = tick / step_ticks;
    if (step == g_midi_dance_step) return;
    g_midi_dance_step = step;

    const uint32_t measure_ticks = g_midi.ticksPerMeasure();
    if (MIDI_DANCE_NOD_MEASURES > 0 && measure_ticks > 0) {
        const uint32_t measure = tick / measure_ticks;
        const uint32_t step_in_measure = (tick % measure_ticks) / step_ticks;
        if (measure > 0 &&
            (measure % MIDI_DANCE_NOD_MEASURES) == 0 &&
            step_in_measure < 2) {
            const float pitch = (step_in_measure == 0) ? 0.30f : -0.10f;
            g_servo.setTarget(0.0f, pitch, ServoController::LERP_FAST);
            return;
        }
    }

    if ((step & 1u) == 0) {
        const float side = (step & 2u) ? -0.28f : 0.28f;
        g_servo.setTarget(side, 0.10f, ServoController::LERP_FAST);
    } else {
        g_servo.setTarget(0.0f, -0.03f, ServoController::LERP_FAST);
    }
#endif
}
#endif

static void clearSideStatus() {
    // 顔画像を 320x240 全画面にしたため、左右のステータス余白はない。
}

static void setState(State s, int face_id = -1, bool note_activity = true) {
    const State prev_state = g_state;
    g_state = s;
    if (face_id > 0) {
        g_face.show(face_id);
        g_mic_overlay_visible = false;
    }
    if (note_activity) {
        g_pwr.noteActivity();
    }
    clearSideStatus();
    // Idle に入る時にまばたき/マイクロ表情タイマを初期化、Idle 以外に出る時は停止。
    if (s == State::Idle) {
        scheduleNextBlink();
        scheduleNextMicro();
        scheduleNextIdleEvent();
    } else {
        g_blink_started_ms = 0;
        g_micro_started_ms = 0;
    }
    // 状態遷移時は一時リアクションの保持も解除しておく (取り残し防止)。
    g_reaction_active = false;
#if SERVO_ENABLED
    if (prev_state != s) {
        switch (s) {
            case State::Idle:      g_servo.goIdle();      break;
            case State::WakeListening:
                // Background wake sampling should not make the head nod every
                // polling cycle. Only real follow-up Listening moves upward.
                break;
            case State::Listening: g_servo.goListening(); break;
            case State::Thinking:  g_servo.goThinking();  break;
            case State::Speaking:  g_servo.goSpeaking();  break;
            case State::Headpat:   g_servo.goHeadpat();   break;
            case State::Sleep:     g_servo.goSleep();     break;
            default: break;
        }
    }
#endif
#if RGB_ENABLED
    g_rgb.onState(s);
#endif
}

static void enterBackgroundWakeListening() {
    // Periodic wake sampling is intentionally invisible. It should not reset
    // idle blink/micro timers, move the servo, change RGB, or redraw the face.
    g_state = State::WakeListening;
}

static void leaveBackgroundWakeListening() {
    // Return to Idle just as quietly. The regular Idle loop will continue
    // blinking/micro expressions from its existing timers.
    g_state = State::Idle;
}

static void drawMicLevel(uint16_t peak, uint16_t rms) {
    constexpr uint16_t ACTIVE_PEAK_MIN = 120;
    constexpr uint16_t ACTIVE_RMS_MIN = 80;
    const bool active = peak >= ACTIVE_PEAK_MIN || rms >= ACTIVE_RMS_MIN;

    if (!active) {
        if (g_mic_overlay_visible) {
            g_face.refresh();
            g_mic_overlay_visible = false;
        }
    } else {
        constexpr int x = 6;
        constexpr int y = 34;
        constexpr int w = 14;
        constexpr int h = 156;
        constexpr uint16_t panel = 0x0000;
        constexpr uint16_t frame = 0x7BEF;
        constexpr uint16_t peak_color = 0xFBE0;
        constexpr uint16_t rms_color = 0x07E0;

        const int rms_h = (int)((uint32_t)rms * h / 12000);
        const int peak_h = (int)((uint32_t)peak * h / 18000);
        const int rh = rms_h > h ? h : rms_h;
        const int ph = peak_h > h ? h : peak_h;

        M5.Display.fillRoundRect(x - 3, y - 3, w + 6, h + 6, 4, panel);
        M5.Display.drawRect(x, y, w, h, frame);
        if (ph > 0) M5.Display.fillRect(x + 2, y + h - ph, w - 4, ph, peak_color);
        if (rh > 0) M5.Display.fillRect(x + 5, y + h - rh, w - 10, rh, rms_color);
        g_mic_overlay_visible = true;
    }

    const uint32_t now = millis();
    if (now - g_last_mic_log_ms >= 500) {
        g_last_mic_log_ms = now;
        Serial.printf("[MIC ] peak=%u rms=%u\n", (unsigned)peak, (unsigned)rms);
    }
}

static void logMicLevel(uint16_t peak, uint16_t rms) {
    const uint32_t now = millis();
    if (now - g_last_mic_log_ms >= 500) {
        g_last_mic_log_ms = now;
        Serial.printf("[MIC ] peak=%u rms=%u\n", (unsigned)peak, (unsigned)rms);
    }
}

static size_t nextUtf8Index(const String& s, size_t i) {
    if (i >= s.length()) return i;
    const uint8_t c = (uint8_t)s[i];
    if ((c & 0x80) == 0) return i + 1;
    if ((c & 0xE0) == 0xC0) return min(i + 2, (size_t)s.length());
    if ((c & 0xF0) == 0xE0) return min(i + 3, (size_t)s.length());
    if ((c & 0xF8) == 0xF0) return min(i + 4, (size_t)s.length());
    return i + 1;
}

static void drawCaption(const String& text) {
    if (text.length() == 0) return;

    constexpr int margin = 8;
    constexpr int pad = 5;
    constexpr int box_h = 42;
    const int w = M5.Display.width();
    const int h = M5.Display.height();
    const int y = h - box_h - 4;
    const int max_w = w - margin * 2 - pad * 2;

    M5.Display.fillRoundRect(margin, y, w - margin * 2, box_h, 4, 0x0000);
    M5.Display.drawRoundRect(margin, y, w - margin * 2, box_h, 4, 0x8410);
    M5.Display.setFont(&fonts::efontJA_12);
    M5.Display.setTextSize(1);
    M5.Display.setTextColor(0xFFFF, 0x0000);

    int line_no = 0;
    size_t pos = 0;
    while (pos < text.length() && line_no < 2) {
        String line;
        size_t next = pos;
        while (next < text.length()) {
            const size_t char_end = nextUtf8Index(text, next);
            String candidate = line + text.substring(next, char_end);
            if (line.length() > 0 && M5.Display.textWidth(candidate) > max_w) break;
            line = candidate;
            next = char_end;
        }
        M5.Display.setCursor(margin + pad, y + pad + line_no * 16);
        M5.Display.print(line);
        pos = next;
        line_no++;
    }
    M5.Display.setFont(&fonts::Font0);
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

// 応答 WAV を再生しながら、PCM の RMS で口パク (口閉/口開/大開 3 段階)
static bool prepareSpeakerPlayback(const char* tag) {
    M5.Speaker.end();
    delay(20);
    const bool speaker_ready = M5.Speaker.begin();
    M5.Speaker.setVolume(SPK_VOLUME);
    M5.Speaker.setAllChannelVolume(255);
    Serial.printf("[AUDIO] speaker begin=%d volume=%u tag=%s\n",
                  speaker_ready ? 1 : 0,
                  (unsigned)SPK_VOLUME,
                  tag ? tag : "-");
    delay(20);
    return speaker_ready;
}

static void playWavWithLipsync(const uint8_t* wav, size_t size,
                               const char* emote = nullptr,
                               const String& caption = String()) {
    if (!wav || size < 44) {
        Serial.printf("[AUDIO] invalid wav ptr=%d bytes=%u\n",
                      wav ? 1 : 0,
                      (unsigned)size);
        return;
    }
    Serial.printf("[AUDIO] playback start bytes=%u emote=%s caption_len=%u\n",
                  (unsigned)size,
                  (emote && emote[0]) ? emote : "neutral",
                  (unsigned)caption.length());

    prepareSpeakerPlayback("speak");

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
        const bool wav_started = M5.Speaker.playWav(
            wav, size, /*repeat=*/1, /*channel=*/0,
            /*stop_current_sound=*/true);
        Serial.printf("[AUDIO] playWav fallback bytes=%u ok=%d\n",
                      (unsigned)size,
                      wav_started ? 1 : 0);
        drawCaption(caption);
        while (M5.Speaker.isPlaying()) delay(4);
        Serial.println("[AUDIO] playback done");
        return;
    }
    pcm_bytes -= pcm_bytes % wav_block;
    const size_t frames = pcm_bytes / wav_block;

    const uint32_t t0 = millis();
    const bool playback_started = M5.Speaker.playRaw(
        reinterpret_cast<const int16_t*>(pcm),
        frames * wav_channels,
        wav_sr,
        wav_channels == 2,
        /*repeat=*/1,
        /*channel=*/0,
        /*stop_current_sound=*/true);
    Serial.printf("[AUDIO] playRaw sr=%u ch=%u bits=%u bytes=%u ok=%d\n",
                  (unsigned)wav_sr,
                  (unsigned)wav_channels,
                  (unsigned)wav_bits,
                  (unsigned)pcm_bytes,
                  playback_started ? 1 : 0);
    if (!playback_started) {
        const bool wav_started = M5.Speaker.playWav(
            wav, size, /*repeat=*/1, /*channel=*/0,
            /*stop_current_sound=*/true);
        Serial.printf("[AUDIO] playWav after playRaw fail bytes=%u ok=%d\n",
                      (unsigned)size,
                      wav_started ? 1 : 0);
    }
    drawCaption(caption);

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

#if RGB_ENABLED
    g_rgb.setSpeakEmote(emote);
#endif
#if SERVO_ENABLED
    // 発話冒頭の 1〜2 秒だけ emote に応じた首の動き (joy=弾む頷き など)
    g_servo.startEmoteMotion(emote);
#endif

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
                drawCaption(caption);
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
    drawCaption(caption);
    Serial.println("[AUDIO] playback done");
}

static void playSleepMurmurIfDue() {
#if !SLEEP_MURMUR_ENABLED
    return;
#else
    const uint32_t now = millis();
    if (g_sleep_next_murmur_ms == 0 || now < g_sleep_next_murmur_ms) return;
    scheduleNextSleepMurmur(false);

#if defined(OFFLINE_MODE) && OFFLINE_MODE
    Serial.println("[SLEEP] murmur skipped (offline)");
    return;
#else
    if (!WiFiManager::ensureConnected()) {
        Serial.println("[SLEEP] murmur skipped (WiFi not connected)");
        return;
    }

    static const char* const kSleepLines[] = {
        "すう、すう。",
        "むにゃ……もう少しだけ。",
        "んん……おやすみなさい。",
        "すぴー……いい夢、見てるよ。",
        "むにゃ、X68……。",
        "くう、くう……。",
    };
    constexpr int kN = sizeof(kSleepLines) / sizeof(kSleepLines[0]);
    const String text(kSleepLines[rand() % kN]);

    Serial.printf("[SLEEP] murmur: %s\n", text.c_str());
    ChatResponse r = ChatClient::speakText(text);
    if (!r.ok) {
        Serial.printf("[SLEEP] murmur failed status=%d body=%u\n",
                      r.http_status,
                      (unsigned)r.body_size);
        if (r.body) free(r.body);
        return;
    }
    // Stay asleep: play the murmur audio without entering Speaking, lipsync,
    // or emote servo motion. This keeps the sleepy face/pose from twitching.
    prepareSpeakerPlayback("sleep-murmur");
    const bool started = M5.Speaker.playWav(
        r.body,
        r.body_size,
        /*repeat=*/1,
        /*channel=*/0,
        /*stop_current_sound=*/true);
    Serial.printf("[SLEEP] murmur playback bytes=%u ok=%d\n",
                  (unsigned)r.body_size,
                  started ? 1 : 0);
    while (M5.Speaker.isPlaying()) {
#if RGB_ENABLED
        g_rgb.update();
#endif
        delay(4);
    }
    free(r.body);
#if RGB_ENABLED
    g_rgb.setScene(RgbScene::Sleep);
#endif
    setState(State::Sleep, faces::F_SLEEPING);
#endif
#endif
}

#if WAKE_WORD_ENABLED && WAKE_REPLY_ENABLED && (!defined(OFFLINE_MODE) || !OFFLINE_MODE)
// ウェイクワード検出時の返事。「なぁに?」等の短い WAV を /speak で合成し、
// PSRAM にキャッシュして 2 回目以降は HTTP なしで即再生する。
// 合成に失敗した時は従来どおり ack beep にフォールバック。
static const char* const kWakeReplies[] = {
    "なぁに？",
    "はーい！",
    "呼んだ？",
};
constexpr int kWakeReplyCount = sizeof(kWakeReplies) / sizeof(kWakeReplies[0]);
static uint8_t* g_wake_reply_wav[kWakeReplyCount]  = {};
static size_t   g_wake_reply_size[kWakeReplyCount] = {};

static void playWakeReply() {
    const int idx = rand() % kWakeReplyCount;
    if (!g_wake_reply_wav[idx]) {
        ChatResponse r = ChatClient::speakText(String(kWakeReplies[idx]));
        if (r.ok && r.body) {
            // body の所有権をキャッシュへ移す (以後 free しない)
            g_wake_reply_wav[idx]  = r.body;
            g_wake_reply_size[idx] = r.body_size;
            Serial.printf("[WAKE] reply cached: '%s' (%u bytes)\n",
                          kWakeReplies[idx], (unsigned)r.body_size);
        } else {
            if (r.body) free(r.body);
            Serial.printf("[WAKE] reply fetch failed status=%d -> beep\n",
                          r.http_status);
            playAckBeep();
            return;
        }
    }
    playWavWithLipsync(g_wake_reply_wav[idx], g_wake_reply_size[idx], "joy");
}
#endif

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

    // まばたきは main.cpp 側の updateIdleBlink() に一本化する。
    // (PekekoFace::enableAutoBlink と二重に動かすと両方が show() を叩いて
    //  チラつき・マイクロ表情との競合が起きるため、こちらは使わない)

#if SERVO_ENABLED
    if (!g_servo.begin()) {
        Serial.println("[WARN] Servo init failed — check wiring");
    }
#endif
#if RGB_ENABLED
    g_rgb.begin();
#endif
#if MIDI_SAM2695_ENABLED
    g_midi.begin(MIDI_UART_TX_PIN, MIDI_UART_RX_PIN);
    setMidiVolume(g_midi_volume);
#endif

    if (!g_rec.begin()) {
        g_face.show(faces::FACE_ERR_GENERIC);
        return;
    }
    g_pwr.begin();
    // WiFi / /ready の失敗時も setup を最後まで通し、State::Error に落として
    // loop 側で定期リトライする (以前は return して永久に復帰しなかった)。
    bool server_ok = true;
#if defined(OFFLINE_MODE) && OFFLINE_MODE
    Serial.println("[OFFLINE] skipping WiFi & server ready check (kawaii test mode)");
#else
    if (!WiFiManager::connectBlocking()) {
        g_face.show(faces::FACE_ERR_WIFI);
        server_ok = false;
    } else {
        g_remote.begin();
        g_remote_begun = true;

        ReadyResponse ready = ChatClient::ready();
        if (!ready.ok) {
            showReadyError(ready);
            server_ok = false;
        } else {
            Serial.printf("[READY] server ok: %s\n", ready.body.c_str());
        }
    }
#endif

    // 短いウェーブの後、Idle 表情へ
    delay(700);
    const uint32_t seed = micros() ^ esp_random();
    randomSeed(seed);
    srand(seed);   // upstream の scheduleNextBlink が rand() を使う
#if WAKE_WORD_ENABLED
    // 初期値 0 のままだと Idle 突入と同時にウェイク待受が始まってしまう。
    g_next_wake_listen_ms = millis() + WAKE_POLL_INTERVAL_MS;
#endif
    if (server_ok) {
        setState(State::Idle, faces::FACE_IDLE);
    } else {
        setState(State::Error);   // 顔は上のエラー表示のまま維持
        g_error_retry_ms = millis() + ERROR_RETRY_INTERVAL_MS;
    }

    // 起動直後の初回ステータス出力（診断強化）。WiFi/ヒープが一目で分かる。
    g_last_status_ms = millis() - STATUS_INTERVAL_MS;
    updateIdleStatus();
}

void loop() {
    M5.update();
    M5StackChan.update();   // 頭頂タッチセンサー (Si12T) のポーリング
#if MIDI_SAM2695_ENABLED
    g_midi.update();
    while (Serial.available()) {
        const char c = (char)Serial.read();
        if (c == 'm' || c == 'M') {
            toggleMidiPlayback("serial");
        } else if (c == '+') {
            adjustMidiVolume(10);
        } else if (c == '-') {
            adjustMidiVolume(-10);
        }
    }
#endif

    // CoreS3 SE は物理ボタンが無く、M5Unified の virtual button マッピングも
    // デフォルトでは効かない (touch 検知はできるが BtnA/B/C は発火しない)。
    // M5.Touch を直接読んで「画面のどこかを触っていれば押下 = 録音」と扱う。
    bool pressed = false;
    if (M5.Touch.getCount() > 0) {
        const auto t = M5.Touch.getDetail(0);
        pressed = t.isPressed();
    }
    g_remote.update();                               // 接続/切断・ボタンのログ
    const bool remote_a_edge = g_remote.btnAEdge();  // 毎フレーム呼ぶ (エッジ追跡)
    const bool remote_b_edge = g_remote.btnBEdge();  // MIDI 再生トグル
    bool remote_ptt_edge = false;
    bool remote_vision_edge = false;
    updateRemoteAButton(remote_a_edge, remote_ptt_edge, remote_vision_edge);

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
            // Sleep / auto actions can return here while the head sensor is
            // still held.  Do not clear the release gate until every input,
            // including Si12T, has actually been released; otherwise the same
            // headpat immediately starts again and creates a sleep/wake loop.
            if (!pressed && !g_remote.btnA() &&
                !M5StackChan.TouchSensor.isPressed()) {
                g_wait_release_after_auto_send = false;
            }

#if MIDI_SAM2695_ENABLED
            // MIDI playback is timing-sensitive because it is streamed from
            // the main loop over UART. Keep manual controls alive, but skip
            // background work that would stall MIDI events.
            const bool midi_playing = g_midi.isPlaying();
            if (midi_playing && remote_b_edge && !g_wait_release_after_auto_send) {
                toggleMidiPlayback("remote BtnB");
                break;
            }
#else
            const bool midi_playing = false;
#endif

            // 頭頂タッチ: スワイプ優先、ホールドでなでなで (headpat)
            // wasPressed() は即発火するためスワイプ判定と競合する。
            // g_touch.update() に一本化して Swipe/Pet を区別する。
            if (!midi_playing && !g_wait_release_after_auto_send) {
                const uint32_t now = millis();
                const auto touch_ev = g_touch.update(M5StackChan.TouchSensor.getIntensities());
                bool headpat_fallback = false;
                if (M5StackChan.TouchSensor.isPressed()) {
                    if (g_headpat_idle_press_ms == 0) {
                        g_headpat_idle_press_ms = now;
                        // Give immediate visual feedback while the gesture is
                        // still being classified as swipe or headpat.
                        g_face.show(faces::F_BASHFUL);
#if RGB_ENABLED
                        // 撫で判定を待たず、触れた瞬間から点灯する。
                        g_rgb.setScene(RgbScene::Headpat);
#endif
                    } else if (now - g_headpat_idle_press_ms >= TouchHandler::HOLD_MS &&
                               now - g_headpat_idle_cool_ms >= 2500) {
                        headpat_fallback = true;
                        g_headpat_idle_cool_ms = now;
                    }
                } else {
                    const bool preview_was_visible = g_headpat_idle_press_ms != 0;
                    g_headpat_idle_press_ms = 0;
#if RGB_ENABLED
                    if (touch_ev == TouchHandler::Event::None) {
                        g_rgb.endHeadpatPreview();
                    }
#endif
                    if (preview_was_visible && touch_ev == TouchHandler::Event::None) {
                        g_face.show(faces::FACE_IDLE);
                    }
                }
                if (touch_ev == TouchHandler::Event::Swipe) {
                    // スワイプ: 喜び表情 + 首振り + 黄色 LED バースト
                    g_reaction_active = false;
                    g_face.show(faces::FACE_SWIPE);
#if RGB_ENABLED
                    g_rgb.setScene(RgbScene::Swipe);
#endif
#if SERVO_ENABLED
                    g_servo.startHappyWaggle();
#endif
                    playAckBeep();
                    break;
                } else if (touch_ev == TouchHandler::Event::Pet || headpat_fallback) {
                    // ホールド: headpat 開始 (はにかみ → とろけ → 眠り)
                    g_reaction_active = false;
                    playHeadpatChime();
                    // タッチ検出の瞬間を基準にし、判定待ち時間も
                    // 撫で時間に含める。
                    g_headpat_start_ms = g_headpat_idle_press_ms != 0
                                             ? g_headpat_idle_press_ms
                                             : now;
                    g_headpat_last_press_ms = now;
                    setState(State::Headpat, faces::F_BASHFUL);
                    Serial.println("[HEADPAT] start");
                    break;
                }
            }

            // 一時リアクション表示中は他のアイドル挙動を抑止して表示を保持。
            // 終了したら Idle 表情 / RGB / まばたきタイマを元に戻す。
            if (g_reaction_active) {
                if (millis() >= g_reaction_end_ms) {
                    g_reaction_active = false;
                    g_face.show(faces::FACE_IDLE);
#if RGB_ENABLED
                    g_rgb.setScene(RgbScene::Idle);
#endif
                    scheduleNextBlink();
                    scheduleNextMicro();
                }
                break;
            }

            // レアイベント (鼻歌 / 伸び)。発火したら反応保持に入る。
            if (!midi_playing) maybePlayIdleEvent();
            if (g_reaction_active) break;

            // IMU: 激しいシェイク → 目回し、持ち上げ/小突き → 驚き。
            if (!midi_playing) {
                const auto imu_ev = g_imu.update();
                if (imu_ev == ImuHandler::Event::Shake) {
                    Serial.println("[IMU ] shake -> dizzy");
                    g_face.show(faces::FACE_SHAKEN);
#if RGB_ENABLED
                    g_rgb.setScene(RgbScene::Shaken);
#endif
#if SERVO_ENABLED
                    g_servo.startDizzyWobble();
#endif
                    playDizzyChime();
                    g_reaction_active = true;
                    g_reaction_end_ms = millis() + 1500;
                    break;
                }
                if (imu_ev == ImuHandler::Event::Lift) {
                    Serial.println("[IMU ] lift -> surprised");
                    g_face.show(faces::F_SPARKLE_EYES);
#if RGB_ENABLED
                    g_rgb.setScene(RgbScene::Swipe);
#endif
                    playLiftBeep();
                    g_reaction_active = true;
                    g_reaction_end_ms = millis() + 900;
                    break;
                }
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
#if MIDI_SAM2695_ENABLED
            // リモコン BtnB: SAM2695 MIDI モジュールで添付曲を再生 / 停止
            if (remote_b_edge && !g_wait_release_after_auto_send) {
                toggleMidiPlayback("remote BtnB");
                break;
            }
#endif
            // リモコン BtnA ダブルクリック: 母艦の内蔵カメラで静止画を撮って vision 応答
            if (remote_vision_edge && !g_wait_release_after_auto_send) {
                Serial.println("[VISION] capture requested by remote double-click");
                g_wait_release_after_auto_send = true;
#if MIDI_SAM2695_ENABLED
                stopMidiPlayback("vision");
#endif
#if RGB_ENABLED
                g_rgb.setScene(RgbScene::Thinking);
#endif
                setState(State::Thinking, faces::FACE_THINKING);
#if defined(OFFLINE_MODE) && OFFLINE_MODE
                Serial.println("[OFFLINE] would POST /vision/capture");
                delay(800);
                playAckBeep();
#else
                if (!WiFiManager::ensureConnected()) {
                    Serial.println("[ERR ] WiFi not connected (vision skipped)");
                    handleHttpError(0);
                } else {
                    const uint32_t t_send_start = millis();
                    ChatResponse r = ChatClient::captureVision();
                    Serial.printf("[TIME] vision_http;dur=%u wav_out=%u\n",
                                  (unsigned)(millis() - t_send_start),
                                  (unsigned)r.body_size);
                    if (r.ok) {
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
                        setState(State::Speaking);
                        playAckBeep();
                        playWavWithLipsync(r.body, r.body_size, r.emote.c_str(), r.bot_text);
                        free(r.body);
                    } else {
                        Serial.printf("[HTTP] /vision/capture failed status=%d body=%u\n",
                                      r.http_status,
                                      (unsigned)r.body_size);
                        handleHttpError(r.http_status);
                    }
                }
#endif
                setState(State::Idle, faces::FACE_IDLE);
                break;
            }

            // LCD タッチ または リモコン BtnA で録音開始
            if ((pressed || remote_ptt_edge) && !g_wait_release_after_auto_send) {
                Serial.printf("[PTT ] start source=%s\n",
                              pressed ? "touch" : "remote");
#if MIDI_SAM2695_ENABLED
                stopMidiPlayback("PTT");
#endif
                if (pressed) {
                    g_face.show(faces::F_SURPRISED);
                    delay(150);
                }
                g_rec.start();
                g_listening_start_ms = millis();
                g_auto_listening_after_wake = false;
                g_auto_listening_heard_voice = false;
                g_auto_listening_last_voice_ms = 0;
                setState(State::Listening, faces::FACE_LISTENING);
                break;
            }
#if WAKE_WORD_ENABLED && (!defined(OFFLINE_MODE) || !OFFLINE_MODE)
            // ウェイクワード待ち受け: 短い音声片だけ録り、母艦 /wake で STT 判定する。
            // 検出したら ack beep の後に通常の発話録音へ入る。
            if (!g_wait_release_after_auto_send &&
                millis() >= g_next_wake_listen_ms &&
                g_blink_started_ms == 0 &&
                g_micro_started_ms == 0 &&
#if MIDI_SAM2695_ENABLED
                !g_midi.isPlaying() &&
#endif
                WiFiManager::ensureConnected()) {
                Serial.println("[WAKE] listen start");
                g_rec.start();
                g_wake_listening_start_ms = millis();
                enterBackgroundWakeListening();
                break;
            }
#endif
#if !defined(OFFLINE_MODE) || !OFFLINE_MODE
            // 待機中: Wi-Fi 回復後だけ /pull を叩く (未接続時の TCP は assert 原因)。
            if (!midi_playing && WiFiManager::ensureConnected()) {
                if (WiFiManager::consumeReconnectEvent()) {
                    g_last_status_ms = millis() - STATUS_INTERVAL_MS - 1;
                }
                if (millis() - g_last_pull_ms >= PULL_INTERVAL_MS) {
                    g_last_pull_ms = millis();
                    PullResponse pr = ChatClient::pull(0);
                    if (pr.ok && pr.body && pr.body_size > 0) {
                        Serial.printf("[PUSH] %s (source=%s emote=%s)\n",
                                      pr.bot_text.c_str(), pr.source.c_str(),
                                      pr.emote.length() ? pr.emote.c_str() : "neutral");
                        setState(State::Speaking);     // 顔/サーボのみ。lipsync が表情を上書き
                        playAckBeep();
                        playWavWithLipsync(pr.body, pr.body_size, pr.emote.c_str(), pr.bot_text);
                        free(pr.body);
                        setState(State::Idle, faces::FACE_IDLE);
                    }
                }
            }
#endif
            // 何もない時はまばたき + マイクロ表情を刻む
            updateIdleBlink();
            updateIdleMicro();
            // 診断用定期ステータス（WiFi/heap/uptime）。回復した直後も見えるようにする。
            updateIdleStatus();
            break;
        }

        case State::WakeListening: {
            g_rec.poll();
            logMicLevel(g_rec.lastPeak(), g_rec.lastRms());

            // 待ち受け中でも手動 PTT が来たら即座に本録音へ切替。
            if ((pressed || remote_ptt_edge) && !g_wait_release_after_auto_send) {
                Serial.println("[WAKE] interrupted by manual PTT");
                g_rec.stop();
                g_rec.start();
                g_listening_start_ms = millis();
                g_auto_listening_after_wake = false;
                g_auto_listening_heard_voice = false;
                g_auto_listening_last_voice_ms = 0;
                g_wake_listening_start_ms = 0;
                setState(State::Listening, faces::FACE_LISTENING);
                break;
            }

            const bool wake_overflow = g_rec.isFull();
            const bool wake_window_done =
                g_wake_listening_start_ms != 0 &&
                millis() - g_wake_listening_start_ms >= WAKE_LISTEN_MS;
            if (wake_overflow || wake_window_done) {
                const size_t n = g_rec.stop();
                g_wake_listening_start_ms = 0;
                bool detected = false;
#if defined(OFFLINE_MODE) && OFFLINE_MODE
                Serial.printf("[OFFLINE] wake sample %u bytes (skip /wake)\n", (unsigned)n);
#else
                if (WiFiManager::ensureConnected()) {
                    WakeResponse wr = ChatClient::wake(g_rec.data(), n);
                    if (wr.timing.length() > 0) {
                        Serial.printf("[TIME] %s\n", wr.timing.c_str());
                    }
                    detected = wr.ok && wr.detected;
                    if (wr.user_text.length() > 0) {
                        Serial.printf("[WAKE] heard='%s' detected=%d\n",
                                      wr.user_text.c_str(),
                                      detected ? 1 : 0);
                    }
                } else {
                    Serial.println("[WAKE] skipped (WiFi not connected)");
                }
#endif
                if (detected) {
#if WAKE_REPLY_ENABLED && (!defined(OFFLINE_MODE) || !OFFLINE_MODE)
                    // 「なぁに?」と返事してから録音に入る (呼びかけへの応答感)
                    setState(State::Speaking);
                    playWakeReply();
#else
                    playAckBeep();
#endif
                    g_rec.start();
                    g_listening_start_ms = millis();
                    g_auto_listening_after_wake = true;
                    g_auto_listening_heard_voice = false;
                    g_auto_listening_last_voice_ms = g_listening_start_ms;
                    setState(State::Listening, faces::FACE_LISTENING);
                } else {
                    g_next_wake_listen_ms = millis() + WAKE_POLL_INTERVAL_MS;
                    leaveBackgroundWakeListening();
                }
            }
            break;
        }

        case State::Listening: {
            g_rec.poll();
            drawMicLevel(g_rec.lastPeak(), g_rec.lastRms());
            const uint32_t listen_elapsed_ms =
                g_listening_start_ms != 0 ? millis() - g_listening_start_ms : 0;
            if (g_auto_listening_after_wake) {
                const bool voice_now =
                    g_rec.lastRms() >= AUTO_LISTEN_RMS_THRESHOLD ||
                    g_rec.lastPeak() >= AUTO_LISTEN_PEAK_THRESHOLD;
                if (voice_now) {
                    g_auto_listening_heard_voice = true;
                    g_auto_listening_last_voice_ms = millis();
                }
            }
            const bool rec_overflow = g_rec.isFull();
            const bool rec_timeout =
                g_listening_start_ms != 0 &&
                millis() - g_listening_start_ms >= ((uint32_t)MAX_REC_SECONDS * 1000u + 500u);
            const bool auto_timeout =
                g_auto_listening_after_wake &&
                listen_elapsed_ms >= AUTO_LISTEN_MAX_MS;
            const bool auto_silence_done =
                g_auto_listening_after_wake &&
                g_auto_listening_heard_voice &&
                listen_elapsed_ms >= AUTO_LISTEN_MIN_MS &&
                millis() - g_auto_listening_last_voice_ms >= AUTO_LISTEN_SILENCE_MS;
            const bool manual_release_done =
                !g_auto_listening_after_wake && !pressed && !g_remote.btnA();
            const bool should_send =
                manual_release_done || auto_silence_done || auto_timeout ||
                rec_overflow || rec_timeout;

            if (should_send) {
                if (rec_overflow || rec_timeout || auto_timeout || auto_silence_done) {
                    Serial.printf("[REC ] %s: auto-sending\n",
                                  rec_overflow ? "Buffer full" :
                                  rec_timeout ? "Timeout" :
                                  auto_timeout ? "Wake listen timeout" :
                                  "Wake listen silence");
                    g_wait_release_after_auto_send = true;
                    if (rec_overflow || rec_timeout) {
                        g_face.show(faces::FACE_REC_OVERFLOW);
                        playOverflowBeep();
                    }
                }
                const size_t n = g_rec.stop();
                Serial.printf("[REC ] stop bytes=%u dur=%u overflow=%d timeout=%d auto=%d touch=%d remote=%d\n",
                              (unsigned)n,
                              (unsigned)listen_elapsed_ms,
                              rec_overflow ? 1 : 0,
                              rec_timeout ? 1 : 0,
                              g_auto_listening_after_wake ? 1 : 0,
                              pressed ? 1 : 0,
                              g_remote.btnA() ? 1 : 0);
                g_listening_start_ms = 0;
                g_auto_listening_after_wake = false;
                g_auto_listening_heard_voice = false;
                g_auto_listening_last_voice_ms = 0;

                // ほぼ空の録音 (0.1 秒未満 = タップして即離した等) は音声を
                // 含み得ないので、サーバへ送らず「聞こえなかった」顔だけ出す。
                constexpr size_t MIN_SEND_BYTES =
                    44 + (MIC_SAMPLE_RATE / 10) * (MIC_BITS / 8) * MIC_CHANNELS;
                if (n < MIN_SEND_BYTES) {
                    Serial.printf("[REC ] too short (%u bytes < %u), skip send\n",
                                  (unsigned)n, (unsigned)MIN_SEND_BYTES);
                    g_face.show(faces::FACE_NO_SPEECH);
                    delay(600);
                    setState(State::Idle, faces::FACE_IDLE);
                    break;
                }

                setState(State::Thinking, faces::FACE_THINKING);
                const uint32_t t_send_start = millis();
#if defined(OFFLINE_MODE) && OFFLINE_MODE
                // OFFLINE: HTTP 呼び出しせず、考えてるフリ → ack → 笑顔 → Idle
                Serial.printf("[OFFLINE] recorded %u bytes (would POST /chat)\n",
                              (unsigned)n);
                delay(800);
                playAckBeep();
                g_face.show(faces::F_SOFT_SMILE);
                delay(600);
#else
                if (!WiFiManager::ensureConnected()) {
                    Serial.println("[ERR ] WiFi not connected (/chat skipped)");
                    handleHttpError(0);
                } else {
                    ChatResponse r = ChatClient::send(g_rec.data(), n);
                    Serial.printf("[TIME] device_http;dur=%u wav_in=%u wav_out=%u\n",
                                  (unsigned)(millis() - t_send_start),
                                  (unsigned)n,
                                  (unsigned)r.body_size);
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
                        setState(State::Speaking);
                        Serial.printf("[PLAY] chat response start bytes=%u\n",
                                      (unsigned)r.body_size);
                        playWavWithLipsync(r.body, r.body_size, r.emote.c_str(), r.bot_text);
                        Serial.println("[PLAY] chat response end");
                        free(r.body);
                    } else {
                        Serial.printf("[HTTP] /chat failed status=%d body=%u\n",
                                      r.http_status,
                                      (unsigned)r.body_size);
                        handleHttpError(r.http_status);
                    }
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
                // 撫でられ終わり: LED フェードアウト → やわらか笑顔 → Idle
                const uint32_t total = now - g_headpat_start_ms;
                Serial.printf("[HEADPAT] end (total=%ums)\n", (unsigned)total);
#if RGB_ENABLED
                g_rgb.setScene(RgbScene::PraiseEnd);
#endif
                g_face.show(faces::F_SOFT_SMILE);
                const uint32_t fade_start = millis();
                while (millis() - fade_start < 600) {
#if RGB_ENABLED
                    g_rgb.update();
#endif
                    delay(10);
                }
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

            if (held_ms >= 3000) {
#if RGB_ENABLED
                g_rgb.setScene(RgbScene::Sleep);
#endif
                g_sleep_enter_ms = millis();
                g_sleep_head_released = false;
                g_sleep_touch_wake_start_ms = 0;
                g_sleep_remote_wake_start_ms = 0;
                g_sleep_head_wake_start_ms = 0;
                scheduleNextSleepMurmur(true);
                g_wait_release_after_auto_send = true;
                setState(State::Sleep, faces::F_SLEEPING);
                Serial.println("[SLEEP] entered by headpat");
                break;
            }

            break;
        }

        case State::Sleep: {
            const uint32_t now = millis();
            const bool head_pressed = M5StackChan.TouchSensor.isPressed();
            const bool sleep_old_enough = now - g_sleep_enter_ms >= SLEEP_WAKE_MIN_AGE_MS;
            if (!head_pressed) {
                g_sleep_head_released = true;
            }
            if (!pressed && !g_remote.btnA() && !head_pressed) {
                g_wait_release_after_auto_send = false;
            }

            if (sleep_old_enough && pressed) {
                if (g_sleep_touch_wake_start_ms == 0) g_sleep_touch_wake_start_ms = now;
            } else {
                g_sleep_touch_wake_start_ms = 0;
            }
            if (sleep_old_enough && g_remote.btnA()) {
                if (g_sleep_remote_wake_start_ms == 0) g_sleep_remote_wake_start_ms = now;
            } else {
                g_sleep_remote_wake_start_ms = 0;
            }
            // A headpat enters Sleep while Si12T is still pressed.  Require a
            // real release after entering Sleep before a new head hold can be
            // interpreted as an intentional wake gesture.
            if (sleep_old_enough && g_sleep_head_released && head_pressed) {
                if (g_sleep_head_wake_start_ms == 0) g_sleep_head_wake_start_ms = now;
            } else if (!head_pressed) {
                g_sleep_head_wake_start_ms = 0;
            }

            const bool touch_wake =
                g_sleep_touch_wake_start_ms != 0 &&
                now - g_sleep_touch_wake_start_ms >= SLEEP_TOUCH_WAKE_HOLD_MS;
            const bool remote_wake =
                g_sleep_remote_wake_start_ms != 0 &&
                now - g_sleep_remote_wake_start_ms >= SLEEP_REMOTE_WAKE_HOLD_MS;
            const bool head_wake =
                g_sleep_head_released &&
                g_sleep_head_wake_start_ms != 0 &&
                now - g_sleep_head_wake_start_ms >= SLEEP_HEAD_WAKE_HOLD_MS;

            if (touch_wake || remote_wake || head_wake) {
                Serial.println("[SLEEP] wake");
                g_wait_release_after_auto_send = true;
                g_sleep_touch_wake_start_ms = 0;
                g_sleep_remote_wake_start_ms = 0;
                g_sleep_head_wake_start_ms = 0;
#if RGB_ENABLED
                g_rgb.setScene(RgbScene::Idle);
#endif
                setState(State::Idle, faces::FACE_IDLE);
            } else if (!pressed && !g_remote.btnA() && !head_pressed) {
                playSleepMurmurIfDue();
            }
            break;
        }

        case State::Error: {
            // setup で WiFi / 母艦 /ready に失敗した場合の定期リトライ。
            // 成功したら Idle へ復帰する (以前は電源再投入まで固まっていた)。
#if !defined(OFFLINE_MODE) || !OFFLINE_MODE
            const uint32_t now = millis();
            if ((int32_t)(now - g_error_retry_ms) < 0) break;
            g_error_retry_ms = now + ERROR_RETRY_INTERVAL_MS;
            if (!WiFiManager::ensureConnected()) {
                Serial.println("[RETRY] WiFi still not connected");
                break;
            }
            if (!g_remote_begun) {
                g_remote.begin();
                g_remote_begun = true;
            }
            ReadyResponse ready = ChatClient::ready();
            if (!ready.ok) {
                Serial.printf("[RETRY] /ready failed: HTTP %d\n", ready.http_status);
                break;
            }
            Serial.printf("[READY] server ok after retry: %s\n", ready.body.c_str());
            setState(State::Idle, faces::FACE_IDLE);
#endif
            break;
        }

        default:
            break;
    }

    // 電源管理: 低電池 (≤5%) は即 powerOff。Idle は段階顔を経てSleepへ。
    g_pwr.poll();

    // Idle 段階化: 3 分 退屈 / 4 分 あくび / 5 分 Zzz / 30 分 Sleep
    const auto stage = g_pwr.idleStage(g_state);
    if (stage != g_idle_stage_last) {
        g_idle_stage_last = stage;
        switch (stage) {
            case PowerManager::IdleStage::Bored:    g_face.show(faces::FACE_IDLE_BORED); break;
            case PowerManager::IdleStage::Yawn:     g_face.show(faces::FACE_IDLE_YAWN);  break;
            case PowerManager::IdleStage::Sleeping: g_face.show(faces::FACE_IDLE_LONG);  break;
            case PowerManager::IdleStage::Active:   /* 顔は setState 側が管理 */         break;
        }
    }
    if (g_pwr.shouldSleep(g_state)) {
        delay(500);   // Zzz 顔を見せる余裕
#if POWER_IDLE_DEEP_SLEEP_ENABLED
        g_pwr.enterDeepSleep();
        // ここには戻ってこない (復帰時はリセット → setup() 再走)
#else
#if MIDI_SAM2695_ENABLED
        stopMidiPlayback("idle sleep");
#endif
#if RGB_ENABLED
        g_rgb.setScene(RgbScene::Sleep);
#endif
        g_sleep_enter_ms = millis();
        g_sleep_head_released = false;
        g_sleep_touch_wake_start_ms = 0;
        g_sleep_remote_wake_start_ms = 0;
        g_sleep_head_wake_start_ms = 0;
        scheduleNextSleepMurmur(true);
        g_wait_release_after_auto_send = true;
        setState(State::Sleep, faces::F_SLEEPING);
        Serial.println("[SLEEP] entered by idle timeout");
#endif
    }

#if SERVO_ENABLED
    if (g_state != State::Sleep && !g_pwr.batteryLow()) {
#if MIDI_SAM2695_ENABLED
        updateMidiDance();
#endif
        g_servo.update();
    }
#endif
#if RGB_ENABLED
    g_rgb.update();
#endif

    // まばたきは State::Idle 内の updateIdleBlink() が担当するため、
    // PekekoFace::tick() (autoblink 未使用) はここでは呼ばない。

    delay(5);
}
