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
    void show(int n) {
        if (n < 1 || n > 36) return;
        if (n == current_) return;
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

    int current() const { return current_; }

    void refresh() {
        const int n = current_;
        current_ = 0;
        show(n);
    }

    void invalidate() { current_ = 0; }

private:
    int      current_ = 0;
    uint16_t bg_      = 0x0000;
};

} // namespace stackchan
