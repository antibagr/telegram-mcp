"""Fork Premium auto-transcription under upstream's per-call tool timeout.

Upstream (52ab7b1) bounds every tool call at TELEGRAM_TOOL_TIMEOUT_SECONDS
(default 55s). attach_transcriptions runs inside get_history/list_messages, so
its waiting has to be one budget for the whole batch, kept under that ceiling:
otherwise a voice-heavy history times out and the reader returns nothing.
"""

import asyncio
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


@pytest.mark.asyncio
async def test_budget_stays_under_the_tool_timeout(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "_tool_timeout_seconds",
        lambda value=None: runtime.TRANSCRIBE_TOOL_TIMEOUT_HEADROOM_SECONDS + 0.2,
    )
    client = _Client(pending=True)
    records = [{"id": 1}]

    started = time.monotonic()
    await runtime.attach_transcriptions(client, "peer", [_voice(1)], records)  # 45s default

    assert time.monotonic() - started < 1.0
    assert records[0]["transcription_pending"] is True


@pytest.mark.asyncio
async def test_unbounded_tool_timeout_keeps_the_configured_budget(monkeypatch):
    monkeypatch.setattr(runtime, "_tool_timeout_seconds", lambda value=None: None)
    client = _Client()
    records = [{"id": 1}]

    await runtime.attach_transcriptions(client, "peer", [_voice(1)], records, max_wait_seconds=5)

    assert records[0]["transcription"] == "t1"
