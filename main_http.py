"""HTTP entrypoint for chigwell/telegram-mcp.

Runs the same FastMCP server and Telethon client as main.py but over
streamable-http transport on port 18797 instead of stdio.
"""

import asyncio
import sys
import sqlite3

import nest_asyncio
import mcp.server.streamable_http as _sh

# Import the FastMCP instance and TelegramClient from the original main module.
# All 70+ tools registered via @mcp.tool() come along for free.
from main import mcp, clients

PORT = 18797


async def _main() -> None:
    try:
        print(f"Starting {len(clients)} Telegram client(s)...")
        for label, cl in clients.items():
            await cl.start()
            print(f"  Client '{label}' started.")
        print("All Telegram clients started. Running MCP HTTP server...")

        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = PORT
        mcp.settings.stateless_http = True
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


def main() -> None:
    nest_asyncio.apply()
    asyncio.run(_main())


if __name__ == "__main__":
    main()
