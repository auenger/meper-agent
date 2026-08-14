"""User MCP credential service — per-application credential bindings.

An application is the authorization boundary (v4). Users authorize an
application by binding their credentials for that external system; the
binding verifies credentials via the application's ``login_config`` and
auto-creates the ``external_identities`` mapping
(``{app_id}:{username}`` → platform_user_id).
"""
from __future__ import annotations

from typing import Any

import httpx
from loguru import logger

from app.core.crypto import decrypt_secret, encrypt_secret, mask_secret
from app.db.mongodb import get_database
from app.db.redis import get_redis_client
from app.models.base import utc_now
from app.models.external_identity import compose_sub
from app.services.external_identity_service import ExternalIdentityService

COLLECTION = "user_mcp_credentials"

# 账密型 session 的 Redis 缓存 key 前缀
_SESSION_KEY_PREFIX = "mcp:session:"

# 绑定凭证对象里需要加密/解密的字段（值是 enc: 前缀，写库加密、运行时解密）
_SENSITIVE_FIELDS = {"password", "username"}


# ---------------------------------------------------------------------------
# 加解密 / 脱敏
# ---------------------------------------------------------------------------


def _encrypt_binding(binding: dict[str, Any]) -> dict[str, Any]:
    """加密绑定凭证对象里的敏感字段（写库前调用）。"""
    out = dict(binding)
    for k, v in binding.items():
        if k in _SENSITIVE_FIELDS and isinstance(v, str) and v and not v.startswith("enc:"):
            out[k] = "enc:" + encrypt_secret(v)
    return out


def _decrypt_binding(binding: dict[str, Any]) -> dict[str, Any]:
    """解密绑定凭证对象里的敏感字段（运行时兑换用）。"""
    out = dict(binding)
    for k, v in binding.items():
        if k in _SENSITIVE_FIELDS and isinstance(v, str) and v.startswith("enc:"):
            try:
                out[k] = decrypt_secret(v[4:])
            except Exception:
                out[k] = v[4:] if v.startswith("enc:") else v
    return out


def _mask_binding(binding: dict[str, Any]) -> dict[str, Any]:
    """脱敏绑定凭证对象（API 响应用）。username 明文返回，password 脱敏。"""
    out = dict(binding)
    for k, v in binding.items():
        if k in _SENSITIVE_FIELDS:
            plaintext = v
            if isinstance(v, str) and v.startswith("enc:"):
                try:
                    plaintext = decrypt_secret(v[4:])
                except Exception:
                    plaintext = "***"
            if k == "username":
                out[k] = plaintext
            else:
                out[k] = mask_secret(plaintext) if isinstance(plaintext, str) and plaintext else "***"
    return out


# ---------------------------------------------------------------------------
# login_url 调用（验证账密）
# ---------------------------------------------------------------------------


async def _verify_credentials(
    login_config: dict[str, Any],
    username: str,
    password: str,
) -> None:
    """调 login_url 验证账密。

    仅验证（登录成功 = 账密正确），不提取任何身份信息——sub 由
    app_id + 用户名组合，与登录响应内容无关。

    Raises:
        PermissionError: 登录失败或响应缺少 token。
    """
    login_url = login_config["login_url"]
    method = login_config.get("method", "POST").upper()
    username_field = login_config.get("username_field", "username")
    password_field = login_config.get("password_field", "password")
    token_jsonpath = login_config.get("token_jsonpath", "data.token")

    body = {username_field: username, password_field: password}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.request(method, login_url, json=body)
        resp.raise_for_status()
        data = resp.json()

    # 校验登录是否成功
    if isinstance(data, dict) and data.get("success") is False:
        msg = data.get("message") or data.get("msg") or data.get("error") or "未知错误"
        raise PermissionError(f"登录失败：{msg}")

    # 按 jsonpath 取 token 确认登录成功（取不到视为失败）
    current: Any = data
    for part in token_jsonpath.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise PermissionError(f"登录响应未找到 token 路径 {token_jsonpath}")
    if not current:
        raise PermissionError("登录响应 token 为空")


# ---------------------------------------------------------------------------
# Session 缓存（Redis）
# ---------------------------------------------------------------------------


def _session_cache_key(platform_user_id: str, app_id: str) -> str:
    return f"{_SESSION_KEY_PREFIX}{platform_user_id}:{app_id}"


async def get_cached_session(platform_user_id: str, app_id: str) -> str | None:
    """从 Redis 取已缓存的 session token。miss 返回 None。"""
    try:
        redis = await get_redis_client()
        return await redis.get(_session_cache_key(platform_user_id, app_id))
    except Exception as exc:
        logger.warning("mcp_session_cache_get_failed", error=str(exc))
        return None


async def set_cached_session(
    platform_user_id: str, app_id: str, session_token: str, ttl: int = 3600
) -> None:
    """写 session token 到 Redis（带 TTL）。"""
    try:
        redis = await get_redis_client()
        await redis.setex(_session_cache_key(platform_user_id, app_id), ttl, session_token)
    except Exception as exc:
        logger.warning("mcp_session_cache_set_failed", error=str(exc))


async def clear_app_session_cache(platform_user_id: str, app_id: str) -> None:
    """清除某用户某应用的 session 缓存。"""
    try:
        redis = await get_redis_client()
        await redis.delete(_session_cache_key(platform_user_id, app_id))
    except Exception as exc:
        logger.warning("mcp_session_cache_clear_failed", error=str(exc))


