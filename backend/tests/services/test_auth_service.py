"""Tests for services/auth_service.py — login, refresh, account lockout, password change, logout."""
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from app.core.errors import UnauthorizedError, ValidationError
from app.core.security import create_access_token, create_refresh_token
from app.services.auth_service import AuthService


@pytest.fixture
def mock_redis():
    """Mock async Redis client."""
    with patch("app.services.auth_service.get_redis_client") as mock_get:
        redis = AsyncMock()
        redis.get.return_value = None  # default: key not found
        mock_get.return_value = redis
        yield redis


@pytest.fixture
def mock_user_service():
    """Mock UserService lookups."""
    with patch("app.services.auth_service.UserService") as mock_svc:
        mock_svc.get_user_by_username = AsyncMock()
        mock_svc.get_user_by_id = AsyncMock()
        mock_svc.update_last_login = AsyncMock()
        # _collection() returns an object with async update_one
        mock_collection = AsyncMock()
        mock_svc._collection.return_value = mock_collection
        yield mock_svc


def _make_user_doc(
    username="admin",
    status="active",
    role="admin",
    password_hash=None,
):
    """Build a fake MongoDB user document."""
    from app.core.security import hash_password

    return {
        "_id": "user_01HTEST",
        "username": username,
        "email": f"{username}@example.com",
        "password_hash": password_hash or hash_password("Strong1234"),
        "role": role,
        "status": status,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "last_login_at": None,
    }


class TestLogin:
    """AC1 + AC5 + AC6: Login success / failure / security."""

    async def test_login_success(self, mock_redis, mock_user_service) -> None:
        """AC1: Valid credentials return tokens and update last_login_at."""
        mock_redis.get.return_value = None  # not locked
        mock_user_service.get_user_by_username.return_value = _make_user_doc()

        result = await AuthService.login("admin", "Strong1234")

        assert result.access_token
        assert result.refresh_token
        assert result.token_type == "bearer"
        assert result.expires_in == 900
        mock_user_service.update_last_login.assert_called_once_with("user_01HTEST")
        # Success clears failed counter
        mock_redis.delete.assert_called_once()

    async def test_login_user_not_found(self, mock_redis, mock_user_service) -> None:
        """AC5: Unknown username returns INVALID_CREDENTIALS (no info leak)."""
        mock_redis.get.return_value = None
        mock_user_service.get_user_by_username.return_value = None

        with pytest.raises(UnauthorizedError) as exc:
            await AuthService.login("nobody", "whatever")
        assert exc.value.code == "INVALID_CREDENTIALS"

    async def test_login_wrong_password(
        self, mock_redis, mock_user_service
    ) -> None:
        """AC5: Wrong password returns INVALID_CREDENTIALS + increments failure."""
        mock_redis.get.return_value = None  # not locked
        mock_redis.incr.return_value = 1
        mock_user_service.get_user_by_username.return_value = _make_user_doc()

        with pytest.raises(UnauthorizedError) as exc:
            await AuthService.login("admin", "WrongPassword1")
        assert exc.value.code == "INVALID_CREDENTIALS"
        # Failed attempt recorded
        mock_redis.incr.assert_called_once()
        mock_redis.expire.assert_called_once()

    async def test_login_locked_account(self, mock_redis, mock_user_service) -> None:
        """AC2: Locked account returns ACCOUNT_LOCKED even with correct password."""
        mock_redis.get.return_value = b"1"  # locked
        mock_redis.ttl.return_value = 600  # 10 min remaining

        with pytest.raises(UnauthorizedError) as exc:
            await AuthService.login("admin", "Strong1234")
        assert exc.value.code == "ACCOUNT_LOCKED"
        assert "15 分钟" in exc.value.message
        # Should NOT check password when locked
        mock_user_service.get_user_by_username.assert_not_called()

    async def test_login_disabled_account(self, mock_redis, mock_user_service) -> None:
        """Disabled account with correct password is rejected with ACCOUNT_DISABLED.

        Checks AFTER password verification (correct password proves identity),
        and never issues tokens / updates last_login.
        """
        mock_redis.get.return_value = None  # not locked
        mock_user_service.get_user_by_username.return_value = _make_user_doc(status="disabled")

        with pytest.raises(UnauthorizedError) as exc:
            await AuthService.login("admin", "Strong1234")
        assert exc.value.code == "ACCOUNT_DISABLED"
        assert "停用" in exc.value.message
        mock_user_service.update_last_login.assert_not_called()


