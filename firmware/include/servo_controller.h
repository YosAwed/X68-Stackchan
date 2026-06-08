// ========================================================
//  SCS0009 シリアルサーボ制御 (Feetech SCS プロトコル)
//
//  UART 半二重: TX=GPIO6 (Servo_TX), RX=GPIO7 (Servo_RX)
//  ボーレート: 1 Mbps
//  アドレス 1 = Yaw (パン/左右), アドレス 2 = Pitch (チルト/上下)
//
//  方向切替は基板上の SN74LVC1G126DC/125DC が自動処理するため
//  GPIO による方向制御は不要。
// ========================================================
#pragma once
#include <Arduino.h>
#include <HardwareSerial.h>
#include <cmath>

namespace stackchan {

class ServoController {
public:
    // ---- UART ピン (StackChan 基板固定) --------------------
    static constexpr int  SRV_TX    = 6;
    static constexpr int  SRV_RX    = 7;
    static constexpr long SRV_BAUD  = 1000000;

    // ---- サーボアドレス ------------------------------------
    static constexpr uint8_t ID_YAW   = 1;
    static constexpr uint8_t ID_PITCH = 2;

    // ---- SCS0009 位置マッピング ----------------------------
    //  0-1023 step で 300°  (1 step ≈ 0.293°)
    //  Yaw   : 中点 512 (150°)  ±44° → step ±150
    //  Pitch : 推奨可動範囲 5°(≈17) ~ 85°(≈290)
    //          中点 45°(≈153) ±38° → step ±130
    static constexpr int YAW_CTR    = 512;
    static constexpr int YAW_HALF   = 150;
    static constexpr int PITCH_CTR  = 153;
    static constexpr int PITCH_HALF = 130;

    // ---- 補間 / 送信設定 -----------------------------------
    static constexpr float    LERP_FAST = 0.10f;
    static constexpr float    LERP_SLOW = 0.05f;
    static constexpr uint32_t SEND_MS   = 50;    // サーボ更新周期 (ms)
    static constexpr uint16_t MOVE_MS   = 80;    // サーボ内部補間時間 (ms)

    // --------------------------------------------------------
    //  初期化: 中点へ移動して待機
    // --------------------------------------------------------
    bool begin() {
        Serial1.begin(SRV_BAUD, SERIAL_8N1, SRV_RX, SRV_TX);
        delay(200);
        writePos(ID_YAW,   YAW_CTR,   500);
        writePos(ID_PITCH, PITCH_CTR, 500);
        last_idle_ms_ = millis();
        return true;
    }

    // --------------------------------------------------------
    //  ターゲット設定 (normalized: -1.0..+1.0)
    // --------------------------------------------------------
    void setTarget(float yaw, float pitch, float speed = LERP_FAST) {
        target_yaw_   = constrain(yaw,   -1.0f, 1.0f);
        target_pitch_ = constrain(pitch, -1.0f, 1.0f);
        lerp_speed_   = speed;
        in_idle_      = false;
    }

    // --------------------------------------------------------
    //  状態別プリセット
    // --------------------------------------------------------
    void goIdle() {
        target_yaw_   = 0.0f;
        target_pitch_ = 0.0f;
        lerp_speed_   = LERP_SLOW;
        in_idle_      = true;
        last_idle_ms_ = millis();
        idle_interval_ms_ = 2000 + (uint32_t)(rand() % 3000);
    }

    void goListening() { setTarget(0.0f,  0.2f, LERP_FAST); }  // 少し上向き
    void goThinking()  { setTarget(0.3f, -0.2f, LERP_SLOW); }  // 右に傾く
    void goSpeaking() {
        speak_base_ = 0.15f;
        setTarget(0.0f, speak_base_, LERP_FAST);
    }

    // スワイプ撫で: 右→左→中央の首振りアニメーション (~1 秒)
    void startHappyWaggle() {
        waggle_start_ms_ = millis();
        in_idle_         = false;
    }

    // シェイク反応: 速い左右振動 + うつむき (~1.4 秒) で「目が回る」動き。
    void startDizzyWobble() {
        dizzy_start_ms_ = millis();
        in_idle_        = false;
    }

    // 発話 RMS (0..1) に連動した微小な頷き
    void setSpeakLipWeight(float w) {
        if (!in_idle_) target_pitch_ = speak_base_ + w * 0.15f;
    }

