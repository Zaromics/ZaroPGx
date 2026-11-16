"""The scorer's copy of PharmCAT's allele names must be PharmCAT's.

`scripts/getrm/pharmcat_alleles.tsv` is 1,353 rows lifted out of the jar: the alleles
PharmCAT defines, which `score.py` reads to tell an allele GeT-RM called that PharmCAT
cannot name. A GeT-RM value with such an allele is set aside before comparing, and the
verdict says so.

So the snapshot decides what counts as an error. Bump PharmCAT without refreshing it and
the scorer keeps setting aside alleles the new version names perfectly well -- the
concordance goes up, nothing fails, and the number is wrong in the flattering direction.
That is the same shape as the defect in the report's warnings box: a copy of a format that
production has moved on from, with no one to notice.

Two checks, because they fail at different times. The version in the snapshot's own header
is checked against the version the stack pins, which needs nothing but the repository. The
alleles themselves are checked against the definitions the pharmcat container writes out
of the jar it actually downloaded, which needs a machine that has run the stack -- true of
a developer's and of the e2e job, and skipped rather than guessed elsewhere.
"""

from __future__ import annotations

import csv
import gzip
import json
import re
from collections import defaultdict
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SNAPSHOT = REPO / "scripts" / "getrm" / "pharmcat_alleles.tsv"
#: What docker/pharmcat/definitions.py writes at container start, from the pinned jar.
DEFINITIONS = REPO / "data" / "pharmcat" / "allele-definitions.json.gz"


def pinned_version() -> str:
    """The PharmCAT the stack builds against, as compose.yml defaults it."""
    text = (REPO / "compose.yml").read_text(encoding="utf-8")
    found = re.search(
        r"PHARMCAT_VERSION[:=]\s*\$\{PHARMCAT_VERSION:-([0-9][^}]*)\}", text
    )
    assert found, "compose.yml no longer carries a PharmCAT version default"
    return found.group(1).strip()


def snapshot_alleles() -> dict:
    by_gene = defaultdict(set)
    with open(SNAPSHOT, encoding="utf-8") as fh:
        rows = csv.DictReader(
            (line for line in fh if not line.startswith("#")), delimiter="\t"
        )
        for row in rows:
            by_gene[row["gene"]].add(row["allele"])
    return dict(by_gene)


def test_the_snapshot_names_the_version_the_stack_pins():
    """Its first line says which PharmCAT it was taken from. If that is not the one
    being built, the snapshot is a previous release's answer."""
    header = SNAPSHOT.read_text(encoding="utf-8").splitlines()[0]
    version = pinned_version()

    assert version in header, (
        f"{SNAPSHOT.name} says {header.strip()[:80]!r} but the stack pins PharmCAT "
        f"{version}. Re-extract it from the new jar, or score.py will keep setting "
        "aside alleles this version can name."
    )


def test_the_snapshot_is_not_empty_and_covers_the_called_genes():
    alleles = snapshot_alleles()

    assert len(alleles) >= 20, alleles.keys()
    assert sum(len(v) for v in alleles.values()) > 1000
    for gene in ("CYP2C19", "CYP2C9", "NAT2", "UGT1A1", "SLCO1B1", "TPMT", "DPYD"):
        assert alleles.get(gene), f"no {gene} alleles in the snapshot"


@pytest.mark.skipif(
    not DEFINITIONS.exists(),
    reason="no data/pharmcat/allele-definitions.json.gz here; the pharmcat container "
    "writes it on first start",
)
def test_the_snapshot_matches_the_definitions_the_container_extracted():
    """Same source, two copies: the TSV lifted by hand and the gzip the container
    writes out of the jar it downloaded. They are allowed to cover different genes --
    the container's export is the called set -- but never to disagree about one."""
    with gzip.open(DEFINITIONS) as fh:
        live = json.load(fh)
    snapshot = snapshot_alleles()

    assert live.get("pharmcat_version") == pinned_version()
    differences = {}
    for gene, entry in live["genes"].items():
        if gene not in snapshot:
            continue
        names = {allele["n"] for allele in entry["alleles"]}
        missing, extra = names - snapshot[gene], snapshot[gene] - names
        if missing or extra:
            differences[gene] = {
                "not in the snapshot": sorted(missing)[:8],
                "only in the snapshot": sorted(extra)[:8],
            }

    assert not differences, differences


@pytest.mark.skipif(not DEFINITIONS.exists(), reason="see above")
def test_the_two_alleles_the_scorer_says_pharmcat_cannot_name_are_still_unnamed():
    """score.py's docstring says PharmCAT has no UGT1A1 *60 and no NUDT15 *2, and reads
    a GeT-RM call carrying one as the reference allele. The day either is defined, that
    reading turns a real disagreement into a match."""
    with gzip.open(DEFINITIONS) as fh:
        genes = json.load(fh)["genes"]

    for gene, allele in (("UGT1A1", "*60"), ("NUDT15", "*2")):
        names = {entry["n"] for entry in genes[gene]["alleles"]}
        assert allele not in names, (
            f"PharmCAT now defines {gene} {allele}; score.py sets it aside as unnameable "
            "and would score that disagreement as a match"
        )
