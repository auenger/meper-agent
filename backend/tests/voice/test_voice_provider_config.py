from __future__ import annotations

import pytest
from app.models.voice_config import AliyunConfig, VoiceConfig, ZhipuConfig
from app.schemas.voice_config import VoiceConfigUpdate
from app.services.voice_config_service import VoiceConfigService
from app.voice.config import get_runtime_config
from pydantic import ValidationError


def test_legacy_document_defaults_to_volcano() -> None:
    cfg = VoiceConfig.model_validate({"_id": "voice_config", "api_key_enc": "key"})

    assert cfg.active_provider == "volcano"
    assert cfg.zhipu == ZhipuConfig()
    assert cfg.aliyun == AliyunConfig()


def test_update_rejects_unknown_provider() -> None:
    with pytest.raises(ValidationError):
        VoiceConfigUpdate(active_provider="unknown")  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "configured"),
    [("volcano", True), ("zhipu", False), ("aliyun", True)],
)
async def test_is_configured_uses_active_provider(
    monkeypatch, provider: str, configured: bool
) -> None:
    cfg = VoiceConfig(
        active_provider=provider,  # type: ignore[arg-type]
        api_key_enc="volcano-key",
        zhipu=ZhipuConfig(api_key_enc=""),
        aliyun=AliyunConfig(api_key_enc="aliyun-key"),
    )

    async def fake_get_config() -> VoiceConfig:
        return cfg

    monkeypatch.setattr(VoiceConfigService, "get_config", fake_get_config)

    assert await VoiceConfigService.is_configured() is configured


@pytest.mark.asyncio
async def test_runtime_config_selects_aliyun(monkeypatch) -> None:
    cfg = VoiceConfig(
        active_provider="aliyun",
        aliyun=AliyunConfig(
            api_key_enc="encrypted",
            asr_model="asr-model",
            asr_url="https://example.test/asr",
            tts_model="tts-model",
            tts_url="https://example.test/tts",
            voice_type="Cherry",
            language_type="Chinese",
        ),
    )

    async def fake_get_config() -> VoiceConfig:
        return cfg

    monkeypatch.setattr(VoiceConfigService, "get_config", fake_get_config)
    monkeypatch.setattr("app.core.crypto.decrypt_secret", lambda value: "plain-key")

    runtime = await get_runtime_config()

    assert runtime.asr.provider == "aliyun"
    assert runtime.asr.api_key == "plain-key"
    assert runtime.tts.provider == "aliyun"
    assert runtime.tts.resource_id == "tts-model"
    assert runtime.tts.language_type == "Chinese"


@pytest.mark.asyncio
async def test_save_preserves_other_provider_keys(monkeypatch) -> None:
    existing = VoiceConfig(
        api_key_enc="volcano-encrypted",
        zhipu=ZhipuConfig(api_key_enc="zhipu-encrypted"),
        aliyun=AliyunConfig(api_key_enc="aliyun-encrypted"),
    )
    saved: dict = {}

    class FakeCollection:
        async def find_one_and_update(self, query, update, *, upsert):
            saved.update(update["$set"])

    class FakeDatabase:
        def __getitem__(self, name):
            return FakeCollection()

    async def fake_get_config() -> VoiceConfig:
        return existing

    monkeypatch.setattr(VoiceConfigService, "get_config", fake_get_config)
    monkeypatch.setattr(
        "app.services.voice_config_service.get_database", lambda: FakeDatabase()
    )
    monkeypatch.setattr(
        "app.services.voice_config_service.get_encryption_key", lambda: b"key"
    )
    monkeypatch.setattr(
        "app.services.voice_config_service.encrypt_secret",
        lambda value, key: f"encrypted:{value}",
    )

    body = VoiceConfigUpdate(
        active_provider="zhipu",
        zhipu={"api_key": "new-zhipu-key"},
    )
    result = await VoiceConfigService.save_config(body)

    assert result.api_key_enc == "volcano-encrypted"
    assert result.zhipu.api_key_enc == "encrypted:new-zhipu-key"
    assert result.aliyun.api_key_enc == "aliyun-encrypted"
    assert saved["active_provider"] == "zhipu"
