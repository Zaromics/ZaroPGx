"""Invariants the compose file must hold.

These are cheap structural assertions over compose.yml, not a running stack. They exist
because the properties they check are easy to undo by accident in a 440-line YAML file and
expensive to notice afterwards: a service that quietly starts listening on every interface
looks exactly like one that does not.

Parsed as plain YAML rather than via `docker compose config` so the suite stays runnable in
CI without a Docker daemon. That means ${VAR:-default} appears literally, which is what the
default-value assertions below check.
"""

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

COMPOSE = Path(__file__).resolve().parent.parent / "compose.yml"

# The app is the front door and is meant to be reachable; BIND_ADDRESS governs it.
PUBLICLY_BINDABLE = {"app"}

# Reached only over the compose network by the app and by each other. None of them
# authenticate, so none may be published to a non-loopback interface by default.
INTERNAL_SERVICES = {
    "db",
    "genome-downloader",
    "pharmcat",
    "fhir-server",
    "gatk-api",
    "pypgx",
    "zarohla",
    "kroki",
    "docs",
}


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _published(entry):
    """Return (host_ip, rest) for a compose short-form port mapping."""
    text = entry if isinstance(entry, str) else str(entry.get("published", ""))
    # "127.0.0.1:5444:5432" -> host_ip 127.0.0.1; "5444:5432" -> no host_ip
    m = re.match(r"^(?P<ip>\$\{[^}]+\}|[\d.]+):(?P<rest>\d+:\d+)$", text)
    if m:
        return m.group("ip"), m.group("rest")
    return None, text


def test_internal_services_are_not_published_to_all_interfaces(compose):
    offenders = []
    for name, svc in compose["services"].items():
        if name in PUBLICLY_BINDABLE:
            continue
        for entry in svc.get("ports") or []:
            host_ip, _ = _published(entry)
            if host_ip is None:
                offenders.append(
                    f"{name}: {entry!r} has no host interface, so it binds 0.0.0.0"
                )
            elif "INTERNAL_BIND_ADDRESS" not in host_ip and host_ip != "127.0.0.1":
                offenders.append(f"{name}: {entry!r} binds {host_ip}")
    assert not offenders, "services published beyond loopback:\n  " + "\n  ".join(
        offenders
    )


def test_internal_bind_address_defaults_to_loopback(compose):
    """An unset or blank INTERNAL_BIND_ADDRESS must fail safe, not open up."""
    for name in INTERNAL_SERVICES:
        for entry in compose["services"][name].get("ports") or []:
            host_ip, _ = _published(entry)
            assert (
                host_ip == "${INTERNAL_BIND_ADDRESS:-127.0.0.1}"
            ), f"{name} must use the shared knob with a loopback default, got {host_ip!r}"


def test_nextflow_is_never_published(compose):
    """runner.py's POST /run is unauthenticated and the service mounts the docker socket.

    A host mapping there is remote code execution, so loopback is not sufficient — it must
    not be published at all.
    """
    nextflow = compose["services"]["nextflow"]
    assert not nextflow.get("ports"), (
        "nextflow must not publish a host port: it exposes an unauthenticated POST /run "
        "and bind-mounts /var/run/docker.sock. Use expose: instead."
    )
    assert "/var/run/docker.sock" in " ".join(
        nextflow.get("volumes") or []
    ), "premise check: if nextflow no longer mounts the docker socket, revisit this test"


def test_app_port_is_operator_controlled(compose):
    """The app is the one service meant to be reachable; keep BIND_ADDRESS in charge."""
    ports = compose["services"]["app"].get("ports") or []
    assert any(
        "BIND_ADDRESS" in str(p) for p in ports
    ), "the app's host mapping must stay driven by BIND_ADDRESS"


def _env_entries(service):
    """Flatten compose environment (list or mapping) to a list of 'KEY=...' strings."""
    env = service.get("environment") or {}
    if isinstance(env, dict):
        return [f"{k}={v}" for k, v in env.items()]
    return [str(item) for item in env]


def test_db_password_has_no_shared_default(compose):
    """A missing DB_PASSWORD must hard-fail at compose parse, not silently use test123."""
    text = COMPOSE.read_text(encoding="utf-8")
    assert "${DB_PASSWORD:-test123}" not in text
    assert "${DB_PASSWORD:?" in text

    db_env = " ".join(_env_entries(compose["services"]["db"]))
    fhir_env = " ".join(_env_entries(compose["services"]["fhir-server"]))
    nextflow_env = " ".join(_env_entries(compose["services"]["nextflow"]))
    assert "${DB_PASSWORD:?" in db_env
    assert "${DB_PASSWORD:?" in fhir_env
    assert "${DB_PASSWORD:?" in nextflow_env
    assert any(
        e.startswith("DB_PASSWORD=")
        for e in _env_entries(compose["services"]["nextflow"])
    )


def test_tracked_env_templates_ship_no_working_credentials():
    """Tracked profiles must not publish a real SECRET_KEY or DB_PASSWORD."""
    root = COMPOSE.parent
    banned = {
        "test123",
        "supersecretkey",
        "supersecretkey_for_development",
        "change_me",
        "change_me_in_production",
        "zaropgx_password",
    }
    for name in (".env.example", ".env.local", ".env.production"):
        values = {}
        for line in (root / name).read_text(encoding="utf-8").splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
        assert values.get("SECRET_KEY", "") not in banned
        assert values.get("DB_PASSWORD", "") not in banned
        assert values.get("SECRET_KEY", "") == ""
        assert values.get("DB_PASSWORD", "") == ""


