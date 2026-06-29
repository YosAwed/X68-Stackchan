// ========================================================
//  Simple SMF player for UART MIDI modules (SAM2695 etc.)
//
//  Sends Standard MIDI File channel events over a 31250 bps UART.
//  Meta events are consumed locally; tempo changes are honored.
// ========================================================
#pragma once

#include <Arduino.h>
#include <pgmspace.h>

namespace stackchan {

class MidiPlayer {
public:
    bool begin(int8_t tx_pin, int8_t rx_pin = -1) {
        if (tx_pin < 0) {
            Serial.println("[MIDI] disabled: tx pin is not configured");
            return false;
        }
        tx_pin_ = tx_pin;
        rx_pin_ = rx_pin;
        serial_.begin(MIDI_BAUD, SERIAL_8N1, rx_pin_, tx_pin_);
        ready_ = true;
        stop();
        Serial.printf("[MIDI] UART ready baud=%u tx=%d rx=%d\n",
                      (unsigned)MIDI_BAUD, tx_pin_, rx_pin_);
        return true;
    }

    bool isReady() const { return ready_; }
    bool isPlaying() const { return playing_; }
    uint16_t division() const { return division_; }
    uint32_t ticksPerMeasure() const {
        if (division_ == 0 || time_sig_denominator_ == 0) return 0;
        return ((uint32_t)division_ * time_sig_numerator_ * 4u) / time_sig_denominator_;
    }

    uint32_t playbackTick() const {
        if (!playing_ || division_ == 0) return current_tick_;
        const uint32_t elapsed = micros() - start_us_;
        if (elapsed <= current_time_us_) return current_tick_;
        return current_tick_ + microsToTicks(elapsed - current_time_us_);
    }

    void setVolume(uint8_t volume) {
        volume_ = clamp7(volume);
        if (ready_) {
            sendAllChannelControl(0x07, volume_);
            sendAllChannelControl(0x0B, 127);
        }
        Serial.printf("[MIDI] volume=%u\n", volume_);
    }

    bool play(const uint8_t* data, size_t size) {
        if (!ready_ || !data || size < 14) return false;
        data_ = data;
        size_ = size;
        track_count_ = 0;
        division_ = 0;
        tempo_us_per_qn_ = DEFAULT_TEMPO_US_PER_QN;
        time_sig_numerator_ = 4;
        time_sig_denominator_ = 4;
        current_tick_ = 0;
        current_time_us_ = 0;

        size_t pos = 0;
        if (readU32(pos) != chunkId('M', 'T', 'h', 'd')) return false;
        const uint32_t header_len = readU32(pos);
        if (header_len < 6 || pos + header_len > size_) return false;
        const uint16_t format = readU16(pos);
        const uint16_t declared_tracks = readU16(pos);
        division_ = readU16(pos);
        pos += header_len - 6;

        if (division_ == 0 || (division_ & 0x8000) != 0) {
            Serial.printf("[MIDI] unsupported time division: 0x%04X\n", division_);
            return false;
        }
        if (format > 1) {
            Serial.printf("[MIDI] unsupported SMF format: %u\n", format);
            return false;
        }

        for (uint16_t i = 0; i < declared_tracks && pos + 8 <= size_; ++i) {
            const uint32_t id = readU32(pos);
            const uint32_t len = readU32(pos);
            if (pos + len > size_) return false;
            if (id == chunkId('M', 'T', 'r', 'k') && track_count_ < MAX_TRACKS) {
                Track& t = tracks_[track_count_++];
                t.pos = pos;
                t.end = pos + len;
                t.tick = 0;
                t.running_status = 0;
                t.done = false;
                if (!readNextDelta(t)) t.done = true;
            }
            pos += len;
        }

        if (track_count_ == 0) return false;
        sendAllChannelControl(0x07, volume_);
        sendAllChannelControl(0x0B, 127);
        start_us_ = micros();
        playing_ = true;
        Serial.printf("[MIDI] play format=%u tracks=%u division=%u bytes=%u volume=%u\n",
                      format, track_count_, division_, (unsigned)size_, volume_);
        return true;
    }

