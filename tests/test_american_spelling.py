"""User-facing text uses American spellings.

The project's own voice is American -- the README, and the sources it cites (CPIC,
the FDA, PharmGKB, ClinPGx). Agent-written prose had drifted British across the
report, the website and the docs: `colour`, `licence`, `analyse`, `normalise`,
`grey`, and `metaboliser` for the CPIC phenotype term. The owner noticed it on the
website.

Three kinds of exception, each deliberate:

* a registered name inside a verbatim legal notice -- SNOMED's "International
  Health Terminology Standards Development Organisation (IHTSDO)" is that body's
  name and the notice must be reproduced as given;
* a library's own symbol -- ReportLab's `colors.grey`;
* code identifiers nothing renders -- `credits.licence`, `_INLINE_SCRIPT_LICENCES`.
  Renaming those changes no text a reader sees, so they are left for a refactor of
  their own rather than mixed into a prose change.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

BRITISH = [
    "colour",
    "behaviour",
    "recognise",
    "analyse",
    "centre",
    "catalogue",
    "favour",
    "labelled",
    "modelling",
    "organisation",
    "summarise",
    "normalise",
    "defence",
    "licence",
    "grey",
    "fulfil",
    "whilst",
    "amongst",
    "initialise",
    "serialise",
    "optimise",
    "customise",
    "utilise",
    "standardise",
    "prioritise",
    "minimise",
    "maximise",
    "visualise",
    "metaboliser",
    "metabolise",
]
# -ed/-ers/-ing/-s on the same stems, so `analysed` and `metabolisers` are caught.
PATTERN = re.compile(
    r"\b(" + "|".join(BRITISH) + r")(s|d|r|rs|ed|es|ing)?\b", re.IGNORECASE
)

ALLOWED = (
    re.compile(r"Terminology Standards Development Organisation"),
    re.compile(r"Organisation \(IHTSDO\)"),
    re.compile(r"colors\.grey"),
    re.compile(r"_INLINE_SCRIPT_LICENCES"),
    re.compile(r"\blicence\b(?=\s*[:=)]|\s*$)"),
)

SURFACES = ["app", "docs", "README.md", "scripts", "pipelines"]
SUFFIXES = {".py", ".html", ".md", ".js", ".css", ".rst", ".txt", ".nf", ".sh"}


def _files():
    for name in SURFACES:
        root = ROOT / name
        if root.is_file():
            yield root
            continue
        for path in root.rglob("*"):
            if (
                path.is_file()
                and path.suffix in SUFFIXES
                and "_build" not in path.parts
                and "node_modules" not in path.parts
                # Vendored third-party code (Swagger UI, ReDoc) ships as its
                # authors wrote it; rewriting it would be a lie about its source.
                and "vendor" not in path.parts
                and path.name != Path(__file__).name
            ):
                yield path


FILES = sorted(_files())


def test_the_audit_covers_the_surfaces_it_claims_to():
    names = {p.name for p in FILES}

    assert "index.html" in names, "the website"
    assert "report_template.html" in names, "the PDF report"
    assert "credits.md" in names, "the docs"
    assert len(FILES) > 100, len(FILES)


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_british_spelling(path):
    found = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for m in PATTERN.finditer(line):
            if any(a.search(line) for a in ALLOWED):
                continue
            found.append((n, m.group(0)))

    assert not found, (
        f"{path.relative_to(ROOT)} uses British spelling: {found[:6]}. "
        "This project writes American English; see the module docstring for the "
        "three exceptions."
    )
