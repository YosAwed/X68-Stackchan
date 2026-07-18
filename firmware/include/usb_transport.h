// USB CDC transport for running the complete Stack-chan pipeline without Wi-Fi.
//
// The host bridge periodically sends "@SCUSB1\n".  Once seen, requests use a
// small length-prefixed binary protocol over the same CDC port that carries
// normal diagnostic logs.  Wi-Fi remains available as an automatic fallback.
#pragma once

#include <Arduino.h>
#include <cstdlib>

namespace stackchan {

enum class UsbOperation : uint8_t {
    Ready = 1,
    Chat = 2,
    Wake = 3,
    Speak = 4,
    Pull = 5,
    VisionCapture = 6,
};

struct UsbResponse {
    int status = 0;
    String metadata;
    uint8_t* body = nullptr;
    size_t body_size = 0;

    void clear() {
        if (body) free(body);
        body = nullptr;
        body_size = 0;
        status = 0;
        metadata = "";
    }
};

class UsbTransport {
public:
    static constexpr size_t MAX_METADATA_BYTES = 16 * 1024;
    static constexpr size_t MAX_BODY_BYTES = 4 * 1024 * 1024;

    // Consume bridge heartbeat lines and queue other bytes for the existing
    // one-character serial controls (MIDI play/volume).
    static void poll() {
        while (Serial.available()) {
            const char c = static_cast<char>(Serial.read());
            if (!heartbeatReading() && c != '@') {
                queueCommand(c);
                continue;
            }
            heartbeatReading() = true;
            if (c == '\n') {
                heartbeatBuffer().trim();
                if (heartbeatBuffer() == "@SCUSB1") {
                    const bool was_connected = connectedRaw();
                    lastHeartbeatMs() = millis();
                    if (!was_connected) Serial.println("[USB ] bridge connected");
                }
                heartbeatBuffer() = "";
                heartbeatReading() = false;
            } else if (c != '\r') {
                if (heartbeatBuffer().length() < 32) heartbeatBuffer() += c;
            }
        }
    }

    static bool commandAvailable() { return commandReadPos() != commandWritePos(); }
    static char readCommand() {
        if (!commandAvailable()) return 0;
        const char value = commandBuffer()[commandReadPos()];
        commandReadPos() = (commandReadPos() + 1) % COMMAND_BUFFER_SIZE;
        return value;
    }

    static bool isConnected() {
        poll();
        return connectedRaw();
    }

    static bool waitForConnection(uint32_t timeout_ms) {
        const uint32_t deadline = millis() + timeout_ms;
        do {
            poll();
            if (connectedRaw()) return true;
            delay(10);
        } while (static_cast<int32_t>(millis() - deadline) < 0);
        return false;
    }

    static bool request(UsbOperation operation,
                        const String& metadata,
                        const uint8_t* body,
                        size_t body_size,
                        UsbResponse& response,
                        uint32_t timeout_ms) {
        response.clear();
        if (!isConnected() || metadata.length() > MAX_METADATA_BYTES ||
            body_size > MAX_BODY_BYTES) {
            return false;
        }

        uint8_t header[16] = {'S', 'C', 'U', '1', static_cast<uint8_t>(operation), 0, 0, 0};
        putU32(header + 8, metadata.length());
        putU32(header + 12, body_size);
        if (!writeAll(header, sizeof(header), 2000) ||
            !writeAll(reinterpret_cast<const uint8_t*>(metadata.c_str()), metadata.length(), 2000) ||
            !writeAll(body, body_size, 5000)) {
            markDisconnected("write failed");
            return false;
        }
        Serial.flush();

        // Scan past a heartbeat that may have been queued immediately before
        // the request. The response itself always starts with SCR1.
        static const uint8_t magic[4] = {'S', 'C', 'R', '1'};
        size_t matched = 0;
        const uint32_t deadline = millis() + timeout_ms;
        while (matched < sizeof(magic)) {
            uint8_t c = 0;
            if (!readByte(c, deadline)) {
                markDisconnected("response timeout");
                return false;
            }
            matched = (c == magic[matched]) ? matched + 1 : (c == magic[0] ? 1 : 0);
        }

        uint8_t rest[12];
        if (!readExact(rest, sizeof(rest), deadline)) {
            markDisconnected("short response header");
            return false;
        }
        response.status = static_cast<int>(getU16(rest));
        const uint32_t metadata_size = getU32(rest + 4);
        const uint32_t response_size = getU32(rest + 8);
        if (metadata_size > MAX_METADATA_BYTES || response_size > MAX_BODY_BYTES) {
            markDisconnected("invalid response size");
            return false;
        }

        if (metadata_size) {
            response.metadata.reserve(metadata_size);
            for (uint32_t i = 0; i < metadata_size; ++i) {
                uint8_t c = 0;
                if (!readByte(c, deadline)) {
                    response.clear();
                    markDisconnected("short response metadata");
                    return false;
                }
                response.metadata += static_cast<char>(c);
            }
        }
        if (response_size) {
            response.body = static_cast<uint8_t*>(ps_malloc(response_size));
            if (!response.body || !readExact(response.body, response_size, deadline)) {
                response.clear();
                markDisconnected("short response body");
                return false;
            }
            response.body_size = response_size;
        }
        lastHeartbeatMs() = millis();
        return true;
    }

private:
    static constexpr size_t COMMAND_BUFFER_SIZE = 16;
    static uint32_t& lastHeartbeatMs() { static uint32_t value = 0; return value; }
    static bool& heartbeatReading() { static bool value = false; return value; }
    static String& heartbeatBuffer() { static String value; return value; }
    static char* commandBuffer() { static char value[COMMAND_BUFFER_SIZE] = {}; return value; }
    static size_t& commandReadPos() { static size_t value = 0; return value; }
    static size_t& commandWritePos() { static size_t value = 0; return value; }

