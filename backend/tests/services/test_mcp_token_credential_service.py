"""Tests for McpTokenCredentialService — 通用 token CRUD + 凭证加解密 + 本地校验。"""
from unittest.mock import AsyncMock, patch

from app.services.mcp_token_credential_service import (
    McpTokenCredentialService,
    _decrypt_binding,
    _encrypt_binding,
    _generate_token,
    _mask_binding,
)

# ---------------------------------------------------------------------------
# 纯函数：token 生成、加解密、脱敏
# ---------------------------------------------------------------------------


class TestTokenGeneration:
    def test_token_has_meper_prefix(self) -> None:
        t = _generate_token()
        assert t.startswith("meper_")

    def test_token_is_random(self) -> None:
        assert _generate_token() != _generate_token()

    def test_token_long_enough(self) -> None:
        assert len(_generate_token()) > 20


class TestBindingEncryptDecrypt:
    def test_token_type_roundtrip(self) -> None:
        original = {
            "credential_type": "token",
            "auth_type": "bearer_token",
            "token": "ghp_secret_value",
        }
        encrypted = _encrypt_binding(dict(original))
        # 敏感字段被加密（enc: 前缀）
        assert encrypted["token"].startswith("enc:")
        assert encrypted["token"] != original["token"]
        # 非敏感字段不变
        assert encrypted["credential_type"] == "token"
        assert encrypted["auth_type"] == "bearer_token"
        # 解密还原
        decrypted = _decrypt_binding(dict(encrypted))
        assert decrypted["token"] == "ghp_secret_value"

    def test_password_type_roundtrip(self) -> None:
        original = {
            "credential_type": "password",
            "auth_type": "bearer_token",
            "username": "admin",
            "password": "pass123",
        }
        encrypted = _encrypt_binding(dict(original))
        assert encrypted["username"].startswith("enc:")
        assert encrypted["password"].startswith("enc:")
        decrypted = _decrypt_binding(dict(encrypted))
        assert decrypted["username"] == "admin"
        assert decrypted["password"] == "pass123"

    def test_already_encrypted_not_double_encrypted(self) -> None:
        """已带 enc: 前缀的值不会被二次加密。"""
        original = {"credential_type": "token", "token": "enc:already_encrypted"}
        encrypted = _encrypt_binding(dict(original))
        assert encrypted["token"] == "enc:already_encrypted"

    def test_empty_value_not_encrypted(self) -> None:
        original = {"credential_type": "token", "token": ""}
        encrypted = _encrypt_binding(dict(original))
        assert encrypted["token"] == ""


class TestBindingMask:
    def test_mask_token_type(self) -> None:
        binding = _encrypt_binding(
            {"credential_type": "token", "auth_type": "bearer_token", "token": "ghp_secret123"}
        )
        masked = _mask_binding(dict(binding))
        # 脱敏后不是明文
        assert masked["token"] != "ghp_secret123"
        # 但保留了特征（前3后4）
        assert "ghp" in masked["token"]

    def test_mask_password_type(self) -> None:
        binding = _encrypt_binding(
            {"credential_type": "password", "username": "admin", "password": "pass123"}
        )
        masked = _mask_binding(dict(binding))
        assert masked["password"] != "pass123"
        # username 明文返回（admin 需要看到用户名）
        assert masked["username"] == "admin"


# ---------------------------------------------------------------------------
# Service 层：verify_token（本地校验，替代外部 introspection）
# ---------------------------------------------------------------------------


class TestVerifyToken:
    async def test_verify_active_token_returns_record(self) -> None:
        record = {"_id": "mcptok_01", "status": "active", "token": "meper_xxx"}
        with patch.object(
            McpTokenCredentialService, "_collection"
        ) as mock_col:
            mock_col.return_value.find_one = AsyncMock(return_value=record)
            result = await McpTokenCredentialService.verify_token("meper_xxx")
        assert result is not None
        assert result["_id"] == "mcptok_01"

    async def test_verify_disabled_token_returns_none(self) -> None:
        record = {"_id": "mcptok_01", "status": "disabled", "token": "meper_xxx"}
        with patch.object(
            McpTokenCredentialService, "_collection"
        ) as mock_col:
            mock_col.return_value.find_one = AsyncMock(return_value=record)
            result = await McpTokenCredentialService.verify_token("meper_xxx")
        assert result is None

    async def test_verify_unknown_token_returns_none(self) -> None:
        with patch.object(
            McpTokenCredentialService, "_collection"
        ) as mock_col:
            mock_col.return_value.find_one = AsyncMock(return_value=None)
            result = await McpTokenCredentialService.verify_token("meper_unknown")
        assert result is None

    async def test_verify_empty_token_returns_none(self) -> None:
        result = await McpTokenCredentialService.verify_token("")
        assert result is None


# ---------------------------------------------------------------------------
# Service 层：resolve_binding（运行时兑换）
# ---------------------------------------------------------------------------


class TestResolveBinding:
    async def test_resolve_token_binding(self) -> None:
        """token 型绑定：解密后返回。"""
        encrypted = _encrypt_binding(
            {"credential_type": "token", "auth_type": "bearer_token", "token": "ghp_xxx"}
        )
        record = {
            "_id": "mcptok_01",
            "mcp_bindings": {"mcp_conn_01": encrypted},
        }
        with patch.object(
            McpTokenCredentialService, "get_record", AsyncMock(return_value=record)
        ):
            result = await McpTokenCredentialService.resolve_binding(
                "mcptok_01", "mcp_conn_01"
            )
        assert result is not None
        assert result["credential_type"] == "token"
        assert result["token"] == "ghp_xxx"  # 解密后的明文

    async def test_resolve_unbound_mcp_returns_none(self) -> None:
        """用户未绑定该 MCP → None。"""
        record = {"_id": "mcptok_01", "mcp_bindings": {}}
        with patch.object(
            McpTokenCredentialService, "get_record", AsyncMock(return_value=record)
        ):
            result = await McpTokenCredentialService.resolve_binding(
                "mcptok_01", "mcp_conn_99"
            )
        assert result is None

    async def test_resolve_unknown_record_returns_none(self) -> None:
        """记录不存在 → None。"""
        with patch.object(
            McpTokenCredentialService, "get_record", AsyncMock(return_value=None)
        ):
            result = await McpTokenCredentialService.resolve_binding(
                "mcptok_unknown", "mcp_conn_01"
            )
        assert result is None
