// ========================================================
//  WAV を multipart で POST → 応答 WAV を PSRAM に受ける
//
//  現状の制約と将来の拡張ポイント:
//  - 現在は Content-Length 必須の同期受信。全 WAV を PSRAM に購めてから再生する。
//  - 将来的にストリーミング再生を実現するには、以下の変更が必要:
//    1. HTTP/1.1 Chunked Transfer Encoding のデコード対応
//       (サーバ側で LLM 出力の文節ごとに TTS して順次送信する場合)
//    2. 受信チャンクを I2S のリングバッファに直接流し込む非同期パイプライン
//    3. または WebSocket への移行で双方向ストリームを実現
//  上記対応までは、サーバ側で全テキストを生成してから TTS する現行実装を維持する。
// ========================================================
#pragma once

#include <WiFi.h>
#include <cstdlib>
#include <cstring>
#include "config.h"

namespace stackchan {

struct ChatResponse {
    bool      ok            = false;
    uint8_t*  body          = nullptr; // ps_malloc 済み。呼び出し側が free
    size_t    body_size     = 0;
    int       http_status   = 0;
    String    user_text;
    String    bot_text;
    String    timing;
    String    tts_backend;
};

class ChatClient {
public:
    // 録音 WAV をサーバに送って、応答 WAV を返す
    static ChatResponse send(const uint8_t* wav, size_t wav_size) {
        ChatResponse r;

        WiFiClient client;
        if (!client.connect(SERVER_HOST, SERVER_PORT)) {
            log_e("connect failed: %s:%u", SERVER_HOST, (unsigned)SERVER_PORT);
            return r;
        }
        client.setTimeout(HTTP_TIMEOUT_MS);

        // ---- multipart ボディ組み立て ----
        const char* boundary = "----stackchanboundary";
        String head;
        head += "--"; head += boundary; head += "\r\n";
        head += "Content-Disposition: form-data; name=\"sid\"\r\n\r\n";
        head += SESSION_ID; head += "\r\n";
        head += "--"; head += boundary; head += "\r\n";
        head += "Content-Disposition: form-data; name=\"audio\"; filename=\"a.wav\"\r\n";
        head += "Content-Type: audio/wav\r\n\r\n";
        String tail;
        tail += "\r\n--"; tail += boundary; tail += "--\r\n";

        const size_t content_length = head.length() + wav_size + tail.length();

        // ---- ヘッダ ----
        client.printf("POST %s HTTP/1.1\r\n", SERVER_PATH);
        client.printf("Host: %s:%u\r\n", SERVER_HOST, (unsigned)SERVER_PORT);
        client.printf("Content-Length: %u\r\n", (unsigned)content_length);
        client.printf("Content-Type: multipart/form-data; boundary=%s\r\n", boundary);
        client.print("Connection: close\r\n\r\n");

        // ---- ボディ ----
        client.print(head);
        // チャンク送信 (大きい WAV を一気に渡すと TCP バッファあふれが怖い)
        constexpr size_t CHUNK = 1024;
        size_t sent = 0;
        while (sent < wav_size) {
            const size_t n = (wav_size - sent) > CHUNK ? CHUNK : (wav_size - sent);
            client.write(wav + sent, n);
            sent += n;
        }
        client.print(tail);

        // ---- レスポンス読み取り ----
        // ステータスライン
        String status = client.readStringUntil('\n');
        int sp1 = status.indexOf(' ');
        int sp2 = status.indexOf(' ', sp1 + 1);
        if (sp1 > 0 && sp2 > sp1) {
            r.http_status = status.substring(sp1 + 1, sp2).toInt();
        }

        size_t resp_len = 0;
        // ヘッダ
        while (client.connected() || client.available()) {
            String line = client.readStringUntil('\n');
            if (line == "\r" || line.length() == 0) break;
            line.trim();
            const int colon = line.indexOf(':');
            if (colon <= 0) continue;

            String name = line.substring(0, colon);
            String value = line.substring(colon + 1);
            name.toLowerCase();
            value.trim();

            if (name == "content-length") {
                resp_len = (size_t)value.toInt();
            } else if (name == "x-stackchan-user-text") {
                r.user_text = urlDecode(value);
            } else if (name == "x-stackchan-bot-text") {
                r.bot_text  = urlDecode(value);
            } else if (name == "x-stackchan-timing") {
                r.timing = value;
            } else if (name == "x-stackchan-tts-backend") {
                r.tts_backend = value;
            }
        }

        if (r.http_status != 200 || resp_len == 0) {
            log_e("HTTP %d, len=%u", r.http_status, (unsigned)resp_len);
            client.stop();
            return r;
        }

        // ボディを PSRAM に受ける
        r.body = static_cast<uint8_t*>(ps_malloc(resp_len));
        if (!r.body) {
            log_e("ps_malloc(%u) failed for response", (unsigned)resp_len);
            client.stop();
            return r;
        }
        size_t got = 0;
        const uint32_t deadline = millis() + HTTP_TIMEOUT_MS;
        while (got < resp_len && millis() < deadline) {
            int n = client.read(r.body + got, resp_len - got);
            if (n > 0) got += n;
            else delay(1);
        }
        r.body_size = got;
        r.ok = (got == resp_len);
        if (!r.ok) {
            free(r.body);
            r.body = nullptr;
            r.body_size = 0;
        }
        client.stop();
        return r;
    }

private:
    static int hexValue(char c) {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    }

    static String urlDecode(const String& encoded) {
        String out;
        out.reserve(encoded.length());
        for (size_t i = 0; i < encoded.length(); ++i) {
            const char c = encoded[i];
            if (c == '%' && i + 2 < encoded.length()) {
                const int hi = hexValue(encoded[i + 1]);
                const int lo = hexValue(encoded[i + 2]);
                if (hi >= 0 && lo >= 0) {
                    out += static_cast<char>((hi << 4) | lo);
                    i += 2;
                    continue;
                }
            }
            out += (c == '+') ? ' ' : c;
        }
        return out;
    }
};

} // namespace stackchan
