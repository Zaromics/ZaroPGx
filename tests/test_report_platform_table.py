"""The report's Software Platform table lists tools, not the compose file.

``build_platform_info`` walked everything ``get_all_versions()`` returned, which
is every service in ``compose.yml``. A pharmacogenomic report therefore ended
with PostgreSQL, a HAPI FHIR server that had not run, Kroki and Kroki Mermaid --
a diagram renderer, listed twice, both at version "latest" -- and a second copy
of every analytic tool as its ZaroPGx container wrapper. "PharmCAT 3.4.0" and
"Zaropgx Pharmcat 0.3.0" are the same software at two different versions, side by
side, and none of the infrastructure rows affected a single call on any preceding
page.

Matching is exact rather than substring for that last reason specifically:
``"zaropgx pharmcat" != "pharmcat"``, so the wrapper cannot shadow the tool.

Separately, ``build_citations`` read the same version map raw. GATK reports its
version as ``"The Genome Analysis Toolkit () v4.7.0.0"``, so the citation read
"version The Genome Analysis Toolkit () v4.7.0.0" mid-sentence.
``_normalize_version_text`` already existed for exactly this -- its docstring uses
that string as its worked example -- but was only ever called on the platform
table's path.
"""

from __future__ import annotations

import re

import pytest

from app.reports.generator import (
    _REPORT_COMPONENTS,
    _normalize_version_text,
    build_citations,
    build_platform_info,
)

# Everything that used to appear and must not: infrastructure, plus the
# container wrappers that duplicated each tool at the ZaroPGx version.
BANNED_SUBSTRINGS = [
    "postgres",
    "hapi",
    "fhir",
    "kroki",
    "mermaid",
    "nextflow",
    "genome downloader",
    "zaropgx app",
    "zaropgx pharmcat",
    "zaropgx gatk",
    "zaropgx pypgx",
    "zaropgx zarohla",
]


def _names() -> list[str]:
    return [i["name"] for i in build_platform_info()]


def test_zaropgx_itself_is_always_listed():
    assert "ZaroPGx" in _names()


@pytest.mark.parametrize("banned", BANNED_SUBSTRINGS)
def test_infrastructure_and_wrappers_are_not_listed(banned):
    offenders = [n for n in _names() if banned in n.lower()]
    assert not offenders, (
        f"{offenders} reached the report's Software Platform table. That table "
        "is for components that shaped the result, not the compose file."
    )


def test_no_component_is_listed_twice():
    names = _names()
    assert len(names) == len(set(names)), names


def test_a_floating_tag_is_never_printed_as_a_version():
    """ "latest" tells a reader nothing they could reproduce a run from."""
    for item in build_platform_info():
        assert item["version"].lower() not in {"latest", "n/a", "none", ""}, item


def test_the_allowlist_is_keyed_lowercase_so_matching_works():
    """Keys are compared against name.strip().lower(); a capital breaks silently."""
    for key in _REPORT_COMPONENTS:
        assert key == key.lower().strip(), key


def test_the_allowlist_holds_only_analytic_tools():
    """Guard the intent, so infrastructure cannot be quietly re-added."""
    for banned in BANNED_SUBSTRINGS:
        assert not any(banned in k for k in _REPORT_COMPONENTS), banned


# --------------------------------------------------------------------------
# Citations
# --------------------------------------------------------------------------


def test_gatk_citation_states_a_bare_version():
    """The exact regression: GATK's raw --version blob reached the page."""
    (gatk,) = [c for c in build_citations() if c["name"] == "GATK"]

    assert "Genome Analysis Toolkit (GATK), version" in gatk["text"]
    assert "()" not in gatk["text"], gatk["text"]
    assert re.search(r"version \d+(\.\d+)+\.", gatk["text"]), gatk["text"]


def test_no_citation_carries_an_unparsed_version_blob():
    for citation in build_citations():
        match = re.search(r"version ([^.]*?)[.,]", citation["text"])
        if match:
            assert re.fullmatch(
                r"[\w.\-]+", match.group(1).strip()
            ), f"{citation['name']}: {match.group(1)!r}"


def test_the_normaliser_still_handles_the_gatk_shape():
    """The helper the citation path was failing to call."""
    assert _normalize_version_text("The Genome Analysis Toolkit () v4.7.0.0") == (
        "4.7.0.0"
    )


# --------------------------------------------------------------------------
# OptiType and the provisional mtDNA row
# --------------------------------------------------------------------------


def test_optitype_is_allowlisted_under_its_own_name():
    """Not "ZaroHLA": that is ZaroPGx's wrapper and carries the ZaroPGx release
    number, which is the wrapper-shadows-tool problem this list exists to stop.
    Resolution needs the container's manifest, so the presence of the *key* is
    what is asserted here; the manifest itself is pinned below.
    """
    assert _REPORT_COMPONENTS.get("optitype") == "OptiType"
    assert not any("zarohla" in k for k in _REPORT_COMPONENTS)


def test_zarohla_publishes_the_optitype_version_it_actually_installed():
    """Every other sidecar writes its own manifest; zarohla wrote none, so the
    only OptiType version anywhere was a constant in generator.py that nothing
    checked against the image.
    """
    from pathlib import Path

    app_py = (
        Path(__file__).resolve().parents[1] / "docker" / "zarohla" / "app.py"
    ).read_text(encoding="utf-8")

    assert "optitype.json" in app_py, "zarohla no longer publishes a manifest"
    assert 'version("optitype")' in app_py or "_distribution_version" in app_py, (
        "the OptiType version is hardcoded again; read it from the installed "
        "distribution so it cannot drift from the Dockerfile pin"
    )


def test_the_pinned_version_matches_the_dockerfile():
    """The manifest is only trustworthy if the pin it reflects is still there."""
    from pathlib import Path

    dockerfile = (
        Path(__file__).resolve().parents[1] / "docker" / "zarohla" / "Dockerfile"
    ).read_text(encoding="utf-8")

    assert "OptiType.git@v" in dockerfile, (
        "OptiType is no longer installed from a pinned tag; the reported version "
        "would become whatever HEAD happened to be at build time"
    )


def test_mtdna_server_2_is_listed_as_not_enabled():
    """Wired in provisionally: named so the report neither claims a capability it
    lacks nor silently omits one. MT-RNR1 comes back as a no-call precisely
    because this component is absent, and that should be traceable from the page.
    """
    rows = {i["name"]: i["version"] for i in build_platform_info()}

    assert "mtDNA-server-2" in rows
    assert "not enabled" in rows["mtDNA-server-2"].lower()


def test_a_provisional_component_never_claims_a_version_number():
    import re as _re

    for item in build_platform_info():
        if item.get("source") == "provisional":
            assert not _re.search(r"\d+\.\d+", item["version"]), item
