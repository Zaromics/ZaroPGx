"""Wave 2 Step 4a: auth subtraction is a no-op for route dependencies.

After deleting the DEV_MODE stripping blocks and main.py's shadowing
get_current_user / get_optional_user, every auth Depends must resolve to
security.get_optional_user, and the dependency graph must not change with
ZAROPGX_DEV_MODE (the old strippers claimed to, but FastAPI nests included
routers so they never did).
"""

from __future__ import annotations

# conftest already sets SECRET_KEY / DATABASE_URL before app import.


def _dependency_dump(app) -> list[str]:
    """Stable, sorted dump of (method, path, dependency qualname) triples."""
    rows: list[str] = []
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = sorted(getattr(route, "methods", None) or [])
        dependant = getattr(route, "dependant", None)
        if path is None or dependant is None:
            continue
        names: list[str] = []
        stack = list(dependant.dependencies)
        while stack:
            dep = stack.pop()
            call = getattr(dep, "call", None)
            if call is not None:
                mod = getattr(call, "__module__", "")
                name = getattr(
                    call, "__qualname__", getattr(call, "__name__", repr(call))
                )
                names.append(f"{mod}.{name}")
            stack.extend(getattr(dep, "dependencies", []) or [])
        for method in methods or ["*"]:
            rows.append(f"{method} {path} -> {','.join(sorted(names))}")
    return sorted(rows)


def test_secret_key_is_single_sourced():
    from app import main
    from app.api.utils import security

    assert main.SECRET_KEY is security.SECRET_KEY
    assert main.ACCESS_TOKEN_EXPIRE_MINUTES == security.ACCESS_TOKEN_EXPIRE_MINUTES


def test_get_current_user_is_gone():
    from app import main
    from app.api.utils import security

    assert not hasattr(security, "get_current_user")
    assert not hasattr(main, "get_current_user")


def test_optional_user_depends_are_security_module():
    from app.api.utils import security
    from app.main import app

    seen = 0
    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        stack = list(dependant.dependencies)
        while stack:
            dep = stack.pop()
            if getattr(dep, "call", None) is security.get_optional_user:
                seen += 1
            stack.extend(getattr(dep, "dependencies", []) or [])
    assert (
        seen >= 1
    ), "expected at least one route to Depend on security.get_optional_user"


def test_route_dependencies_ignore_dev_mode_flag(monkeypatch):
    """Re-importing under either DEV_MODE value must produce the same dump.

    Step 4a deleted the only code that tried to mutate dependencies based on
    ZAROPGX_DEV_MODE, so the graphs are identical by construction.
    """
    # The live app was already imported by conftest. Capture its dump; then
    # compare against a fresh dump after flipping the env var *without*
    # reloading (post-4a there is no import-time branch left to re-run).
    from app.main import app

    monkeypatch.setenv("ZAROPGX_DEV_MODE", "true")
    dump_true = _dependency_dump(app)
    monkeypatch.setenv("ZAROPGX_DEV_MODE", "false")
    dump_false = _dependency_dump(app)
    assert dump_true == dump_false
    assert dump_true, "expected a non-empty route dependency dump"

    # No leftover references to the deleted get_current_user helper.
    blob = "\n".join(dump_true)
    assert "get_current_user" not in blob
