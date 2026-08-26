"""Tests for UserService admin operations — list, create, update, delete, reset password."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.core.errors import ForbiddenError, ValidationError
from app.services.user_service import UserService


class AsyncIterator:
    """Wrap items in an async iterator (for cursor mock)."""

    def __init__(self, items):
        self._items = items

    async def to_list(self, length):
        return self._items[:length]


class MockMongoCursor:
    """Motor cursor mock — find() returns this sync."""

    def __init__(self, items):
        self._items = items

    def sort(self, key, direction):
        return self

    def skip(self, n):
        return self

    def limit(self, n):
        return self

    async def to_list(self, length):
        return self._items[:length]


@pytest.fixture
def mock_collection():
    """Mock the async MongoDB users collection."""
    with patch.object(UserService, "_collection") as mock:
        col = MagicMock()
        col.find_one = AsyncMock()
        col.find = MagicMock()
        col.insert_one = AsyncMock()
        col.update_one = AsyncMock()
        col.delete_one = AsyncMock()
        col.count_documents = AsyncMock()
        mock.return_value = col
        yield col


def _make_admin_doc(_id="user_01HADMIN", username="admin", status="active", is_super_admin=False):
    return {
        "_id": _id,
        "username": username,
        "email": f"{username}@example.com",
        "password_hash": "$2b$12$hash",
        "role": "admin",
        "status": status,
        "is_super_admin": is_super_admin,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
        "last_login_at": None,
    }


def _make_user_doc(_id="user_01HDEV", username="dev", role="developer", status="active"):
    return {
        "_id": _id,
        "username": username,
        "email": f"{username}@example.com",
        "password_hash": "$2b$12$hash",
        "role": role,
        "status": status,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
        "last_login_at": None,
    }


class TestListUsers:
    """AC1: Admin can list users with pagination and filtering."""

    async def test_list_all_users(self, mock_collection) -> None:
        """Returns paginated user list."""
        docs = [_make_admin_doc(), _make_user_doc()]
        mock_collection.count_documents.return_value = 2
        mock_collection.find.return_value = MockMongoCursor(docs)

        items, total = await UserService.list_users(page=1, page_size=20)

        assert total == 2
        assert len(items) == 2
        mock_collection.count_documents.assert_called_once_with({})

    async def test_list_users_with_filters(self, mock_collection) -> None:
        """Filters by username, role, and status."""
        mock_collection.count_documents.return_value = 1
        mock_collection.find.return_value = MockMongoCursor([_make_user_doc()])

        items, total = await UserService.list_users(
            page=1, page_size=20, role="developer", status="active"
        )

        assert total == 1
        # Verify filter was passed
        call_kwargs = mock_collection.count_documents.call_args[0][0]
        assert call_kwargs.get("role") == "developer"
        assert call_kwargs.get("status") == "active"

    async def test_list_users_empty(self, mock_collection) -> None:
        """Returns empty list when no users match."""
        mock_collection.count_documents.return_value = 0
        mock_collection.find.return_value = MockMongoCursor([])

        items, total = await UserService.list_users(page=1, page_size=20)

        assert total == 0
        assert len(items) == 0

    async def test_list_users_escapes_regex_injection(self, mock_collection) -> None:
        """问题1：username 搜索的 regex 特殊字符必须被转义。

        输入 ``.*`` 时不能拼成匹配所有文档的 ``$regex: ".*"``，
        而应转义为 ``\\.*``（字面匹配）。
        """
        mock_collection.count_documents.return_value = 0
        mock_collection.find.return_value = MockMongoCursor([])

        await UserService.list_users(page=1, page_size=20, username=".*")

        call_kwargs = mock_collection.count_documents.call_args[0][0]
        regex_filter = call_kwargs["username"]
        # 转义后应包含反斜杠；原始 ".*" 不会匹配任意内容
        assert regex_filter["$regex"] == r"\.\*"
        assert regex_filter["$options"] == "i"


class TestCreateUserByAdmin:
    """AC2: Admin can create users."""

    async def test_create_user_success(self, mock_collection) -> None:
        """Creates a user successfully."""
        mock_collection.find_one.return_value = None  # no conflicts
        mock_collection.insert_one.return_value = AsyncMock()

        doc = await UserService.create_user_by_admin(
            username="newuser",
            email="new@example.com",
            password="Strong1234",
            role="developer",
        )

        assert doc["username"] == "newuser"
        assert doc["role"] == "developer"
        assert doc["status"] == "active"
        assert "password_hash" in doc
        mock_collection.insert_one.assert_called_once()

    async def test_create_user_username_conflict(self, mock_collection) -> None:
        """Raises ValidationError on duplicate username."""
        mock_collection.find_one.side_effect = [
            {"_id": "existing", "username": "newuser"},
            None,
        ]

        with pytest.raises(ValidationError) as exc:
            await UserService.create_user_by_admin(
                username="newuser",
                email="new@example.com",
                password="Strong1234",
                role="developer",
            )
        assert exc.value.code == "USERNAME_CONFLICT"

    async def test_create_user_email_conflict(self, mock_collection) -> None:
        """Raises ValidationError on duplicate email."""
        mock_collection.find_one.side_effect = [
            None,
            {"_id": "existing", "email": "new@example.com"},
        ]

        with pytest.raises(ValidationError) as exc:
            await UserService.create_user_by_admin(
                username="newuser",
                email="new@example.com",
                password="Strong1234",
                role="developer",
            )
        assert exc.value.code == "EMAIL_CONFLICT"

    async def test_create_user_weak_password(self, mock_collection) -> None:
        """Raises ValidationError on weak password."""
        with pytest.raises(ValidationError) as exc:
            await UserService.create_user_by_admin(
                username="newuser",
                email="new@example.com",
                password="weak",
                role="developer",
            )
        assert "PASSWORD" in exc.value.code


class TestUpdateUser:
    """AC3: Admin can update user info."""

    async def test_update_role(self, mock_collection) -> None:
        """Updates user role successfully."""
        mock_collection.find_one.return_value = _make_user_doc()

        result = await UserService.update_user(
            user_id="user_01HDEV",
            updates={"role": "operator"},
            current_user_id="user_01HADMIN",
        )

        assert result is not None
        mock_collection.update_one.assert_called_once()

    async def test_update_user_not_found(self, mock_collection) -> None:
        """Raises NotFoundError when user doesn't exist."""
        mock_collection.find_one.return_value = None

        result = await UserService.update_user(
            user_id="user_nonexistent",
            updates={"role": "operator"},
            current_user_id="user_01HADMIN",
        )
        assert result is None

    async def test_cannot_demote_self(self, mock_collection) -> None:
        """Cannot demote own admin role (permission suicide)."""
        mock_collection.find_one.return_value = _make_admin_doc(
            _id="user_01HADMIN", username="admin"
        )

        with pytest.raises(ValidationError) as exc:
            await UserService.update_user(
                user_id="user_01HADMIN",
                updates={"role": "developer"},
                current_user_id="user_01HADMIN",
            )
        assert "self" in exc.value.code.lower() or "PERMISSION" in exc.value.code

    async def test_cannot_disable_last_admin(self, mock_collection) -> None:
        """Cannot disable the last admin (acting as super admin)."""
        # Only one admin in the system
        mock_collection.find_one.return_value = _make_admin_doc()
        mock_collection.count_documents.return_value = 1

        with pytest.raises(ValidationError) as exc:
            await UserService.update_user(
                user_id="user_01HADMIN",
                updates={"status": "disabled"},
                current_user_id="user_02HANOTHER",
                acting_is_super_admin=True,
            )
        assert "LAST_ADMIN" in exc.value.code


