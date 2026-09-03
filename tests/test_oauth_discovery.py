"""OAuth discovery surface (RFC 9728).

ChatGPT offers no static-header option for custom connectors, so a bearer-only
server is rejected outright with "does not implement OAuth". Discovery is what
lets such a client get far enough to learn where to authenticate.

The surface is off by default: a deployment that only serves Claude and n8n must
keep answering its configured status and must not advertise a flow this server
cannot complete.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from ga4_remote_mcp.config.settings import clear_settings_cache
from ga4_remote_mcp.transport.app import build_app

METADATA_PATHS = [
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
]
ISSUER = "https://example-tenant.eu.auth0.com"


@pytest.fixture
def oauth_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GA4MCP_OAUTH_ENABLED", "true")
    monkeypatch.setenv("GA4MCP_OAUTH_ISSUER", ISSUER)
    monkeypatch.setenv("GA4MCP_AUTH_MODE", "bearer")
    monkeypatch.setenv("GA4MCP_BEARER_TOKEN", "static-token-for-claude")
    clear_settings_cache()


@pytest.fixture
def oauth_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GA4MCP_OAUTH_ENABLED", raising=False)
    monkeypatch.setenv("GA4MCP_AUTH_MODE", "bearer")
    monkeypatch.setenv("GA4MCP_BEARER_TOKEN", "static-token-for-claude")
    monkeypatch.setenv("GA4MCP_BEARER_FAILURE_HTTP_STATUS", "403")
    clear_settings_cache()


@pytest.mark.parametrize("path", METADATA_PATHS)
def test_metadata_served_without_credentials(oauth_on: None, path: str) -> None:
    """Requiring a token here would be circular: the client reads this to learn how
    to get one."""
    with TestClient(build_app()) as client:
        r = client.get(path)
    assert r.status_code == 200
    body = r.json()
    assert body["authorization_servers"] == [ISSUER]
    assert body["bearer_methods_supported"] == ["header"]


def test_metadata_resource_matches_requested_host(oauth_on: None) -> None:
    """`resource` must equal the URL the user typed into the client, so it follows
    the Host header rather than a separate setting that could drift out of sync."""
    with TestClient(build_app(), base_url="https://ga4.example.com") as client:
        r = client.get(METADATA_PATHS[0])
    assert r.json()["resource"] == "https://ga4.example.com/mcp"


@pytest.mark.parametrize("path", METADATA_PATHS)
def test_metadata_absent_when_oauth_disabled(oauth_off: None, path: str) -> None:
    with TestClient(build_app()) as client:
        r = client.get(path)
    assert r.status_code == 404


def test_missing_token_challenges_with_metadata_pointer(oauth_on: None) -> None:
    with TestClient(build_app(), base_url="https://ga4.example.com") as client:
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    challenge = r.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert (
        'resource_metadata="https://ga4.example.com/.well-known/oauth-protected-resource"'
        in challenge
    )


def test_missing_token_keeps_configured_status_when_oauth_disabled(oauth_off: None) -> None:
    """Bearer-only deployments must not start advertising OAuth."""
    with TestClient(build_app()) as client:
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 403
    assert "www-authenticate" not in r.headers


def test_static_token_still_accepted_while_oauth_enabled(oauth_on: None) -> None:
    """Enabling discovery must not lock out the clients that already work."""
    with TestClient(build_app()) as client:
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={
                "Authorization": "Bearer static-token-for-claude",
                "Accept": "application/json, text/event-stream",
            },
        )
    assert r.status_code != 401
