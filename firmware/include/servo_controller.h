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
#include <M5Unified.h>

#ifndef SERVO_STATE_MOTION_ENABLED
#define SERVO_STATE_MOTION_ENABLED 0
#endif

#ifndef SERVO_IDLE_MOTION_ENABLED
#define SERVO_IDLE_MOTION_ENABLED 0
#endif

#ifndef SERVO_LIPSYNC_MOTION_ENABLED
#define SERVO_LIPSYNC_MOTION_ENABLED 0
#endif

#ifndef SERVO_WAGGLE_ENABLED
#define SERVO_WAGGLE_ENABLED 0
#endif

#ifndef SERVO_IDLE_MOTION_DEBUG
#define SERVO_IDLE_MOTION_DEBUG 0
#endif

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

    // ---- SCSCL 位置マッピング ------------------------------
    // M5Stack 公式 StackChan firmware の HAL-Servo に合わせる。
    //   yaw defaultZeroPos   = 460, rawPosLimit = 0..1000
    //   pitch defaultZeroPos = 620, rawPosLimit = 0..1000
    // 公式 FTServo(SCSCL) は 16bit 値を high byte -> low byte で送る。
    static constexpr int RAW_MIN    = 0;
    static constexpr int RAW_MAX    = 1000;
    static constexpr int YAW_CTR    = 460;
    static constexpr int YAW_HALF   = 410;
    static constexpr int PITCH_CTR  = 620;
    static constexpr int PITCH_HALF = 270;

    // ---- 補間 / 送信設定 -----------------------------------
    static constexpr float    LERP_FAST = 0.10f;
    static constexpr float    LERP_SLOW = 0.05f;
    static constexpr uint32_t SEND_MS   = 50;    // サーボ更新周期 (ms)
    static constexpr uint16_t MOVE_MS   = 80;    // サーボ内部補間時間 (ms)
    static constexpr uint32_t AUTO_TORQUE_OFF_MS = 300;

    // --------------------------------------------------------
    //  初期化: 中点へ移動して待機
    // --------------------------------------------------------
    bool begin() {
        beginServoPower();
        Serial1.begin(SRV_BAUD, SERIAL_8N1, SRV_RX, SRV_TX);
        delay(200);
#if defined(SERVO_RELAX_ONLY) && SERVO_RELAX_ONLY
        writePos(ID_YAW,   YAW_CTR,   500);
        writePos(ID_PITCH, PITCH_CTR, 500);
        delay(600);
        torqueOff(ID_YAW);
        torqueOff(ID_PITCH);
        Serial.println("[SRV ] centered, then torque off (SERVO_RELAX_ONLY=1)");
        return true;
#else
        writePos(ID_YAW,   YAW_CTR,   500);
        writePos(ID_PITCH, PITCH_CTR, 500);
        last_idle_ms_ = millis();
        torque_release_at_ms_ = millis() + 800;
        return true;
#endif
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
        idle_interval_ms_ = idleMotionInterval();
    }

    void holdStill() {
        target_yaw_   = 0.0f;
        target_pitch_ = 0.0f;
        lerp_speed_   = LERP_FAST;
        in_idle_      = false;
        idle_nudge_return_ms_ = 0;
    }

    void goListening() {
#if SERVO_STATE_MOTION_ENABLED
        setTarget(0.0f,  0.2f, LERP_FAST);  // 少し上向き
#else
        holdStill();
#endif
    }

    void goThinking() {
#if SERVO_STATE_MOTION_ENABLED
        setTarget(0.3f, -0.2f, LERP_SLOW);  // 右に傾く
#else
        holdStill();
#endif
    }

    void goSpeaking() {
#if SERVO_STATE_MOTION_ENABLED
        speak_base_ = 0.15f;
        setTarget(0.0f, speak_base_, LERP_FAST);
#else
        speak_base_ = 0.0f;
        holdStill();
#endif
    }

    // スワイプ撫で: 右→左→中央の首振りアニメーション (~1 秒)
    void startHappyWaggle() {
#if SERVO_WAGGLE_ENABLED
        waggle_start_ms_ = millis();
        in_idle_         = false;
#endif
    }

    // 発話 RMS (0..1) に連動した微小な頷き
    void setSpeakLipWeight(float w) {
#if SERVO_LIPSYNC_MOTION_ENABLED
        if (!in_idle_) target_pitch_ = speak_base_ + w * 0.15f;
#else
        (void)w;
#endif
    }

    // --------------------------------------------------------
    //  毎ループ呼び出し (約 10 ms 周期)
    // --------------------------------------------------------
    void update() {
#if defined(SERVO_RELAX_ONLY) && SERVO_RELAX_ONLY
        return;
#endif
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

        // Idle 時: たまに小さく動き、短時間で中央へ戻る。
#if SERVO_IDLE_MOTION_ENABLED
        if (idle_nudge_return_ms_ != 0 && now >= idle_nudge_return_ms_) {
            idle_nudge_return_ms_ = 0;
            target_yaw_   = 0.0f;
            target_pitch_ = 0.0f;
            lerp_speed_   = LERP_SLOW;
        }
#endif
        if (in_idle_ && now - last_idle_ms_ > idle_interval_ms_) {
            last_idle_ms_     = now;
            idle_interval_ms_ = idleMotionInterval();
#if SERVO_IDLE_MOTION_ENABLED
            const int yaw_dir = (rand() & 1) ? 1 : -1;
            const int yaw_mag = 2 + (rand() % 2);
            target_yaw_   = (float)(yaw_dir * yaw_mag) * 0.060f;
            target_pitch_ = (float)(rand() % 3 - 1) * 0.070f;
            lerp_speed_   = 0.08f;
            idle_nudge_return_ms_ = now + 1600;
            Serial.printf("[SRV ] idle nudge yaw=%.3f pitch=%.3f\n", target_yaw_, target_pitch_);
#else
            target_yaw_   = 0.0f;
            target_pitch_ = 0.0f;
#endif
        }

        // Lerp
        current_yaw_   += (target_yaw_   - current_yaw_)   * lerp_speed_;
        current_pitch_ += (target_pitch_ - current_pitch_) * lerp_speed_;

        // 送信は SEND_MS ごと
        if (now - last_send_ms_ < SEND_MS) return;
        last_send_ms_ = now;

        const int yp = constrain(YAW_CTR   + (int)(current_yaw_   * YAW_HALF),
                                 YAW_CTR   - YAW_HALF, YAW_CTR   + YAW_HALF);
        const int pp = constrain(PITCH_CTR + (int)(current_pitch_  * PITCH_HALF),
                                 PITCH_CTR - PITCH_HALF, PITCH_CTR + PITCH_HALF);
        const bool target_reached =
            fabsf(target_yaw_ - current_yaw_) < 0.01f &&
            fabsf(target_pitch_ - current_pitch_) < 0.01f;
        const bool position_changed = yp != last_sent_yaw_pos_ || pp != last_sent_pitch_pos_;

        if (position_changed) {
            writePos(ID_YAW,   yp, MOVE_MS);
            writePos(ID_PITCH, pp, MOVE_MS);
            last_sent_yaw_pos_   = yp;
            last_sent_pitch_pos_ = pp;
            torque_release_at_ms_ = target_reached ? now + AUTO_TORQUE_OFF_MS : 0;
            return;
        }

        if (target_reached && torque_release_at_ms_ == 0 && torque_enabled_) {
            torque_release_at_ms_ = now + AUTO_TORQUE_OFF_MS;
        }

        if (torque_release_at_ms_ != 0 && now >= torque_release_at_ms_) {
            torqueOff(ID_YAW);
            torqueOff(ID_PITCH);
            torque_enabled_ = false;
            torque_release_at_ms_ = 0;
            Serial.println("[SRV ] auto torque off");
        }
    }

    void center() { setTarget(0.0f, 0.0f, LERP_FAST); }