def _env_file_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        keys.add(line.partition("=")[0].strip())
    return keys


# Keys that exist only for compose interpolation / image selection, not app code.
_COMPOSE_ONLY_ENV_KEYS = {
    "ZAROPGX_TAG",
    "BIND_ADDRESS",
    "INTERNAL_BIND_ADDRESS",
    "NETWORK_SUBNET",
    "HAPI_FHIR_TAG",
    "PHARMCAT_VERSION",
    "JAVA_OPTS",
    "DOWNLOAD_ON_STARTUP",
    "MAX_MEMORY",
    "PYPGX_MEMORY_LIMIT",
    "PYPGX_MAX_PARALLEL_GENES",
    "PYPGX_BATCH_SIZE",
    "PYPGX_PHARMCAT_PREFERENCE",
    "PHARMCAT_LOG_LEVEL",
    "PHARMCAT_ABSENT_TO_REF",
    "PHARMCAT_UNSPECIFIED_TO_REF",
    "DB_USER",
    "DB_PASSWORD",
    "DB_HOST",
    "DB_PORT",
    "DB_NAME",
}


def test_app_declares_env_file(compose):
    env_files = compose["services"]["app"].get("env_file") or []
    assert env_files, "app must declare env_file so .env reaches the container"
    # Compose 2.24+ long form: {path: .env, required: false}
    normalized = []
    for entry in env_files:
        if isinstance(entry, dict):
            normalized.append(entry.get("path"))
        else:
            normalized.append(str(entry))
    assert ".env" in normalized


def test_app_environment_does_not_hardcode_behavioural_keys(compose):
    """Keys that also live in .env.example must not be bare literals in compose."""
    example_keys = _env_file_keys(COMPOSE.parent / ".env.example")
    offenders = []
    for entry in _env_entries(compose["services"]["app"]):
        if "=" not in entry:
            continue
        key, _, value = entry.partition("=")
        if key not in example_keys:
            continue
        if value.startswith("${"):
            continue
        # Topology URLs are intentionally hardcoded (compose owns topology).
        if (
            key.endswith("_URL")
            or key.endswith("_PATH")
            or key
            in {
                "PYTHONPATH",
                "WORKFLOW_API_BASE",
                "PYTHONDONTWRITEBYTECODE",
                "DATABASE_URL",
            }
        ):
            continue
        offenders.append(entry)
    assert (
        not offenders
    ), "behavioural keys hardcoded in app environment:\n  " + "\n  ".join(offenders)


def test_include_pharmcat_json_tsv_default_true_in_profiles():
    root = COMPOSE.parent
    for name in (".env.example", ".env.local", ".env.production"):
        values = {}
        for line in (root / name).read_text(encoding="utf-8").splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
        assert values.get("INCLUDE_PHARMCAT_JSON") == "true", name
        assert values.get("INCLUDE_PHARMCAT_TSV") == "true", name
        assert "KROKI_URL" not in values, f"{name} must not ship KROKI_URL"


def test_env_example_keys_are_referenced_or_allowlisted():
    """Every .env.example key must be used somewhere, or listed as compose-only."""
    root = COMPOSE.parent
    example_keys = _env_file_keys(root / ".env.example")
    search_roots = [
        root / "app",
        root / "docker",
        root / "pipelines",
        root / "scripts",
        root / "compose.yml",
    ]
    corpus_parts: list[str] = []
    for base in search_roots:
        if base.is_file():
            corpus_parts.append(base.read_text(encoding="utf-8", errors="replace"))
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {
                ".py",
                ".sh",
                ".ps1",
                ".yml",
                ".yaml",
                ".nf",
                ".md",
                ".env",
                ".txt",
                ".cfg",
                ".toml",
            }:
                continue
            try:
                corpus_parts.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    corpus = "\n".join(corpus_parts)
    missing = sorted(
        k for k in example_keys if k not in _COMPOSE_ONLY_ENV_KEYS and k not in corpus
    )
    # Keys appear as themselves in .env.example; strip that file from the check
    # by requiring a hit outside the example file — corpus already excludes it.
    assert not missing, (
        "orphaned .env.example keys (not referenced, not allowlisted):\n  "
        + "\n  ".join(missing)
    )


def test_docs_do_not_teach_bare_bind_address_zero():
    """BIND_ADDRESS=0.0.0.0 alone is an invalid compose hostPort."""
    root = COMPOSE.parent
    bad = re.compile(r"(?m)^BIND_ADDRESS=0\.0\.0\.0\s*$")
    offenders = []
    for path in (root / "docs").rglob("*.md"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if bad.search(text):
            offenders.append(str(path.relative_to(root)))
    assert not offenders, "docs still teach BIND_ADDRESS=0.0.0.0:\n  " + "\n  ".join(
        offenders
    )


def test_docs_do_not_ufw_allow_internal_service_ports():
    root = COMPOSE.parent
    deployment = (root / "docs" / "developer" / "deployment.md").read_text(
        encoding="utf-8"
    )
    for port in ("5444", "5001", "5002", "5053", "8090"):
        assert (
            f"ufw allow {port}" not in deployment
        ), f"deployment.md still opens internal port {port} in the firewall"
