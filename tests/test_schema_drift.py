"""Schema drift must be reported as such, not as a generic error code.

A `TypeNotFoundError` means the installed TL schema is older than what the server
sends. Hidden behind the generic message, it reads like "no such user/chat" and
costs hours of debugging in the wrong direction.
"""

from telegram_mcp.runtime import log_and_format_error
from telethon.errors.common import TypeNotFoundError

from fork_semantics import sync_tool_error_text


def test_schema_drift_is_named_actionable_and_omits_exception_payload():
    error = TypeNotFoundError(0xD58A08C6, b"raw-payload-secret")

    msg = sync_tool_error_text(log_and_format_error, "list_chats", error)

    assert "MTProto schema mismatch" in msg
    assert "NOT a missing user or chat" in msg
    assert str(error) not in msg
    assert "raw-payload-secret" not in msg


def test_ordinary_error_keeps_the_generic_format():
    msg = sync_tool_error_text(log_and_format_error, "get_chat", ValueError("boom"))
    # Fork (13aec40): an ordinary failure carries the real exception, not a hashed code.
    assert msg == "ValueError: boom"
    assert "MTProto schema mismatch" not in msg
