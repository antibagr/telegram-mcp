"""Import tool modules so their MCP decorators register with the shared server."""

from telegram_mcp.tools.accounts import *
from telegram_mcp.tools.contacts import *
from telegram_mcp.tools.chats import *
from telegram_mcp.tools.messages import *
from telegram_mcp.tools.groups import *
from telegram_mcp.tools.media import *
from telegram_mcp.tools.profile import *
from telegram_mcp.tools.folders import *
from telegram_mcp.tools.events import *

from telegram_mcp.runtime import disable_string_output_schemas

# Runs here rather than in an entrypoint so both main.py and main_http.py get it
# simply by importing the tools.
disable_string_output_schemas()

__all__ = [name for name in globals() if not name.startswith("_")]
