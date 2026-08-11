"""MCP token credential service — 通用 token 的 CRUD + 凭证加解密 + 本地校验。

存储集合 ``mcp_token_credentials``。凭证值用 ``enc:`` 前缀 + AES-256-GCM 加密
（复用 ``app.core.crypto``）。通用 token 随机不透明串（``meper_`` 前缀），
本身既是身份标识也是查绑定的 key（经记录 _id）。

详见 docs/planning-artifacts/mcp-credential-broker-design.md。
"""
from __future__ import annotations

import secrets
from typing import Any

from loguru import logger

from app.core.crypto import decrypt_secret, encrypt_secret, mask_secret
from app.db.mongodb import get_database
from app.db.redis import get_redis_client
from app.models.base import generate_id, utc_now
from app.models.mcp_token_credential import (
    TOKEN_PREFIX,
    McpTokenStatus,
)

COLLECTION = "mcp_token_credentials"

# 账密型 session 的 Redis 缓存 key 前缀
_SESSION_KEY_PREFIX = "mcp:session:"

# 绑定凭证对象里需要加密/解密的字段（值是 enc: 前缀，写库加密、运行时解密）
_SENSITIVE_FIELDS = {"token", "password", "username", "api_key", "secret", "bearer_token"}

# API 响应时需要脱敏（mask）的字段。username 不脱敏——admin 需要看到用户名，
# 只有真正的凭证（password/token/api_key）才隐藏。
_MASK_FIELDS = {"token", "password", "api_key", "secret", "bearer_token"}


