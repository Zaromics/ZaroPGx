"""The bundle says why a verdict was withheld, not just that there isn't one.

The AMP/CAP Tier 1 gate empties a gene's verdict when the run could not assess an
allele a clinical assay for that gene must include. The report says so on the page --
an alert naming the gene and the alleles, and, beside the drug guidance, that no
genotype is under it.

The bundle said `valueCodeableConcept: {code: "unknown", text: "Unknown"}` and nothing
else. A receiving system could not tell that from a gene a tool tested and could not
call, and the two mean different things to whoever reads it next: this one says the
assay did not cover what a clinical test for the gene must cover, so a proper
genotyping test would still be informative.

This is the mirror of the two defects fixed in #265 and #266, where the bundle was
honest and the page was not.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.reports import tier1_gate
from app.services.fhir_export_service import FHIRExportService


def _withheld_row(missing=("*2", "*3", "*8")):
    rows = [{"gene": "CYP2C9", "diplotype": "*1/*1", "phenotype": "Normal Metabolizer"}]
    report = {
        "genes": {
            "CYP2C9": {
                "uncalledHaplotypes": list(missing),
                "sourceDiplotypes": [
                    {"allele1": {"name": "*1"}, "allele2": {"name": "*1"}}
                ],
            }
        }
    }
    assert tier1_gate.withhold_inadequate_calls(rows, report) == ["CYP2C9"]
    return rows[0]


def _observation(row):
    return FHIRExportService(MagicMock())._create_genotype_observation(
        row, "urn:uuid:p", "run-1"
    )


def _notes(observation):
    return [note.get("text", "") for note in observation.get("note") or []]


def test_the_note_names_the_alleles_the_run_could_not_assess():
    observation = _observation(_withheld_row())

    notes = " ".join(_notes(observation))
    assert "*2, *3, *8" in notes
    assert "AMP/CAP minimum allele set" in notes


def test_the_value_is_still_unknown():
    """DataAbsentReason has no code for "the assay was inadequate for this gene", so
    the value stays `unknown` and the note carries the meaning. Inventing a code
    would make the bundle unreadable to anything that validates it."""
    observation = _observation(_withheld_row())

    coding = observation["valueCodeableConcept"]["coding"][0]
    assert coding["code"] == "unknown"
    assert coding["system"].endswith("data-absent-reason")


def test_a_called_gene_carries_no_such_note():
    row = {"gene": "CYP2C9", "diplotype": "*1/*2", "phenotype": "Intermediate"}

    observation = _observation(row)

    assert not [n for n in _notes(observation) if "could not assess" in n]


def test_a_marker_with_no_alleles_adds_no_empty_note():
    """The gate always names what was missed, but a note reading "could not assess ,
    which the AMP/CAP minimum allele set includes" would be worse than none."""
    row = _withheld_row()
    row["tier1_withheld"] = {"gene": "CYP2C9", "missing": [], "citation": "x"}

    assert not [n for n in _notes(_observation(row)) if "could not assess" in n]


def test_it_joins_the_other_notes_rather_than_replacing_them():
    """Three other things can annotate a genotype observation; this is a fourth, and
    the FHIR note field is a list for exactly that reason."""
    row = _withheld_row()
    row["unlisted_flag"] = {"fhir_note": "An unlisted variant was found."}
    row["caller_disagreement"] = {"fhir_note": "The two callers disagree."}

    notes = _notes(_observation(row))

    assert len(notes) == 3
    assert any("could not assess" in n for n in notes)
    assert "An unlisted variant was found." in notes
    assert "The two callers disagree." in notes
