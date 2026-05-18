// ========================================================
//  WAV を multipart で POST → 応答 WAV を PSRAM に受ける
// ========================================================
#pragma once

#include <WiFi.h>
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
        client.setTimeout(HTTP_TIMEOUT_MS / 1000);

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
            if (line.startsWith("Content-Length:")) {
                resp_len = (size_t)line.substring(15).toInt();
            } else if (line.startsWith("X-Stackchan-User-Text:")) {
                r.user_text = line.substring(22); r.user_text.trim();
            } else if (line.startsWith("X-Stackchan-Bot-Text:")) {
                r.bot_text  = line.substring(21); r.bot_text.trim();
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
        client.stop();
        return r;
    }
};

} // namespace stackchan
