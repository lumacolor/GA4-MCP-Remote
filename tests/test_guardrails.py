"""Guardrail validation (prd §16)."""

from __future__ import annotations

import pytest

from ga4_remote_mcp.config.settings import clear_settings_cache, get_settings
from ga4_remote_mcp.policy.guardrails import validate_tool_arguments


def test_run_report_limit_exceeded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GA4MCP_MAX_ROW_LIMIT", "100")
    clear_settings_cache()
    s = get_settings()
    ok, code, msg, _ = validate_tool_arguments(
        "run_report",
        {"limit": 101, "date_ranges": []},
        s,
    )
    assert ok is False
    assert code == "invalid_request"


def test_run_report_date_span(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GA4MCP_MAX_DATE_RANGE_DAYS", "2")
    clear_settings_cache()
    s = get_settings()
    ok, code, msg, _ = validate_tool_arguments(
        "run_report",
        {
            "date_ranges": [
                {"start_date": "2025-01-01", "end_date": "2025-01-10"},
            ],
        },
        s,
    )
    assert ok is False
    assert code == "invalid_request"


def test_realtime_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GA4MCP_ENABLE_REALTIME", "false")
    clear_settings_cache()
    s = get_settings()
    ok, code, msg, _ = validate_tool_arguments("run_realtime_report", {}, s)
    assert ok is False
    assert code == "tool_disabled"


def test_google_ads_links_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Google Ads links are out of scope for this deployment (see docs/code-review.md)."""
    monkeypatch.delenv("GA4MCP_ENABLE_GOOGLE_ADS_LINKS", raising=False)
    clear_settings_cache()
    s = get_settings()
    ok, code, msg, _ = validate_tool_arguments("list_google_ads_links", {"property_id": "1"}, s)
    assert ok is False
    assert code == "tool_disabled"


def test_google_ads_links_can_be_re_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GA4MCP_ENABLE_GOOGLE_ADS_LINKS", "true")
    clear_settings_cache()
    s = get_settings()
    ok, code, msg, _ = validate_tool_arguments("list_google_ads_links", {"property_id": "1"}, s)
    assert ok is True
    assert code is None


def test_date_range_error_names_the_limit_and_the_way_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare "guardrail validation failed" is what cost the 04.09.2026 field test
    four attempts and a wrong conclusion -- the caller blamed its dimension."""
    clear_settings_cache()
    s = get_settings()
    ok, code, msg, _ = validate_tool_arguments(
        "run_report",
        {"date_ranges": [{"start_date": "2024-01-01", "end_date": "2026-08-31"}]},
        s,
    )
    assert ok is False
    assert code == "invalid_request"
    assert str(s.max_date_range_days) in msg
    assert "date_ranges" in msg


def test_limit_error_names_the_maximum(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_settings_cache()
    s = get_settings()
    ok, code, msg, _ = validate_tool_arguments(
        "run_report", {"date_ranges": [], "limit": s.max_row_limit + 1}, s
    )
    assert ok is False
    assert str(s.max_row_limit) in msg
