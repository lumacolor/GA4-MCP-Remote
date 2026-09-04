"""Google access tokens are only accepted under three conditions at once.

Each check closes a different hole, so each one is tested on its own:

* wrong audience -- a token the user granted to some unrelated Google app would
  otherwise be replayable here
* unverified address -- an unproven email must not match an allowlist entry
* address not on the allowlist -- being a valid Google user says nothing about
  being entitled to this customer's analytics
"""

from __future__ import annotations

import httpx
import pytest

from ga4_remote_mcp.auth import google_oauth
from ga4_remote_mcp.config.settings import Settings

CLIENT_ID = "123-abc.apps.googleusercontent.com"
ALLOWED = "kunde@example.com"


def _settings(**overrides: object) -> Settings:
    base = {
        "oauth_enabled": True,
        "oauth_issuer": "https://accounts.google.com",
        "oauth_client_id": CLIENT_ID,
        "oauth_allowed_emails": ALLOWED,
        "auth_mode": "bearer",
        "bearer_token": "static",
    }
    base.update(overrides)
    return Settings(**base)


def _stub_tokeninfo(monkeypatch: pytest.MonkeyPatch, payload: dict, status: int = 200) -> None:
    class _Response:
        status_code = status

        def json(self) -> dict:
            return payload

    class _Client:
        def __init__(self, *a: object, **k: object) -> None: ...
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: object) -> None: ...
        async def get(self, *a: object, **k: object) -> _Response:
            return _Response()

    monkeypatch.setattr(google_oauth.httpx, "AsyncClient", _Client)


@pytest.fixture(autouse=True)
def _clear() -> None:
    google_oauth.clear_token_cache()
    yield
    google_oauth.clear_token_cache()


@pytest.mark.asyncio
async def test_accepts_verified_allowlisted_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_tokeninfo(monkeypatch, {"aud": CLIENT_ID, "email": ALLOWED, "email_verified": "true"})
    assert await google_oauth.verify_google_access_token("t", _settings()) == ALLOWED


@pytest.mark.asyncio
async def test_rejects_token_issued_for_another_app(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_tokeninfo(
        monkeypatch,
        {"aud": "999-other.apps.googleusercontent.com", "email": ALLOWED, "email_verified": "true"},
    )
    assert await google_oauth.verify_google_access_token("t", _settings()) is None


@pytest.mark.asyncio
async def test_rejects_unverified_address(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_tokeninfo(monkeypatch, {"aud": CLIENT_ID, "email": ALLOWED, "email_verified": "false"})
    assert await google_oauth.verify_google_access_token("t", _settings()) is None


@pytest.mark.asyncio
async def test_rejects_address_outside_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_tokeninfo(
        monkeypatch, {"aud": CLIENT_ID, "email": "fremd@example.com", "email_verified": "true"}
    )
    assert await google_oauth.verify_google_access_token("t", _settings()) is None


@pytest.mark.asyncio
async def test_unreachable_verifier_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Client:
        def __init__(self, *a: object, **k: object) -> None: ...
        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *a: object) -> None: ...
        async def get(self, *a: object, **k: object) -> object:
            raise httpx.ConnectError("no network")

    monkeypatch.setattr(google_oauth.httpx, "AsyncClient", _Client)
    assert await google_oauth.verify_google_access_token("t", _settings()) is None


@pytest.mark.asyncio
async def test_accepts_address_from_an_allowed_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Agencies and operators change staff; naming each person would force a
    redeploy of every service they look after."""
    _stub_tokeninfo(
        monkeypatch,
        {"aud": CLIENT_ID, "email": "neue.person@2grow.de", "email_verified": "true"},
    )
    s = _settings(oauth_allowed_emails="@2grow.de," + ALLOWED)
    assert await google_oauth.verify_google_access_token("t", s) == "neue.person@2grow.de"


@pytest.mark.asyncio
async def test_domain_entry_does_not_match_a_lookalike(monkeypatch: pytest.MonkeyPatch) -> None:
    """ "@2grow.de" must not let "evil-2grow.de" in -- the leading @ is what stops it."""
    _stub_tokeninfo(
        monkeypatch,
        {"aud": CLIENT_ID, "email": "angreifer@evil-2grow.de", "email_verified": "true"},
    )
    s = _settings(oauth_allowed_emails="@2grow.de")
    assert await google_oauth.verify_google_access_token("t", s) is None


@pytest.mark.asyncio
async def test_domain_entry_still_requires_the_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    """A domain entry widens who may enter, never how a token may be obtained."""
    _stub_tokeninfo(
        monkeypatch,
        {
            "aud": "999-other.apps.googleusercontent.com",
            "email": "chef@2grow.de",
            "email_verified": "true",
        },
    )
    s = _settings(oauth_allowed_emails="@2grow.de")
    assert await google_oauth.verify_google_access_token("t", s) is None


@pytest.mark.asyncio
async def test_operator_domains_reach_every_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """The two operator domains are a constant in the onboarding script, not an
    input -- a service nobody at the operator can sign in to is a dead end."""
    _stub_tokeninfo(
        monkeypatch,
        {"aud": CLIENT_ID, "email": "moin@christophknaup.de", "email_verified": "true"},
    )
    s = _settings(
        oauth_allowed_emails="@impulsdigital.de,@christophknaup.de,@2grow.de,kunde@example.com"
    )
    assert await google_oauth.verify_google_access_token("t", s) == "moin@christophknaup.de"