private:
    static constexpr uint8_t PY32_ADDR = 0x6F;
    static constexpr uint32_t PY32_I2C_FREQ = 100000;
    static constexpr uint8_t REG_GPIO_M_L = 0x03;
    static constexpr uint8_t REG_GPIO_M_H = 0x04;
    static constexpr uint8_t REG_GPIO_O_L = 0x05;
    static constexpr uint8_t REG_GPIO_O_H = 0x06;
    static constexpr uint8_t REG_GPIO_PU_L = 0x09;
    static constexpr uint8_t REG_GPIO_PU_H = 0x0A;
    static constexpr uint8_t REG_GPIO_PD_L = 0x0B;
    static constexpr uint8_t REG_GPIO_PD_H = 0x0C;
    static constexpr uint8_t REG_GPIO_DRV_L = 0x13;
    static constexpr uint8_t REG_GPIO_DRV_H = 0x14;
    static constexpr uint8_t PY32_PIN_SERVO_POWER = 0;

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
    uint32_t idle_nudge_return_ms_ = 0;
    uint32_t torque_release_at_ms_ = 0;
    int      last_sent_yaw_pos_    = -1;
    int      last_sent_pitch_pos_  = -1;
    bool     torque_enabled_       = false;
    static constexpr uint32_t WAGGLE_DURATION_MS = 1050;  // 3 phases × 350 ms

    uint32_t idleMotionInterval() const {
#if SERVO_IDLE_MOTION_ENABLED
        return 3500 + (uint32_t)(rand() % 3000);
#else
        return 3000;
#endif
    }

    void beginServoPower() {
        if (!M5.In_I2C.scanID(PY32_ADDR, PY32_I2C_FREQ)) {
            Serial.println("[SRV ] PY32 servo power control not found");
            return;
        }
        // M5 Stack-chan base: PY32 pin 0 enables the servo VM rail.
        writePy32Bit(REG_GPIO_M_L, REG_GPIO_M_H, PY32_PIN_SERVO_POWER, true);
        writePy32Bit(REG_GPIO_PU_L, REG_GPIO_PU_H, PY32_PIN_SERVO_POWER, true);
        writePy32Bit(REG_GPIO_PD_L, REG_GPIO_PD_H, PY32_PIN_SERVO_POWER, false);
        writePy32Bit(REG_GPIO_DRV_L, REG_GPIO_DRV_H, PY32_PIN_SERVO_POWER, false);
        writePy32Bit(REG_GPIO_O_L, REG_GPIO_O_H, PY32_PIN_SERVO_POWER, true);
        Serial.println("[SRV ] servo power enabled via PY32");
        delay(1500);
    }

    void writePy32Bit(uint8_t reg_l, uint8_t reg_h, uint8_t pin, bool value) {
        const uint8_t reg = pin < 8 ? reg_l : reg_h;
        const uint8_t bit = pin < 8 ? pin : pin - 8;
        uint8_t current = M5.In_I2C.readRegister8(PY32_ADDR, reg, PY32_I2C_FREQ);
        if (value) current |= (1 << bit);
        else current &= ~(1 << bit);
        M5.In_I2C.writeRegister8(PY32_ADDR, reg, current, PY32_I2C_FREQ);
    }

    // --------------------------------------------------------
    //  SCS プロトコル: Goal Position (0x2A) + Time + Speed 書き込み
    //
    //  パケット: FF FF [ID] [LEN=9] [INST=0x03] [ADDR=0x2A]
    //            [PL] [PH] [TL] [TH] [SL=0] [SH=0] [CHKSUM]
    //  CHKSUM  : ~(ID+LEN+INST+ADDR+全データ) & 0xFF
    // --------------------------------------------------------
    void writePos(uint8_t id, int pos, uint16_t time_ms) {
        if (!torque_enabled_) {
            torqueOn(ID_YAW);
            torqueOn(ID_PITCH);
            torque_enabled_ = true;
        }
        pos = constrain(pos, RAW_MIN, RAW_MAX);
        uint8_t buf[13];
        buf[0]  = 0xFF;
        buf[1]  = 0xFF;
        buf[2]  = id;
        buf[3]  = 9;           // LEN = instr(1)+addr(1)+data(6)+chk(1)
        buf[4]  = 0x03;        // WRITE
        buf[5]  = 42;          // SCSCL_GOAL_POSITION_L
        buf[6]  = (pos >> 8) & 0xFF;
        buf[7]  = pos & 0xFF;
        buf[8]  = (time_ms >> 8) & 0xFF;
        buf[9]  = time_ms & 0xFF;
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

    void writeByte(uint8_t id, uint8_t addr, uint8_t value) {
        uint8_t buf[7];
        buf[0] = 0xFF;
        buf[1] = 0xFF;
        buf[2] = id;
        buf[3] = 4;     // instr(1)+addr(1)+data(1)+chk(1)
        buf[4] = 0x03;  // WRITE
        buf[5] = addr;
        buf[6] = value;
        uint8_t chk = 0;
        for (int i = 2; i < 7; i++) chk += buf[i];
        const uint8_t checksum = ~chk;

        Serial1.write(buf, 7);
        Serial1.write(checksum);
        Serial1.flush();

        const uint32_t t0 = millis();
        while (millis() - t0 < 10) {
            while (Serial1.available()) Serial1.read();
        }
    }

    void torqueOff(uint8_t id) {
        // Feetech SCS/STS Torque Enable register.
        static constexpr uint8_t ADDR_TORQUE_ENABLE = 0x28;
        writeByte(id, ADDR_TORQUE_ENABLE, 0);
    }

    void torqueOn(uint8_t id) {
        static constexpr uint8_t ADDR_TORQUE_ENABLE = 0x28;
        writeByte(id, ADDR_TORQUE_ENABLE, 1);
    }
};

} // namespace stackchan
