"""Fork-only messaging behaviour layered on upstream's tools.

Upstream has no ``silent``/``reply_to`` on send_message, no ``silent`` on
reply_to_message, no ``thread_id`` on list_messages and no Premium
auto-transcription in the readers, so nothing in upstream's suite pins these.
The date-chip path (upstream ``format_date``) builds its own SendMessageRequest,
so it must carry the fork's flags too.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon.tl import functions, types

from telegram_mcp.tools import messages


@pytest.fixture
def client(monkeypatch):
    cl = AsyncMock()
    monkeypatch.setattr(messages, "get_client", lambda account=None: cl)
    monkeypatch.setattr(messages, "resolve_entity", AsyncMock(return_value=types.User(id=42)))
    return cl


@pytest.mark.asyncio
async def test_send_message_plain_path_forwards_reply_to_and_silent(client):
    result = await messages.send_message(42, "hi", reply_to=7, silent=True, account="test")

    assert result == "Message sent successfully."
    kwargs = client.send_message.await_args.kwargs
    assert kwargs["reply_to"] == 7
    assert kwargs["silent"] is True


@pytest.mark.asyncio
async def test_send_message_date_chip_keeps_reply_to_and_silent(client):
    result = await messages.send_message(
        42,
        "Lunch 13/09 17:00",
        reply_to=7,
        silent=True,
        format_date="13/09 17:00",
        account="test",
    )

    assert result == "Message sent successfully."
    req = client.await_args.args[0]
    assert isinstance(req, functions.messages.SendMessageRequest)
    assert req.silent is True
    assert isinstance(req.reply_to, types.InputReplyToMessage)
    assert req.reply_to.reply_to_msg_id == 7
    assert isinstance(req.entities[0], types.MessageEntityFormattedDate)


@pytest.mark.asyncio
async def test_send_message_date_chip_defaults_to_loud_and_unthreaded(client):
    await messages.send_message(42, "Lunch 13/09", format_date="13/09", account="test")

    req = client.await_args.args[0]
    assert not req.silent
    assert req.reply_to is None


@pytest.mark.asyncio
async def test_send_message_rich_path_forwards_reply_to_and_silent(client, monkeypatch):
    send_rich = AsyncMock(return_value='{"sent": true}')
    monkeypatch.setattr(messages, "_send_rich", send_rich)

    await messages.send_message(
        42, "| a |", reply_to=7, silent=True, parse_mode="rich", account="test"
    )

    assert send_rich.await_args.kwargs == {"reply_to": 7, "silent": True}


@pytest.mark.asyncio
async def test_reply_to_message_plain_path_forwards_silent(client):
    await messages.reply_to_message(42, 7, "ok", silent=True, account="test")

    kwargs = client.send_message.await_args.kwargs
    assert kwargs["reply_to"] == 7
    assert kwargs["silent"] is True


@pytest.mark.asyncio
async def test_reply_to_message_date_chip_keeps_silent(client):
    await messages.reply_to_message(
        42, 7, "At 13/09 17:00", silent=True, format_date="13/09 17:00", account="test"
    )

    req = client.await_args.args[0]
    assert isinstance(req, functions.messages.SendMessageRequest)
    assert req.silent is True
    assert req.reply_to.reply_to_msg_id == 7


@pytest.mark.asyncio
async def test_reply_to_message_rich_path_forwards_silent(client, monkeypatch):
    send_rich = AsyncMock(return_value='{"sent": true}')
    monkeypatch.setattr(messages, "_send_rich", send_rich)

    await messages.reply_to_message(42, 7, "| a |", parse_mode="rich", silent=True, account="test")

    assert send_rich.await_args.kwargs == {"reply_to": 7, "silent": True}


# --- Premium auto-transcription in the readers --------------------------------


class _ReaderClient:
    """History/thread reads plus Telegram's native TranscribeAudioRequest."""

    def __init__(self, history):
        self.history = history
        self.transcribed = []
        self.iter_params = None

    async def get_messages(self, entity, limit=None):
        return list(self.history)

    async def iter_messages(self, entity, **params):
        self.iter_params = params
        for msg in self.history:
            yield msg

    async def __call__(self, request):
        assert isinstance(request, functions.messages.TranscribeAudioRequest)
        self.transcribed.append(request.msg_id)
        return SimpleNamespace(text=f"transcript of {request.msg_id}", pending=False)


def _msg(msg_id, topic, *, voice):
    return SimpleNamespace(
        id=msg_id,
        topic=topic,
        date=None,
        voice=object() if voice else None,
        video_note=None,
        audio=None,
    )