class TestAccountLockout:
    """AC2: 5 failures → lock 15 min."""

    async def test_fifth_failure_triggers_lock(
        self, mock_redis, mock_user_service
    ) -> None:
        """After 5th failure, account gets locked."""
        mock_redis.get.return_value = None  # not locked yet
        mock_redis.incr.return_value = 5  # 5th attempt
        mock_user_service.get_user_by_username.return_value = _make_user_doc()

        with pytest.raises(UnauthorizedError) as exc:
            await AuthService.login("admin", "WrongPassword1")
        assert exc.value.code == "INVALID_CREDENTIALS"
        # Lock key should be set
        mock_redis.set.assert_called_once()
        assert "auth:locked:admin" in mock_redis.set.call_args[0][0]

    async def test_lock_auto_expires(self, mock_redis) -> None:
        """Lock key has 15min TTL."""
        mock_redis.get.return_value = None
        is_locked = await AuthService.is_account_locked("admin")
        assert is_locked is False


class TestRefreshToken:
    """AC4: Refresh token flow."""

    async def test_refresh_success(self, mock_redis, mock_user_service) -> None:
        """Valid refresh_token returns new access_token."""
        user_doc = _make_user_doc()
        refresh = create_refresh_token(subject="user_01HTEST")

        mock_user_service.get_user_by_id.return_value = user_doc

        result = await AuthService.refresh_token(refresh)
        assert result.access_token
        assert result.token_type == "bearer"
        assert result.expires_in == 900

    async def test_refresh_with_expired_token(self) -> None:
        """Expired refresh_token returns 401."""

        # Create already-expired token
        import jwt as pyjwt
        from app.core.config import settings

        payload = {
            "sub": "user_01HTEST",
            "type": "refresh",
            "iat": datetime.now(UTC) - timedelta(days=8),
            "exp": datetime.now(UTC) - timedelta(days=1),
        }
        expired_token = pyjwt.encode(
            payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
        )

        with pytest.raises(UnauthorizedError):
            await AuthService.refresh_token(expired_token)

    async def test_refresh_with_access_token_rejected(self, mock_redis) -> None:
        """Using access_token as refresh_token returns 401."""
        access = create_access_token(subject="user_01HTEST")

        with pytest.raises(UnauthorizedError) as exc:
            await AuthService.refresh_token(access)
        assert "type" in exc.value.message.lower() or "TOKEN" in exc.value.code

    async def test_refresh_disabled_user(
        self, mock_redis, mock_user_service
    ) -> None:
        """Disabled user cannot refresh tokens."""
        refresh = create_refresh_token(subject="user_01HTEST")
        mock_user_service.get_user_by_id.return_value = _make_user_doc(
            status="disabled"
        )

        with pytest.raises(UnauthorizedError) as exc:
            await AuthService.refresh_token(refresh)
        assert exc.value.code == "ACCOUNT_DISABLED"


class TestPasswordChange:
    """AC1: Password change flow."""

    async def test_change_password_success(
        self, mock_redis, mock_user_service
    ) -> None:
        """AC1: Valid current password + strong new password succeeds."""
        user_doc = _make_user_doc()
        mock_user_service.get_user_by_id.return_value = user_doc

        await AuthService.change_password(
            user_id="user_01HTEST",
            current_password="Strong1234",
            new_password="NewStrong567",
        )

        # User-level revocation key should be set
        set_call_args = mock_redis.set.call_args
        assert set_call_args is not None
        assert "auth:revoked:user:" in set_call_args[0][0]

    async def test_change_password_wrong_current(
        self, mock_redis, mock_user_service
    ) -> None:
        """AC1: Wrong current password → CURRENT_PASSWORD_MISMATCH."""
        user_doc = _make_user_doc()
        mock_user_service.get_user_by_id.return_value = user_doc

        with pytest.raises(UnauthorizedError) as exc:
            await AuthService.change_password(
                user_id="user_01HTEST",
                current_password="WrongPassword1",
                new_password="NewStrong567",
            )
        assert exc.value.code == "CURRENT_PASSWORD_MISMATCH"

    async def test_change_password_too_weak(
        self, mock_redis, mock_user_service
    ) -> None:
        """AC1: Weak new password → PASSWORD_TOO_SHORT."""
        user_doc = _make_user_doc()
        mock_user_service.get_user_by_id.return_value = user_doc

        with pytest.raises(ValidationError) as exc:
            await AuthService.change_password(
                user_id="user_01HTEST",
                current_password="Strong1234",
                new_password="short",
            )
        assert exc.value.code == "PASSWORD_TOO_SHORT"

    async def test_change_password_missing_complexity(
        self, mock_redis, mock_user_service
    ) -> None:
        """AC1: Password without digit → PASSWORD_MISSING_COMPLEXITY."""
        user_doc = _make_user_doc()
        mock_user_service.get_user_by_id.return_value = user_doc

        with pytest.raises(ValidationError) as exc:
            await AuthService.change_password(
                user_id="user_01HTEST",
                current_password="Strong1234",
                new_password="abcdefghij",
            )
        assert exc.value.code == "PASSWORD_MISSING_COMPLEXITY"

    async def test_change_password_same_as_current(
        self, mock_redis, mock_user_service
    ) -> None:
        """AC1: New password same as current → PASSWORD_SAME_AS_CURRENT."""
        user_doc = _make_user_doc()
        mock_user_service.get_user_by_id.return_value = user_doc

        with pytest.raises(ValidationError) as exc:
            await AuthService.change_password(
                user_id="user_01HTEST",
                current_password="Strong1234",
                new_password="Strong1234",
            )
        assert exc.value.code == "PASSWORD_SAME_AS_CURRENT"


