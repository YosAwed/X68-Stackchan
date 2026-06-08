// ========================================================
//  状態定義のみ。 描画は pekeko_face.h + face_map.h に分離
// ========================================================
#pragma once

namespace stackchan {

enum class State {
    Boot,
    Idle,
    Listening,
    Thinking,
    Speaking,
    Headpat,    // 画面上部 (頭エリア) をタッチされた状態
    Sleep,      // なでなで継続で眠った状態。自発動作は止める
    Error,
};

} // namespace stackchan
