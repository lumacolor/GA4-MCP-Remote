"""Guardrails (prd §16, tech §9.3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ga4_remote_mcp.config.settings import Settings


def _parse_ymd(s: str) -> datetime | None:
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d")
    except (ValueError, AttributeError):
        return None


def _max_span_days_in_ranges(
    date_ranges: list[dict[str, Any]],
    max_days: int,
) -> tuple[bool, str | None]:
    """Return (ok, error_message). Fails closed on unparseable literal ranges only."""
    for dr in date_ranges:
        if not isinstance(dr, dict):
            return False, "date_ranges entries must be objects"
        start = dr.get("start_date")
        end = dr.get("end_date")
        if not isinstance(start, str) or not isinstance(end, str):
            continue
        ds = _parse_ymd(start)
        de = _parse_ymd(end)
        if ds is None or de is None:
            # Relative names like yesterday — skip strict span check
            continue
        span = abs((de - ds).days) + 1
        if span > max_days:
            return False, f"date range exceeds GA4MCP_MAX_DATE_RANGE_DAYS ({max_days})"
    return True, None


def validate_tool_arguments(
    tool_name: str,
    arguments: dict[str, Any],
    settings: Settings,
) -> tuple[bool, str | None, dict[str, Any] | None]:
    """
    Return (ok, error_code, mutated_args).

    If ok, mutated_args is a copy of arguments with policy applied (or None if unchanged).
    """
    args = dict(arguments)

    if tool_name == "run_realtime_report" and not settings.enable_realtime:
        return False, "tool_disabled", None

    if tool_name == "list_google_ads_links" and not settings.enable_google_ads_links:
        return False, "tool_disabled", None

    if tool_name == "run_report":
        dr = args.get("date_ranges")
        if isinstance(dr, list) and dr:
            ok, msg = _max_span_days_in_ranges(dr, settings.max_date_range_days)
            if not ok:
                return False, "invalid_request", None
        limit = args.get("limit")
        if limit is not None:
            try:
                lim = int(limit)
            except (TypeError, ValueError):
                return False, "invalid_request", None
            if lim > settings.max_row_limit:
                return False, "invalid_request", None
        if not settings.return_property_quota_default:
            args["return_property_quota"] = False

    return True, None, args if args != arguments else None