class TestDeleteUser:
    """AC4: Admin can delete users."""

    async def test_delete_user_success(self, mock_collection) -> None:
        """Deletes a user successfully."""
        mock_collection.find_one.return_value = _make_user_doc()
        mock_collection.delete_one.return_value = MagicMock(deleted_count=1)

        result = await UserService.delete_user(
            user_id="user_01HDEV",
            current_user_id="user_01HADMIN",
        )
        assert result is True

    async def test_cannot_delete_self(self, mock_collection) -> None:
        """Cannot delete own account."""
        with pytest.raises(ValidationError) as exc:
            await UserService.delete_user(
                user_id="user_01HADMIN",
                current_user_id="user_01HADMIN",
            )
        assert "SELF" in exc.value.code

    async def test_cannot_delete_last_admin(self, mock_collection) -> None:
        """Cannot delete the last admin (acting as super admin)."""
        mock_collection.find_one.return_value = _make_admin_doc()
        mock_collection.count_documents.return_value = 1

        with pytest.raises(ValidationError) as exc:
            await UserService.delete_user(
                user_id="user_01HADMIN",
                current_user_id="user_02HANOTHER",
                acting_is_super_admin=True,
            )
        assert "LAST_ADMIN" in exc.value.code

    async def test_delete_user_not_found(self, mock_collection) -> None:
        """Returns False when user not found."""
        mock_collection.find_one.return_value = None

        result = await UserService.delete_user(
            user_id="user_nonexistent",
            current_user_id="user_01HADMIN",
        )
        assert result is False


