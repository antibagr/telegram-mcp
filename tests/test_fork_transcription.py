"""Fork Premium auto-transcription under upstream's per-call tool timeout.

Upstream (52ab7b1) bounds every tool call at TELEGRAM_TOOL_TIMEOUT_SECONDS
(default 55s). attach_transcriptions runs inside get_history/list_messages, so
its waiting has to be one budget for the whole batch, kept under that ceiling:
otherwise a voice-heavy history times out and the reader returns nothing.
"""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from telethon.tl import functions

from telegram_mcp import runtime


class _Client:
    def __init__(self, *, delay=0.0, pending=False):
        self.delay = delay
        self.pending = pending
        self.calls = []

    async def __call__(self, request):
        assert isinstance(request, functions.messages.TranscribeAudioRequest)
        self.calls.append(request.msg_id)
        await asyncio.sleep(self.delay)
        if self.pending:
            return SimpleNamespace(text="", pending=True)
        return SimpleNamespace(text=f"t{request.msg_id}", pending=False)


def _voice(msg_id):
    return SimpleNamespace(id=msg_id, voice=object(), video_note=None, audio=None)


@pytest.mark.asyncio
async def test_still_pending_notes_are_flagged_within_the_batch_budget():
    client = _Client(pending=True)
    records = [{"id": 1}, {"id": 2}]

    started = time.monotonic()
    await runtime.attach_transcriptions(
        client, "peer", [_voice(1), _voice(2)], records, max_wait_seconds=0.2
    )

    assert time.monotonic() - started < 1.0  # not a 1.5s re-poll, let alone 45s per note
    assert all(r.get("transcription_pending") is True for r in records)
    assert not any("transcription" in r for r in records)


@pytest.mark.asyncio
async def test_budget_covers_the_whole_batch_not_each_message():
    client = _Client(delay=0.15)
    records = [{"id": 1}, {"id": 2}, {"id": 3}]

    started = time.monotonic()
    await runtime.attach_transcriptions(
        client,
        "peer",
        [_voice(1), _voice(2), _voice(3)],
        records,
        max_wait_seconds=0.25,
        concurrency=1,
    )

    assert time.monotonic() - started < 0.6
    assert records[0]["transcription"] == "t1"
    assert records[1].get("transcription_pending") is True
    assert records[2].get("transcription_pending") is True


def _under_call_deadline(seconds_left):
    """Pretend a tools/call with ``seconds_left`` before its ceiling is being served."""
    return runtime._TOOL_CALL_DEADLINE.set(time.monotonic() + seconds_left)


@pytest.mark.asyncio
async def test_budget_stays_under_the_tool_call_deadline():
    client = _Client(pending=True)
    records = [{"id": 1}]
    token = _under_call_deadline(runtime.TRANSCRIBE_TOOL_TIMEOUT_HEADROOM_SECONDS + 0.2)
    try:
        started = time.monotonic()
        await runtime.attach_transcriptions(client, "peer", [_voice(1)], records)  # 45s default
    finally:
        runtime._TOOL_CALL_DEADLINE.reset(token)

    assert time.monotonic() - started < 1.0
    assert records[0]["transcription_pending"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_time_spent_before_transcribing_counts_against_the_call(monkeypatch, mode):
    """The budget is measured from the call's start (its ceiling), not from when
    transcription begins: a slow fetch or reconnect must not push the read past
    GEN-TIMEOUT and lose the records."""
    from mcp.client import Client
    from mcp.server.mcpserver import MCPServer

    monkeypatch.setattr(runtime, "TRANSCRIBE_TOOL_TIMEOUT_HEADROOM_SECONDS", 0.1)
    monkeypatch.setenv("TELEGRAM_TOOL_TIMEOUT_SECONDS", "0.5")

    async def history() -> str:
        await asyncio.sleep(0.3)  # a slow fetch / reconnect before transcribing
        records = [{"id": 1}]
        await runtime.attach_transcriptions(_Client(pending=True), "peer", [_voice(1)], records)
        return json.dumps(records)

    server = MCPServer("probe")
    server.add_tool(history, name="history")
    runtime._install_tool_timeout(server)
    async with Client(server, mode=mode) as client:
        result = await client.call_tool("history", {})

    assert result.is_error is False
    assert json.loads(result.content[0].text) == [{"id": 1, "transcription_pending": True}]


@pytest.mark.asyncio
async def test_spent_budget_sends_no_request():
    client = _Client()
    records = [{"id": 1}, {"id": 2}]

    await runtime.attach_transcriptions(
        client, "peer", [_voice(1), _voice(2)], records, max_wait_seconds=0
    )

    assert client.calls == []
    assert all(r.get("transcription_pending") is True for r in records)


@pytest.mark.asyncio
async def test_a_client_timeout_is_an_error_not_pending():
    class _TimingOutClient(_Client):
        async def __call__(self, request):
            self.calls.append(request.msg_id)
            raise TimeoutError("request timed out")

    records = [{"id": 1}]
    await runtime.attach_transcriptions(
        _TimingOutClient(), "peer", [_voice(1)], records, max_wait_seconds=5
    )

    assert "transcription_pending" not in records[0]
    assert "request timed out" in records[0]["transcription_error"]


@pytest.mark.asyncio
async def test_outside_a_bounded_call_the_configured_budget_applies():
    assert runtime._TOOL_CALL_DEADLINE.get() is None
    client = _Client()
    records = [{"id": 1}]

    await runtime.attach_transcriptions(client, "peer", [_voice(1)], records, max_wait_seconds=5)

    assert records[0]["transcription"] == "t1"


# --- the dedicated transcribe_audio tool ----------------------------------------


@pytest.fixture
def audio_tool(monkeypatch):
    from unittest.mock import AsyncMock

    from telegram_mcp.tools import media

    def install(client):
        client.get_messages = AsyncMock(return_value=SimpleNamespace(media=object()))
        monkeypatch.setattr(media, "get_client", lambda account=None: client)
        monkeypatch.setattr(media, "resolve_entity", AsyncMock(return_value="peer"))
        return media

    return install


@pytest.mark.asyncio
async def test_transcribe_audio_is_disabled_when_transcription_is_off(audio_tool, monkeypatch):
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE", "off")
    client = _Client()
    media = audio_tool(client)

    result = json.loads(await media.transcribe_audio(chat_id=42, message_id=1, account="test"))

    assert result == {"transcribed": False, "reason": "transcription_disabled"}
    assert client.calls == []


@pytest.mark.asyncio
async def test_transcribe_audio_wait_is_capped_by_the_call_deadline(audio_tool):
    client = _Client(pending=True)
    media = audio_tool(client)
    token = _under_call_deadline(runtime.TRANSCRIBE_TOOL_TIMEOUT_HEADROOM_SECONDS + 0.3)
    try:
        started = time.monotonic()
        result = json.loads(
            await media.transcribe_audio(
                chat_id=42, message_id=1, max_wait_seconds=30, account="test"
            )
        )
    finally:
        runtime._TOOL_CALL_DEADLINE.reset(token)

    assert time.monotonic() - started < 3.0  # one 1.5s re-poll at most, not 30s
    assert result["pending"] is True
