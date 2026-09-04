"""Request-scoped context (ASGI → MCP tool handlers)."""

from __future__ import annotations

import contextvars

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "ga4mcp_request_id",
    default="-",
)

client_identifier_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "ga4mcp_client_identifier",
    default="unknown",
)

# Set when a request authenticated via OAuth rather than the shared static token.
# Logged so operators can tell the two apart, and tell OAuth users apart from
# each other -- the static token is anonymous by design.
authenticated_email_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "ga4mcp_authenticated_email",
    default="-",
)
