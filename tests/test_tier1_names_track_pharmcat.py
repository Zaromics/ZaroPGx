"""The Tier 1 gate's allele names are tied to the PharmCAT they were read from.

``amp_tier1.TIER1`` decides whether a verdict is reportable by asking whether PharmCAT
left a Tier 1 allele uncalled, and it asks by **name**. AMP writes DPYD's Tier 1 as
``c.1905+1G>A`` and PharmCAT writes ``c.1905+1G>A (*2A)``, so the table carries
PharmCAT's spellings, read out of a real ``uncallableHaplotypes`` list.

A PharmCAT that renames one does not break anything loudly: the name stops
matching, the gene looks adequate, and **the gate fails open -- every gene reportable,
silently**, which
is the one failure a gate must not have. ``docs/to-do.md`` records this as "a
re-read on every PharmCAT bump". This makes the re-read a test rather than a chore,
by failing at the bump itself: raise ``PHARMCAT_VERSION`` without re-reading the
names and CI says so.

``test_amp_tier1.py`` already pins the spellings against real 3.4.0 output, so what
was missing was only the tie between those facts and the version the stack builds.

**The allele definitions are the wrong oracle for this, which cost an attempt to
learn.**
``data/pharmcat/allele-definitions.json.gz`` names what PharmCAT defines by
variant, and comparing the table against it reports **CYP2D6 ``*5``** as unknown --
correctly, because ``*5`` is the whole-gene deletion and has no defining variant. A
check built on that file would either be wrong about structural alleles or carve
them out by hand. ``uncallableHaplotypes`` is the list that matters, and it is
per-run output rather than a file in the repository, which is why the fixtures in
``test_amp_tier1.py`` carry it.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.pharmcat.amp_tier1 import TIER1, VERIFIED_AGAINST

REPO = Path(__file__).resolve().parents[1]

#: The file whose fixtures are verbatim `uncallableHaplotypes` output, with the version
#: they were taken from in a comment above them.
SPELLINGS = REPO / "tests" / "test_amp_tier1.py"

#: The annotation bundle pins its own PharmCAT, and fetches that release's
#: pharmcat_positions.vcf by checksum (test_annotation_bundle.py ties the two).
BUNDLE = REPO / "docker" / "genome-downloader" / "annotation_bundle.py"


def _pinned_version() -> str:
    """The PharmCAT the stack builds against, as compose.yml defaults it."""
    text = (REPO / "compose.yml").read_text(encoding="utf-8")
    found = re.search(
        r"PHARMCAT_VERSION[:=]\s*\$\{PHARMCAT_VERSION:-([0-9][^}]*)\}", text
    )
    assert found, "compose.yml no longer carries a PharmCAT version default"
    return found.group(1).strip()


def test_the_table_was_read_from_the_pharmcat_the_stack_pins():
    pinned = _pinned_version()
    assert VERIFIED_AGAINST == pinned, (
        f"amp_tier1.VERIFIED_AGAINST is {VERIFIED_AGAINST} and the stack pins PharmCAT "
        f"{pinned}. Re-read the uncallableHaplotypes names out of the new jar before "
        "bumping it: a renamed allele makes the gate fail open, every gene reportable, "
        "with nothing to say so."
    )


def test_the_fixtures_say_which_pharmcat_they_came_from():
    """They are verbatim output, so they are a previous release's answer after a
    bump."""
    text = SPELLINGS.read_text(encoding="utf-8")
    stated = re.search(r"PharmCAT ([0-9][0-9.]*)'s own uncallableHaplotypes", text)
    assert stated, (
        f"{SPELLINGS.name} no longer says which PharmCAT its uncallableHaplotypes "
        "fixtures were measured from"
    )
    assert stated.group(1) == VERIFIED_AGAINST, (
        f"{SPELLINGS.name}'s fixtures are from PharmCAT {stated.group(1)} and the "
        f"table was read from {VERIFIED_AGAINST}; one of the two is stale"
    )


def test_the_table_asks_about_something():
    """Every assertion about the names is over TIER1; an empty one would pass them all.

    Not hypothetical: a draft of this file read ``tier.primary`` and
    ``tier.secondary``, fields ``Tier1Set`` does not have, so it compared nothing and
    reported every name as matching. The real field is ``alleles``.

    One gene's set is empty on purpose -- AMP placed CYP4F2*3 in Tier 2 and nothing in
    Tier 1, and the entry records that it was looked up -- so the check is that the
    table as a whole names something, not that each gene does.
    """
    assert TIER1, "amp_tier1.TIER1 is empty"
    named = {allele for tier in TIER1.values() for allele in tier.alleles}
    assert len(named) > 10, (
        f"the whole table names only {sorted(named)}, which is too few to be the "
        "AMP/CAP minimum sets; a check over it would be testing nothing"
    )


def test_every_entry_records_where_it_came_from():
    """Including the empty one: an empty set is a finding about AMP, not a blank."""
    for symbol, tier in TIER1.items():
        assert tier.citation, f"{symbol} cites no source"
        assert tier.source, f"{symbol} does not say which tier its set is"


def test_the_annotation_bundle_pins_the_pharmcat_the_stack_runs():
    """The positions panel and the caller must be the same release.

    ``annotation_bundle.PHARMCAT_VERSION`` chooses which release's
    ``pharmcat_positions.vcf`` is fetched, and ``test_annotation_bundle.py`` ties that
    choice to its checksum and its URL -- but nothing tied it to the PharmCAT the stack
    actually runs. Bump one and not the other and the pipeline pre-fills the positions
    of one release while another calls them, which no checksum notices because each
    file is the right file for the version named beside it.
    """
    text = BUNDLE.read_text(encoding="utf-8")
    declared = re.search(r'^PHARMCAT_VERSION\s*=\s*"([^"]+)"', text, re.M)
    assert declared, f"{BUNDLE.name} no longer declares PHARMCAT_VERSION as a literal"

    pinned = _pinned_version()
    assert declared.group(1) == pinned, (
        f"{BUNDLE.name} builds the annotation bundle against PharmCAT "
        f"{declared.group(1)} and the stack runs {pinned}: the positions panel and the "
        "caller would be different releases"
    )
