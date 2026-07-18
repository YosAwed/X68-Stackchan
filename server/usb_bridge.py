#!/usr/bin/env python3
"""Bridge CoreS3 USB CDC requests to the local Stack-chan FastAPI server.

No third-party serial package is required. The bridge uses POSIX termios and
therefore works on macOS and Linux (including a USB device passed into WSL).
"""

from __future__ import annotations

import argparse
import errno
import glob
import json
import os
import select
import struct
import sys
import termios
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, unquote
from urllib.request import Request, urlopen


REQUEST_MAGIC = b"SCU1"
RESPONSE_MAGIC = b"SCR1"
REQUEST_HEADER = struct.Struct("<4sB3xII")
RESPONSE_HEADER = struct.Struct("<4sH2xII")
HEARTBEAT = b"@SCUSB1\n"
MAX_METADATA_BYTES = 16 * 1024
MAX_BODY_BYTES = 4 * 1024 * 1024

OP_READY = 1
OP_CHAT = 2
OP_WAKE = 3
OP_SPEAK = 4
OP_PULL = 5
OP_VISION_CAPTURE = 6


@dataclass
class BridgeResponse:
    status: int
    metadata: dict[str, object]
    body: bytes


def serial_candidates() -> list[str]:
    patterns = (
        "/dev/cu.usbmodem*",
        "/dev/ttyACM*",
        "/dev/cu.usbserial*",
        "/dev/ttyUSB*",
        "/dev/cu.SLAB_USBtoUART*",
        "/dev/cu.wchusbserial*",
    )
    return sorted({path for pattern in patterns for path in glob.glob(pattern)})


def configure_serial(fd: int) -> None:
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0  # iflag
    attrs[1] = 0  # oflag
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    attrs[3] = 0  # lflag
    attrs[4] = termios.B115200
    attrs[5] = termios.B115200
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 1
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)


def open_serial(path: str) -> int:
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        configure_serial(fd)
    except Exception:
        os.close(fd)
        raise
    return fd


def write_all(fd: int, data: bytes, timeout: float = 10.0) -> None:
    view = memoryview(data)
    deadline = time.monotonic() + timeout
    while view:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("USB serial write timed out")
        _, writable, _ = select.select([], [fd], [], min(remaining, 1.0))
        if not writable:
            continue
        try:
            # ESP32-S3 HWCDC has a finite software RX queue and does not expose
            # application-level flow control to the tty driver. Pace responses
            # so multi-megabyte WAV data can be drained without dropped bytes.
            written = os.write(fd, view[:512])
        except BlockingIOError:
            continue
        view = view[written:]
        if view:
            time.sleep(0.001)


