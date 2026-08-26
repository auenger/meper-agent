"""Tests for services/role_service.py — system role defaults and the
application-permissions backfill migration."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.services.role_service import (
    _BACKFILL_V1_MARKER,
    _BACKFILL_V1_TARGETS,
    _BACKFILL_V3_TARGETS,
    DEFAULT_SYSTEM_ROLE_PERMISSIONS,
    RoleService,
)


class TestDefaultSystemRolePermissions:
    """Guard: application permissions must stay in the admin/developer
    defaults — they were once missing, leaving even admins without the
    "create application" button."""

    def test_admin_has_application_permissions(self) -> None:
        assert "application:read" in DEFAULT_SYSTEM_ROLE_PERMISSIONS["admin"]
        assert "application:write" in DEFAULT_SYSTEM_ROLE_PERMISSIONS["admin"]

    def test_developer_has_application_permissions(self) -> None:
        assert "application:read" in DEFAULT_SYSTEM_ROLE_PERMISSIONS["developer"]
        assert "application:write" in DEFAULT_SYSTEM_ROLE_PERMISSIONS["developer"]

    def test_developer_has_tool_mcp_write_permissions(self) -> None:
        """RBAC 迁移回归守卫：developer 必须保留工具/MCP 管理能力。"""
        assert "tool:write" in DEFAULT_SYSTEM_ROLE_PERMISSIONS["developer"]
        assert "mcp:write" in DEFAULT_SYSTEM_ROLE_PERMISSIONS["developer"]

    def test_operator_viewer_have_tool_mcp_read_permissions(self) -> None:
        """平台资源只读浏览对全员开放：operator/viewer 可读工具与 MCP。"""
        for role in ("operator", "viewer"):
            assert "tool:read" in DEFAULT_SYSTEM_ROLE_PERMISSIONS[role]
            assert "mcp:read" in DEFAULT_SYSTEM_ROLE_PERMISSIONS[role]

    def test_v3_backfill_targets_are_subset_of_defaults(self) -> None:
        """v3 backfill targets 不得超出新默认矩阵授予的权限，
        否则迁移会把权限补到矩阵不再承诺的范围。"""
        for role, perms in _BACKFILL_V3_TARGETS.items():
            defaults = set(DEFAULT_SYSTEM_ROLE_PERMISSIONS[role])
            assert set(perms) <= defaults


@pytest.fixture
def mock_role_collections():
    """Mock the roles + schema_migrations collections used by the backfill."""
    with (
        patch.object(RoleService, "_collection") as mock_roles,
        patch("app.services.role_service.get_database") as mock_get_db,
        patch.object(RoleService, "invalidate_cache", new=AsyncMock()) as mock_invalidate,
    ):
        roles_col = MagicMock()
        roles_col.update_one = AsyncMock()
        mock_roles.return_value = roles_col

        markers_col = MagicMock()
        markers_col.find_one = AsyncMock(return_value=None)
        markers_col.update_one = AsyncMock()
        db = MagicMock()
        db.__getitem__.return_value = markers_col
        mock_get_db.return_value = db

        yield roles_col, markers_col, mock_invalidate


class TestBackfillSystemRolePermissions:
    async def test_first_run_patches_missing_permissions(
        self, mock_role_collections
    ) -> None:
        """Stale roles get $addToSet with the application permission keys,
        the marker is recorded, and their Redis caches are invalidated."""
        roles_col, markers_col, mock_invalidate = mock_role_collections
        roles_col.update_one.return_value = MagicMock(modified_count=1)

        await RoleService.backfill_system_role_permissions()

        assert roles_col.update_one.await_count == len(_BACKFILL_V1_TARGETS)
        for call in roles_col.update_one.await_args_list:
            query, update = call.args[0], call.args[1]
            assert query["name"] in _BACKFILL_V1_TARGETS
            assert query["role_type"] == "system"
            assert update["$addToSet"]["permissions"]["$each"] == [
                "application:read",
                "application:write",
            ]

        markers_col.update_one.assert_awaited_once()
        assert markers_col.update_one.await_args.args[0] == {"_id": _BACKFILL_V1_MARKER}
        assert mock_invalidate.await_count == len(_BACKFILL_V1_TARGETS)

    async def test_second_run_is_a_noop(self, mock_role_collections) -> None:
        """With the marker present, nothing is written — permissions an
        admin deliberately removed must survive restarts."""
        roles_col, markers_col, mock_invalidate = mock_role_collections
        markers_col.find_one = AsyncMock(return_value={"_id": _BACKFILL_V1_MARKER})

        await RoleService.backfill_system_role_permissions()

        roles_col.update_one.assert_not_awaited()
        markers_col.update_one.assert_not_awaited()
        mock_invalidate.assert_not_awaited()

    async def test_marker_written_even_when_already_patched(
        self, mock_role_collections
    ) -> None:
        """Fresh installs already carry the defaults (modified_count=0) —
        the marker is still recorded so the scan doesn't repeat on every
        startup, and no cache invalidation happens."""
        roles_col, markers_col, mock_invalidate = mock_role_collections
        roles_col.update_one.return_value = MagicMock(modified_count=0)

        await RoleService.backfill_system_role_permissions()

        markers_col.update_one.assert_awaited_once()
        mock_invalidate.assert_not_awaited()
