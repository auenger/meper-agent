"""Path-scoped CORS: permissive only where embeds require it, whitelist elsewhere.

背景（收敛自原 main.py 的 CORS 注释）：``/api/v1/ext/*`` 是给第三方站点
iframe 嵌入用的公开接口，接入方 origin 不可预知，必须放开；但把整个 API
面都配置成 ``allow_origin_regex=".*" + allow_credentials=True`` 等于全域
放开带凭据跨域。此中间件按路径二分：

- ``/api/v1/ext/*`` → 正则放开（任意 origin，带凭据）
- 其余路径 → ``CORS_ORIGINS`` 白名单（逗号分隔）

实现为纯 ASGI 中间件（非 BaseHTTPMiddleware），内部组装两个
``CORSMiddleware`` 实例按路径分发——不用继承/复制 Starlette 的 CORS 逻辑，
也避免 BaseHTTPMiddleware 包装对 SSE 流式响应的干扰。
"""
from __future__ import annotations

from typing import Any

from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

#: 响应中透出的跨域可读 header（与原 CORS 配置保持一致）。
_EXPOSE_HEADERS = [
    "X-Request-ID",
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Reset",
]

#: 放开的路径前缀（对外公开 API，第三方嵌入）。
_EXT_PREFIX = "/api/v1/ext/"


class ScopedCorsMiddleware:
    """Path-scoped CORS 分发器：ext 公开面放开，其余走白名单。"""

    def __init__(self, app: ASGIApp, allow_origins: list[str]) -> None:
        common: dict[str, Any] = {
            "allow_credentials": True,
            "allow_methods": ["*"],
            "allow_headers": ["*"],
            "expose_headers": _EXPOSE_HEADERS,
        }
        self._ext_cors = CORSMiddleware(app, allow_origin_regex=".*", **common)
        self._default_cors = CORSMiddleware(app, allow_origins=allow_origins, **common)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and str(scope.get("path", "")).startswith(_EXT_PREFIX):
            await self._ext_cors(scope, receive, send)
        else:
            await self._default_cors(scope, receive, send)


def parse_cors_origins(raw: str) -> list[str]:
    """解析 CORS_ORIGINS 配置（逗号分隔）；``*`` 保留原样交给 Starlette 处理。"""
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


__all__ = ["ScopedCorsMiddleware", "parse_cors_origins"]