def _generate_token() -> str:
    """生成通用 token：meper_ + 32 字节随机串（URL-safe）。"""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def _merge_binding(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """字段级合并：前端编辑时未修改的凭证字段（传回脱敏值或空值）保留原加密值。

    判断"未修改"的条件（针对 _SENSITIVE_FIELDS）：
    - 值为空字符串 → 不改，保留 old
    - 值含 **** → 脱敏值，不改，保留 old
    - 值以 enc: 开头 → 已经是加密态（不应出现但兼容），保留 old
    - 其他 → 新值，覆盖
    非敏感字段（credential_type/auth_type/header_name）直接用 new。
    """
    merged = dict(old)  # 以旧加密态为基础
    merged["credential_type"] = new.get("credential_type", old.get("credential_type"))
    merged["auth_type"] = new.get("auth_type", old.get("auth_type"))
    if new.get("header_name") is not None:
        merged["header_name"] = new["header_name"]
    for k in _SENSITIVE_FIELDS:
        if k not in new:
            continue
        v = new[k]
        if not isinstance(v, str) or not v:
            # 空值 → 保留 old
            continue
        if "****" in v or v.startswith("enc:"):
            # 脱敏值或已加密 → 保留 old
            continue
        # 真实新值 → 覆盖（_encrypt_binding 会加密）
        merged[k] = v
    return merged


def _encrypt_binding(binding: dict[str, Any]) -> dict[str, Any]:
    """加密绑定凭证对象里的敏感字段（写库前调用）。

    对 _SENSITIVE_FIELDS 里的字段值加 enc: 前缀加密。
    credential_type / auth_type / mcp_connection_id 等非敏感字段原样保留。
    """
    out = dict(binding)
    for k, v in binding.items():
        if k in _SENSITIVE_FIELDS and isinstance(v, str) and v and not v.startswith("enc:"):
            out[k] = "enc:" + encrypt_secret(v)
    return out


def _decrypt_binding(binding: dict[str, Any]) -> dict[str, Any]:
    """解密绑定凭证对象里的敏感字段（运行时兑换用）。

    剥掉 enc: 前缀后解密。解密失败（旧数据/窜改）原样返回，由上层决定降级。
    """
    out = dict(binding)
    for k, v in binding.items():
        if k in _SENSITIVE_FIELDS and isinstance(v, str) and v.startswith("enc:"):
            try:
                out[k] = decrypt_secret(v[4:])
            except Exception:
                # 解密失败保留原值（可能是历史明文数据）
                out[k] = v[4:] if v.startswith("enc:") else v
    return out


def _mask_binding(binding: dict[str, Any]) -> dict[str, Any]:
    """脱敏绑定凭证对象（API 响应用）。

    - _MASK_FIELDS 里的字段（password/token 等）：解密后脱敏
    - username：解密后明文返回（admin 需要看到用户名）
    - 其他字段：原值返回
    """
    out = dict(binding)
    for k, v in binding.items():
        if k in _SENSITIVE_FIELDS:
            # 先解密
            plaintext = v
            if isinstance(v, str) and v.startswith("enc:"):
                try:
                    plaintext = decrypt_secret(v[4:])
                except Exception:
                    plaintext = "***"
            # username 明文返回，其他敏感字段脱敏
            if k == "username":
                out[k] = plaintext
            elif k in _MASK_FIELDS:
                if isinstance(plaintext, str) and plaintext:
                    out[k] = mask_secret(plaintext)
                else:
                    out[k] = "***"
            else:
                out[k] = plaintext
    return out


class McpTokenCredentialService:
    """通用 token 记录 + MCP 绑定的业务服务层。"""

    COLLECTION = COLLECTION

    @staticmethod
    def _collection():
        return get_database()[COLLECTION]

    # ------------------------------------------------------------------
    # 创建
    # ------------------------------------------------------------------

    @staticmethod
    async def create_record(
        *,
        name: str,
        mcp_bindings: dict[str, dict[str, Any]] | None = None,
        api_key_id: str = "",
        created_by: str = "",
    ) -> tuple[dict, str]:
        """创建一条通用 token 记录。

        Args:
            name: 用户名称。
            mcp_bindings: 初始绑定（明文凭证）。key=mcp_connection_id。
            api_key_id: 归属接入方（可选）。
            created_by: 平台 admin user_id。

        Returns:
            (doc, token) —— doc 是入库后的记录（凭证已加密），token 是通用 token
            明文（仅此一次返回，调用方应交给 admin 下发给终端用户）。
        """
        token = _generate_token()
        now_iso = utc_now().isoformat()

        # 加密每个绑定的敏感字段
        encrypted_bindings: dict[str, dict[str, Any]] = {}
        for conn_id, binding in (mcp_bindings or {}).items():
            encrypted_bindings[conn_id] = _encrypt_binding(dict(binding))

        doc = {
            "_id": generate_id("mcptok"),
            "name": name,
            "token": token,
            "api_key_id": api_key_id,
            "status": McpTokenStatus.ACTIVE.value,
            "mcp_bindings": encrypted_bindings,
            "created_at": now_iso,
            "updated_at": now_iso,
            "created_by": created_by,
        }
        await McpTokenCredentialService._collection().insert_one(doc)
        logger.info(
            "mcp_token_created",
            record_id=doc["_id"],
            name=name,
            binding_count=len(encrypted_bindings),
        )
        return doc, token

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    @staticmethod
    async def get_record(record_id: str) -> dict | None:
        """按 _id 查记录（凭证保持加密态，由调用方按需解密）。"""
        return await McpTokenCredentialService._collection().find_one({"_id": record_id})

    @staticmethod
    async def list_records(
        page: int = 1,
        page_size: int = 20,
        name: str | None = None,
        status: str | None = None,
    ) -> tuple[list[dict], int]:
        """分页列表（凭证脱敏）。"""
        filter_query: dict = {}
        if name:
            from re import escape

            filter_query["name"] = {"$regex": escape(name), "$options": "i"}
        if status:
            filter_query["status"] = status

        col = McpTokenCredentialService._collection()
        total = await col.count_documents(filter_query)
        cursor = (
            col.find(filter_query)
            .sort("updated_at", -1)
            .skip((page - 1) * page_size)
            .limit(page_size)
        )
        items = await cursor.to_list(length=page_size)
        return [McpTokenCredentialService._to_masked(d) for d in items], total

    # ------------------------------------------------------------------
    # 本地校验（替代外部 introspection）
    # ------------------------------------------------------------------

    @staticmethod
    async def verify_token(token: str) -> dict | None:
        """本地校验通用 token。

        命中且 status=active 返回记录（凭证加密态）；否则返回 None。
        这是 get_api_key_principal 里 X-User-Token 校验的入口。
        """
        if not token:
            return None
        doc = await McpTokenCredentialService._collection().find_one({"token": token})
        if doc is None:
            return None
        if doc.get("status") != McpTokenStatus.ACTIVE.value:
            return None
        return doc

    # ------------------------------------------------------------------
    # 运行时兑换（供 UserCredentialResolver 调用）
    # ------------------------------------------------------------------

    @staticmethod
    async def resolve_binding(
        record_id: str,
        mcp_connection_id: str,
    ) -> dict[str, Any] | None:
        """查 + 解密某用户对某 MCP 的绑定凭证。

        返回解密后的凭证对象，如：
            token 型: {credential_type:'token', auth_type:'bearer', token:'xxx'}
            账密型:   {credential_type:'password', auth_type:'bearer', username:'u', password:'p'}
        未找到记录或未绑定该 MCP 返回 None。
        """
        doc = await McpTokenCredentialService.get_record(record_id)
        if doc is None:
            return None
        binding = (doc.get("mcp_bindings") or {}).get(mcp_connection_id)
        if not binding:
            return None
        return _decrypt_binding(dict(binding))

    # ------------------------------------------------------------------
    # 更新
    # ------------------------------------------------------------------

    @staticmethod
    async def update_record(
        record_id: str,
        *,
        name: str | None = None,
        status: str | None = None,
        mcp_bindings: dict[str, dict[str, Any]] | None = None,
    ) -> dict | None:
        """更新记录（全量替换 mcp_bindings 时，旧 session 缓存会被清除）。

        Args:
            mcp_bindings: 传入则全量替换（明文凭证，内部加密）。 None 表示不改。
        """
        existing = await McpTokenCredentialService._collection().find_one({"_id": record_id})
        if existing is None:
            return None

        set_fields: dict[str, Any] = {"updated_at": utc_now().isoformat()}
        if name is not None:
            set_fields["name"] = name
        if status is not None:
            set_fields["status"] = status
        if mcp_bindings is not None:
            # 字段级合并：前端编辑时未修改的凭证字段会传回脱敏值（含 ****）
            # 或空字符串，这些不应覆盖原值。只有真正改了的字段才更新。
            existing_bindings = existing.get("mcp_bindings") or {}
            encrypted: dict[str, dict[str, Any]] = {}
            for conn_id, new_binding in mcp_bindings.items():
                old = existing_bindings.get(conn_id) or {}
                merged = _merge_binding(old, new_binding)
                encrypted[conn_id] = _encrypt_binding(merged)
            # 删除前端没传的绑定（前端传的是全量列表，没传=已删除）
            set_fields["mcp_bindings"] = encrypted

        await McpTokenCredentialService._collection().update_one(
            {"_id": record_id}, {"$set": set_fields}
        )

        # 改绑/禁用 → 清该用户所有相关的账密 session 缓存
        await _clear_session_cache(record_id)

        logger.info("mcp_token_updated", record_id=record_id)
        return await McpTokenCredentialService.get_record(record_id)

    # ------------------------------------------------------------------
    # 轮换 / 删除
    # ------------------------------------------------------------------

    @staticmethod
    async def rotate_token(record_id: str) -> tuple[dict | None, str]:
        """轮换通用 token（旧 token 立即失效）。返回 (新记录, 新 token 明文)。"""
        existing = await McpTokenCredentialService._collection().find_one({"_id": record_id})
        if existing is None:
            return None, ""

        new_token = _generate_token()
        await McpTokenCredentialService._collection().update_one(
            {"_id": record_id},
            {"$set": {"token": new_token, "updated_at": utc_now().isoformat()}},
        )
        await _clear_session_cache(record_id)
        logger.info("mcp_token_rotated", record_id=record_id)
        doc = await McpTokenCredentialService.get_record(record_id)
        return doc, new_token

    @staticmethod
    async def delete_record(record_id: str) -> bool:
        """删除记录（吊销 token）。返回是否删除成功。"""
        existing = await McpTokenCredentialService._collection().find_one({"_id": record_id})
        if existing is None:
            return False
        await McpTokenCredentialService._collection().delete_one({"_id": record_id})
        await _clear_session_cache(record_id)
        logger.info("mcp_token_deleted", record_id=record_id)
        return True

    # ------------------------------------------------------------------
    # 脱敏响应
    # ------------------------------------------------------------------

    @staticmethod
    def _to_masked(doc: dict) -> dict:
        """转 API 响应（凭证脱敏，token 部分脱敏）。返回 id（无 alias）。"""
        bindings = doc.get("mcp_bindings") or {}
        masked_bindings = {
            conn_id: _mask_binding(dict(b)) for conn_id, b in bindings.items()
        }
        return {
            "id": doc["_id"],
            "name": doc.get("name", ""),
            "token": mask_secret(doc.get("token", "")),
            "api_key_id": doc.get("api_key_id", ""),
            "status": doc.get("status", McpTokenStatus.ACTIVE.value),
            "mcp_bindings": masked_bindings,
            "created_at": doc.get("created_at", ""),
            "updated_at": doc.get("updated_at", ""),
            "created_by": doc.get("created_by", ""),
        }


# ---------------------------------------------------------------------------
# 账密型 session 的 Redis 缓存（供 UserCredentialResolver 使用）
# ---------------------------------------------------------------------------


def _session_cache_key(record_id: str, conn_id: str) -> str:
    return f"{_SESSION_KEY_PREFIX}{record_id}:{conn_id}"


async def get_cached_session(record_id: str, conn_id: str) -> str | None:
    """从 Redis 取已缓存的 session token（账密型）。miss 返回 None。"""
    try:
        redis = await get_redis_client()
        return await redis.get(_session_cache_key(record_id, conn_id))
    except Exception as exc:
        logger.warning("mcp_session_cache_get_failed", error=str(exc))
        return None


async def set_cached_session(
    record_id: str, conn_id: str, session_token: str, ttl: int = 3600
) -> None:
    """写 session token 到 Redis（带 TTL）。"""
    try:
        redis = await get_redis_client()
        await redis.setex(_session_cache_key(record_id, conn_id), ttl, session_token)
    except Exception as exc:
        logger.warning("mcp_session_cache_set_failed", error=str(exc))


async def _clear_session_cache(record_id: str) -> int:
    """清除某用户所有 MCP 的 session 缓存（改绑/轮换/禁用/删除时调用）。

    用 SCAN 匹配 mcp:session:{record_id}:* 后批量删除。
    """
    try:
        redis = await get_redis_client()
        pattern = f"{_SESSION_KEY_PREFIX}{record_id}:*"
        deleted = 0
        async for key in redis.scan_iter(match=pattern, count=100):
            await redis.delete(key)
            deleted += 1
        if deleted:
            logger.debug("mcp_session_cache_cleared", record_id=record_id, count=deleted)
        return deleted
    except Exception as exc:
        logger.warning("mcp_session_cache_clear_failed", record_id=record_id, error=str(exc))
        return 0
