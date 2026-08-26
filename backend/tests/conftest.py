"""Shared pytest fixtures for the backend test suite."""
import os

# Ensure tests don't require a real DB / Redis during import-time config load
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-not-for-prod")
# Fixed 32-byte key (Base64) for channel/crypto tests that round-trip
# encrypt_secret → decrypt_secret. NOT for production — test fixture only.
os.environ.setdefault(
    "MODEL_ENCRYPTION_KEY",
    "ZDswO0/08pEWbyhmnBhMZ6L0Sf/esm7VlhjI0Mx8h6A=",
)

import pytest
from app.main import app
from app.workers.celery_app import celery_app
from fastapi.testclient import TestClient

# ── Celery eager mode for tests ──
# Run Celery tasks synchronously in-process during tests instead of
# dispatching them to a real Redis broker. This prevents test-triggered
# .delay() calls from leaking into a running worker (which would execute
# them against test data and pollute logs with "task not found" errors).
celery_app.conf.task_always_eager = True
celery_app.conf.task_eager_propagates = True


@pytest.fixture(autouse=True)
def _reset_motor_client_singleton():
    """每个测试前后重置 motor client 单例（跨 event-loop 复用根治）。

    AsyncIOMotorClient 创建时绑定当前 event loop，而 pytest-asyncio 为每个
    测试启用新 loop：某个测试真实触达 get_database() 后，单例就绑死在那个
    测试的 loop 上；测试结束 loop 关闭，后续测试复用同一单例即报
    "RuntimeError: Event loop is closed"（全量跑必现，曾长期污染
    test_chat_context_declaration_unchanged）。每测试重置让单例在需要时
    于当前 loop 上重建。
    """
    import contextlib

    import app.db.mongodb as mongodb_mod

    def _reset() -> None:
        if mongodb_mod._client is not None:
            with contextlib.suppress(Exception):
                mongodb_mod._client.close()
            mongodb_mod._client = None

    _reset()
    yield
    _reset()


@pytest.fixture
def client() -> TestClient:
    """A FastAPI TestClient that bypasses real network IO."""
    return TestClient(app)