def multipart_audio(wav: bytes, *, sid: str | None = None) -> tuple[bytes, str]:
    boundary = "----stackchan-usb-bridge"
    chunks: list[bytes] = []
    if sid is not None:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                b'Content-Disposition: form-data; name="sid"\r\n\r\n',
                sid.encode("utf-8"),
                b"\r\n",
            ]
        )
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="audio"; filename="audio.wav"\r\n',
            b"Content-Type: audio/wav\r\n\r\n",
            wav,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def http_call(
    server_url: str,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    content_type: str | None = None,
    timeout: float = 65.0,
) -> tuple[int, dict[str, str], bytes]:
    headers = {"Accept": "audio/wav, application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    request = Request(
        f"{server_url.rstrip('/')}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers.items()), response.read()
    except HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read()


def response_metadata(headers: dict[str, str]) -> dict[str, str]:
    lower = {key.lower(): value for key, value in headers.items()}
    return {
        "user_text": unquote(lower.get("x-stackchan-user-text", "")),
        "bot_text": unquote(lower.get("x-stackchan-bot-text", "")),
        "timing": lower.get("x-stackchan-timing", ""),
        "tts_backend": lower.get("x-stackchan-tts-backend", ""),
        "emote": lower.get("x-stackchan-emote", ""),
        "source": lower.get("x-stackchan-source", ""),
    }


def dispatch(server_url: str, operation: int, metadata: bytes, body: bytes) -> BridgeResponse:
    try:
        meta = json.loads(metadata.decode("utf-8")) if metadata else {}
        if not isinstance(meta, dict):
            raise ValueError("metadata must be an object")

        if operation == OP_READY:
            status, headers, response_body = http_call(server_url, "GET", "/ready", timeout=10)
        elif operation == OP_CHAT:
            request_body, content_type = multipart_audio(body, sid=str(meta.get("sid", "default")))
            status, headers, response_body = http_call(
                server_url, "POST", "/chat", body=request_body, content_type=content_type
            )
        elif operation == OP_WAKE:
            request_body, content_type = multipart_audio(body)
            status, headers, response_body = http_call(
                server_url, "POST", "/wake", body=request_body, content_type=content_type
            )
        elif operation == OP_SPEAK:
            request_body = urlencode({"text": str(meta.get("text", ""))}).encode()
            status, headers, response_body = http_call(
                server_url,
                "POST",
                "/speak",
                body=request_body,
                content_type="application/x-www-form-urlencoded",
            )
        elif operation == OP_PULL:
            wait = max(0, min(int(meta.get("wait", 0)), 60))
            status, headers, response_body = http_call(
                server_url, "GET", f"/pull?wait={wait}", timeout=wait + 10
            )
        elif operation == OP_VISION_CAPTURE:
            status, headers, response_body = http_call(
                server_url, "POST", "/vision/capture", body=b""
            )
        else:
            return BridgeResponse(400, {"error": f"unknown operation {operation}"}, b"")
        return BridgeResponse(status, response_metadata(headers), response_body)
    except (ValueError, UnicodeDecodeError, URLError, TimeoutError, OSError) as exc:
        return BridgeResponse(503, {"error": str(exc)}, b"")


def encode_response(response: BridgeResponse) -> bytes:
    metadata = json.dumps(response.metadata, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    body = response.body
    if len(metadata) > MAX_METADATA_BYTES:
        metadata = b'{"error":"response metadata too large"}'
        body = b""
        status = 500
    elif len(body) > MAX_BODY_BYTES:
        metadata = b'{"error":"response body too large"}'
        body = b""
        status = 413
    else:
        status = response.status
    return RESPONSE_HEADER.pack(RESPONSE_MAGIC, status, len(metadata), len(body)) + metadata + body


def emit_log(data: bytes) -> None:
    if not data:
        return
    sys.stdout.write(data.decode("utf-8", errors="replace"))
    sys.stdout.flush()


def serve_port(fd: int, path: str, server_url: str) -> None:
    print(f"USB bridge: connected to {path}", flush=True)
    buffer = bytearray()
    last_heartbeat = 0.0
    while True:
        now = time.monotonic()
        if now - last_heartbeat >= 1.0:
            write_all(fd, HEARTBEAT, timeout=2.0)
            last_heartbeat = now

        readable, _, _ = select.select([fd], [], [], 0.2)
        if readable:
            chunk = os.read(fd, 65536)
            if not chunk:
                raise OSError(errno.ENODEV, "USB serial device disconnected")
            buffer.extend(chunk)

        while buffer:
            marker = buffer.find(REQUEST_MAGIC)
            if marker < 0:
                keep = min(len(REQUEST_MAGIC) - 1, len(buffer))
                emit_log(bytes(buffer[:-keep] if keep else buffer))
                if keep:
                    del buffer[:-keep]
                else:
                    buffer.clear()
                break
            if marker:
                emit_log(bytes(buffer[:marker]))
                del buffer[:marker]
            if len(buffer) < REQUEST_HEADER.size:
                break
            _, operation, metadata_size, body_size = REQUEST_HEADER.unpack_from(buffer)
            if metadata_size > MAX_METADATA_BYTES or body_size > MAX_BODY_BYTES:
                emit_log(bytes(buffer[:1]))
                del buffer[:1]
                continue
            frame_size = REQUEST_HEADER.size + metadata_size + body_size
            if len(buffer) < frame_size:
                break
            metadata_start = REQUEST_HEADER.size
            body_start = metadata_start + metadata_size
            metadata = bytes(buffer[metadata_start:body_start])
            body = bytes(buffer[body_start:frame_size])
            del buffer[:frame_size]

            print(
                f"USB bridge: operation={operation} request_body={len(body)} bytes",
                flush=True,
            )
            response = dispatch(server_url, operation, metadata, body)
            write_all(fd, encode_response(response), timeout=15.0)
            print(
                f"USB bridge: status={response.status} response_body={len(response.body)} bytes",
                flush=True,
            )
            last_heartbeat = time.monotonic()


def run(port: str, server_url: str) -> None:
    while True:
        candidates = [port] if port != "auto" else serial_candidates()
        if not candidates:
            print("USB bridge: waiting for CoreS3 USB serial device", flush=True)
            time.sleep(2)
            continue
        if port == "auto" and len(candidates) > 1:
            print(f"USB bridge: multiple ports found; trying {candidates[0]}", flush=True)
        path = candidates[0]
        try:
            fd = open_serial(path)
            try:
                serve_port(fd, path, server_url)
            finally:
                os.close(fd)
        except (OSError, TimeoutError) as exc:
            print(f"USB bridge: {path}: {exc}; retrying", file=sys.stderr, flush=True)
            time.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=os.getenv("STACKCHAN_PORT", "auto"))
    parser.add_argument(
        "--server-url", default=os.getenv("STACKCHAN_SERVER_URL", "http://127.0.0.1:8000")
    )
    args = parser.parse_args()
    run(args.port, args.server_url)


if __name__ == "__main__":
    main()
