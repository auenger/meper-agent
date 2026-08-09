"""Voice config CRUD — single-doc upsert, encrypted tokens, masked responses.

Singleton stored at ``_id="voice_config"``. Tokens are AES-256-GCM encrypted
on write and decrypted only on demand (runtime config for the voice session);
API responses always mask them.
"""
from __future__ import annotations

from loguru import logger

from app.core.crypto import (
    decrypt_secret,
    encrypt_secret,
    get_encryption_key,
    mask_secret,
)
from app.db.mongodb import get_database
from app.models.base import utc_now
from app.models.voice_config import (
    ASRConfig,
    COLLECTION,
    CONFIG_DOC_ID,
    TTSConfig,
    VoiceConfig,
)


class VoiceConfigService:
    @staticmethod
    async def get_config() -> VoiceConfig | None:
        db = get_database()
        doc = await db[COLLECTION].find_one({"_id": CONFIG_DOC_ID})
        if doc is None:
            return None
        return VoiceConfig(**doc)

    @staticmethod
    async def save_config(body) -> VoiceConfig:
        """Upsert the singleton. A null/empty ``access_token`` keeps the existing value."""
        db = get_database()
        existing = await VoiceConfigService.get_config()
        master_key = get_encryption_key()

        def resolve_token(new_token: str | None, old_enc: str) -> str:
            if new_token:
                return encrypt_secret(new_token, master_key)
            return old_enc or ""

        cfg = VoiceConfig(
            asr=ASRConfig(
                appid=body.asr.appid,
                access_token_enc=resolve_token(
                    body.asr.access_token, existing.asr.access_token_enc if existing else ""
                ),
                resource_id=body.asr.resource_id,
                url=body.asr.url,
            ),
            tts=TTSConfig(
                appid=body.tts.appid,
                access_token_enc=resolve_token(
                    body.tts.access_token, existing.tts.access_token_enc if existing else ""
                ),
                resource_id=body.tts.resource_id,
                url=body.tts.url,
                voice_type=body.tts.voice_type,
            ),
            audio={"input_rate": body.audio.input_rate, "output_rate": body.audio.output_rate},
            vad={
                "mode": body.vad.mode,
                "threshold": body.vad.threshold,
                "silence_ms": body.vad.silence_ms,
            },
            last_test_success=existing.last_test_success if existing else None,
            last_test_at=existing.last_test_at if existing else "",
        )
        doc = cfg.model_dump(by_alias=True)
        await db[COLLECTION].find_one_and_update(
            {"_id": CONFIG_DOC_ID}, {"$set": doc}, upsert=True
        )
        logger.info("voice_config_saved")
        return cfg

    @staticmethod
    async def update_test_result(success: bool) -> None:
        db = get_database()
        await db[COLLECTION].update_one(
            {"_id": CONFIG_DOC_ID},
            {"$set": {"last_test_success": success, "last_test_at": utc_now().isoformat()}},
            upsert=True,
        )

    @staticmethod
    def to_masked(cfg: VoiceConfig) -> dict:
        def mask(enc: str) -> str:
            if not enc:
                return ""
            try:
                return mask_secret(decrypt_secret(enc))
            except Exception:
                return "****"

        return {
            "asr": {
                "appid": cfg.asr.appid,
                "access_token_masked": mask(cfg.asr.access_token_enc),
                "resource_id": cfg.asr.resource_id,
                "url": cfg.asr.url,
            },
            "tts": {
                "appid": cfg.tts.appid,
                "access_token_masked": mask(cfg.tts.access_token_enc),
                "resource_id": cfg.tts.resource_id,
                "url": cfg.tts.url,
                "voice_type": cfg.tts.voice_type,
            },
            "audio": {"input_rate": cfg.audio.input_rate, "output_rate": cfg.audio.output_rate},
            "vad": {
                "mode": cfg.vad.mode,
                "threshold": cfg.vad.threshold,
                "silence_ms": cfg.vad.silence_ms,
            },
            "last_test_success": cfg.last_test_success,
            "last_test_at": cfg.last_test_at,
        }
