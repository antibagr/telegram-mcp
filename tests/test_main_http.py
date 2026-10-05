"""main_http.py: the Docker/OpenClaw HTTP entrypoint (fork-only).

Since upstream f8fc3cf the runner serves streamable HTTP itself, so main_http
only supplies this deployment's defaults and delegates to runner.main(). That
keeps every startup hook upstream adds to runner.main() (file-extension
overrides, exposed-tools mode, transcription config, session lock) in force for
the container instead of a hand-maintained copy silently missing them.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from telegram_mcp import runner

# main_http.py is a deployment entrypoint, not an installed module.
_spec = importlib.util.spec_from_file_location(
    "main_http", Path(__file__).resolve().parent.parent / "main_http.py"
)
main_http = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(main_http)

_KEYS = (
    "MCP_TRANSPORT",
    "MCP_HOST",
    "MCP_PORT",
    "MCP_ALLOWED_HOSTS",
    "MCP_ALLOWED_ORIGINS",
)


@pytest.fixture
def captured_run(monkeypatch):
    for key in _KEYS:
        monkeypatch.delenv(key, raising=False)
    calls = []
    monkeypatch.setattr(runner, "main", lambda: calls.append(dict(main_http.os.environ)))
    return calls


def test_defaults_serve_http_on_the_deployment_port(captured_run, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["main_http.py", "/data"])

    main_http.main()

    (env,) = captured_run
    assert env["MCP_TRANSPORT"] == "http"
    assert env["MCP_HOST"] == "0.0.0.0"
    assert env["MCP_PORT"] == "18797"
    hosts = env["MCP_ALLOWED_HOSTS"].split(",")
    origins = env["MCP_ALLOWED_ORIGINS"].split(",")
    for host in ("127.0.0.1:*", "localhost:*", "[::1]:*", "host.docker.internal:*"):
        assert host in hosts
        assert f"http://{host}" in origins


def test_explicit_environment_wins_over_defaults(captured_run, monkeypatch):
    monkeypatch.setenv("MCP_PORT", "19000")
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "example.test:*")

    main_http.main()

    (env,) = captured_run
    assert env["MCP_PORT"] == "19000"
    assert env["MCP_ALLOWED_HOSTS"] == "example.test:*"


def test_runner_builds_the_transport_security_main_http_asks_for(captured_run, monkeypatch):
    """The defaults must parse into the same rebinding protection main_http used to build."""
    main_http.main()
    (env,) = captured_run
    for key in ("MCP_ALLOWED_HOSTS", "MCP_ALLOWED_ORIGINS"):
        monkeypatch.setenv(key, env[key])

    settings = runner._configure_transport_security()

    assert settings.enable_dns_rebinding_protection is True
    assert "host.docker.internal:*" in settings.allowed_hosts
    assert "http://host.docker.internal:*" in settings.allowed_origins
