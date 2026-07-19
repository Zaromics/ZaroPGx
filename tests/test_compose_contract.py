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
