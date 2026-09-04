"""Verify Google OAuth access tokens for clients that cannot send a static header.

ChatGPT's connector dialog offers OAuth only, so a bearer-only server is refused
outright. Customers already hold a Google account -- they could not own a GA4
property otherwise -- so signing in with it avoids issuing them yet another
credential.

The token is checked against Google's tokeninfo endpoint and the resulting email
matched against a per-service allowlist.
"""

from __future__ import annotations

import hashlib
import time

import httpx

from ga4_remote_mcp.config.settings import Settings

TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"

# Google access tokens live about an hour. Caching for a fraction of that keeps
# the hot path off the network without holding a revoked token for long.
_CACHE_TTL_SECONDS = 300
_cache: dict[str, tuple[float, str | None]] = {}


def _cache_key(token: str) -> str:
    # Never key the cache on the raw token: this dict is easy to end up in a heap
    # dump or a debugger.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def clear_token_cache() -> None:
    _cache.clear()


async def verify_google_access_token(token: str, settings: Settings) -> str | None:
    """Return the verified email address, or ``None`` if the token is not acceptable.

    Three conditions must all hold. Dropping any one of them would open a hole:

    * ``aud`` equals our own client id. Without this check any Google token from
      any application would be accepted, so a user could authorise some unrelated
      app and replay its token here.
    * the address is verified by Google.
    * the address appears in this service's allowlist, either by name or through a
      whole-domain entry. Authentication says who somebody is; it does not say
      they may read this customer's analytics.
    """
    allowed = settings.parsed_oauth_allowed_emails()
    allowed_domains = settings.parsed_oauth_allowed_domains()
    if not (allowed or allowed_domains) or not settings.oauth_client_id.strip():
        # Fail closed: an OAuth deployment without these is misconfigured, and
        # settings validation already refuses to start in that state.
        return None

    key = _cache_key(token)
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(TOKENINFO_URL, params={"access_token": token})
    except httpx.HTTPError:
        # Treat an unreachable verifier as a failed check, never as a pass.
        return None

    email: str | None = None
    if resp.status_code == 200:
        info = resp.json()
        aud_ok = info.get("aud") == settings.oauth_client_id.strip()
        verified = str(info.get("email_verified", "")).lower() == "true"
        candidate = (info.get("email") or "").strip().lower()
        # endswith on "@domain" rather than a split: "@2grow.de" must not match
        # "someone@evil-2grow.de", and the leading "@" is what guarantees that.
        domain_ok = any(candidate.endswith(d) for d in allowed_domains)
        if aud_ok and verified and (candidate in allowed or domain_ok):
            email = candidate

    _cache[key] = (now + _CACHE_TTL_SECONDS, email)
    return email
