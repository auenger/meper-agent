from __future__ import annotations

import gzip
import json
import struct

from app.voice.providers.volcano_protocol import (
    EventType,
    MsgFlag,
    MsgType,
    TTSMessage,
    build_asr_audio,
    build_asr_full_request,
    parse_asr_response,
    tts_event,
)


def test_tts_start_session_matches_event_protocol() -> None:
    wire = tts_event(
        EventType.START_SESSION,
        session_id="session-1",
        payload={"event": 100, "req_params": {"text": "你好"}},
    )

    assert wire[:4] == bytes([0x11, 0x14, 0x10, 0x00])
    parsed = TTSMessage.from_bytes(wire)
    assert parsed.type == MsgType.FULL_CLIENT_REQUEST
    assert parsed.flag == MsgFlag.WITH_EVENT
    assert parsed.event == EventType.START_SESSION
    assert parsed.session_id == "session-1"
    assert json.loads(parsed.payload)["req_params"]["text"] == "你好"


def test_tts_parses_connection_started_with_connect_id() -> None:
    connect_id = b"connect-1"
    payload = b"{}"
    wire = (
        bytes([0x11, 0x94, 0x10, 0x00])
        + struct.pack(">iI", EventType.CONNECTION_STARTED, len(connect_id))
        + connect_id
        + struct.pack(">I", len(payload))
        + payload
    )

    parsed = TTSMessage.from_bytes(wire)
    assert parsed.event == EventType.CONNECTION_STARTED
    assert parsed.connect_id == "connect-1"
    assert parsed.payload == b"{}"


def test_tts_parses_sentence_start_event() -> None:
    wire = tts_event(
        EventType.TTS_SENTENCE_START,
        session_id="session-1",
        payload={"text": "你好"},
    )

    parsed = TTSMessage.from_bytes(wire)
    assert parsed.event == EventType.TTS_SENTENCE_START
    assert parsed.session_id == "session-1"


def test_tts_preserves_unknown_downstream_event() -> None:
    wire = (
        bytes([0x11, 0x94, 0x10, 0x00])
        + struct.pack(">iI", 398, len(b"session-1"))
        + b"session-1"
        + struct.pack(">I", 2)
        + b"{}"
    )

    parsed = TTSMessage.from_bytes(wire)
    assert parsed.event == 398


def test_asr_full_request_uses_sequence_json_and_gzip() -> None:
    wire = build_asr_full_request(7, {"request": {"model_name": "bigmodel"}})

    assert wire[:4] == bytes([0x11, 0x11, 0x11, 0x00])
    assert struct.unpack(">i", wire[4:8])[0] == 7
    size = struct.unpack(">I", wire[8:12])[0]
    assert json.loads(gzip.decompress(wire[12 : 12 + size])) == {
        "request": {"model_name": "bigmodel"}
    }


def test_asr_last_audio_packet_uses_negative_sequence() -> None:
    wire = build_asr_audio(9, b"\x01\x02", is_last=True)

    assert wire[:4] == bytes([0x11, 0x23, 0x01, 0x00])
    assert struct.unpack(">i", wire[4:8])[0] == -9
    size = struct.unpack(">I", wire[8:12])[0]
    assert gzip.decompress(wire[12 : 12 + size]) == b"\x01\x02"


def test_parse_asr_gzip_json_response() -> None:
    payload = gzip.compress(json.dumps({"result": {"text": "你好"}}).encode())
    wire = (
        bytes([0x11, 0x91, 0x11, 0x00]) + struct.pack(">iI", 2, len(payload)) + payload
    )

    parsed = parse_asr_response(wire)
    assert parsed.sequence == 2
    assert parsed.payload == {"result": {"text": "你好"}}
