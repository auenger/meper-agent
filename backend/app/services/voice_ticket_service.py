"""Voice WS ticket — one-time, short-TTL credential exchange.

浏览器 WS 无法自定义 header，af_live_ key / 接入方 user_token 不能进 URL
query（生产 uvicorn access log 与 Caddy 均记录完整 URI，长命凭证会落日志）。
外部嵌入端先经标准 header 鉴权调 POST /api/v1/ext/voice/ticket 换取短命
票据，再以 ``?ticket=`` 连接语音 WS。GETDEL 保证单次使用，TTL 兜底。
"""
from __future__ import annotations

import json
import secrets

from loguru import logger

from app.core.auth_apikey import ApiKeyPrincipal
from app.db.redis import get_redis_client

_TICKET_PREFIX = "voice:ticket"
TICKET_TTL = 60  # seconds — ticket validity window

# 票据中携带的 principal 字段（WS 侧需要完整授权上下文：限流/绑定/scope、
# 凭证复查的 introspect_url + user_token、MCP 兑换的 user_token）
_TICKET_FIELDS = (
    "key_id",
    "owner_user_id",
    "scopes",
    "bindings",
    "rate_limit",
    "user_id",
    "token_record_id",
    "user_token",
    "app_id",
    "introspect_url",
)


async def issue_ticket(principal: ApiKeyPrincipal) -> str:
    """Mint a single-use ticket carrying the authenticated principal."""
    ticket = secrets.token_urlsafe(32)
    payload = {name: getattr(principal, name) for name in _TICKET_FIELDS}
    redis = await get_redis_client()
    await redis.set(
        f"{_TICKET_PREFIX}:{ticket}",
        json.dumps(payload, ensure_ascii=False),
        ex=TICKET_TTL,
        nx=True,
    )
    return ticket


async def consume_ticket(ticket: str) -> ApiKeyPrincipal | None:
    """Atomically redeem a ticket (GETDEL = single use). None if invalid."""
    if not ticket:
        return None
    redis = await get_redis_client()
    raw = await redis.getdel(f"{_TICKET_PREFIX}:{ticket}")
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        principal = ApiKeyPrincipal(
            **{name: payload.get(name) for name in _TICKET_FIELDS}
        )
        if not principal.user_id:
            return None
        return principal
    except (ValueError, TypeError) as e:
        logger.warning("voice_ticket_malformed", error=str(e))
        return None
