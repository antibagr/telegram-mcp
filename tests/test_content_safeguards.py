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


@pytest.mark.asyncio
async def test_call_tool_timeout_returns_an_explicit_annotated_error(monkeypatch):
    async def call_next(_ctx):
        await asyncio.Event().wait()

    monkeypatch.setenv("TELEGRAM_TOOL_TIMEOUT_SECONDS", "0.01")
    response = await _hook()(_TOOLS_CALL, call_next)

    assert response["isError"] is True
    assert response["content"][0]["text"] == (
        "Telegram MCP tool timed out after 0.01s (code: GEN-TIMEOUT). "
        "Completion is unknown; a write may already have succeeded. "
        "Check destination state before retrying non-idempotent operations."
    )
    assert response["content"][0]["annotations"]["audience"] == ["user"]
    # The short-circuit result must still be a valid CallToolResult on the wire.
    assert CallToolResult.model_validate(response).is_error is True


@pytest.mark.asyncio
async def test_timeout_after_accepted_write_reports_unknown_completion_once(monkeypatch):
    marker = "synthetic-write-marker-4f1c"
    accepted_writes = []

    async def call_next(_ctx):
        accepted_writes.append(marker)  # the write landed, then the call stalled
        await asyncio.Event().wait()

    monkeypatch.setenv("TELEGRAM_TOOL_TIMEOUT_SECONDS", "0.01")
    response = await _hook()(_TOOLS_CALL, call_next)

    assert accepted_writes == [marker]  # dispatched exactly once, never retried
    assert response["isError"] is True
    assert len(response["content"]) == 1
    text = response["content"][0]["text"]
    assert "code: GEN-TIMEOUT" in text
    assert "Completion is unknown" in text
    assert "a write may already have succeeded" in text
    assert "before retrying" in text
    assert marker not in text
    assert response["content"][0]["annotations"]["audience"] == ["user"]


@pytest.mark.parametrize(
    "value, expected",
    [(None, 55.0), ("", 55.0), ("garbage", 55.0), ("3.5", 3.5), ("0", None)],
)
def test_tool_timeout_parsing(monkeypatch, value, expected):
    monkeypatch.delenv("TELEGRAM_TOOL_TIMEOUT_SECONDS", raising=False)
    assert runtime._tool_timeout_seconds(value) == expected


@pytest.mark.asyncio
async def test_disabled_tool_timeout_does_not_relabel_handler_timeout(monkeypatch):
    async def call_next(_ctx):
        raise asyncio.TimeoutError("tool-specific timeout")

    monkeypatch.setenv("TELEGRAM_TOOL_TIMEOUT_SECONDS", "0")
    with pytest.raises(asyncio.TimeoutError, match="tool-specific timeout"):
        await _hook()(_TOOLS_CALL, call_next)


@pytest.mark.asyncio
async def test_tool_timeout_only_bounds_tool_calls(monkeypatch):
    """tools/list and every other method pass straight through, unbounded."""
    finished = []

    async def call_next(_ctx):
        await asyncio.sleep(0.05)
        finished.append(True)
        return {"tools": []}

    monkeypatch.setenv("TELEGRAM_TOOL_TIMEOUT_SECONDS", "0.01")
    response = await _hook()(SimpleNamespace(method="tools/list"), call_next)

    assert response == {"tools": []}
    assert finished == [True]
