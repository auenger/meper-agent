"""Binary protocol helpers for Volcengine Agent Plan ASR/TTS v3 APIs.

TTS uses the event-based protocol published with the Agent Plan example.
ASR uses the sequence + gzip protocol from the streaming ASR example.
Keeping wire handling here makes the provider adapter small and testable.
"""

from __future__ import annotations

import gzip
import io
import json
import struct
from dataclasses import dataclass
from enum import IntEnum


class MsgType(IntEnum):
    FULL_CLIENT_REQUEST = 0b0001
    AUDIO_ONLY_CLIENT = 0b0010
    FULL_SERVER_RESPONSE = 0b1001
    AUDIO_ONLY_SERVER = 0b1011
    FRONTEND_RESULT_SERVER = 0b1100
    ERROR = 0b1111


class MsgFlag(IntEnum):
    NO_SEQUENCE = 0
    POSITIVE_SEQUENCE = 0b0001
    LAST_NO_SEQUENCE = 0b0010
    NEGATIVE_SEQUENCE = 0b0011
    WITH_EVENT = 0b0100


class EventType(IntEnum):
    NONE = 0
    START_CONNECTION = 1
    FINISH_CONNECTION = 2
    CONNECTION_STARTED = 50
    CONNECTION_FAILED = 51
    CONNECTION_FINISHED = 52
    START_SESSION = 100
    CANCEL_SESSION = 101
    FINISH_SESSION = 102
    SESSION_STARTED = 150
    SESSION_CANCELED = 151
    SESSION_FINISHED = 152
    SESSION_FAILED = 153
    USAGE_RESPONSE = 154
    TASK_REQUEST = 200
    TTS_SENTENCE_START = 350
    TTS_SENTENCE_END = 351
    TTS_RESPONSE = 352
    TTS_ENDED = 359


@dataclass
class TTSMessage:
    type: MsgType
    flag: MsgFlag = MsgFlag.NO_SEQUENCE
    event: EventType | int = EventType.NONE
    session_id: str = ""
    connect_id: str = ""
    sequence: int = 0
    error_code: int = 0
    payload: bytes = b""
    serialization: int = 1
    compression: int = 0

    def marshal(self) -> bytes:
        out = io.BytesIO()
        out.write(
            bytes(
                [
                    0x11,
                    (int(self.type) << 4) | int(self.flag),
                    (self.serialization << 4) | self.compression,
                    0,
                ]
            )
        )
        if self.flag == MsgFlag.WITH_EVENT:
            out.write(struct.pack(">i", int(self.event)))
            if self.event not in {
                EventType.START_CONNECTION,
                EventType.FINISH_CONNECTION,
                EventType.CONNECTION_STARTED,
                EventType.CONNECTION_FAILED,
            }:
                encoded = self.session_id.encode()
                out.write(struct.pack(">I", len(encoded)))
                out.write(encoded)
        if self.flag in {MsgFlag.POSITIVE_SEQUENCE, MsgFlag.NEGATIVE_SEQUENCE}:
            out.write(struct.pack(">i", self.sequence))
        out.write(struct.pack(">I", len(self.payload)))
        out.write(self.payload)
        return out.getvalue()

    @classmethod
    def from_bytes(cls, data: bytes) -> TTSMessage:
        if len(data) < 4:
            raise ValueError("Volcengine TTS response is shorter than its header")
        header_size = (data[0] & 0x0F) * 4
        if len(data) < header_size:
            raise ValueError("Volcengine TTS response has an invalid header size")
        msg_type = MsgType(data[1] >> 4)
        flag = MsgFlag(data[1] & 0x0F)
        msg = cls(
            type=msg_type,
            flag=flag,
            serialization=data[2] >> 4,
            compression=data[2] & 0x0F,
        )
        buf = io.BytesIO(data[header_size:])
        if flag in {MsgFlag.POSITIVE_SEQUENCE, MsgFlag.NEGATIVE_SEQUENCE}:
            msg.sequence = _read_i32(buf)
        if msg_type == MsgType.ERROR:
            msg.error_code = _read_u32(buf)
        if flag == MsgFlag.WITH_EVENT:
            event_value = _read_i32(buf)
            try:
                msg.event = EventType(event_value)
            except ValueError:
                # New downstream events must not kill an otherwise valid
                # audio stream. Preserve the numeric value for diagnostics.
                msg.event = event_value
            if msg.event not in {
                EventType.START_CONNECTION,
                EventType.FINISH_CONNECTION,
                EventType.CONNECTION_STARTED,
                EventType.CONNECTION_FAILED,
                EventType.CONNECTION_FINISHED,
            }:
                msg.session_id = _read_text(buf)
            if msg.event in {
                EventType.CONNECTION_STARTED,
                EventType.CONNECTION_FAILED,
                EventType.CONNECTION_FINISHED,
            }:
                msg.connect_id = _read_text(buf)
        size = _read_u32(buf)
        msg.payload = buf.read(size)
        if len(msg.payload) != size:
            raise ValueError("Volcengine TTS response payload is truncated")
        return msg


