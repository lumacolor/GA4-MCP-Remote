"""Smoke tests for ``scripts/deploy-cloud-run.sh``'s production-auth guard.

These tests run the script in a subprocess with a hand-built environment
and a stubbed ``gcloud`` binary placed at the front of ``PATH``. The goal
is to exercise *only* the early guard logic; the gcloud invocations
further down are intercepted by the stub so they cannot reach Google
Cloud or block on auth/network.

Why the stub: GitHub-hosted ``ubuntu-latest`` runners ship with a real
``gcloud`` at ``/usr/bin/gcloud``, so a PATH that only excludes our own
fakes still resolves to the real binary and the test can hang for the
full subprocess timeout. Stubbing makes the test deterministic on every
runner.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "deploy-cloud-run.sh"

STUB_GCLOUD_MARKER = "stub-gcloud invoked"


def _safe_path() -> str:
    """Return a PATH containing core shell utilities (``mktemp``, ``cat``, ...)."""
    candidate_dirs = ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    return ":".join(d for d in candidate_dirs if pathlib.Path(d).exists())


def _make_stub_bin(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a temp dir with a stub ``gcloud`` that exits non-zero immediately.

    The stub prints :data:`STUB_GCLOUD_MARKER` to stderr so tests can
    assert whether the script reached the gcloud step or not.
    """
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir()
    gcloud_stub = stub_bin / "gcloud"
    gcloud_stub.write_text(f"#!/usr/bin/env bash\necho '{STUB_GCLOUD_MARKER}' >&2\nexit 127\n")
    gcloud_stub.chmod(0o755)
    return stub_bin


VALID_IMAGE = "europe-west3-docker.pkg.dev/dummy-project/ga4-mcp/ga4-remote-mcp:v0.1.0"
DUMMY_SA = "ga4-mcp-test@dummy-project.iam.gserviceaccount.com"


def _base_env(stub_bin: pathlib.Path) -> dict[str, str]:
    """Every variable the script requires, so a test can drop exactly one."""
    return {
        "PATH": f"{stub_bin}:{_safe_path()}",
        "GCP_PROJECT_ID": "dummy-project",
        "GA4MCP_ALLOWED_PROPERTY_IDS": "123456789",
        "GA4MCP_IMAGE": VALID_IMAGE,
        "CLOUD_RUN_SERVICE_ACCOUNT": DUMMY_SA,
        "GA4MCP_BEARER_SECRET_NAME": "ga4-remote-mcp-bearer",
    }


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=10
    )


pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="bash is required for shell-script smoke tests",
)


def test_script_passes_static_syntax_check() -> None:
    """Regression net: ``bash -n`` catches accidental shell-syntax mistakes."""
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_script_blocks_when_bearer_secret_unset(tmp_path: pathlib.Path) -> None:
    env = _base_env(_make_stub_bin(tmp_path))
    del env["GA4MCP_BEARER_SECRET_NAME"]
    result = _run(env)
    assert result.returncode != 0
    assert "GA4MCP_BEARER_SECRET_NAME" in result.stderr
    combined = result.stdout + result.stderr
    # The guard must fire before any gcloud invocation or post-guard echo.
    assert "Project=" not in combined
    assert STUB_GCLOUD_MARKER not in combined


def test_script_passes_guard_when_bearer_secret_set(tmp_path: pathlib.Path) -> None:
    result = _run(_base_env(_make_stub_bin(tmp_path)))
    combined = result.stdout + result.stderr
    # Guard must NOT have fired (bearer secret is set), and the script
    # must have reached the first gcloud call (intercepted by the stub).
    assert "GA4MCP_BEARER_SECRET_NAME is not set" not in combined
    assert STUB_GCLOUD_MARKER in combined
    # The stub returns 127, and ``set -e`` propagates that.
    assert result.returncode != 0


def test_script_blocks_without_service_account(tmp_path: pathlib.Path) -> None:
    """Deploying without an explicit SA would silently use the default compute SA."""
    env = _base_env(_make_stub_bin(tmp_path))
    del env["CLOUD_RUN_SERVICE_ACCOUNT"]
    result = _run(env)
    assert result.returncode != 0
    assert "CLOUD_RUN_SERVICE_ACCOUNT" in result.stderr
    assert STUB_GCLOUD_MARKER not in result.stdout + result.stderr


def test_script_blocks_without_image(tmp_path: pathlib.Path) -> None:
    env = _base_env(_make_stub_bin(tmp_path))
    del env["GA4MCP_IMAGE"]
    result = _run(env)
    assert result.returncode != 0
    assert "GA4MCP_IMAGE" in result.stderr


@pytest.mark.parametrize(
    "image",
    [
        "europe-west3-docker.pkg.dev/p/r/ga4-remote-mcp:latest",
        "europe-west3-docker.pkg.dev/p/r/ga4-remote-mcp",
    ],
)
def test_script_rejects_unversioned_image(tmp_path: pathlib.Path, image: str) -> None:
    """Per-customer rollback needs a tag that keeps pointing at the same build."""
    env = _base_env(_make_stub_bin(tmp_path))
    env["GA4MCP_IMAGE"] = image
    result = _run(env)
    assert result.returncode != 0
    assert "version tag" in result.stderr
    assert STUB_GCLOUD_MARKER not in result.stdout + result.stderr