class TestResetPassword:
    """AC5: Admin can reset user password."""

    async def test_reset_password_success(self, mock_collection) -> None:
        """Resets password successfully."""
        mock_collection.find_one.return_value = _make_user_doc()
        mock_collection.update_one.return_value = AsyncMock()
        mock_collection.update_one.return_value.modified_count = 1

        result = await UserService.reset_password(
            user_id="user_01HDEV",
            new_password="NewStrong5678",
        )
        assert result is True
        mock_collection.update_one.assert_called_once()

    async def test_reset_password_weak(self, mock_collection) -> None:
        """Rejects weak password."""
        with pytest.raises(ValidationError):
            await UserService.reset_password(
                user_id="user_01HDEV",
                new_password="weak",
            )

    async def test_reset_password_user_not_found(self, mock_collection) -> None:
        """Returns False when user not found."""
        mock_collection.find_one.return_value = None

        result = await UserService.reset_password(
            user_id="user_nonexistent",
            new_password="NewStrong5678",
        )
        assert result is False


class TestSuperAdminGuards:
    """Super-admin guards — a regular admin cannot manage other admins."""

    async def test_regular_admin_cannot_disable_other_admin(self, mock_collection) -> None:
        """Non-super admin disabling another admin → SUPER_ADMIN_REQUIRED."""
        mock_collection.find_one.return_value = _make_admin_doc()

        with pytest.raises(ForbiddenError) as exc:
            await UserService.update_user(
                user_id="user_01HADMIN",
                updates={"status": "disabled"},
                current_user_id="user_02HANOTHER",
            )
        assert exc.value.code == "SUPER_ADMIN_REQUIRED"

    async def test_regular_admin_cannot_delete_other_admin(self, mock_collection) -> None:
        """Non-super admin deleting another admin → SUPER_ADMIN_REQUIRED."""
        mock_collection.find_one.return_value = _make_admin_doc()

        with pytest.raises(ForbiddenError) as exc:
            await UserService.delete_user(
                user_id="user_01HADMIN",
                current_user_id="user_02HANOTHER",
            )
        assert exc.value.code == "SUPER_ADMIN_REQUIRED"

    async def test_regular_admin_cannot_reset_other_admin_password(self, mock_collection) -> None:
        """Non-super admin resetting another admin's password → SUPER_ADMIN_REQUIRED
        (prevents account takeover via password reset)."""
        mock_collection.find_one.return_value = _make_admin_doc()

        with pytest.raises(ForbiddenError) as exc:
            await UserService.reset_password(
                user_id="user_01HADMIN",
                new_password="NewStrong5678",
                current_user_id="user_02HANOTHER",
            )
        assert exc.value.code == "SUPER_ADMIN_REQUIRED"

    async def test_any_admin_can_reset_own_password(self, mock_collection) -> None:
        """Resetting one's OWN password is always allowed (self is exempt from
        the super-admin guard) — super admin and regular admin alike."""
        mock_collection.find_one.return_value = _make_admin_doc(
            _id="user_01HADMIN", is_super_admin=True
        )

        # Super admin resetting own password.
        result = await UserService.reset_password(
            user_id="user_01HADMIN",
            new_password="NewStrong5678",
            current_user_id="user_01HADMIN",
            acting_is_super_admin=True,
        )
        assert result is True

        # Regular admin resetting own password.
        mock_collection.find_one.return_value = _make_admin_doc(
            _id="user_02HANOTHER", is_super_admin=False
        )
        result = await UserService.reset_password(
            user_id="user_02HANOTHER",
            new_password="NewStrong5678",
            current_user_id="user_02HANOTHER",
            acting_is_super_admin=False,
        )
        assert result is True

    async def test_regular_admin_cannot_promote_to_admin(self, mock_collection) -> None:
        """Non-super admin promoting anyone to admin → SUPER_ADMIN_REQUIRED."""
        mock_collection.find_one.return_value = _make_user_doc()

        with pytest.raises(ForbiddenError) as exc:
            await UserService.update_user(
                user_id="user_01HDEV",
                updates={"role": "admin"},
                current_user_id="user_02HANOTHER",
            )
        assert exc.value.code == "SUPER_ADMIN_REQUIRED"

    async def test_super_admin_can_disable_other_admin(self, mock_collection) -> None:
        """Super admin CAN manage other admins (with last-admin protection)."""
        mock_collection.find_one.return_value = _make_admin_doc()
        mock_collection.count_documents.return_value = 2  # 2 admins, 1 super

        result = await UserService.update_user(
            user_id="user_01HADMIN",
            updates={"status": "disabled"},
            current_user_id="user_09HSUPER",
            acting_is_super_admin=True,
        )
        assert result is not None
        mock_collection.update_one.assert_called_once()

    async def test_cannot_disable_last_super_admin(self, mock_collection) -> None:
        """The only super admin cannot be disabled even by another (super) admin."""
        mock_collection.find_one.return_value = _make_admin_doc(is_super_admin=True)
        # count_documents({"is_super_admin": True}) → 1 (the last one)
        mock_collection.count_documents.return_value = 1

        with pytest.raises(ValidationError) as exc:
            await UserService.update_user(
                user_id="user_01HADMIN",
                updates={"status": "disabled"},
                current_user_id="user_09HSUPER",
                acting_is_super_admin=True,
            )
        assert exc.value.code == "LAST_SUPER_ADMIN_PROTECTED"

    async def test_cannot_delete_last_super_admin(self, mock_collection) -> None:
        """The only super admin cannot be deleted."""
        mock_collection.find_one.return_value = _make_admin_doc(is_super_admin=True)
        # First count_documents call: super admin count → 1 (the last one)
        mock_collection.count_documents.return_value = 1

        with pytest.raises(ValidationError) as exc:
            await UserService.delete_user(
                user_id="user_01HADMIN",
                current_user_id="user_09HSUPER",
                acting_is_super_admin=True,
            )
        assert exc.value.code == "LAST_SUPER_ADMIN_PROTECTED"


class TestSuperAdminBackfill:
    """Bootstrap backfill — earliest admin becomes super admin when none exists."""

    async def test_backfills_earliest_admin_when_none_exists(self, mock_collection) -> None:
        mock_collection.count_documents.return_value = 0  # no super admin
        mock_collection.find_one.return_value = _make_admin_doc()

        await UserService.ensure_super_admin_backfill()

        mock_collection.update_one.assert_called_once_with(
            {"_id": "user_01HADMIN"}, {"$set": {"is_super_admin": True}}
        )

    async def test_noop_when_super_admin_exists(self, mock_collection) -> None:
        mock_collection.count_documents.return_value = 1

        await UserService.ensure_super_admin_backfill()

        mock_collection.update_one.assert_not_called()

    async def test_noop_when_no_admins(self, mock_collection) -> None:
        mock_collection.count_documents.return_value = 0
        mock_collection.find_one.return_value = None

        await UserService.ensure_super_admin_backfill()

        mock_collection.update_one.assert_not_called()
