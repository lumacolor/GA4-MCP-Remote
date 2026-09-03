"""Policy denials must be detectable without parsing the payload text.

A rejected property used to come back as a normal successful result whose text
happened to contain an error object. Clients that do not parse that text treat
it as a successful empty answer -- observed with n8n's MCP node, where a
rejected property produced a green "1 item" and the workflow continued. A
scheduled report built that way says "no data" instead of surfacing a broken
configuration, and nobody looks at the config.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from mcp import types as mcp_types

from ga4_remote_mcp.config.settings import clear_settings_cache
from ga4_remote_mcp.ga.coordinator import call_mcp_tool

ALLOWED = "409494221"
DENIED = "299454785"


def _call(name: str, args: dict) -> mcp_types.CallToolResult:
    return asyncio.run(call_mcp_tool(name, args))


def _payload(result: mcp_types.CallToolResult) -> dict:
    return json.loads(result.content[0].text)


@pytest.fixture(autouse=True)
def _allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GA4MCP_ALLOWED_PROPERTY_IDS", ALLOWED)
    monkeypatch.setenv("GA4MCP_ALLOW_ALL_PROPERTIES", "false")
    clear_settings_cache()


def test_unauthorized_property_is_flagged_as_error() -> None:
    result = _call(
        "run_report", {"property_id": DENIED, "date_ranges": [], "dimensions": [], "metrics": []}
    )
    assert result.isError is True
    body = _payload(result)
    assert body["error_code"] == "unauthorized_property"
    # The denied id is echoed so operators can correlate with the request log.
    assert body["property_id"] == DENIED


def test_missing_property_is_flagged_as_error() -> None:
    result = _call("run_report", {"date_ranges": [], "dimensions": [], "metrics": []})
    assert result.isError is True
    assert _payload(result)["error_code"] == "invalid_property"


def test_disabled_tool_is_flagged_as_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GA4MCP_ENABLE_GOOGLE_ADS_LINKS", raising=False)
    clear_settings_cache()
    result = _call("list_google_ads_links", {"property_id": ALLOWED})
    assert result.isError is True
    assert _payload(result)["error_code"] == "tool_disabled"


def test_unknown_tool_is_flagged_as_error() -> None:
    result = _call("does_not_exist", {})
    assert result.isError is True
    assert _payload(result)["error_code"] == "invalid_request"