    static void queueCommand(char command) {
        const size_t next = (commandWritePos() + 1) % COMMAND_BUFFER_SIZE;
        if (next == commandReadPos()) return;
        commandBuffer()[commandWritePos()] = command;
        commandWritePos() = next;
    }

    static bool connectedRaw() {
        const uint32_t last = lastHeartbeatMs();
        return last != 0 && millis() - last < 7000;
    }

    static void markDisconnected(const char* reason) {
        lastHeartbeatMs() = 0;
        heartbeatBuffer() = "";
        heartbeatReading() = false;
        Serial.printf("[USB ] bridge disconnected: %s\n", reason);
    }

    static void putU32(uint8_t* out, uint32_t value) {
        out[0] = value & 0xff;
        out[1] = (value >> 8) & 0xff;
        out[2] = (value >> 16) & 0xff;
        out[3] = (value >> 24) & 0xff;
    }
    static uint16_t getU16(const uint8_t* in) {
        return static_cast<uint16_t>(in[0]) |
               (static_cast<uint16_t>(in[1]) << 8);
    }
    static uint32_t getU32(const uint8_t* in) {
        return static_cast<uint32_t>(in[0]) |
               (static_cast<uint32_t>(in[1]) << 8) |
               (static_cast<uint32_t>(in[2]) << 16) |
               (static_cast<uint32_t>(in[3]) << 24);
    }

    static bool writeAll(const uint8_t* data, size_t size, uint32_t timeout_ms) {
        if (!data && size) return false;
        size_t sent = 0;
        uint32_t last_progress = millis();
        while (sent < size) {
            const size_t chunk = min<size_t>(size - sent, 4096);
            const size_t n = Serial.write(data + sent, chunk);
            if (n) {
                sent += n;
                last_progress = millis();
            } else {
                if (millis() - last_progress >= timeout_ms) return false;
                delay(1);
            }
        }
        return true;
    }

    static bool readByte(uint8_t& out, uint32_t deadline) {
        while (static_cast<int32_t>(millis() - deadline) < 0) {
            if (Serial.available()) {
                out = static_cast<uint8_t>(Serial.read());
                return true;
            }
            delay(1);
        }
        return false;
    }

    static bool readExact(uint8_t* out, size_t size, uint32_t deadline) {
        size_t got = 0;
        while (got < size && static_cast<int32_t>(millis() - deadline) < 0) {
            const int available = Serial.available();
            if (available > 0) {
                const size_t want = min<size_t>(size - got, static_cast<size_t>(available));
                const int n = Serial.read(out + got, want);
                if (n > 0) got += static_cast<size_t>(n);
            } else {
                delay(1);
            }
        }
        return got == size;
    }
};

}  // namespace stackchan
