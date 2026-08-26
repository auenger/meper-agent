"""Rate limiter — Redis sliding window for API Key quotas."""
from __future__ import annotations

import time
import uuid

from loguru import logger

from app.db.redis import get_redis_client

# Key pattern: ratelimit:{api_key_id}
_KEY_PREFIX = "ratelimit"
_WINDOW_SECONDS = 60  # sliding window length
_WINDOW_TTL = 120  # seconds — auto-cleanup after 2 minutes

# 原子化滑动窗口：清理过期 → 计数 → 未超限才写入，全部在一次 Lua 执行内完成。
# 旧实现（pipeline zadd 后超限再 zrem）在并发下会短暂虚高计数，且 zset member
# 用 ``str(now)`` 在同微秒并发时互相覆盖；本脚本以 "now:uuid" 作 member 消除碰撞，
# 并返回真实的水位重置时间（最老条目 + 窗口长度）而非拍脑袋的 now+60。
#
# KEYS[1]=限流键 ARGV: now(float) / window / limit / member(唯一) / ttl
# 返回 {allowed(0|1), remaining, reset_ts}
_RATE_LIMIT_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
local ttl = tonumber(ARGV[5])

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)

local oldest
if count < limit then
    redis.call('ZADD', key, now, member)
    redis.call('EXPIRE', key, ttl)
    oldest = tonumber(redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')[2]) or now
    return {1, limit - count - 1, math.floor(oldest + window)}
end

redis.call('EXPIRE', key, ttl)
oldest = tonumber(redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')[2]) or now
return {0, 0, math.floor(oldest + window)}
"""


async def check_rate_limit(api_key_id: str, limit: int) -> tuple[bool, int, int]:
    """Check if a request is within the rate limit.

    Uses a Redis sorted set as a sliding window counter, with the
    trim/count/add sequence executed atomically in Lua.

    Args:
        api_key_id: The API Key ID to check.
        limit: Maximum requests per 60-second window.

    Returns:
        Tuple of (allowed, remaining, reset_timestamp).
        - allowed: True if the request is within the limit.
        - remaining: Number of requests remaining in the current window.
        - reset_timestamp: Unix timestamp when the current window resets.
    """
    redis = await get_redis_client()
    now = time.time()
    member = f"{now}:{uuid.uuid4().hex[:12]}"  # unique even under same-microsecond concurrency
    key = f"{_KEY_PREFIX}:{api_key_id}"

    allowed, remaining, reset_ts = await redis.eval(
        _RATE_LIMIT_LUA, 1, key, now, _WINDOW_SECONDS, limit, member, _WINDOW_TTL,
    )

    if not allowed:
        logger.info(
            "rate_limit_exceeded",
            api_key_id=api_key_id,
            limit=limit,
        )

    return bool(allowed), int(remaining), int(reset_ts)
