// ========================================================
//  LittleFS から face_NN.jpg を読み出して画面いっぱいに描く
//  CoreS3 (320x240) の全画面サイズで表示する。
// ========================================================
#pragma once

#include <M5Unified.h>
#include <LittleFS.h>
#include <cstdio>

namespace stackchan {

class PekekoFace {
public:
    static constexpr int kWidth  = 320;
    static constexpr int kHeight = 240;

    bool begin() {
        if (!LittleFS.begin(false)) {
            log_e("LittleFS mount failed");
            return false;
        }
        // 念の為 face_01.jpg の存在チェック
        if (!LittleFS.exists("/face_01.jpg")) {
            log_e("/face_01.jpg not found. did you upload fs image?");
            return false;
        }
        // 余白を埋める背景色 (X68 BlueGray)
        bg_ = 0x2104;
        M5.Display.fillScreen(bg_);
        return true;
    }

    void setBackground(uint16_t color) {
        bg_ = color;
        M5.Display.fillScreen(bg_);
        invalidate();
    }

    // 顔番号 (1..36) を表示。前回と同じなら何もしない (チラつき防止)
    void show(int n) { showInternal(n, /*force=*/false); }

    int current() const { return current_; }

    // ---- 自動瞬き --------------------------------------------------------
    //  base 顔を表示している間だけ、min..max ms ランダム間隔で
    //  overlay 顔を hold_ms だけ挟む。tick() を毎ループ呼ぶ。
    //  base 以外の表情中は休止し、base に戻った時点から計時し直す。
    void enableAutoBlink(int base, int overlay,
                          uint32_t min_interval_ms = 3500,
                          uint32_t max_interval_ms = 6500,
                          uint32_t hold_ms = 90) {
        blink_base_     = base;
        blink_overlay_  = overlay;
        blink_min_ms_   = min_interval_ms;
        blink_max_ms_   = max_interval_ms;
        blink_hold_ms_  = hold_ms;
        scheduleNextBlink(millis());
    }

    void tick() {
        if (blink_base_ < 1) return;
        const uint32_t now = millis();

        if (blinking_) {
            if (now - blink_started_ms_ >= blink_hold_ms_) {
                blinking_ = false;
                showInternal(restore_to_, /*force=*/true);
                scheduleNextBlink(now);
            }
            return;
        }

        // base 顔じゃない時は計時を進めない (次に base に戻った時点から数える)
        if (current_ != blink_base_) {
            scheduleNextBlink(now);
            return;
        }
        if (now >= next_blink_at_ms_) {
            restore_to_       = current_;
            blinking_         = true;
            blink_started_ms_ = now;
            showInternal(blink_overlay_, /*force=*/true);
        }
    }

    void refresh() {
        const int n = current_;
        current_ = 0;
        show(n);
    }

    void invalidate() { current_ = 0; }

private:
    void showInternal(int n, bool force) {
        if (n < 1 || n > 36) return;
        if (!force && n == current_) return;
        current_ = n;
        char path[20];
        std::snprintf(path, sizeof(path), "/face_%02d.jpg", n);
        const int dx = (M5.Display.width()  - kWidth) / 2;
        const int dy = (M5.Display.height() - kHeight) / 2;
        // M5GFX (Arduino-ESP32 v3.x / IDF v5.x) では File* を取る drawJpg
        // オーバーロードが廃止。drawJpgFile(<fs>, path, ...) のテンプレート
        // 版に切り替える。
        // 注意: この呼び出しが効くには、main.cpp 側で LittleFS.h を
        // M5Unified.h より先に include しておく必要がある (M5GFX 内部の
        // DataWrapperT<LittleFSFS> 特殊化が _LITTLEFS_H_ で gate されているため)。
        if (!M5.Display.drawJpgFile(LittleFS, path, dx, dy)) {
            log_e("drawJpgFile failed: %s", path);
        }
    }

    void scheduleNextBlink(uint32_t now) {
        const uint32_t span = (blink_max_ms_ > blink_min_ms_)
            ? (blink_max_ms_ - blink_min_ms_) : 1;
        next_blink_at_ms_ = now + blink_min_ms_ + (uint32_t)(rand() % span);
    }

private:
    int      current_ = 0;
    uint16_t bg_      = 0x0000;

    int      blink_base_       = -1;
    int      blink_overlay_    = -1;
    uint32_t blink_min_ms_     = 3500;
    uint32_t blink_max_ms_     = 6500;
    uint32_t blink_hold_ms_    = 90;
    bool     blinking_         = false;
    uint32_t blink_started_ms_ = 0;
    uint32_t next_blink_at_ms_ = 0;
    int      restore_to_       = 0;
};

} // namespace stackchan
