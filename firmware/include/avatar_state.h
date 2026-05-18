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
    Error,
};

} // namespace stackchan
