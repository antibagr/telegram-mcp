"""HTTP entrypoint for chigwell/telegram-mcp.

Runs the same MCP server and Telethon clients as main.py but over
streamable-http transport on port 18797 instead of stdio.
"""

import asyncio
import sqlite3
import sys

import mcp.server.streamable_http as _sh

from telegram_mcp.install_guard import UnsafeInstallationError, assert_safe_distribution

try:
    assert_safe_distribution()
except UnsafeInstallationError as exc:
    raise SystemExit(str(exc)) from None

from telegram_mcp.runtime import (
    _configure_allowed_roots_from_cli,
    clients,
    mcp,
)
import telegram_mcp.tools  # noqa: F401 — registers MCP tools via decorators

from telegram_mcp.runner import _connect_authorized_client

PORT = 18797


async def _main() -> None:
    try:
        labels = ", ".join(clients.keys())
        print(f"Starting {len(clients)} Telegram client(s) ({labels})...", file=sys.stderr)
        await asyncio.gather(
            *(_connect_authorized_client(label, cl) for label, cl in clients.items())
        )

        # Warm entity caches in the background. StringSession has no persistent
        # cache, so dialogs are fetched once to populate it — but doing this
        # synchronously can block startup for the length of a GetDialogsRequest
        # flood wait, delaying the port bind and failing the container
        # healthcheck. resolve_entity() re-warms on a cache miss anyway.
        print("Warming entity caches (background)...", file=sys.stderr)

        async def _warm_caches() -> None:
            try:
                await asyncio.gather(*(cl.get_dialogs() for cl in clients.values()))
                print("Entity caches warmed.", file=sys.stderr)
            except Exception as warm_exc:
                print(f"Entity cache warm failed: {warm_exc}", file=sys.stderr)

        warm_task = asyncio.create_task(_warm_caches())

        print(
            f"Telegram client(s) started ({labels}). Running MCP HTTP server on :{PORT}...",
            file=sys.stderr,
        )

        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = PORT
        mcp.settings.transport_security = _sh.TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[
                "127.0.0.1:*",
                "localhost:*",
                "[::1]:*",
                "host.docker.internal:*",
            ],
            allowed_origins=[
                "http://127.0.0.1:*",
                "http://localhost:*",
                "http://[::1]:*",
                "http://host.docker.internal:*",
            ],
        )

        await mcp.run_streamable_http_async()
    except Exception as e:
        print(f"Error starting client: {e}", file=sys.stderr)
        if isinstance(e, sqlite3.OperationalError) and "database is locked" in str(e):
            print(
                "Database lock detected. Ensure no other instances are running.",
                file=sys.stderr,
            )
        sys.exit(1)
    finally:
        try:
            await asyncio.gather(
                *(cl.disconnect() for cl in clients.values()), return_exceptions=True
            )
        except Exception:
            pass


def main() -> None:
    _configure_allowed_roots_from_cli(sys.argv[1:])
    asyncio.run(_main())


if __name__ == "__main__":
    main()
