"""ASGI middleware: request context, Bearer auth (headers only — no receive consumption)."""

from __future__ import annotations

import hmac
import json
import uuid

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ga4_remote_mcp.auth import verify_google_access_token
from ga4_remote_mcp.config.settings import get_settings
from ga4_remote_mcp.context import (
    authenticated_email_var,
    client_identifier_var,
    request_id_var,
)
from ga4_remote_mcp.errors.normalize import tool_error_payload


def _bearer_matches_authorization_header(header_value: str, secret: str) -> bool:
    """Accept Secret Manager / client quirks: trim, case-insensitive ``Bearer``, inner spaces.

    Token comparison uses :func:`hmac.compare_digest` to mitigate timing attacks
    on the configured bearer secret.
    """
    h = (header_value or "").strip()
    if not h:
        return False
    parts = h.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    expected = (secret or "").strip()
    if not expected:
        # Defense in depth: settings validation already rejects empty secret in
        # bearer mode; never let a request authenticate against an empty secret.
        return False
    provided = parts[1].strip()
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def _presented_bearer(header_value: str) -> str | None:
    """Return the token from an ``Authorization: Bearer x`` header, if well-formed."""
    parts = (header_value or "").strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _get_header(scope: Scope, key: str) -> str | None:
    lk = key.lower().encode("latin1")
    for k, v in scope.get("headers") or []:
        if k == lk:
            return v.decode("latin1")
    return None


def _client_identifier_from_scope(scope: Scope) -> str:
    settings = get_settings()
    if settings.trusted_proxy_hops > 0:
        xff = _get_header(scope, "x-forwarded-for")
        if xff:
            parts = [p.strip() for p in xff.split(",") if p.strip()]
            if parts:
                idx = min(settings.trusted_proxy_hops, len(parts)) - 1
                return parts[idx]
    client = scope.get("client")
    if isinstance(client, (list, tuple)) and len(client) >= 1:
        return str(client[0])
    return "unknown"


class RequestContextMiddleware:
    """Set request_id + client_identifier context vars; echo X-Request-ID."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        rid = _get_header(scope, "x-request-id") or str(uuid.uuid4())
        token_rid = request_id_var.set(rid)
        token_cid = client_identifier_var.set(_client_identifier_from_scope(scope))
        try:

            async def send_wrapper(message: dict) -> None:
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers") or [])
                    headers.append((b"x-request-id", rid.encode("latin1")))
                    message = {**message, "headers": headers}
                await send(message)

            await self.app(scope, receive, send_wrapper)
        finally:
            client_identifier_var.reset(token_cid)
            request_id_var.reset(token_rid)


class BearerAuthMiddleware:
    """HTTP 401 or 403 when GA4MCP_AUTH_MODE=bearer and token mismatch (see settings)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        settings = get_settings()
        path = scope.get("path") or ""
        method = (scope.get("method") or "").upper()
        if settings.auth_mode != "bearer":
            await self.app(scope, receive, send)
            return

        # CORS preflight and similar probes must not return 401 without
        # WWW-Authenticate resource_metadata (some MCP clients then fail OAuth discovery).
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        if path in ("/health", "/ready"):
            await self.app(scope, receive, send)
            return

        # Discovery metadata must be readable without credentials -- a client that
        # cannot read it never learns how to authenticate in the first place.
        if path.startswith("/.well-known/"):
            await self.app(scope, receive, send)
            return

        auth = _get_header(scope, "authorization") or ""
        authorized = _bearer_matches_authorization_header(auth, settings.bearer_token)

        if not authorized and settings.oauth_enabled:
            # Second path for clients that cannot send a fixed header. Checked only
            # after the static token fails, so the common case stays off the network.
            presented = _presented_bearer(auth)
            if presented:
                email = await verify_google_access_token(presented, settings)
                if email:
                    authorized = True
                    authenticated_email_var.set(email)

        if not authorized:
            rid = _get_header(scope, "x-request-id") or str(uuid.uuid4())
            body = json.loads(
                tool_error_payload(
                    code="authentication_failed",
                    message="Invalid or missing bearer token",
                )
            )
            headers = {"X-Request-ID": rid}
            status = settings.bearer_failure_http_status
            if settings.oauth_enabled:
                # An OAuth-only client needs 401 plus a pointer to the metadata;
                # anything else reads as "this server does not do OAuth" and it
                # gives up. Bearer-only deployments keep their configured status
                # so they never invite a flow this server cannot complete.
                host = _get_header(scope, "host") or ""
                status = 401
                headers["WWW-Authenticate"] = (
                    "Bearer resource_metadata="
                    f'"https://{host}/.well-known/oauth-protected-resource"'
                )
            resp = JSONResponse(body, status_code=status, headers=headers)
            await resp(scope, receive, send)
            return

        await self.app(scope, receive, send)


def wrap_with_middleware(inner: ASGIApp) -> ASGIApp:
    app: ASGIApp = inner
    app = BearerAuthMiddleware(app)
    app = RequestContextMiddleware(app)
    return app
