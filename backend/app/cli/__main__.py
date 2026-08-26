"""CLI entry point — run with: uv run python -m app.cli <command>."""
import argparse
import asyncio
import sys


def main() -> None:
    """Parse CLI arguments and dispatch to subcommands."""
    parser = argparse.ArgumentParser(
        prog="agent-flow",
        description="MEPER Agent — administrative CLI",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # --- create-admin subcommand ---
    admin_parser = subparsers.add_parser(
        "create-admin",
        help="Create the first admin user (only works when no admin exists)",
    )
    admin_parser.add_argument(
        "--username", required=True, help="Admin username (unique)"
    )
    admin_parser.add_argument(
        "--password",
        required=True,
        help="Admin password (>= 8 chars, letters + digits)",
    )
    admin_parser.add_argument(
        "--email", required=True, help="Admin email (unique)"
    )

    # --- promote-super-admin subcommand ---
    promote_parser = subparsers.add_parser(
        "promote-super-admin",
        help="Promote an existing admin user to super admin",
    )
    promote_parser.add_argument(
        "--username", required=True, help="Username of an existing admin"
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "create-admin":
        asyncio.run(_handle_create_admin(args))
    elif args.command == "promote-super-admin":
        asyncio.run(_handle_promote_super_admin(args))


async def _handle_create_admin(args: argparse.Namespace) -> None:
    """Handle the create-admin subcommand."""
    from app.core.errors import AppError
    from app.services.user_service import UserService

    # Create admin user (AC1, AC2, AC3, AC5) — async DB call
    # AC3 password strength validation is enforced inside create_admin_user
    try:
        result = await UserService.create_admin_user(
            username=args.username,
            password=args.password,
            email=args.email,
        )
    except AppError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        sys.exit(1)

    # AC1 + AC4: Print success with tokens
    print(result.message)
    print(f"user_id:       {result.user_id}")
    print(f"access_token:  {result.tokens.access_token}")
    print(f"refresh_token: {result.tokens.refresh_token}")


async def _handle_promote_super_admin(args: argparse.Namespace) -> None:
    """Handle the promote-super-admin subcommand."""
    from app.core.errors import AppError
    from app.services.user_service import UserService

    try:
        doc = await UserService.promote_super_admin(username=args.username)
    except AppError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        sys.exit(1)

    print(f"用户 {doc.get('username')} 已提升为超级管理员")
    print(f"user_id: {doc.get('_id')}")


if __name__ == "__main__":
    main()