async def clear_user_session_cache(platform_user_id: str) -> int:
    """清除某用户所有应用的 session 缓存。"""
    try:
        redis = await get_redis_client()
        pattern = f"{_SESSION_KEY_PREFIX}{platform_user_id}:*"
        deleted = 0
        async for key in redis.scan_iter(match=pattern, count=100):
            await redis.delete(key)
            deleted += 1
        return deleted
    except Exception as exc:
        logger.warning("mcp_session_cache_clear_all_failed", error=str(exc))
        return 0


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class UserMcpCredentialService:
    """Per-application credential bindings for platform users."""

    COLLECTION = COLLECTION

    @staticmethod
    def _collection():
        return get_database()[COLLECTION]

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    @staticmethod
    async def get_binding(platform_user_id: str, app_id: str) -> dict[str, Any] | None:
        """查 + 解密某用户对某应用的绑定凭证。未找到返回 None。"""
        col = UserMcpCredentialService._collection()
        doc = await col.find_one({"platform_user_id": platform_user_id})
        if doc is None:
            return None
        binding = (doc.get("app_bindings") or {}).get(app_id)
        if not binding:
            return None
        return _decrypt_binding(dict(binding))

    @staticmethod
    async def list_bindings(platform_user_id: str) -> dict[str, Any] | None:
        """查看某用户的全部绑定（凭证脱敏）。"""
        col = UserMcpCredentialService._collection()
        doc = await col.find_one({"platform_user_id": platform_user_id})
        if doc is None:
            return None
        bindings = doc.get("app_bindings") or {}
        masked = {aid: _mask_binding(dict(b)) for aid, b in bindings.items()}
        return {
            "platform_user_id": platform_user_id,
            "app_bindings": masked,
            "updated_at": doc.get("updated_at", ""),
        }

    # ------------------------------------------------------------------
    # 绑定 / 解绑
    # ------------------------------------------------------------------

    @staticmethod
    async def bind_credential(
        platform_user_id: str,
        app_id: str,
        username: str,
        password: str,
        login_config: dict[str, Any],
    ) -> dict[str, Any]:
        """授权应用：绑定/更新账密。

        内部流程：
        1. 调 login_url 验证账密（仅验证，不提取身份）
        2. sub = {app_id}:{username} → 写 external_identities（含抢注保护）
        3. 加密账密写入 app_bindings
        4. 清该应用 session 缓存

        Raises:
            PermissionError: 账密验证失败。
            ConflictError: 该外部身份已被其他平台用户绑定。
        """
        # 1. 验证账密
        await _verify_credentials(login_config, username, password)

        # 2. 写 external_identities（sub → platform_user_id）
        sub = compose_sub(app_id, username)
        await ExternalIdentityService.upsert(sub, platform_user_id)

        # 3. 加密账密 + 写 app_bindings
        col = UserMcpCredentialService._collection()
        encrypted = _encrypt_binding({"username": username, "password": password})

        now_iso = utc_now().isoformat()
        await col.update_one(
            {"platform_user_id": platform_user_id},
            {
                "$set": {
                    f"app_bindings.{app_id}": encrypted,
                    "updated_at": now_iso,
                },
            },
            upsert=True,
        )

        # 4. 清 session 缓存
        await clear_app_session_cache(platform_user_id, app_id)

        logger.info(
            "mcp_credential_bound",
            platform_user_id=platform_user_id,
            app_id=app_id,
            sub=sub,
        )
        return await UserMcpCredentialService.list_bindings(platform_user_id)  # type: ignore[return-value]

    @staticmethod
    async def unbind_credential(platform_user_id: str, app_id: str) -> dict[str, Any] | None:
        """取消授权：解绑某应用。

        同时删除绑定时建立的身份映射（sub 由 app_id + 已绑定的用户名组合，
        可直接从解绑的凭证里取 username，无需再调 login_url）。

        Returns:
            更新后的脱敏绑定列表，或 None 如果用户记录不存在。
        """
        col = UserMcpCredentialService._collection()
        doc = await col.find_one({"platform_user_id": platform_user_id})
        if doc is None:
            return None

        bindings = doc.get("app_bindings") or {}
        if app_id not in bindings:
            return UserMcpCredentialService.list_bindings(platform_user_id)

        # 删除身份映射（用绑定时存的 username 组合 sub）
        binding = _decrypt_binding(dict(bindings[app_id]))
        username = binding.get("username", "")
        if username:
            sub = compose_sub(app_id, username)
            await ExternalIdentityService.delete_by_sub_and_user(sub, platform_user_id)

        # 删除 app_bindings 里的该应用
        now_iso = utc_now().isoformat()
        await col.update_one(
            {"platform_user_id": platform_user_id},
            {
                "$unset": {f"app_bindings.{app_id}": ""},
                "$set": {"updated_at": now_iso},
            },
        )

        # 清 session 缓存
        await clear_app_session_cache(platform_user_id, app_id)

        logger.info(
            "mcp_credential_unbound",
            platform_user_id=platform_user_id,
            app_id=app_id,
        )
        return await UserMcpCredentialService.list_bindings(platform_user_id)