    // --------------------------------------------------------
    //  毎ループ呼び出し (約 10 ms 周期)
    // --------------------------------------------------------
    void update() {
        const uint32_t now = millis();

        // ハッピー首振りアニメーション (右→左→中央, 各 350 ms)
        if (waggle_start_ms_ > 0) {
            const uint32_t elapsed = now - waggle_start_ms_;
            if (elapsed < WAGGLE_DURATION_MS) {
                const uint32_t phase = elapsed / 350;
                const float    t     = (float)(elapsed % 350) / 350.0f;
                switch (phase) {
                    case 0: target_yaw_ =        t * 0.45f;  break;  // 0 → +0.45
                    case 1: target_yaw_ =  0.45f - t * 0.90f; break; // +0.45 → -0.45
                    case 2: target_yaw_ = -0.45f + t * 0.45f; break; // -0.45 → 0
                    default: target_yaw_ = 0.0f;             break;
                }
                target_pitch_ = 0.1f;
                lerp_speed_   = LERP_FAST;
            } else {
                waggle_start_ms_ = 0;
                goIdle();
            }
        }

        // シェイク反応: 速い左右振動 + うつむき (くらくら)。終わったら Idle へ。
        if (dizzy_start_ms_ > 0) {
            const uint32_t elapsed = now - dizzy_start_ms_;
            if (elapsed < DIZZY_DURATION_MS) {
                const float t = elapsed / 1000.0f;
                target_yaw_   = sinf(t * 12.0f) * 0.5f;            // 速い首振り
                target_pitch_ = -0.25f + sinf(t * 6.0f) * 0.10f;   // うつむき気味に揺れる
                lerp_speed_   = LERP_FAST;
            } else {
                dizzy_start_ms_ = 0;
                goIdle();
            }
        }

        // Idle 時: ゆっくりランダムにさ迷う
        if (in_idle_ && now - last_idle_ms_ > idle_interval_ms_) {
            last_idle_ms_     = now;
            idle_interval_ms_ = 2000 + (uint32_t)(rand() % 3000);
            target_yaw_   = (float)(rand() % 7 - 3) * 0.15f;
            target_pitch_ = (float)(rand() % 5 - 2) * 0.08f;
        }

        // Lerp
        current_yaw_   += (target_yaw_   - current_yaw_)   * lerp_speed_;
        current_pitch_ += (target_pitch_ - current_pitch_) * lerp_speed_;

        // 送信は SEND_MS ごと
        if (now - last_send_ms_ < SEND_MS) return;
        last_send_ms_ = now;

        // Idle 中はピッチに微小なサイン揺れを足して「息遣い」を演出する。
        // (target_* には触れず描画位置だけずらすので Idle のランダム彷徨と両立)
        float pitch_render = current_pitch_;
        if (in_idle_) {
            const float ph = (now / 1000.0f) * BREATH_HZ * 6.28318530718f;
            pitch_render += sinf(ph) * BREATH_AMP;
        }

        const int yp = constrain(YAW_CTR   + (int)(current_yaw_   * YAW_HALF),
                                 YAW_CTR   - YAW_HALF, YAW_CTR   + YAW_HALF);
        const int pp = constrain(PITCH_CTR + (int)(pitch_render   * PITCH_HALF),
                                 PITCH_CTR - PITCH_HALF, PITCH_CTR + PITCH_HALF);
        writePos(ID_YAW,   yp, MOVE_MS);
        writePos(ID_PITCH, pp, MOVE_MS);
    }

    void center() { setTarget(0.0f, 0.0f, LERP_FAST); }

private:
    float current_yaw_    = 0.0f;
    float current_pitch_  = 0.0f;
    float target_yaw_     = 0.0f;
    float target_pitch_   = 0.0f;
    float lerp_speed_     = LERP_FAST;
    float speak_base_     = 0.0f;

    bool     in_idle_          = false;
    uint32_t last_idle_ms_     = 0;
    uint32_t last_send_ms_     = 0;
    uint32_t idle_interval_ms_ = 3000;
    uint32_t waggle_start_ms_  = 0;
    static constexpr uint32_t WAGGLE_DURATION_MS = 1050;  // 3 phases × 350 ms
    uint32_t dizzy_start_ms_   = 0;
    static constexpr uint32_t DIZZY_DURATION_MS  = 1400;

    // Idle 中の「息遣い」微小揺れ
    static constexpr float BREATH_HZ  = 0.25f;  // 1 呼吸 / 4 秒
    static constexpr float BREATH_AMP = 0.05f;  // ごく微小なチルト振幅

    // --------------------------------------------------------
    //  SCS プロトコル: Goal Position (0x2A) + Time + Speed 書き込み
    //
    //  パケット: FF FF [ID] [LEN=9] [INST=0x03] [ADDR=0x2A]
    //            [PL] [PH] [TL] [TH] [SL=0] [SH=0] [CHKSUM]
    //  CHKSUM  : ~(ID+LEN+INST+ADDR+全データ) & 0xFF
    // --------------------------------------------------------
    void writePos(uint8_t id, int pos, uint16_t time_ms) {
        pos = constrain(pos, 0, 1023);
        uint8_t buf[13];
        buf[0]  = 0xFF;
        buf[1]  = 0xFF;
        buf[2]  = id;
        buf[3]  = 9;           // LEN = instr(1)+addr(1)+data(6)+chk(1)
        buf[4]  = 0x03;        // WRITE
        buf[5]  = 0x2A;        // Goal Position register
        buf[6]  = pos & 0xFF;
        buf[7]  = (pos >> 8) & 0xFF;
        buf[8]  = time_ms & 0xFF;
        buf[9]  = (time_ms >> 8) & 0xFF;
        buf[10] = 0;           // Speed L (0 = 時間指定に従う)
        buf[11] = 0;           // Speed H
        uint8_t chk = 0;
        for (int i = 2; i < 12; i++) chk += buf[i];
        buf[12] = ~chk;

        Serial1.write(buf, 13);
        Serial1.flush();

        // 半二重エコー読み捨て (13 バイト or 10 ms タイムアウト)
        const uint32_t t0 = millis();
        int n = 0;
        while (n < 13 && millis() - t0 < 10) {
            if (Serial1.available()) { Serial1.read(); n++; }
        }
    }
};

} // namespace stackchan
