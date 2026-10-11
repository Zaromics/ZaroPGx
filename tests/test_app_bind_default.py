"""The app listens on loopback unless an operator widens it.

The templates shipped BIND_ADDRESS=8765 (and .env.production 0.0.0.0:8765), which
Compose publishes on every interface; with ZAROPGX_AUTH_MODE=open that put every
report on the local network without a password, while the comments said
"localhost only".
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _env_value(path: Path, key: str) -> str:
    match = re.search(rf"(?m)^{key}=(.*)$", path.read_text(encoding="utf-8"))
    assert match, f"{path.name} has no {key}"
    return match.group(1).strip()


def test_every_template_binds_the_app_to_loopback():
    for name in (".env.example", ".env.local", ".env.production"):
        assert _env_value(ROOT / name, "BIND_ADDRESS") == "127.0.0.1:8765", name


def test_composes_fallback_is_loopback_too():
    compose = yaml.safe_load((ROOT / "compose.yml").read_text(encoding="utf-8"))
    assert compose["services"]["app"]["ports"] == [
        "${BIND_ADDRESS:-127.0.0.1:8765}:8000"
    ]