    void stop() {
        if (ready_) {
            allNotesOff();
        }
        playing_ = false;
    }

    void update() {
        if (!playing_) return;

        uint16_t guard = 0;
        while (playing_ && guard++ < 256) {
            const uint32_t next_tick = nextTick();
            if (next_tick == NO_TICK) {
                stop();
                Serial.println("[MIDI] finished");
                return;
            }

            if (next_tick > current_tick_) {
                const uint32_t delta_ticks = next_tick - current_tick_;
                current_time_us_ += ticksToMicros(delta_ticks);
                current_tick_ = next_tick;
            }

            const uint32_t elapsed = micros() - start_us_;
            if (elapsed < current_time_us_) return;

            bool progressed = false;
            for (uint8_t i = 0; i < track_count_; ++i) {
                Track& t = tracks_[i];
                while (!t.done && t.tick == current_tick_) {
                    processEvent(t);
                    progressed = true;
                    if (!t.done && !readNextDelta(t)) t.done = true;
                }
            }
            if (!progressed) return;
        }
    }

private:
    struct Track {
        size_t pos = 0;
        size_t end = 0;
        uint32_t tick = 0;
        uint8_t running_status = 0;
        bool done = true;
    };

    static constexpr uint32_t MIDI_BAUD = 31250;
    static constexpr uint32_t DEFAULT_TEMPO_US_PER_QN = 500000;
    static constexpr uint8_t MAX_TRACKS = 16;
    static constexpr uint32_t NO_TICK = 0xFFFFFFFFu;

    HardwareSerial serial_{2};
    bool ready_ = false;
    bool playing_ = false;
    int8_t tx_pin_ = -1;
    int8_t rx_pin_ = -1;
    const uint8_t* data_ = nullptr;
    size_t size_ = 0;
    Track tracks_[MAX_TRACKS];
    uint8_t track_count_ = 0;
    uint16_t division_ = 0;
    uint32_t tempo_us_per_qn_ = DEFAULT_TEMPO_US_PER_QN;
    uint8_t time_sig_numerator_ = 4;
    uint8_t time_sig_denominator_ = 4;
    uint32_t start_us_ = 0;
    uint32_t current_tick_ = 0;
    uint32_t current_time_us_ = 0;
    uint8_t volume_ = 48;

    static uint8_t clamp7(uint8_t value) {
        return value > 127 ? 127 : value;
    }

    uint8_t scale7(uint8_t value) const {
        return (uint8_t)(((uint16_t)clamp7(value) * volume_ + 63) / 127);
    }

    static constexpr uint32_t chunkId(char a, char b, char c, char d) {
        return ((uint32_t)(uint8_t)a << 24) |
               ((uint32_t)(uint8_t)b << 16) |
               ((uint32_t)(uint8_t)c << 8) |
               (uint32_t)(uint8_t)d;
    }

    uint8_t peek(size_t pos) const {
        if (pos >= size_) return 0;
        return pgm_read_byte(data_ + pos);
    }

    uint8_t read8(size_t& pos) const {
        const uint8_t v = peek(pos);
        if (pos < size_) ++pos;
        return v;
    }

    uint16_t readU16(size_t& pos) const {
        const uint16_t hi = read8(pos);
        const uint16_t lo = read8(pos);
        return (hi << 8) | lo;
    }

    uint32_t readU32(size_t& pos) const {
        uint32_t v = 0;
        for (int i = 0; i < 4; ++i) v = (v << 8) | read8(pos);
        return v;
    }

    uint32_t readVarLen(size_t& pos, size_t end, bool& ok) const {
        uint32_t value = 0;
        ok = false;
        for (uint8_t i = 0; i < 4 && pos < end; ++i) {
            const uint8_t b = read8(pos);
            value = (value << 7) | (b & 0x7F);
            if ((b & 0x80) == 0) {
                ok = true;
                return value;
            }
        }
        return value;
    }

    bool readNextDelta(Track& t) {
        if (t.pos >= t.end) return false;
        bool ok = false;
        const uint32_t delta = readVarLen(t.pos, t.end, ok);
        if (!ok) return false;
        t.tick += delta;
        return true;
    }

