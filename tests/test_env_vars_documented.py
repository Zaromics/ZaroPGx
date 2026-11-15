"""Every setting the app reads is written down where someone can find it.

contributing.md states the rule: "Document a new variable in `.env.example` and in
docs/user/configuration". Nothing checked it, and sixteen variables had drifted out
of one or both -- among them `MTDNA_ENABLED`, which an operator who has not deployed
the mtDNA service needs, and `SHARED_INPUT_ROOTS`, which decides what a service may
read off the host in place.

Not every name a module reads is a setting. Some are injected by the pipeline for one
process, some are set by Compose where a value in `.env` is ignored, and some are
internal paths inside the image. Each of those is named below with the reason, so the
list is a decision someone made rather than a gap nobody noticed.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Injected per-process by the Nextflow pipeline or the job client: not configuration,
# and an operator setting one in .env would have no effect.
RUNTIME_INJECTED = {
    "JOB_ID",
    "STEP_NAME",
    "NXF_PROCESS_NAME",
    "JOB_API_BASE",
}

# Set by compose.yml, where configuration.md's "Set by Compose" section says a value
# in .env is ignored. They matter only to code run outside Compose.
SET_BY_COMPOSE = {
    "DATABASE_URL",
    "KROKI_URL",
    "PHARMCAT_JAR_PATH",
}

# Paths and switches internal to an image or to a developer tool, with no operator
# meaning: changing one does not configure the deployment, it breaks it.
INTERNAL = {
    "ALLELE_MAP_JSON",
    "PHARMCAT_DEFINITIONS",
    "POSTGRES_VERSION",
    "VERSION_MANAGER_INCLUDE_ALL_COMPOSE",
}

EXEMPT = RUNTIME_INJECTED | SET_BY_COMPOSE | INTERNAL

READS = re.compile(
    r"""(?:os\.getenv|os\.environ\.get|env_flag)\(\s*["']([A-Z][A-Z0-9_]*)["']"""
)


def _variables_read() -> dict:
    found = {}
    for path in (REPO / "app").rglob("*.py"):
        for name in READS.findall(path.read_text(encoding="utf-8", errors="replace")):
            found.setdefault(name, str(path.relative_to(REPO)))
    return found


def test_every_setting_is_in_the_example_env():
    example = (REPO / ".env.example").read_text(encoding="utf-8")
    listed = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", example, re.M))

    missing = {
        name: where
        for name, where in _variables_read().items()
        if name not in listed and name not in EXEMPT
    }

    assert not missing, "read by the app, absent from .env.example: " + ", ".join(
        f"{n} ({w})" for n, w in sorted(missing.items())
    )


def test_every_setting_is_in_the_configuration_doc():
    doc = (REPO / "docs" / "user" / "configuration.md").read_text(encoding="utf-8")

    missing = {
        name: where
        for name, where in _variables_read().items()
        if not re.search(rf"\b{re.escape(name)}\b", doc) and name not in EXEMPT
    }

    assert (
        not missing
    ), "read by the app, absent from docs/user/configuration.md: " + ", ".join(
        f"{n} ({w})" for n, w in sorted(missing.items())
    )


def test_the_exemptions_are_all_still_read_somewhere():
    """An exemption for a variable nothing reads any more is a stale excuse, and it
    would quietly cover a future variable that happened to take the same name."""
    read = set(_variables_read())
    stale = sorted(name for name in EXEMPT if name not in read)

    assert not stale, "exempted but no longer read by the app: " + ", ".join(stale)
