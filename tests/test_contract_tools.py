"""MCP tool name contract (prd §12.1): 7 tools, stable names."""

from __future__ import annotations

import asyncio

import pytest

from ga4_remote_mcp.config.settings import clear_settings_cache, get_settings  # noqa: F401
from ga4_remote_mcp.ga.coordinator import list_tools, mcp_tools

EXPECTED = frozenset(
    {
        "get_account_summaries",
        "list_google_ads_links",
        "get_property_details",
        "list_property_annotations",
        "get_custom_dimensions_and_metrics",
        "run_report",
        "run_realtime_report",
    }
)


def test_tool_names_match_official_set() -> None:
    assert {t.name for t in mcp_tools} == EXPECTED


def test_list_tools_hides_disabled_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Registered set stays stable; exposure is what the flags control."""
    monkeypatch.delenv("GA4MCP_ENABLE_GOOGLE_ADS_LINKS", raising=False)
    monkeypatch.setenv("GA4MCP_ENABLE_REALTIME", "false")
    clear_settings_cache()
    exposed = {t.name for t in asyncio.run(list_tools())}
    assert "list_google_ads_links" not in exposed
    assert "run_realtime_report" not in exposed
    assert "run_report" in exposed
