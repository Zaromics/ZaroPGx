"""compose.yml sets HAPI's schema handling with the key HAPI reads, and nothing else.

Until Nov 2026 it set ``spring.jpa.hibernate.ddl-auto=update``, which the starter never
reads (it builds Hibernate from ``spring.jpa.properties``), beside five
``hapi.fhir.jpa.*`` lines naming settings HAPI does not have. Measured on
hapiproject/hapi:v8.12.0-2 against an empty Postgres schema: ``ddl-auto=none`` and HAPI
built its 60 tables anyway; ``spring.jpa.properties.hibernate.hbm2ddl.auto=none`` and it
built none and exited. ``update`` came from the image's own application.yaml all along.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

COMPOSE = Path(__file__).resolve().parents[1] / "compose.yml"
READ = "spring.jpa.properties.hibernate.hbm2ddl.auto"
IGNORED = ("spring.jpa.hibernate.ddl-auto", "hapi.fhir.jpa.")


@pytest.fixture(scope="module")
def environment():
    service = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    env = service["fhir-server"]["environment"]
    return dict(item.split("=", 1) for item in env)


def test_the_schema_is_set_by_the_key_hapi_reads(environment):
    assert environment.get(READ) == "update"


def test_no_setting_hapi_ignores_is_left_to_look_like_one(environment):
    ignored = [k for k in environment if k.startswith(IGNORED)]
    assert ignored == []


def test_the_schema_stays_in_its_own_postgres_schema(environment):
    assert environment["spring.jpa.properties.hibernate.default_schema"] == "fhir"
    assert environment["spring.datasource.url"].endswith("?currentSchema=fhir")
