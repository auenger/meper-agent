"""FastAPI application entry point."""
import asyncio
import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.middleware.exception_mw import ExceptionMiddleware
from app.api.middleware.logging_mw import LoggingMiddleware
from app.api.middleware.request_id import RequestIDMiddleware
from app.api.middleware.scoped_cors import ScopedCorsMiddleware, parse_cors_origins
from app.api.v1.ext import ExtApiStatsMiddleware
from app.api.v1.router import api_v1_router
from app.core.bootstrap import background_boot, init_critical_path, shutdown
from app.core.config import settings
from app.core.logging import setup_logging

# Initialize structured logging before app creation
setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup/shutdown lifecycle for external connections.

    Startup is split into:
    1. **Critical path** (awaited): checkpointer, notification, event bridge.
    2. **Background boot** (create_task): indexes, schedulers, recovery,
       channel connections — deferred so the first request isn't blocked.
    """
    # Critical path — must complete before serving requests.
    await init_critical_path()

    # Inject MCP credential resolver into harness（统一凭证兑换）。
    # harness 保持无 DB 依赖，app 层在此注入实现。外部路径（/ext/*）的 MCP
    # 调用会经兑换器按用户绑定凭证；内部路径（JWT 测试）不受影响。
    from agent_flow_harness.mcp.loader import set_credential_resolver

    from app.engine.mcp.user_credential_resolver import UserCredentialResolver

    set_credential_resolver(UserCredentialResolver())

    # Background boot — indexes/schedulers/recovery/channels.
    # Store the task reference on app.state to prevent GC, and to allow
    # graceful shutdown ordering (cancel before closing DB clients).
    boot_task = asyncio.create_task(background_boot())
    app.state._boot_task = boot_task

    # Capture schedulers when background boot completes (best-effort;
    # if it hasn't finished by shutdown, we cancel it).
    app.state._scheduler = None
    app.state._trigger_scheduler = None

    async def _capture_schedulers():
        try:
            scheduler, trigger_scheduler = await boot_task
            app.state._scheduler = scheduler
            app.state._trigger_scheduler = trigger_scheduler
        except Exception as exc:
            from loguru import logger
            logger.error("background_boot_failed error={}", exc)

    asyncio.create_task(_capture_schedulers())

    yield

    # Shutdown — cancel background boot if still running, then graceful stop.
    if not boot_task.done():
        boot_task.cancel()
    await shutdown(app.state._scheduler, app.state._trigger_scheduler)


app = FastAPI(
    title=settings.APP_NAME,
    description="MEPER Agent - AI Agent orchestration platform",
    version="0.1.0",
    docs_url="/api/v1/docs",
    redoc_url="/api/v1/redoc",
    openapi_url="/api/v1/openapi.json",
    lifespan=lifespan,
)

# Middleware 顺序（Starlette 语义：**最后** add 的在最外层、请求时最先执行）。
# 按 add 顺序 = 内→外书写，实际请求链为：
#   ScopedCors → RequestID → Exception → ExtApiStats → Logging → 路由
# 设计理由：
# - CORS 最外：preflight 短路；错误信封也能带上 CORS 头（浏览器才读得到）。
# - RequestID 次外：请求进入即分配 id；错误信封响应会带上 X-Request-ID。
# - Exception 居中兜底：捕获所有内层中间件与路由的异常，统一错误信封。
# - Logging 最内：完整覆盖内层耗时；能观察到原始异常（error_raised 分支）。
app.add_middleware(LoggingMiddleware)
app.add_middleware(ExtApiStatsMiddleware)
app.add_middleware(ExceptionMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    ScopedCorsMiddleware,
    allow_origins=parse_cors_origins(settings.CORS_ORIGINS),
)

# API routes
app.include_router(api_v1_router, prefix="/api/v1")

# Agent 头像静态目录（无鉴权读取，<img src> 直接用；上传端点仍鉴权）。
_avatars_dir = pathlib.Path(settings.AVATARS_CONTAINER_DIR)
_avatars_dir.mkdir(parents=True, exist_ok=True)
app.mount("/api/v1/agent-avatars", StaticFiles(directory=str(_avatars_dir)), name="agent-avatars")

# Skill (Tool) 头像静态目录（同上，独立目录避免与 Agent 混淆）。
_skill_avatars_dir = pathlib.Path(settings.SKILL_AVATARS_CONTAINER_DIR)
_skill_avatars_dir.mkdir(parents=True, exist_ok=True)
app.mount("/api/v1/skill-avatars", StaticFiles(directory=str(_skill_avatars_dir)), name="skill-avatars")


@app.get("/", tags=["root"])
async def root() -> dict[str, str]:
    """Root endpoint - redirects users to docs."""
    return {
        "message": f"Welcome to {settings.APP_NAME}",
        "docs": "/api/v1/docs",
        "redoc": "/api/v1/redoc",
    }


@app.get("/health", tags=["health"])
async def health_check() -> dict[str, str]:
    """Liveness probe (also exposed at /api/v1/health for consistency)."""
    return {"status": "ok"}
