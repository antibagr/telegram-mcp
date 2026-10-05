"""Every None-defaulted tool parameter must accept an explicit null (fork 163bda3).

A parameter annotated ``name: str = None`` makes the SDK advertise
``{"type": "string", "default": null}``: a model reading ``default: null`` sends
null, and Pydantic rejects it ("Input should be a valid string"). Upstream keeps
adding parameters in that shape, so this guards every registered tool rather
than a list of known names.
"""

import pytest

import telegram_mcp.tools  # noqa: F401 - registers every MCP tool
from telegram_mcp import runtime


def _allows_null(schema: dict) -> bool:
    if schema.get("type") == "null":
        return True
    if isinstance(schema.get("type"), list) and "null" in schema["type"]:
        return True
    return any(_allows_null(option) for option in schema.get("anyOf", []))


def _null_default_violations():
    violations = []
    for tool in runtime.mcp._tool_manager.list_tools():
        for name, schema in tool.parameters.get("properties", {}).items():
            if "default" in schema and schema["default"] is None and not _allows_null(schema):
                violations.append(f"{tool.name}.{name}")
    return sorted(violations)


def test_no_tool_advertises_a_null_default_it_would_reject():
    assert _null_default_violations() == []


@pytest.mark.asyncio
async def test_explicit_nulls_validate_for_a_none_defaulted_parameter():
    """The schema fix is only half of it: the call itself must validate."""
    tool = next(t for t in runtime.mcp._tool_manager.list_tools() if t.name == "list_messages")
    model = tool.fn_metadata.arg_model

    parsed = model.model_validate(
        {"chat_id": 1, "search_query": None, "from_date": None, "account": None}
    )

    assert parsed.search_query is None
    assert parsed.account is None
