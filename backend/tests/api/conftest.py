"""Fixtures for API unit tests — permission resolution without IO.

``require_permission`` 端点在单测环境（无 Redis/Mongo）下走 RoleService 三级
回退时，Mongo server-selection 超时会让每个用例挂起数十秒。这里把
``app.core.security.get_role_permissions`` 替换为纯内存实现（同
``DEFAULT_SYSTEM_ROLE_PERMISSIONS`` 硬编码默认矩阵），保持快速且确定性。

需要自定义角色权限的用例可直接请求 ``_perm_overrides`` fixture 并写入
``role → set(permission)`` 映射（优先于默认矩阵）。
"""
import pytest


@pytest.fixture(autouse=True)
def _perm_overrides(monkeypatch: pytest.MonkeyPatch) -> dict[str, set[str]]:
    from app.core import security
    from app.services.role_service import DEFAULT_SYSTEM_ROLE_PERMISSIONS

    overrides: dict[str, set[str]] = {}

    async def _fake_get_role_permissions(role_name: str) -> set[str]:
        if role_name in overrides:
            return set(overrides[role_name])
        return set(DEFAULT_SYSTEM_ROLE_PERMISSIONS.get(role_name, set()))

    monkeypatch.setattr(security, "get_role_permissions", _fake_get_role_permissions)
    yield overrides
