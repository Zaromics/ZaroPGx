import os

import httpx
import pytest

from tests.e2e.harness import e2e_requested


@pytest.fixture(scope="session")
def e2e_base_url(pytestconfig) -> str:
    if not e2e_requested(
        os.environ,
        cli_flag=bool(pytestconfig.getoption("--zaropgx-e2e")),
    ):
        pytest.skip(
            "Set ZAROPGX_E2E=1 or pass --zaropgx-e2e, and bring the stack up "
            "via scripts/e2e-up.sh / scripts/e2e.sh"
        )
    return os.environ.get("ZAROPGX_E2E_BASE_URL", "http://127.0.0.1:18765").rstrip("/")


@pytest.fixture(scope="session")
def e2e_client(e2e_base_url):
    try:
        with httpx.Client(base_url=e2e_base_url, timeout=60.0) as client:
            r = client.get("/health")
            if r.status_code != 200:
                pytest.skip(f"app not healthy at {e2e_base_url}: {r.status_code}")
            yield client
    except httpx.HTTPError as exc:
        pytest.skip(f"app not reachable at {e2e_base_url}: {exc}")
