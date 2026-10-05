"""Safeguards that must survive tools returning image content, not just text."""

import asyncio
from types import SimpleNamespace

import pytest
from mcp.server.mcpserver import Image
from mcp.types import CallToolResult, ImageContent, TextContent

from telegram_mcp import runtime


# mcp 2.x: the annotation/timeout hook is context-tier middleware (installed last,
# so innermost) operating on the wire dict ServerRunner serialised the result to.
def _hook():
    return runtime.mcp.middleware[-1]


def _wire(result: CallToolResult) -> dict:
    return result.model_dump(by_alias=True, mode="json", exclude_none=True)


_TOOLS_CALL = SimpleNamespace(method="tools/call")


@pytest.fixture
def two_accounts(monkeypatch):
    monkeypatch.setattr(runtime, "clients", {"personal": object(), "work": object()})


@pytest.mark.asyncio
async def test_text_only_fan_out_keeps_the_joined_string(two_accounts):
    @runtime.with_account(readonly=True)
    async def describe(account=None):
        return f"described by {account}"

    result = await describe()

    assert result == "[personal]\ndescribed by personal\n\n[work]\ndescribed by work"


@pytest.mark.asyncio
async def test_image_fan_out_returns_content_blocks_instead_of_stringifying(two_accounts):
    @runtime.with_account(readonly=True)
    async def render(account=None):
        return Image(data=b"jpeg-bytes-" + account.encode(), format="jpeg")

    result = await render()

    assert isinstance(result, list)
    assert result[0] == "[personal]"
    assert isinstance(result[1], Image)
    assert result[2] == "[work]"
    assert isinstance(result[3], Image)


@pytest.mark.asyncio
async def test_mixed_text_and_image_fan_out_is_flattened(two_accounts):
    @runtime.with_account(readonly=True)
    async def overview(account=None):
        return [f"index for {account}", Image(data=b"sheet", format="jpeg")]

    result = await overview()

    assert result[0] == "[personal]"
    assert result[1] == "index for personal"
    assert isinstance(result[2], Image)
    assert result[3] == "[work]"


@pytest.mark.asyncio
async def test_image_results_are_annotated_as_user_audience():
    async def call_next(_ctx):
        return _wire(
            CallToolResult(
                content=[
                    TextContent(type="text", text="caption"),
                    ImageContent(type="image", data="Zm9v", mime_type="image/jpeg"),
                ]
            )
        )

    response = await _hook()(_TOOLS_CALL, call_next)

    text_block, image_block = response["content"]
    assert text_block["annotations"]["audience"] == ["user"]
    assert image_block["annotations"]["audience"] == ["user"]


# mcp 2.x: the timeout wraps the tools/call handler (runtime._install_tool_timeout),
# so these drive a real server through the SDK's in-memory client, once per
# protocol era: 2026-07-28 rejects a result without the envelope the runner adds.
_PROTOCOL_MODES = ["legacy", "auto"]  # 2025-11-25 and 2026-07-28


def _probe_server(**tools):
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("probe")
    for name, fn in tools.items():
        server.add_tool(fn, name=name)
    runtime._install_tool_timeout(server)
    runtime._install_annotation_hook(server)
    return server


async def _call(server, mode, name):
    from mcp.client import Client

    async with Client(server, mode=mode) as client:
        return await client.call_tool(name, {})


def test_production_server_installs_the_timeout_and_the_annotations():
    handler = runtime.mcp._lowlevel_server.get_request_handler("tools/call").handler
    assert handler.__name__ == "call_tool_within_timeout"
    assert runtime.mcp.middleware[-1].__name__ == "annotate_user_audience"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", _PROTOCOL_MODES)
async def test_call_tool_timeout_returns_an_explicit_annotated_error(monkeypatch, mode):
    async def hang() -> str:
        await asyncio.Event().wait()
        return "never"

    monkeypatch.setenv("TELEGRAM_TOOL_TIMEOUT_SECONDS", "0.05")
    result = await _call(_probe_server(hang=hang), mode, "hang")

    assert result.is_error is True
    assert result.content[0].text == (
        "Telegram MCP tool timed out after 0.05s (code: GEN-TIMEOUT). "
        "Completion is unknown; a write may already have succeeded. "
        "Check destination state before retrying non-idempotent operations."
    )
    assert result.content[0].annotations.audience == ["user"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", _PROTOCOL_MODES)
async def test_timeout_after_accepted_write_reports_unknown_completion_once(monkeypatch, mode):
    marker = "synthetic-write-marker-4f1c"
    accepted_writes = []

    async def write() -> str:
        accepted_writes.append(marker)  # the write landed, then the call stalled
        await asyncio.Event().wait()
        return "never"

    monkeypatch.setenv("TELEGRAM_TOOL_TIMEOUT_SECONDS", "0.05")
    result = await _call(_probe_server(write=write), mode, "write")

    assert accepted_writes == [marker]  # dispatched exactly once, never retried
    assert result.is_error is True
    assert len(result.content) == 1
    text = result.content[0].text
    assert "code: GEN-TIMEOUT" in text
    assert "Completion is unknown" in text
    assert "a write may already have succeeded" in text
    assert "before retrying" in text
    assert marker not in text
    assert result.content[0].annotations.audience == ["user"]


@pytest.mark.parametrize(
    "value, expected",
    [(None, 55.0), ("", 55.0), ("garbage", 55.0), ("3.5", 3.5), ("0", None)],
)
def test_tool_timeout_parsing(monkeypatch, value, expected):
    monkeypatch.delenv("TELEGRAM_TOOL_TIMEOUT_SECONDS", raising=False)
    assert runtime._tool_timeout_seconds(value) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", _PROTOCOL_MODES)
async def test_disabled_tool_timeout_does_not_relabel_handler_timeout(monkeypatch, mode):
    async def own_timeout() -> str:
        raise asyncio.TimeoutError("tool-specific timeout")

    monkeypatch.setenv("TELEGRAM_TOOL_TIMEOUT_SECONDS", "0")
    result = await _call(_probe_server(own_timeout=own_timeout), mode, "own_timeout")

    assert result.is_error is True
    assert "tool-specific timeout" in result.content[0].text
    assert "GEN-TIMEOUT" not in result.content[0].text


@pytest.mark.asyncio
async def test_fast_tool_under_the_ceiling_is_untouched(monkeypatch):
    async def quick() -> str:
        return "done"

    monkeypatch.setenv("TELEGRAM_TOOL_TIMEOUT_SECONDS", "5")
    result = await _call(_probe_server(quick=quick), "auto", "quick")

    assert result.is_error is False
    assert result.content[0].text == "done"
    assert result.content[0].annotations.audience == ["user"]


@pytest.mark.asyncio
async def test_disabled_tool_timeout_lets_a_slow_tool_finish(monkeypatch):
    async def slow() -> str:
        await asyncio.sleep(0.2)
        return "done"

    monkeypatch.setenv("TELEGRAM_TOOL_TIMEOUT_SECONDS", "0")
    result = await _call(_probe_server(slow=slow), "auto", "slow")

    assert result.is_error is False
    assert result.content[0].text == "done"