@dataclass
class ASRMessage:
    message_type: int
    sequence: int = 0
    is_last: bool = False
    error_code: int = 0
    payload: dict | None = None


def tts_event(
    event: EventType, *, session_id: str = "", payload: dict | None = None
) -> bytes:
    body = json.dumps(payload or {}, ensure_ascii=False).encode()
    return TTSMessage(
        type=MsgType.FULL_CLIENT_REQUEST,
        flag=MsgFlag.WITH_EVENT,
        event=event,
        session_id=session_id,
        payload=body,
    ).marshal()


def build_asr_full_request(sequence: int, payload: dict) -> bytes:
    compressed = gzip.compress(json.dumps(payload, ensure_ascii=False).encode())
    return (
        bytes(
            [
                0x11,
                (MsgType.FULL_CLIENT_REQUEST << 4) | MsgFlag.POSITIVE_SEQUENCE,
                0x11,
                0,
            ]
        )
        + struct.pack(">iI", sequence, len(compressed))
        + compressed
    )


def build_asr_audio(sequence: int, pcm16: bytes, *, is_last: bool = False) -> bytes:
    compressed = gzip.compress(pcm16)
    flag = MsgFlag.NEGATIVE_SEQUENCE if is_last else MsgFlag.POSITIVE_SEQUENCE
    wire_sequence = -abs(sequence) if is_last else sequence
    return (
        bytes([0x11, (MsgType.AUDIO_ONLY_CLIENT << 4) | flag, 0x01, 0])
        + struct.pack(">iI", wire_sequence, len(compressed))
        + compressed
    )


def parse_asr_response(data: bytes) -> ASRMessage:
    if len(data) < 4:
        raise ValueError("Volcengine ASR response is shorter than its header")
    header_size = (data[0] & 0x0F) * 4
    message_type = data[1] >> 4
    flags = data[1] & 0x0F
    compression = data[2] & 0x0F
    serialization = data[2] >> 4
    buf = io.BytesIO(data[header_size:])
    response = ASRMessage(message_type=message_type, is_last=bool(flags & 0b0010))
    if flags & 0b0001:
        response.sequence = _read_i32(buf)
    if flags & 0b0100:
        _read_i32(buf)  # event; unused by the streaming ASR endpoint
    if message_type == MsgType.ERROR:
        response.error_code = _read_i32(buf)
    size = _read_u32(buf)
    payload = buf.read(size)
    if len(payload) != size:
        raise ValueError("Volcengine ASR response payload is truncated")
    if compression == 1 and payload:
        payload = gzip.decompress(payload)
    if serialization == 1 and payload:
        response.payload = json.loads(payload)
    return response


def _read_exact(buf: io.BytesIO, size: int) -> bytes:
    value = buf.read(size)
    if len(value) != size:
        raise ValueError("Volcengine protocol field is truncated")
    return value


def _read_i32(buf: io.BytesIO) -> int:
    return struct.unpack(">i", _read_exact(buf, 4))[0]


def _read_u32(buf: io.BytesIO) -> int:
    return struct.unpack(">I", _read_exact(buf, 4))[0]


def _read_text(buf: io.BytesIO) -> str:
    size = _read_u32(buf)
    return _read_exact(buf, size).decode() if size else ""
