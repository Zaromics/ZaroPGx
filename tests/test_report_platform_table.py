"""The report's software list names the tools that did the work, not the compose file.

The Software Platform table once walked every service in ``compose.yml``: a
pharmacogenomic report ended with PostgreSQL, a HAPI FHIR server that had not run,
"Kroki Mermaid" at version "latest", and a second copy of every analytic tool as its
ZaroPGx container wrapper ("PharmCAT 3.4.0" beside "Zaropgx Pharmcat 0.3.0").

The credits (app/reports/credits.py) now name each tool and data source a run
used, at the version its service recorded. Kroki, Mermaid and Nextflow are among
them on purpose: Nextflow ran every step, Kroki and Mermaid drew the report's
diagram. The database, the FHIR server and the container wrappers still are not.

GATK records its version as ``"The Genome Analysis Toolkit () v4.7.0.0"``; the
report prints 4.7.0.0.
"""

from __future__ import annotations

import pytest

from app.reports.credits import CREDITS, CreditRun, report_credits

# Infrastructure and the container wrappers that duplicated each tool at the
# ZaroPGx version.
BANNED_SUBSTRINGS = [
    "postgres",
    "hapi",
    "fhir",
    "genome downloader",
    "zaropgx app",
    "zaropgx pharmcat",
    "zaropgx gatk",
    "zaropgx pypgx",
    "zaropgx zarohla",
    "zarohla",
]

VERSIONS = {
    "pharmcat": "3.4.0",
    "pypgx": "0.27.0",
    "gatk": "The Genome Analysis Toolkit () v4.7.0.0",
    "optitype": "1.5.0",
    "mtdna-server-2": "v2.1.16",
    "nextflow": "25.10.8",
    # What get_versions_dict() also returns: none of it may reach the report.
    "postgresql": "18.6",
    "hapi fhir server": "8.12.0",
    "kroki mermaid": "latest",
    "zaropgx pharmcat": "0.3.0",
}


def _software(run=None):
    return report_credits(run or CreditRun(), VERSIONS, {}, "0.4.0")["software"]


def _names() -> list[str]:
    return [row["name"] for row in _software()]


def test_zaropgx_itself_is_always_listed_first():
    assert _names()[0] == "ZaroPGx"
    assert _software()[0]["version"] == "0.4.0"


@pytest.mark.parametrize("banned", BANNED_SUBSTRINGS)
def test_infrastructure_and_wrappers_are_not_listed(banned):
    offenders = [n for n in _names() if banned in n.lower()]
    assert not offenders, (
        f"{offenders} reached the report's software list. It is for what shaped "
        "the result or drew the report, not the compose file."
    )


def test_no_credit_is_listed_twice():
    names = [credit.name for credit in CREDITS]
    assert len(names) == len(set(names)), names
    keys = [credit.key for credit in CREDITS]
    assert len(keys) == len(set(keys)), keys


def test_a_floating_tag_is_never_printed_as_a_version():
    """ "latest" tells a reader nothing they could reproduce a run from."""
    for row in _software():
        assert row["version"].lower() not in {"latest", "n/a", "none", ""}, row


def test_gatk_prints_a_bare_version():
    """The banner GATK records must not reach the page."""
    (gatk,) = [row for row in _software() if row["name"] == "GATK"]
    assert gatk["version"] == "4.7.0.0"


def test_mtdna_server_2_prints_its_release_without_the_v():
    (mtdna,) = [row for row in _software() if row["name"] == "mtDNA-Server 2"]
    assert mtdna["version"] == "2.1.16"


def test_a_tool_without_a_recorded_version_says_so():
    rows = report_credits(CreditRun(), {}, {}, None)["software"]
    for row in rows:
        if row["name"] not in ("Chart.js", "D3", "BWA-MEM"):
            assert row["version"] == "not recorded", row


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


def test_mtdna_server_2_publishes_its_own_manifest():
    """The manifest the row above depends on actually exists in the sidecar,
    the same way test_zarohla_publishes_the_optitype_version_it_actually_
    installed pins OptiType's.
    """
    from pathlib import Path

    app_py = (
        Path(__file__).resolve().parents[1] / "docker" / "mtdna-server-2" / "app.py"
    ).read_text(encoding="utf-8")

    assert (
        "mtdna-server-2.json" in app_py
    ), "docker/mtdna-server-2/app.py no longer publishes a version manifest"
