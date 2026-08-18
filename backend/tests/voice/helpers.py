"""Shared fakes for voice session tests (external-channel gates)."""
from __future__ import annotations

import json
from typing import Any

from app.core.auth_apikey import ApiKeyPrincipal
from app.voice.config import ASRRuntime, TTSRuntime, VoiceRuntimeConfig
from app.voice.session import VoiceSession


class FakeWebSocket:
    """Records JSON messages + close() calls; enough for VoiceSession."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.closed: list[dict[str, Any]] = []

    async def send_text(self, raw: str) -> None:
        self.messages.append(json.loads(raw))

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed.append({"code": code, "reason": reason})


class FakeASR:
    def __init__(self) -> None:
        self.opened = False

    def bind(self, **callbacks: Any) -> None:
        self.callbacks = callbacks

    async def open(self) -> None:
        self.opened = True

    async def feed(self, pcm16_frame: bytes) -> None:
        return None

    async def close(self) -> None:
        return None


class FakeTTS:
    async def stop(self) -> None:
        return None

    async def close(self) -> None:
        return None


def runtime_config() -> VoiceRuntimeConfig:
    return VoiceRuntimeConfig(
        asr=ASRRuntime(api_key="key", resource_id="asr", url="wss://asr"),
        tts=TTSRuntime(
            api_key="key", resource_id="tts", url="wss://tts", voice_type="voice"
        ),
        input_rate=16000,
        output_rate=24000,
        vad_mode="energy",
        vad_threshold=0.12,
        vad_silence_ms=600,
    )


def make_principal(
    *,
    scopes: list[str] | None = None,
    agent_bindings: list[str] | None = None,
    user_id: str = "ext_user_1",
    user_token: str | None = None,
    introspect_url: str | None = None,
) -> ApiKeyPrincipal:
    """External-channel principal; full access by default."""
    return ApiKeyPrincipal(
        key_id="apikey_test",
        owner_user_id="user_owner",
        scopes=scopes if scopes is not None else ["agents:read", "agents:invoke"],
        bindings={"agents": agent_bindings or [], "workflows": []},
        rate_limit=60,
        user_id=user_id,
        token_record_id=user_id,
        user_token=user_token,
        introspect_url=introspect_url or "",
    )


def make_session(
    ws: FakeWebSocket,
    *,
    principal: ApiKeyPrincipal | None = None,
    user_id: str = "user-1",
) -> VoiceSession:
    return VoiceSession(
        ws,  # type: ignore[arg-type]
        user_id,
        cfg=runtime_config(),
        asr_factory=FakeASR,  # type: ignore[arg-type]
        tts_factory=FakeTTS,  # type: ignore[arg-type]
        principal=principal,
    )
