// ========================================================
//  LittleFS から face_NN.jpg を読み出して画面中央に描く
//  240x240 を CoreS3 (320x240) の中央寄せで表示する。
// ========================================================
#pragma once

#include <M5Unified.h>
#include <LittleFS.h>
#include <cstdio>

namespace stackchan {

class PekekoFace {
public:
    static constexpr int kSize = 240;

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
        // 余白だけ塗り直す
        const int dx = (M5.Display.width() - kSize) / 2;
        if (dx > 0) {
            M5.Display.fillRect(0, 0, dx, M5.Display.height(), bg_);
            M5.Display.fillRect(dx + kSize, 0,
                                M5.Display.width() - dx - kSize,
                                M5.Display.height(), bg_);
        }
    }

    // 顔番号 (1..36) を表示。前回と同じなら何もしない (チラつき防止)
    void show(int n) {
        if (n < 1 || n > 36) return;
        if (n == current_) return;
        current_ = n;
        char path[20];
        std::snprintf(path, sizeof(path), "/face_%02d.jpg", n);
        const int dx = (M5.Display.width()  - kSize) / 2;
        const int dy = (M5.Display.height() - kSize) / 2;
        fs::File file = LittleFS.open(path, "r");
        if (!file) {
            log_e("%s not found", path);
            return;
        }
        M5.Display.drawJpg(&file, dx, dy);
        file.close();
    }

    int current() const { return current_; }

private:
    int      current_ = 0;
    uint16_t bg_      = 0x0000;
};

} // namespace stackchan
