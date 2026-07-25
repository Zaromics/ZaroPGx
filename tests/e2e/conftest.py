import os

import httpx
import pytest


@pytest.fixture(scope="session")
def e2e_base_url() -> str:
    if os.environ.get("ZAROPGX_E2E") != "1":
        pytest.skip(
            "Set ZAROPGX_E2E=1 and bring the stack up via scripts/e2e-up.sh"
        )
    return os.environ.get("ZAROPGX_E2E_BASE_URL", "http://127.0.0.1:18765").rstrip(
        "/"
    )


@pytest.fixture(scope="session")
def e2e_client(e2e_base_url):
    with httpx.Client(base_url=e2e_base_url, timeout=60.0) as client:
        r = client.get("/health")
        if r.status_code != 200:
            pytest.skip(f"app not healthy at {e2e_base_url}: {r.status_code}")
        yield client