    uint32_t nextTick() const {
        uint32_t tick = NO_TICK;
        for (uint8_t i = 0; i < track_count_; ++i) {
            if (!tracks_[i].done && tracks_[i].tick < tick) {
                tick = tracks_[i].tick;
            }
        }
        return tick;
    }

    uint32_t ticksToMicros(uint32_t ticks) const {
        return (uint32_t)(((uint64_t)ticks * tempo_us_per_qn_) / division_);
    }

    uint32_t microsToTicks(uint32_t us) const {
        return (uint32_t)(((uint64_t)us * division_) / tempo_us_per_qn_);
    }

    void processEvent(Track& t) {
        if (t.pos >= t.end) {
            t.done = true;
            return;
        }

        uint8_t status = read8(t.pos);
        if (status < 0x80) {
            if (t.running_status == 0) {
                t.done = true;
                return;
            }
            --t.pos;
            status = t.running_status;
        } else if (status < 0xF0) {
            t.running_status = status;
        }

        if (status == 0xFF) {
            processMeta(t);
            return;
        }
        if (status == 0xF0 || status == 0xF7) {
            skipSysex(t);
            return;
        }
        if (status >= 0x80 && status <= 0xEF) {
            sendChannelEvent(t, status);
            return;
        }

        t.done = true;
    }

    void processMeta(Track& t) {
        if (t.pos >= t.end) {
            t.done = true;
            return;
        }
        const uint8_t type = read8(t.pos);
        bool ok = false;
        const uint32_t len = readVarLen(t.pos, t.end, ok);
        if (!ok || t.pos + len > t.end) {
            t.done = true;
            return;
        }
        if (type == 0x2F) {
            t.pos = t.end;
            t.done = true;
            return;
        }
        if (type == 0x51 && len == 3) {
            uint32_t tempo = 0;
            tempo = ((uint32_t)peek(t.pos) << 16) |
                    ((uint32_t)peek(t.pos + 1) << 8) |
                    (uint32_t)peek(t.pos + 2);
            if (tempo > 0) tempo_us_per_qn_ = tempo;
        } else if (type == 0x58 && len >= 2) {
            const uint8_t numerator = peek(t.pos);
            const uint8_t denominator_power = peek(t.pos + 1);
            if (numerator > 0 && denominator_power <= 7) {
                time_sig_numerator_ = numerator;
                time_sig_denominator_ = (uint8_t)(1u << denominator_power);
            }
        }
        t.pos += len;
    }

    void skipSysex(Track& t) {
        bool ok = false;
        const uint32_t len = readVarLen(t.pos, t.end, ok);
        if (!ok || t.pos + len > t.end) {
            t.done = true;
            return;
        }
        t.pos += len;
    }

    void sendChannelEvent(Track& t, uint8_t status) {
        const uint8_t command = status & 0xF0;
        const uint8_t data_len = (command == 0xC0 || command == 0xD0) ? 1 : 2;
        if (t.pos + data_len > t.end) {
            t.done = true;
            return;
        }
        uint8_t data1 = read8(t.pos);
        uint8_t data2 = 0;
        if (data_len == 2) data2 = read8(t.pos);

        if (command == 0xB0 && (data1 == 0x07 || data1 == 0x0B)) {
            data2 = scale7(data2);
        } else if (command == 0x90 && data2 > 0) {
            data2 = scale7(data2);
        }

        serial_.write(status);
        serial_.write(data1);
        if (data_len == 2) serial_.write(data2);
    }

    void allNotesOff() {
        sendAllChannelControl(0x7B, 0); // All Notes Off
        sendAllChannelControl(0x78, 0); // All Sound Off
        sendAllChannelControl(0x07, volume_);
        sendAllChannelControl(0x0B, 127);
        serial_.flush();
    }

    void sendAllChannelControl(uint8_t controller, uint8_t value) {
        value = clamp7(value);
        for (uint8_t ch = 0; ch < 16; ++ch) {
            serial_.write((uint8_t)(0xB0 | ch));
            serial_.write(controller);
            serial_.write(value);
        }
    }
};

} // namespace stackchan
