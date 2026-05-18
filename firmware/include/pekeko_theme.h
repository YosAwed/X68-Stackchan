// ========================================================
//  X68000 風起動スプラッシュ (Human68k 風カウントダウン)
//  Avatar 用カラーパレットは PekekoFace に統合したのでここからは外している
// ========================================================
#pragma once

#include <M5Unified.h>

namespace stackchan {

// X68000 風カラー (M5GFX の 16bit RGB565)
constexpr uint16_t X68_BG       = 0x2104;  // ほぼ黒に近い濃紺
constexpr uint16_t X68_INK      = 0xFFFF;  // 白
constexpr uint16_t X68_ACCENT   = 0x07FF;  // 水色

// Avatar.init() 相当の前 (PekekoFace::begin の前) に呼ぶ。
// 画面に Human68k 風起動メッセージを点滅カーソル付きで表示する。
inline void showBootSplash(uint32_t hold_ms = 1800) {
    auto& d = M5.Display;
    d.fillScreen(X68_BG);
    d.setTextColor(X68_INK, X68_BG);
    d.setFont(&fonts::Font0);
    d.setTextSize(2);

    int y = 8;
    d.setCursor(8, y); d.print("Human68k version 3.02");          y += 22;
    d.setCursor(8, y); d.print("Copyright 1987-1993 SHARP");      y += 22;
    d.setCursor(8, y); d.print("Loading PEKEKO.SYS ...");         y += 26;
    d.setTextColor(X68_ACCENT, X68_BG);
    d.setCursor(8, y); d.print("A>"); d.setTextColor(X68_INK, X68_BG);
    d.print(" stackchan.x");

    const uint32_t end = millis() + hold_ms;
    bool on = true;
    while (millis() < end) {
        d.setCursor(8 + 16 * 12, y);
        d.setTextColor(on ? X68_INK : X68_BG, X68_BG);
        d.print("_");
        on = !on;
        delay(220);
    }
}

} // namespace stackchan
