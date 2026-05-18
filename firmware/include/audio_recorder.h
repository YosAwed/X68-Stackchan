// ========================================================
//  内蔵 PDM マイクから WAV (RIFF/PCM16) を PSRAM にためる
// ========================================================
#pragma once

#include <M5Unified.h>
#include <cstdint>
#include <cstring>

#include "config.h"

namespace stackchan {

class AudioRecorder {
public:
    bool begin() {
        const size_t max_samples =
            static_cast<size_t>(MIC_SAMPLE_RATE) * MAX_REC_SECONDS;
        const size_t bytes = max_samples * sizeof(int16_t) + 44; // + WAV header

        buf_ = static_cast<uint8_t*>(ps_malloc(bytes));
        if (!buf_) {
            log_e("AudioRecorder: ps_malloc failed (%u)", (unsigned)bytes);
            return false;
        }
        cap_ = bytes;
        return true;
    }

    // 録音開始 (バッファ先頭の 44byte 分はヘッダ用に予約)
    void start() {
        write_pos_ = 44;
        recording_ = true;
        auto cfg = M5.Mic.config();
        cfg.sample_rate = MIC_SAMPLE_RATE;
        M5.Mic.config(cfg);
        M5.Mic.begin();
    }

    // タイトループから呼んで PSRAM に流し込む。
    // M5Unified::Mic::record は非同期で、true を返したらバッファ満了時に
    // フレームが書き込まれる。同じバッファを再利用したいので isRecording()
    // でひとつ前の録音が終わってから次の record() を投げ直す。
    void poll() {
        if (!recording_) return;
        if (M5.Mic.isRecording()) return;  // 直前の record() がまだ進行中

        // 直前 record() で埋まったぶんを確定 (初回は last_bytes_ == 0)
        if (last_bytes_ > 0) {
            if (write_pos_ + last_bytes_ > cap_) {
                stop();
                return;
            }
            std::memcpy(buf_ + write_pos_, chunk_, last_bytes_);
            write_pos_ += last_bytes_;
            last_bytes_ = 0;
        }

        constexpr size_t CHUNK = 512; // samples
        if (!M5.Mic.record(chunk_, CHUNK, MIC_SAMPLE_RATE)) return;
        last_bytes_ = CHUNK * sizeof(int16_t);
    }

    // 録音停止。WAV ヘッダを書き込んで size を返す
    size_t stop() {
        if (!recording_) return write_pos_;
        recording_ = false;
        // 進行中の record() があれば終わるまで待つ
        while (M5.Mic.isRecording()) { delay(1); }
        if (last_bytes_ > 0 && write_pos_ + last_bytes_ <= cap_) {
            std::memcpy(buf_ + write_pos_, chunk_, last_bytes_);
            write_pos_ += last_bytes_;
        }
        last_bytes_ = 0;
        M5.Mic.end();
        writeWavHeader();
        return write_pos_;
    }

    const uint8_t* data() const { return buf_; }
    size_t         size() const { return write_pos_; }
    bool isRecording() const    { return recording_; }

private:
    void writeWavHeader() {
        const uint32_t data_bytes = static_cast<uint32_t>(write_pos_) - 44u;
        const uint32_t riff_size  = data_bytes + 36u;
        const uint16_t channels   = MIC_CHANNELS;
        const uint32_t sr         = MIC_SAMPLE_RATE;
        const uint16_t bits       = MIC_BITS;
        const uint16_t block      = channels * bits / 8;
        const uint32_t byterate   = sr * block;

        auto* h = buf_;
        std::memcpy(h + 0,  "RIFF", 4);
        std::memcpy(h + 4,  &riff_size, 4);
        std::memcpy(h + 8,  "WAVE", 4);
        std::memcpy(h + 12, "fmt ", 4);
        const uint32_t fmt_size = 16;
        std::memcpy(h + 16, &fmt_size, 4);
        const uint16_t fmt_pcm = 1;
        std::memcpy(h + 20, &fmt_pcm,  2);
        std::memcpy(h + 22, &channels, 2);
        std::memcpy(h + 24, &sr,       4);
        std::memcpy(h + 28, &byterate, 4);
        std::memcpy(h + 32, &block,    2);
        std::memcpy(h + 34, &bits,     2);
        std::memcpy(h + 36, "data", 4);
        std::memcpy(h + 40, &data_bytes, 4);
    }

    uint8_t* buf_         = nullptr;
    size_t   cap_         = 0;
    size_t   write_pos_   = 44; // 先頭 44byte はヘッダ予約
    bool     recording_   = false;
    int16_t  chunk_[512]  = {};
    size_t   last_bytes_  = 0;
};

} // namespace stackchan
