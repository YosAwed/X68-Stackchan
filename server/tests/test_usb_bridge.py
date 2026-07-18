import json
import usb_bridge


def test_multipart_audio_contains_sid_and_wav():
    wav = b"RIFF-test-wave"
    body, content_type = usb_bridge.multipart_audio(wav, sid="stackchan-01")
    assert content_type.startswith("multipart/form-data; boundary=")
    assert b'name="sid"' in body
    assert b"stackchan-01" in body
    assert b'name="audio"' in body
    assert wav in body


def test_dispatch_speak_forwards_form_and_metadata(monkeypatch):
    seen = {}

    def fake_http(server_url, method, path, **kwargs):
        seen.update(server_url=server_url, method=method, path=path, **kwargs)
        return 200, {
            "X-Stackchan-Bot-Text": "%E3%83%86%E3%82%B9%E3%83%88",
            "X-Stackchan-Emote": "joy",
        }, b"RIFFreply"

    monkeypatch.setattr(usb_bridge, "http_call", fake_http)
    response = usb_bridge.dispatch(
        "http://127.0.0.1:8000",
        usb_bridge.OP_SPEAK,
        json.dumps({"text": "テスト"}).encode(),
        b"",
    )

    assert response.status == 200
    assert response.metadata["bot_text"] == "テスト"
    assert response.metadata["emote"] == "joy"
    assert response.body == b"RIFFreply"
    assert seen["path"] == "/speak"
    assert b"text=%E3%83%86%E3%82%B9%E3%83%88" in seen["body"]


def test_encode_response_header_and_utf8_metadata():
    encoded = usb_bridge.encode_response(
        usb_bridge.BridgeResponse(200, {"bot_text": "こんにちは"}, b"RIFF")
    )
    magic, status, metadata_size, body_size = usb_bridge.RESPONSE_HEADER.unpack_from(encoded)
    assert magic == usb_bridge.RESPONSE_MAGIC
    assert status == 200
    assert body_size == 4
    metadata_start = usb_bridge.RESPONSE_HEADER.size
    metadata = json.loads(encoded[metadata_start : metadata_start + metadata_size])
    assert metadata["bot_text"] == "こんにちは"
    assert encoded[-4:] == b"RIFF"


def test_oversized_response_is_rejected():
    response = usb_bridge.BridgeResponse(
        200, {}, b"x" * (usb_bridge.MAX_BODY_BYTES + 1)
    )
    encoded = usb_bridge.encode_response(response)
    _, status, _, body_size = usb_bridge.RESPONSE_HEADER.unpack_from(encoded)
    assert status == 413
    assert body_size == 0
