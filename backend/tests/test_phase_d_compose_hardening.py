"""Phase D - D5 (plus the compose half of D3): production container hardening.

Two layers of evidence:

* STATIC - the overlay file itself uses ``!override`` so the base file's
  ``./backend:/app`` source bind mount and postgres's published port cannot
  silently merge back in, every host-specific value is a ``${VAR:?...}``
  interpolation rather than a hardcoded domain, and Google OAuth stays
  optional.
* MERGED - ``docker compose config`` is run with synthetic values and the
  real merge is asserted. This is the only way to prove what Docker will
  actually do; reading the two files separately cannot show that a bind mount
  survived the merge. It is read-only and starts nothing.

Synthetic values only: nothing here is a real secret, and no value is ever
printed - assertions compare, they do not echo.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from app.core import config as config_module

BACKEND = Path(config_module.__file__).resolve().parent.parent.parent
REPO_ROOT = BACKEND.parent
PROD = REPO_ROOT / "docker-compose.prod.yml"
BASE = REPO_ROOT / "docker-compose.yml"
N8N = REPO_ROOT / "docker-compose.n8n.yml"

requires_compose_files = pytest.mark.skipif(
    not (PROD.is_file() and BASE.is_file()),
    reason="repository compose files not present (backend-only image)",
)

# Values the production overlay makes mandatory. Filled with obviously
# synthetic placeholders purely so `docker compose config` can compute a
# merge; none of them is a secret and none is checked in anywhere.
_DUMMY = {
    "POSTGRES_USER": "syntheticuser",
    "POSTGRES_PASSWORD": "synthetic-not-a-real-password-0123456789",
    "POSTGRES_DB": "syntheticdb",
    "SECRET_KEY": "synthetic-" + "K7f2Qx9ZpL4mNb8vRc3TwY6dH1JgA5eU0" * 2,
    "INGEST_API_KEY": "synthetic-" + "I9g1Wp4TnR2mQz7Xk5Vb3Lc8Hs6Df0Ja" * 2,
    "AUTH_PASSWORD": "synthetic-auth-password-0123456789",
    "FRONTEND_URL": "https://app.example.internal",
    "OAUTH_REDIRECT_URI": "https://api.example.internal/api/integrations/google/callback",
    "CORS_ORIGINS": "https://app.example.internal",
    "OLLAMA_BASE_URL": "http://ollama:11434",
    "FRONTEND_API_URL": "https://api.example.internal",
}


# ---------------------------------------------------------------------------
# loaders
# ---------------------------------------------------------------------------


class _OverrideLoader(yaml.SafeLoader):
    """Accept Docker Compose's ``!override`` merge directive."""


def _construct_override(loader, node):
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node)


_OverrideLoader.add_constructor("!override", _construct_override)


def _load(path: Path) -> dict:
    return yaml.load(path.read_text(), Loader=_OverrideLoader)


def _bind_sources(services: dict) -> list[str]:
    found = []
    for name, service in services.items():
        for volume in service.get("volumes") or []:
            if isinstance(volume, dict) and volume.get("type") == "bind":
                found.append(f"{name}:{volume.get('source')}")
    return found


