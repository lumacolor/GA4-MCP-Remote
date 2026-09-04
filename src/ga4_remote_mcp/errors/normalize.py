"""Map exceptions to PRD §17.2-style error codes."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from google.api_core import exceptions as google_exceptions

from ga4_remote_mcp.context import request_id_var

# Generic message returned to clients for any exception that does not match a
# known upstream category. The original exception detail must never appear in
# the client-facing payload — callers are expected to log the underlying error
# server-side (see ``ga.coordinator`` and ``transport.app``).
INTERNAL_ERROR_MESSAGE = "Internal server error"


def map_exception_to_code(exc: BaseException) -> tuple[str, str]:
    """Return ``(error_code, client_message)`` for an upstream exception.

    For unmapped exceptions the message is intentionally generic
    (:data:`INTERNAL_ERROR_MESSAGE`) so that internal stack traces / settings
    validation strings / SDK error chatter are not leaked to MCP clients.
    """
    if isinstance(exc, google_exceptions.InvalidArgument):
        # The Data API explains exactly what is wrong with a field name and often
        # suggests the right one ("Did you mean purchaserRate? Field purchases is
        # not a valid metric."). Collapsing that into "Internal server error" left
        # clients guessing: in the 04.09.2026 field test a model mistook an invalid
        # dimension name for a data problem and told the customer their tracking
        # was broken. Nothing here is internal -- the valid names are published in
        # Google's own schema docs.
        return "invalid_argument", str(exc)
    if isinstance(exc, google_exceptions.ResourceExhausted):
        return "quota_exceeded", str(exc)
    if isinstance(exc, google_exceptions.Unauthenticated):
        return "upstream_auth_failed", str(exc)
    if isinstance(exc, google_exceptions.PermissionDenied):
        return "upstream_auth_failed", str(exc)
    if isinstance(exc, google_exceptions.DeadlineExceeded):
        return "timeout", str(exc)
    if isinstance(exc, google_exceptions.ServiceUnavailable):
        return "upstream_unavailable", str(exc)
    if isinstance(exc, TimeoutError):
        return "timeout", str(exc)
    if isinstance(exc, asyncio.CancelledError):
        return "timeout", "cancelled"
    return "internal_error", INTERNAL_ERROR_MESSAGE


def tool_error_payload(
    *,
    code: str,
    message: str,
    extra: dict[str, Any] | None = None,
) -> str:
    body: dict[str, Any] = {
        "error_code": code,
        "message": message,
        "request_id": request_id_var.get(),
    }
    if extra:
        body.update(extra)
    return json.dumps(body, ensure_ascii=False)
