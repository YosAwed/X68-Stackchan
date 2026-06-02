// ========================================================
//  ぺけ子ちゃん表情 (face_NN.jpg) の論理名と、シーン→表情の対応表
//  ─────────────────────────────────────────────────────────
//  配役を変えたい時は下半分の FACE_xxx の右辺だけ書き換える。
//  上半分 (F_xxx) は 1..36 の絶対番号なので原則触らない。
// ========================================================
#pragma once

#include <cstring>  // resolve_speak_pair の strcmp

namespace stackchan {
namespace faces {

// ---- 36 表情の論理名 ----------------------------------------------------
// (番号は 36 枚スプライトシートの ID)
inline constexpr int F_NEUTRAL           = 1;   // 01 中立
inline constexpr int F_SMILE             = 2;   // 02 微笑み (口閉じ)
inline constexpr int F_LAUGH_EYES_CLOSED = 3;   // 03 目閉じ大笑い
inline constexpr int F_WINK              = 4;   // 04 ウインク + キラリ
inline constexpr int F_SURPRISED         = 5;   // 05 驚き
inline constexpr int F_EMBARRASSED       = 6;   // 06 困り (汗)
inline constexpr int F_ANGRY             = 7;   // 07 怒り
inline constexpr int F_SAD               = 8;   // 08 悲しみ
inline constexpr int F_SLEEPING          = 9;   // 09 就寝 (Zzz)
inline constexpr int F_MISCHIEF          = 10;  // 10 イタズラ笑い
inline constexpr int F_SOFT_SMILE        = 11;  // 11 柔らかな笑顔
inline constexpr int F_STERN             = 12;  // 12 真顔
inline constexpr int F_SHOUTING          = 13;  // 13 叫び怒り
inline constexpr int F_LAUGH_OPEN        = 14;  // 14 目閉じ笑顔 (×印)
inline constexpr int F_QUESTION          = 15;  // 15 はてな ?
inline constexpr int F_PANIC             = 16;  // 16 慌て (汗 + 大目)
inline constexpr int F_POUT              = 17;  // 17 拗ね
inline constexpr int F_YAWN_SMALL        = 18;  // 18 あくび (小)
inline constexpr int F_CONFIDENT         = 19;  // 19 自信 (キラリ)
inline constexpr int F_CONCERNED         = 20;  // 20 心配
inline constexpr int F_THINKING_POSE     = 21;  // 21 考え中 (手を顎)
inline constexpr int F_BORED             = 22;  // 22 退屈
inline constexpr int F_COLD              = 23;  // 23 冷淡
inline constexpr int F_CRYING            = 24;  // 24 泣き
inline constexpr int F_SHY               = 25;  // 25 恥ずかしげ (手を口元)
inline constexpr int F_SHOCKED           = 26;  // 26 ショック (大目)
inline constexpr int F_SPARKLE_EYES      = 27;  // 27 キラキラ目 (期待)
inline constexpr int F_RELIEVED          = 28;  // 28 安堵
inline constexpr int F_DETERMINED        = 29;  // 29 やる気 (にこっ)
inline constexpr int F_JOY               = 30;  // 30 喜び (目閉じ口開け)
inline constexpr int F_BASHFUL           = 31;  // 31 はにかみ
inline constexpr int F_FLUSTERED         = 32;  // 32 あたふた
inline constexpr int F_INDIFFERENT       = 33;  // 33 無関心
inline constexpr int F_DIZZY             = 34;  // 34 目回し (錯乱)
inline constexpr int F_YAWN_HAND         = 35;  // 35 あくび (手で隠す)
inline constexpr int F_WAVE              = 36;  // 36 バイバイ / 挨拶

// ---- シーン → 表情 マッピング (ここを書き換えれば配役変更) ----------
// 起動完了の挨拶
inline constexpr int FACE_BOOT_DONE     = F_WAVE;
// 通常待機
inline constexpr int FACE_IDLE          = F_NEUTRAL;
// 長時間放置 (未使用、将来用)
inline constexpr int FACE_IDLE_LONG     = F_SLEEPING;
// まばたきで一瞬挟む顔 (Idle 中に短時間表示してすぐ FACE_IDLE に戻す)
inline constexpr int FACE_BLINK         = F_LAUGH_EYES_CLOSED;
// 録音中
inline constexpr int FACE_LISTENING     = F_QUESTION;
// サーバ問い合わせ中
inline constexpr int FACE_THINKING      = F_THINKING_POSE;
// Thinking 長時間 (未使用、将来用)
inline constexpr int FACE_THINKING_LONG = F_BORED;
// 無音だった (no-speech 応答)
inline constexpr int FACE_NO_SPEECH     = F_EMBARRASSED;
// 口パク (発話中) — neutral 既定。3 段階運用 (静か / 通常 / 大声) で
// 大声の音節 (例: 文末の「〜だよっ!」) に "wide" 顔を当てる。
inline constexpr int FACE_SPEAK_CLOSED  = F_SMILE;       // 口閉じ
inline constexpr int FACE_SPEAK_OPEN    = F_DETERMINED;  // 口開け (目は維持)
inline constexpr int FACE_SPEAK_WIDE    = F_JOY;         // 大開け / 笑い climax

// ---- 感情ごとの口パクペア ----
// サーバから X-Stackchan-Emote で受け取ったタグに応じて、発話中の口パク
// (FACE_SPEAK_OPEN / FACE_SPEAK_CLOSED) を以下のペアに差し替える。
// 未知タグ / 空文字なら上の既定値にフォールバック。
//   { closed, open }  並び順
inline constexpr int FACE_EMOTE_JOY_CLOSED         = F_SOFT_SMILE;        // 11 やわらか
inline constexpr int FACE_EMOTE_JOY_OPEN           = F_JOY;               // 30 喜び口開け
inline constexpr int FACE_EMOTE_SAD_CLOSED         = F_SAD;               // 08 悲しみ
inline constexpr int FACE_EMOTE_SAD_OPEN           = F_CRYING;            // 24 泣き
inline constexpr int FACE_EMOTE_EMBARRASSED_CLOSED = F_BASHFUL;           // 31 はにかみ
inline constexpr int FACE_EMOTE_EMBARRASSED_OPEN   = F_EMBARRASSED;       // 06 困り (汗)
inline constexpr int FACE_EMOTE_CONFUSED_CLOSED    = F_QUESTION;          // 15 はてな
inline constexpr int FACE_EMOTE_CONFUSED_OPEN      = F_POUT;              // 17 拗ね
inline constexpr int FACE_EMOTE_SURPRISED_CLOSED   = F_SPARKLE_EYES;      // 27 キラキラ
inline constexpr int FACE_EMOTE_SURPRISED_OPEN     = F_SURPRISED;         // 05 驚き
inline constexpr int FACE_EMOTE_SLEEPY_CLOSED      = F_SLEEPING;          // 09 就寝
inline constexpr int FACE_EMOTE_SLEEPY_OPEN        = F_YAWN_SMALL;        // 18 あくび
inline constexpr int FACE_EMOTE_CONFIDENT_CLOSED   = F_SOFT_SMILE;        // 11 やわらか
inline constexpr int FACE_EMOTE_CONFIDENT_OPEN     = F_CONFIDENT;         // 19 自信

// ---- 3 段階口パクの "wide" (大声音節) 顔 ----
// 大半の emote は wide = open のまま (resolve_speak_triple 側で既定処理)。
// ここで定義するのは「2 段目より更にエスカレートする顔」を持つものだけ。
inline constexpr int FACE_EMOTE_SURPRISED_WIDE     = F_SHOCKED;           // 26 ショック
inline constexpr int FACE_EMOTE_EMBARRASSED_WIDE   = F_FLUSTERED;         // 32 あたふた
inline constexpr int FACE_EMOTE_SLEEPY_WIDE        = F_YAWN_HAND;         // 35 大あくび

// emote タグ → (open, closed) のペアを返す軽量ルックアップ。
// `out_open` / `out_closed` に書き戻す。未知タグなら既定値のまま。
inline void resolve_speak_pair(const char* emote,
                               int& out_open, int& out_closed) {
    out_open   = FACE_SPEAK_OPEN;
    out_closed = FACE_SPEAK_CLOSED;
    if (!emote || !*emote) return;
    // strcmp は <cstring> 込み。main.cpp 側で <cstring> を include している前提。
    if      (!strcmp(emote, "joy"))         { out_open = FACE_EMOTE_JOY_OPEN;
                                              out_closed = FACE_EMOTE_JOY_CLOSED; }
    else if (!strcmp(emote, "sad"))         { out_open = FACE_EMOTE_SAD_OPEN;
                                              out_closed = FACE_EMOTE_SAD_CLOSED; }
    else if (!strcmp(emote, "embarrassed")) { out_open = FACE_EMOTE_EMBARRASSED_OPEN;
                                              out_closed = FACE_EMOTE_EMBARRASSED_CLOSED; }
    else if (!strcmp(emote, "confused"))    { out_open = FACE_EMOTE_CONFUSED_OPEN;
                                              out_closed = FACE_EMOTE_CONFUSED_CLOSED; }
    else if (!strcmp(emote, "surprised"))   { out_open = FACE_EMOTE_SURPRISED_OPEN;
                                              out_closed = FACE_EMOTE_SURPRISED_CLOSED; }
    else if (!strcmp(emote, "sleepy"))      { out_open = FACE_EMOTE_SLEEPY_OPEN;
                                              out_closed = FACE_EMOTE_SLEEPY_CLOSED; }
    else if (!strcmp(emote, "confident"))   { out_open = FACE_EMOTE_CONFIDENT_OPEN;
                                              out_closed = FACE_EMOTE_CONFIDENT_CLOSED; }
    // "neutral" やその他は既定値のまま
}

// 3 段階口パク (closed / open / wide) を返す。wide は大声音節 (RMS が高い瞬間)
// に当てる顔。既定では wide = open になり、上で WIDE が定義された emote
// のみ独自の顔に上書きされる。neutral (emote 空) では既定の F_JOY を使う。
inline void resolve_speak_triple(const char* emote,
                                 int& out_closed, int& out_open, int& out_wide) {
    // まず既存ペア解決に委譲して closed / open を決める。
    resolve_speak_pair(emote, out_open, out_closed);
    // wide の既定: emote 無しなら専用の FACE_SPEAK_WIDE、それ以外は open と同じ。
    out_wide = (!emote || !*emote) ? FACE_SPEAK_WIDE : out_open;
    // エスカレーション顔を持つ emote のみ wide を上書き。
    if (!emote || !*emote) return;
    if      (!strcmp(emote, "surprised"))   out_wide = FACE_EMOTE_SURPRISED_WIDE;
    else if (!strcmp(emote, "embarrassed")) out_wide = FACE_EMOTE_EMBARRASSED_WIDE;
    else if (!strcmp(emote, "sleepy"))      out_wide = FACE_EMOTE_SLEEPY_WIDE;
    // joy / sad / confused / confident / neutral 以外は wide = open のまま
}
// エラー系
inline constexpr int FACE_ERR_WIFI      = F_CONCERNED;
inline constexpr int FACE_ERR_HTTP      = F_CONCERNED;
inline constexpr int FACE_ERR_GENERIC   = F_SHOCKED;
inline constexpr int FACE_ERR_TOO_LARGE = F_EMBARRASSED; // 413: 録音が長すぎた
inline constexpr int FACE_ERR_SERVER    = F_DIZZY;       // 5xx: サーバ内部エラー
inline constexpr int FACE_ERR_TIMEOUT   = F_BORED;       // 接続失敗/タイムアウト
inline constexpr int FACE_REC_OVERFLOW  = F_SURPRISED;   // 録音上限到達
// インタラクション
inline constexpr int FACE_PET           = F_BASHFUL;     // なでなで (ホールド) → はにかみ
inline constexpr int FACE_SWIPE         = F_JOY;         // スワイプ撫で → 喜び (目閉じ口開け)
inline constexpr int FACE_SHAKEN        = F_DIZZY;       // シェイク → 目回し

} // namespace faces
} // namespace stackchan