@pytest.fixture(scope="module")
def merged() -> dict:
    """The real merged production configuration, as Docker computes it."""
    compose = shutil.which("docker")
    if compose is None:
        pytest.skip("docker CLI not available")
    probe = subprocess.run(
        [compose, "compose", "version"], capture_output=True, text=True, timeout=60
    )
    if probe.returncode != 0:
        pytest.skip("docker compose plugin not available")

    env = {**os.environ, **_DUMMY}
    proc = subprocess.run(
        [
            compose,
            "compose",
            "-f",
            str(BASE),
            "-f",
            str(PROD),
            "config",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"docker compose config failed:\n{proc.stderr}"
    return yaml.safe_load(proc.stdout)


# ---------------------------------------------------------------------------
# static: the overlay says what it means
# ---------------------------------------------------------------------------


@requires_compose_files
def test_postgres_is_unpublished_via_override_not_a_merge():
    """A plain ``ports: []`` would MERGE with the base file's loopback publish
    and the database would still be reachable from the host."""
    text = PROD.read_text()
    assert "ports: !override []" in text, (
        "postgres must drop its published port with !override"
    )
    prod = _load(PROD)
    assert prod["services"]["postgres"]["ports"] == []


@requires_compose_files
def test_the_backend_source_bind_mount_is_dropped_with_override():
    text = PROD.read_text()
    assert "volumes: !override" in text
    prod = _load(PROD)
    mounts = prod["services"]["backend"]["volumes"]
    # Every entry must be a named volume in short syntax ("NAME:/target"),
    # never a host path ("./backend:/app" or an absolute one).
    assert mounts, "the overlay must mount something (writable DB-owned dirs)"
    assert all(isinstance(v, str) for v in mounts), f"expected short syntax: {mounts}"
    assert all(not v.startswith(("./", "/")) for v in mounts), mounts
    targets = {v.split(":", 1)[1] for v in mounts}
    assert targets == {"/app/uploads", "/app/cache", "/app/vapid"}
    assert "/app/credentials" not in targets, (
        "OAuth credentials live in the database; credentials/ is read-only"
    )
    # And the env_file entry must remain a file reference, not a mount.
    prod_backend = _load(PROD)["services"]["backend"]
    entries = prod_backend.get("env_file") or []
    paths = [e["path"] if isinstance(e, dict) else e for e in entries]
    assert any(str(p).endswith(".env") for p in paths), "env_file must stay a file"


@requires_compose_files
def test_backend_runs_read_only_non_root_and_capability_free():
    prod = _load(PROD)["services"]["backend"]
    assert prod["user"] == "10001:10001"
    assert prod["read_only"] is True
    assert prod["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in prod["security_opt"]
    assert prod["tmpfs"] == ["/tmp:mode=1777"]
    limits = prod["deploy"]["resources"]["limits"]
    assert limits["memory"] and limits["cpus"]


@requires_compose_files
def test_frontend_runs_read_only_non_root_and_capability_free():
    prod = _load(PROD)["services"]["frontend"]
    assert prod["user"] == "1000:1000"
    assert prod["read_only"] is True
    assert prod["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in prod["security_opt"]
    assert "/app/.next/cache:mode=1777" in prod["tmpfs"]
    limits = prod["deploy"]["resources"]["limits"]
    assert limits["memory"] and limits["cpus"]


@requires_compose_files
def test_the_frontend_url_is_a_build_argument_never_a_runtime_env():
    prod = _load(PROD)["services"]["frontend"]
    assert prod["build"]["args"]["NEXT_PUBLIC_API_URL"] == (
        "${FRONTEND_API_URL:?FRONTEND_API_URL must be set "
        "(public browser-facing backend URL, e.g. https://api.example.com)}"
    )
    assert "environment" not in prod or "NEXT_PUBLIC_API_URL" not in prod["environment"]


@requires_compose_files
def test_every_host_specific_value_is_an_interpolation_not_a_domain():
    """No future production domain may be written into a tracked file."""
    prod = _load(PROD)
    for service in prod["services"].values():
        for key, value in (service.get("environment") or {}).items():
            if not isinstance(value, str):
                continue
            if "://" in value:
                assert value.startswith("${"), f"{key} hardcodes a URL: {value!r}"
        for arg in ((service.get("build") or {}).get("args") or {}).values():
            if isinstance(arg, str) and "://" in arg:
                assert arg.startswith("${"), f"build arg hardcodes a URL: {arg!r}"


@requires_compose_files
def test_the_deployment_addresses_are_mandatory_and_never_defaulted():
    text = PROD.read_text()
    for name in (
        "FRONTEND_URL",
        "OAUTH_REDIRECT_URI",
        "CORS_ORIGINS",
        "FRONTEND_API_URL",
        "OLLAMA_BASE_URL",
        "SECRET_KEY",
        "INGEST_API_KEY",
        "AUTH_PASSWORD",
        "POSTGRES_PASSWORD",
    ):
        assert f"${{{name}:?" in text, f"{name} must be mandatory in production"


@requires_compose_files
def test_google_oauth_remains_optional():
    """Google can be switched off deliberately; a deployment that does not use
    it must still be able to start."""
    text = PROD.read_text()
    assert "${GOOGLE_CLIENT_ID:-}" in text
    assert "${GOOGLE_CLIENT_SECRET:-}" in text
    assert "${GOOGLE_CLIENT_ID:?" not in text
    assert "${GOOGLE_CLIENT_SECRET:?" not in text


@requires_compose_files
def test_production_reads_host_configuration_from_a_file_not_the_image():
    """backend/.dockerignore keeps .env out of the image and this overlay
    removes the bind mount, so the env_file is what keeps non-secret business
    settings (schedule defaults, retention, VAPID contact) configurable."""
    prod = _load(PROD)
    entries = prod["services"]["backend"].get("env_file") or []
    paths = [e["path"] if isinstance(e, dict) else e for e in entries]
    assert paths == ["./backend/.env"]
    if entries and isinstance(entries[0], dict):
        assert entries[0]["required"] is False


@requires_compose_files
def test_the_documented_postgres_exceptions_are_explicit():
    """postgres keeps a root-start privilege hand-off, so NNP/cap_drop are NOT
    set on it. Those three settings have not been verified against the
    official image, and guessing on the database is how a deployment loses its
    data directory. Its exposure is removed by not publishing the port, and it
    still gets a memory ceiling."""
    prod = _load(PROD)["services"]["postgres"]
    assert "cap_drop" not in prod
    assert "read_only" not in prod
    assert "security_opt" not in prod
    assert prod["deploy"]["resources"]["limits"]["memory"]


@requires_compose_files
def test_the_ollama_model_server_is_loopback_only_and_unmetered():
    prod = _load(PROD)["services"]["ollama"]
    ports = prod["ports"]
    assert ports == ["127.0.0.1:11434:11434"], ports
    # A loaded model is measured in GiB: an arbitrary ceiling would OOM-kill
    # inference mid-request, so the sidecar is deliberately unlimited.
    assert "deploy" not in prod


@requires_compose_files
def test_the_n8n_admin_ui_is_loopback_only():
    assert "127.0.0.1:5678:5678" in N8N.read_text()
    n8n = yaml.safe_load(N8N.read_text())
    ports = n8n["services"]["n8n"]["ports"]
    assert ports == ["127.0.0.1:5678:5678"], ports
    assert not any(str(p).startswith("0.0.0.0") for p in ports)


@requires_compose_files
def test_the_development_compose_keeps_every_service_on_loopback():
    text = BASE.read_text()
    for bind in (
        "${POSTGRES_HOST_BIND:-127.0.0.1}",
        "${BACKEND_HOST_BIND:-127.0.0.1}",
        "${FRONTEND_HOST_BIND:-127.0.0.1}",
        "${OLLAMA_HOST_BIND:-127.0.0.1}",
    ):
        assert bind in text, f"{bind} must be the default bind address"


@requires_compose_files
def test_the_development_compose_builds_the_frontend_url_in():
    base = _load(BASE)
    frontend = base["services"]["frontend"]
    assert frontend["build"]["args"]["NEXT_PUBLIC_API_URL"].startswith("${FRONTEND_API_URL")
    environment = frontend.get("environment") or {}
    assert "NEXT_PUBLIC_API_URL" not in environment, (
        "a runtime environment entry cannot change an already-built bundle"
    )


# ---------------------------------------------------------------------------
# merged: what Docker will actually do
# ---------------------------------------------------------------------------


def test_no_host_source_bind_mount_survives_the_production_merge(merged):
    assert _bind_sources(merged["services"]) == []


def test_postgres_publishes_no_port_in_the_production_merge(merged):
    assert not merged["services"]["postgres"].get("ports")


def test_backend_hardening_survives_the_production_merge(merged):
    backend = merged["services"]["backend"]
    assert backend["user"] == "10001:10001"
    assert backend["read_only"] is True
    assert backend["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in backend["security_opt"]
    assert "/tmp:mode=1777" in backend["tmpfs"]
    targets = sorted(v["target"] for v in backend["volumes"])
    assert targets == ["/app/cache", "/app/uploads", "/app/vapid"]
    assert all(v["type"] == "volume" for v in backend["volumes"])
    limits = backend["deploy"]["resources"]["limits"]
    assert limits["memory"] == "1610612736" or int(limits["memory"]) == 1610612736
    assert float(limits["cpus"]) == 1.5


def test_frontend_hardening_survives_the_production_merge(merged):
    frontend = merged["services"]["frontend"]
    assert frontend["user"] == "1000:1000"
    assert frontend["read_only"] is True
    assert frontend["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in frontend["security_opt"]
    assert "/app/.next/cache:mode=1777" in frontend["tmpfs"]
    assert frontend["build"]["args"]["NEXT_PUBLIC_API_URL"] == _DUMMY["FRONTEND_API_URL"]
    assert "NEXT_PUBLIC_API_URL" not in (frontend.get("environment") or {})
    limits = frontend["deploy"]["resources"]["limits"]
    assert int(limits["memory"]) == 536870912
    assert float(limits["cpus"]) == 0.5


def test_postgres_merge_keeps_its_documented_exceptions_and_its_ceiling(merged):
    postgres = merged["services"]["postgres"]
    assert "cap_drop" not in postgres
    assert "read_only" not in postgres
    assert int(postgres["deploy"]["resources"]["limits"]["memory"]) == 1073741824
    assert postgres["networks"] or postgres.get("network_mode")


def test_ollama_merge_stays_loopback_only(merged):
    ollama = merged["services"]["ollama"]
    assert len(ollama.get("ports") or []) == 1
    port = ollama["ports"][0]
    host_ip = port.get("host_ip") if isinstance(port, dict) else None
    assert host_ip == "127.0.0.1", port
    assert "deploy" not in ollama


def test_mandatory_values_reach_the_backend_environment(merged):
    """`environment:` must beat the env_file - otherwise the mandatory
    secrets would be quietly satisfied by whatever backend/.env happens to
    hold."""
    environment = merged["services"]["backend"]["environment"]
    for key in (
        "SECRET_KEY",
        "INGEST_API_KEY",
        "AUTH_PASSWORD",
        "FRONTEND_URL",
        "OAUTH_REDIRECT_URI",
        "OLLAMA_BASE_URL",
    ):
        assert environment[key] == _DUMMY[key], f"{key} was overridden by env_file"


def test_the_env_file_still_contributes_business_configuration(merged):
    """Non-secret settings an operator must be able to tune in production."""
    environment = merged["services"]["backend"]["environment"]
    for key in ("VAPID_CLAIMS_EMAIL", "EMAIL_RETENTION_DAYS"):
        assert key in environment, f"{key} must remain configurable"


def test_the_production_merge_keeps_the_named_volumes(merged):
    assert {"pgdata", "uploads", "app_cache", "vapid_keys", "ollama_data"} <= set(
        merged.get("volumes", {})
    )
