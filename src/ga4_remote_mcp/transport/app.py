"""Starlette ASGI app: /health, /ready, OAuth resource metadata, Streamable HTTP /mcp."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from ga4_remote_mcp.config.settings import get_settings
from ga4_remote_mcp.errors.normalize import tool_error_payload
from ga4_remote_mcp.ga.coordinator import app as mcp_lowlevel_server
from ga4_remote_mcp.policy.semaphores_registry import init_property_semaphores
from ga4_remote_mcp.structured_log.jsonlog import log_line
from ga4_remote_mcp.transport.middleware import wrap_with_middleware


async def health_endpoint(_: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


async def ready_endpoint(_: Request) -> JSONResponse:
    try:
        get_settings()
    except Exception as e:
        # Settings validation messages can echo env-var names, file paths,
        # and other configuration internals. Keep the client-facing body
        # minimal and stash the detail in the server-side log only.
        log_line(
            {
                "event": "ready_check_failed",
                "level": "error",
                "error_class": type(e).__name__,
                "error_message": str(e),
            }
        )
        return JSONResponse({"ok": False}, status_code=503)
    return JSONResponse({"ok": True})


PROTECTED_RESOURCE_PATH = "/.well-known/oauth-protected-resource"


async def protected_resource_metadata(request: Request) -> JSONResponse:
    """RFC 9728 protected-resource metadata.

    Clients that only speak OAuth -- ChatGPT among them -- read this to find the
    authorization server. It must be reachable without credentials, otherwise the
    client cannot get far enough to learn how to authenticate.

    ``resource`` has to match the URL the user typed into the client, so it is
    derived from the request's own Host header rather than configured separately.
    """
    settings = get_settings()
    if not settings.oauth_enabled:
        return JSONResponse({"error": "not_found"}, status_code=404)

    host = request.headers.get("host", "")
    body: dict[str, object] = {
        "resource": f"https://{host}/mcp",
        "authorization_servers": [settings.oauth_issuer.rstrip("/")],
        "bearer_methods_supported": ["header"],
    }
    scopes = settings.parsed_oauth_scopes()
    if scopes:
        body["scopes_supported"] = scopes
    return JSONResponse(body)


class McpHttpBridge:
    """Forward /mcp to StreamableHTTPSessionManager."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        application = scope.get("app")
        if application is None:
            log_line(
                {
                    "event": "mcp_bridge_misconfigured",
                    "level": "error",
                    "error_message": "ASGI scope is missing the Starlette app reference",
                }
            )
            body = json.loads(
                tool_error_payload(
                    code="internal_error",
                    message="Internal server error",
                )
            )
            await JSONResponse(body, status_code=500)(scope, receive, send)
            return
        sm = application.state.session_manager
        await sm.handle_request(scope, receive, send)


def build_app() -> ASGIApp:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(starlette_app: Starlette):
        init_property_semaphores(settings)
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=settings.enable_dns_rebinding_protection,
            allowed_hosts=settings.parsed_allowed_hosts(),
            allowed_origins=settings.parsed_allowed_origins(),
        )
        session_manager = StreamableHTTPSessionManager(
            mcp_lowlevel_server,
            json_response=settings.json_response,
            stateless=True,
            security_settings=security,
        )
        async with session_manager.run():
            starlette_app.state.session_manager = session_manager
            yield

    routes = [
        Route("/health", endpoint=health_endpoint, methods=["GET", "HEAD"]),
        Route("/ready", endpoint=ready_endpoint, methods=["GET", "HEAD"]),
        # Both shapes: clients probe the path-suffixed form first, then the bare one.
        Route(
            f"{PROTECTED_RESOURCE_PATH}/mcp",
            endpoint=protected_resource_metadata,
            methods=["GET", "HEAD"],
        ),
        Route(
            PROTECTED_RESOURCE_PATH,
            endpoint=protected_resource_metadata,
            methods=["GET", "HEAD"],
        ),
        Route(
            "/mcp",
            endpoint=McpHttpBridge(),
            methods=["GET", "POST", "DELETE", "OPTIONS"],
        ),
    ]

    star = Starlette(routes=routes, lifespan=lifespan)
    return wrap_with_middleware(star)