class TestLogout:
    """AC2: Logout / token revocation."""

    async def test_logout_revokes_token(self, mock_redis) -> None:
        """AC2: Valid refresh_token gets blacklisted."""
        refresh = create_refresh_token(subject="user_01HTEST")

        await AuthService.logout(refresh)

        # Should have set a revoked key
        set_call_args = mock_redis.set.call_args
        assert set_call_args is not None
        assert "auth:revoked:" in set_call_args[0][0]

    async def test_logout_idempotent(self, mock_redis) -> None:
        """AC2: Calling logout twice does not raise."""
        refresh = create_refresh_token(subject="user_01HTEST")

        await AuthService.logout(refresh)
        await AuthService.logout(refresh)  # second call — no error

    async def test_logout_invalid_token(self, mock_redis) -> None:
        """AC2: Invalid token logout returns silently (idempotent)."""
        await AuthService.logout("totally.invalid.token")  # no error


class TestTokenRevocation:
    """AC3: Token blacklist check in refresh_token."""

    async def test_refresh_with_revoked_token(
        self, mock_redis, mock_user_service
    ) -> None:
        """AC3: Revoked single token → TOKEN_REVOKED."""
        refresh = create_refresh_token(subject="user_01HTEST")
        token_hash = AuthService._token_hash(refresh)
        revoked_key = AuthService._revoked_key(token_hash)

        # Simulate the token being revoked in Redis
        async def _get_side_effect(key):
            if key == revoked_key:
                return b"user_01HTEST"
            return None

        mock_redis.get.side_effect = _get_side_effect

        with pytest.raises(UnauthorizedError) as exc:
            await AuthService.refresh_token(refresh)
        assert exc.value.code == "TOKEN_REVOKED"

    async def test_refresh_after_password_change(
        self, mock_redis, mock_user_service
    ) -> None:
        """AC3: User-level revocation timestamp > token.iat → TOKEN_REVOKED."""
        # Create a token "in the past" using a custom iat
        import jwt as pyjwt
        from app.core.config import settings

        past_iat = datetime.now(UTC) - timedelta(hours=1)
        token = pyjwt.encode(
            {
                "sub": "user_01HTEST",
                "type": "refresh",
                "iat": past_iat,
                "exp": datetime.now(UTC) + timedelta(days=6),
            },
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )

        # Simulate password change happening AFTER token's iat
        user_revoked_ts = (past_iat + timedelta(minutes=5)).timestamp()

        async def _get_side_effect(key):
            if AuthService._user_revoked_key("user_01HTEST") in key:
                return str(user_revoked_ts).encode()
            return None

        mock_redis.get.side_effect = _get_side_effect

        with pytest.raises(UnauthorizedError) as exc:
            await AuthService.refresh_token(token)
        assert exc.value.code == "TOKEN_REVOKED"

    async def test_refresh_unaffected_by_other_user_revocation(
        self, mock_redis, mock_user_service
    ) -> None:
        """AC3: User A revocation does not affect User B's tokens."""
        refresh = create_refresh_token(subject="user_01HTEST")

        # User B has a revocation timestamp
        async def _get_side_effect(key):
            if AuthService._user_revoked_key("other_user") in key:
                return b"1000000000"
            return None

        mock_redis.get.side_effect = _get_side_effect
        mock_user_service.get_user_by_id.return_value = _make_user_doc()

        result = await AuthService.refresh_token(refresh)
        assert result.access_token
        assert result.refresh_token