@pytest.fixture
def reader(monkeypatch):
    def install(history):
        cl = _ReaderClient(history)
        monkeypatch.setattr(messages, "get_client", lambda account=None: cl)
        monkeypatch.setattr(messages, "resolve_entity", AsyncMock(return_value="entity"))
        monkeypatch.setattr(messages, "get_marked_id", lambda entity: 42)
        monkeypatch.setattr(messages.transcription, "prefetch_transcripts", AsyncMock())
        monkeypatch.setattr(
            messages,
            "message_to_dict",
            lambda msg, chat_id=None: {"id": msg.id, "reply_to": msg.topic},
        )
        return cl

    return install


@pytest.mark.asyncio
async def test_get_history_topic_filter_keeps_transcripts_on_their_own_messages(reader):
    # Newest first, as Telethon returns them; only 3 and 1 belong to topic 7.
    cl = reader([_msg(3, 7, voice=True), _msg(2, 9, voice=True), _msg(1, 7, voice=False)])

    result = json.loads(await messages.get_history(42, topic_id=7, account="test"))["results"]

    assert [r["id"] for r in result] == [3, 1]
    assert result[0]["transcription"] == "transcript of 3"
    assert "transcription" not in result[1]
    assert cl.transcribed == [3]  # the other topic's voice note is never transcribed


@pytest.mark.asyncio
async def test_get_history_without_topic_transcribes_every_voice_note(reader):
    cl = reader([_msg(2, 9, voice=True), _msg(1, 7, voice=True)])

    result = json.loads(await messages.get_history(42, account="test"))["results"]

    assert [r.get("transcription") for r in result] == ["transcript of 2", "transcript of 1"]
    assert cl.transcribed == [2, 1]


@pytest.mark.asyncio
async def test_get_history_transcription_can_be_switched_off(reader):
    cl = reader([_msg(1, 7, voice=True)])

    result = json.loads(await messages.get_history(42, transcribe_audio=False, account="test"))[
        "results"
    ]

    assert "transcription" not in result[0]
    assert cl.transcribed == []


@pytest.mark.asyncio
async def test_list_messages_thread_reads_the_thread_and_transcribes_it(reader):
    cl = reader([_msg(5, 4, voice=True), _msg(4, 4, voice=False)])

    result = json.loads(await messages.list_messages(42, thread_id=4, account="test"))["results"]

    assert cl.iter_params == {"reply_to": 4}
    assert [r["id"] for r in result] == [5, 4]
    assert result[0]["transcription"] == "transcript of 5"
    assert "transcription" not in result[1]


@pytest.mark.asyncio
async def test_transcribe_off_disables_the_fork_auto_transcription_too(reader, monkeypatch):
    """TELEGRAM_TRANSCRIBE=off is upstream's global switch; it silences ours as well."""
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE", "off")
    cl = reader([_msg(2, 7, voice=True), _msg(1, 7, voice=True)])

    history = json.loads(await messages.get_history(42, account="test"))["results"]
    listed = json.loads(await messages.list_messages(42, thread_id=7, account="test"))["results"]

    assert not any("transcription" in r for r in history + listed)
    assert cl.transcribed == []


@pytest.mark.asyncio
async def test_on_demand_default_keeps_the_fork_auto_transcription(reader, monkeypatch):
    monkeypatch.delenv("TELEGRAM_TRANSCRIBE", raising=False)  # upstream default: on-demand
    cl = reader([_msg(1, 7, voice=True)])

    history = json.loads(await messages.get_history(42, account="test"))["results"]

    assert history[0]["transcription"] == "transcript of 1"
    assert cl.transcribed == [1]


# --- forward_message: the fork's old top_msg_id name -----------------------------


class _ForwardClient:
    def __init__(self):
        self.requests = []

    async def get_messages(self, entity, ids=None):
        return None  # not part of an album

    async def __call__(self, request):
        self.requests.append(request)
        return SimpleNamespace(updates=[])


@pytest.fixture
def forwarder(monkeypatch):
    cl = _ForwardClient()
    monkeypatch.setattr(messages, "get_client", lambda account=None: cl)
    monkeypatch.setattr(
        messages,
        "resolve_entity",
        AsyncMock(side_effect=lambda chat, client: types.InputPeerChat(abs(int(chat)))),
    )
    return cl


@pytest.mark.asyncio
async def test_forward_message_still_honours_the_old_top_msg_id_name(forwarder):
    """Unknown arguments are dropped silently, so a caller still using the fork's
    pre-v3.2.66 name would otherwise land in the main chat instead of the topic."""
    result = await messages.forward_message(1, 10, 2, top_msg_id=77, account="test")

    (req,) = forwarder.requests
    assert isinstance(req, functions.messages.ForwardMessagesRequest)
    assert req.top_msg_id == 77
    assert "forwarded" in result


@pytest.mark.asyncio
async def test_forward_message_refuses_conflicting_topic_names(forwarder):
    result = await messages.forward_message(1, 10, 2, topic_id=5, top_msg_id=77, account="test")

    assert "top_msg_id" in result and "topic_id" in result
    assert forwarder.requests == []
