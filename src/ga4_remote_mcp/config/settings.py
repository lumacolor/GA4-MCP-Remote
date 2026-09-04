"""Environment configuration (GA4MCP_*)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GA4MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    port: int = 8080
    log_level: str = "INFO"
    env: Literal["development", "staging", "production"] = "development"

    auth_mode: Literal["none", "bearer"] = "none"
    bearer_token: str = ""
    # Dify は HTTP 401 を OAuth 再認証の合図とみなし /.well-known を探す。
    # 403 にすると誤った Bearer でも「OAuth メタデータ取得失敗」ではなく通常の接続エラーになる。
    # Cloud Run / env では "403" が文字列で渡るため before バリデータで int に寄せる。
    bearer_failure_http_status: Literal[401, 403] = 401

    @field_validator("bearer_failure_http_status", mode="before")
    @classmethod
    def coerce_bearer_failure_http_status(cls, v: object) -> object:
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
        return v

    # --- OAuth discovery (RFC 9728) ---
    # ChatGPT only offers OAuth for custom connectors -- no static-header option --
    # and refuses a server that does not advertise protected-resource metadata.
    # Off by default so existing bearer-only deployments keep answering 403 and
    # never invite a client into an OAuth flow this server cannot finish.
    oauth_enabled: bool = False
    oauth_issuer: str = ""
    oauth_scopes: str = ""
    # Audience check: tokens must be issued for *our* client, otherwise a token
    # the user granted to any other Google app would be accepted here.
    oauth_client_id: str = ""
    # Authentication establishes who somebody is; it does not entitle them to
    # this customer's analytics. That is what the allowlist is for.
    oauth_allowed_emails: str = ""

    allowed_property_ids: str = ""
    allow_all_properties: bool = False

    max_date_range_days: int = Field(default=366, ge=1)
    max_row_limit: int = Field(default=100_000, ge=1, le=250_000)
    max_concurrent_per_property: int = Field(default=4, ge=1)
    enable_realtime: bool = True
    enable_google_ads_links: bool = False
    return_property_quota_default: bool = False
    request_timeout_ms: int = Field(default=60_000, ge=1000)

    # Default off for local dev; enable in production (see README).
    enable_dns_rebinding_protection: bool = False
    allowed_hosts: str = ""
    allowed_origins: str = ""

    json_response: bool = False

    trusted_proxy_hops: int = Field(
        default=0,
        ge=0,
        le=10,
        description="If >0, take client IP from X-Forwarded-For (first hop after proxy).",
    )

    @field_validator("bearer_token", mode="before")
    @classmethod
    def strip_bearer_token(cls, v: object) -> str:
        if v is None:
            return ""
        return str(v).strip()

    @field_validator("allowed_property_ids", mode="before")
    @classmethod
    def strip_csv(cls, v: str) -> str:
        if v is None:
            return ""
        return str(v).strip()

    @model_validator(mode="after")
    def validate_bearer(self) -> Settings:
        if self.auth_mode == "bearer" and not self.bearer_token.strip():
            msg = "GA4MCP_BEARER_TOKEN is required when GA4MCP_AUTH_MODE=bearer"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def validate_production_auth(self) -> Settings:
        # Public deployments of this server (Cloud Run + --allow-unauthenticated
        # is the documented path) must require a bearer token. Refuse to start
        # in production without it so an operator misconfiguration does not
        # silently expose GA4 data over the network.
        if self.env == "production" and self.auth_mode == "none":
            msg = (
                "GA4MCP_AUTH_MODE=none is not allowed when GA4MCP_ENV=production. "
                "Set GA4MCP_AUTH_MODE=bearer and provide GA4MCP_BEARER_TOKEN "
                "(scripts/deploy-cloud-run.sh wires this from "
                "GA4MCP_BEARER_SECRET_NAME via Secret Manager)."
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def validate_production_allowlist(self) -> Settings:
        if self.env == "production" and not self.allow_all_properties:
            ids = self.parsed_allowed_property_ids()
            if not ids:
                msg = (
                    "Production requires GA4MCP_ALLOWED_PROPERTY_IDS non-empty "
                    "or GA4MCP_ALLOW_ALL_PROPERTIES=true"
                )
                raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def validate_oauth(self) -> Settings:
        if not self.oauth_enabled:
            return self
        missing = [
            name
            for name, value in (
                ("GA4MCP_OAUTH_ISSUER", self.oauth_issuer),
                ("GA4MCP_OAUTH_CLIENT_ID", self.oauth_client_id),
                ("GA4MCP_OAUTH_ALLOWED_EMAILS", self.oauth_allowed_emails),
            )
            if not value.strip()
        ]
        if missing:
            # Starting without these would advertise an OAuth flow that either
            # cannot complete or, worse, would accept anyone Google authenticates.
            msg = f"{', '.join(missing)} required when GA4MCP_OAUTH_ENABLED=true"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def validate_dns_hosts(self) -> Settings:
        if self.enable_dns_rebinding_protection and self.env == "production":
            if not self.allowed_hosts.strip():
                msg = (
                    "GA4MCP_ALLOWED_HOSTS is required when DNS rebinding protection is on "
                    "in production"
                )
                raise ValueError(msg)
        return self

    def parsed_allowed_property_ids(self) -> frozenset[str]:
        if not self.allowed_property_ids.strip():
            return frozenset()
        out: set[str] = set()
        for p in self.allowed_property_ids.split(","):
            s = p.strip()
            if not s:
                continue
            if s.startswith("properties/"):
                s = s.split("/", 1)[-1].strip()
            out.add(s)
        return frozenset(out)

    def parsed_oauth_allowed_emails(self) -> frozenset[str]:
        if not self.oauth_allowed_emails.strip():
            return frozenset()
        return frozenset(
            e.strip().lower() for e in self.oauth_allowed_emails.split(",") if e.strip()
        )

    def parsed_oauth_scopes(self) -> list[str]:
        if not self.oauth_scopes.strip():
            return []
        return [s.strip() for s in self.oauth_scopes.split(",") if s.strip()]

    def parsed_allowed_hosts(self) -> list[str]:
        if not self.allowed_hosts.strip():
            return []
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

    def parsed_allowed_origins(self) -> list[str]:
        if not self.allowed_origins.strip():
            return []
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
