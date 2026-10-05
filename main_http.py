"""HTTP entrypoint for the shared Docker deployment (OpenClaw, Claude Desktop, Codex).

Upstream's runner serves streamable HTTP itself since f8fc3cf (MCP_TRANSPORT=http),
so this file only supplies this deployment's defaults and hands over to
``telegram_mcp.runner.main``. Every startup hook upstream adds there (file-extension
overrides, exposed-tools mode, transcription config, session lock) therefore applies
to the container too, instead of a hand-maintained copy silently missing them.

Positional arguments are server-side allowed roots for file-path tools, exactly as
for main.py (the compose file passes /data). Explicit environment wins over these
defaults.
"""

import os

_LOOPBACK_AND_DOCKER_HOSTS = ("127.0.0.1:*", "localhost:*", "[::1]:*", "host.docker.internal:*")

DEFAULTS = {
    "MCP_TRANSPORT": "http",
    # 0.0.0.0 inside the container; compose publishes the port on 127.0.0.1 only.
    "MCP_HOST": "0.0.0.0",
    "MCP_PORT": "18797",
    # DNS-rebinding protection: loopback, plus host.docker.internal for OpenClaw.
    "MCP_ALLOWED_HOSTS": ",".join(_LOOPBACK_AND_DOCKER_HOSTS),
    "MCP_ALLOWED_ORIGINS": ",".join(f"http://{host}" for host in _LOOPBACK_AND_DOCKER_HOSTS),
}


def main() -> None:
    for key, value in DEFAULTS.items():
        os.environ.setdefault(key, value)

    from telegram_mcp.runner import main as run

    run()


if __name__ == "__main__":
    main()
