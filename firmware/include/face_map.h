// ========================================================
//  ぺけ子ちゃん表情 (face_NN.jpg) の論理名と、シーン→表情の対応表
//  ─────────────────────────────────────────────────────────
//  配役を変えたい時は下半分の FACE_xxx の右辺だけ書き換える。
//  上半分 (F_xxx) は 1..36 の絶対番号なので原則触らない。
// ========================================================
#pragma once

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
// 録音中
inline constexpr int FACE_LISTENING     = F_QUESTION;
// サーバ問い合わせ中
inline constexpr int FACE_THINKING      = F_THINKING_POSE;
// Thinking 長時間 (未使用、将来用)
inline constexpr int FACE_THINKING_LONG = F_BORED;
// 無音だった (no-speech 応答)
inline constexpr int FACE_NO_SPEECH     = F_EMBARRASSED;
// 口パク (発話中)
inline constexpr int FACE_SPEAK_CLOSED  = F_SMILE;       // 口閉じ
inline constexpr int FACE_SPEAK_OPEN    = F_DETERMINED;  // 口開け (目は維持)
// エラー系
inline constexpr int FACE_ERR_WIFI      = F_FLUSTERED;
inline constexpr int FACE_ERR_HTTP      = F_PANIC;
inline constexpr int FACE_ERR_GENERIC   = F_SHOCKED;

} // namespace faces
} // namespace stackchan
