"""The FHIR bundle codes each drug in RxNorm where config/rxnorm_drugs.json has a code.

PharmCAT names a drug by its ClinPGx accession and its name; a receiving EHR matches
medications by RxNorm, so a text-only drug reached it as a string to read. The table
is built by scripts/build_rxnorm_drugs.py from the PharmCAT jar's own drug list and
NLM's RxNav, and is tied here to the PharmCAT the stack pins.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import app.reports.generator as generator_module
from app.services.fhir import report_data, rxnorm, terminology
from app.services.fhir_export_service import FHIRExportService

REPO = Path(__file__).resolve().parents[1]
TABLE = json.loads((REPO / "config" / "rxnorm_drugs.json").read_text("utf-8"))
ROWS = TABLE["drugs"]
FIXTURES_340 = [
    REPO / "test_data" / "pharmcat.example.v340.report.json",
    REPO / "test_data" / "pharmcat.unphased.example.report.json",
]


def _pinned_pharmcat() -> str:
    text = (REPO / "compose.yml").read_text(encoding="utf-8")
    found = re.search(
        r"PHARMCAT_VERSION[:=]\s*\$\{PHARMCAT_VERSION:-([0-9][^}]*)\}", text
    )
    assert found, "compose.yml no longer carries a PharmCAT version default"
    return found.group(1).strip()


# --- the table ----------------------------------------------------------------------


def test_the_table_was_built_from_the_pharmcat_the_stack_pins():
    """A PharmCAT bump adds drugs the table does not know; they would go out as text
    with nothing to say so. Rebuild: scripts/build_rxnorm_drugs.py."""
    assert TABLE["pharmcat_version"] == _pinned_pharmcat()


@pytest.mark.parametrize("fixture", FIXTURES_340, ids=lambda p: p.name)
def test_every_drug_a_real_report_names_is_in_the_table(fixture):
    report = json.loads(fixture.read_text(encoding="utf-8"))
    assert report["pharmcatVersion"] == TABLE["pharmcat_version"]
    named = {
        drug["id"]: name
        for section in report["drugs"].values()
        for name, drug in section.items()
    }
    assert named
    assert {k: v for k, v in named.items() if k not in ROWS} == {}
    for accession, name in named.items():
        assert ROWS[accession]["name"] == name


def test_every_code_says_what_it_is_and_how_it_was_found():
    for accession, row in ROWS.items():
        assert accession.startswith("PA"), accession
        if row["rxcui"] is None:
            assert row["tty"] is None and row["match"] is None, accession
            continue
        assert row["rxcui"].isdigit(), accession
        assert row["tty"] in ("IN", "MIN", "PIN", "BN"), accession
        assert row["match"] in ("exact", "normalized", "reviewed"), accession
        assert row["rxnorm_name"], accession
        if row["match"] == "exact" and accession not in SYNONYMS:
            # The concept whose name is the drug's name.
            assert row["rxnorm_name"].lower() == row["name"].lower(), accession


#: RxNav's exact match also matches a concept's synonyms. Each of these was read and
#: is the same substance under RxNorm's name (2026-11-18); a new one after a rebuild
#: fails the test above until someone has read it too.
SYNONYMS = {
    "PA164781042",  # flucloxacillin -> floxacillin (its US name)
    "PA166182735",  # lidocaine and tetracaine -> lidocaine / tetracaine
    "PA166268821",  # toluidine blue -> tolonium chloride
}


@pytest.mark.parametrize(
    "name, rxcui",
    [
        # Long-standing RxNorm ingredient codes, independent of how the table was
        # built.
        ("clopidogrel", "32968"),
        ("codeine", "2670"),
        ("warfarin", "11289"),
        # ClinPGx's own column says 224925, which is the brand Marinol.
        ("dronabinol", "10402"),
    ],
)
def test_known_codes(name, rxcui):
    [row] = [r for r in ROWS.values() if r["name"] == name]
    assert row["rxcui"] == rxcui


def test_most_drugs_are_coded():
    coded = sum(1 for row in ROWS.values() if row["rxcui"])
    assert coded / len(ROWS) > 0.95, f"{coded} of {len(ROWS)}"


# --- the lookup ---------------------------------------------------------------------


def test_by_accession_then_by_name():
    assert rxnorm.coding("PA449053", None)["code"] == "32968"
    # A bundle rebuilt from PharmCAT's database rows may have the name only.
    assert rxnorm.coding(None, "Clopidogrel")["code"] == "32968"
    assert rxnorm.coding("PA449053", "anything")["code"] == "32968"


def test_a_drug_without_a_code_stays_text():
    assert rxnorm.coding("PA166292902", "ribostamycin") is None
    assert rxnorm.drug_concept("ribostamycin", "PA166292902") == {
        "text": "ribostamycin"
    }
    assert rxnorm.drug_concept("not a drug") == {"text": "not a drug"}


def test_the_coding_is_rxnorms():
    concept = rxnorm.drug_concept("clopidogrel", "PA449053")
    assert concept == {
        "text": "clopidogrel",
        "coding": [
            {
                "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                "code": "32968",
                "display": "clopidogrel",
            }
        ],
    }
    assert terminology.RXNORM == concept["coding"][0]["system"]


def test_an_unreadable_table_leaves_every_drug_text(monkeypatch, tmp_path):
    monkeypatch.setattr(rxnorm, "TABLE", tmp_path / "missing.json")
    rxnorm._table.cache_clear()
    try:
        assert rxnorm.drug_concept("clopidogrel", "PA449053") == {"text": "clopidogrel"}
    finally:
        rxnorm._table.cache_clear()


# --- the bundle, from what the generator saves --------------------------------------


def _bundle_from_the_generator(monkeypatch, tmp_path):
    """generate_report over a real PharmCAT 3.4.0 report.json, then the bundle built
    from the data it saved, as save_fhir_export builds it."""
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG, key, key == "write_html"
            )
    monkeypatch.setattr(generator_module, "fhir_export_enabled", lambda: False)
    job_id = "11111111-1111-1111-1111-111111111111"
    generator_module.generate_report(
        {"data": json.loads(FIXTURES_340[0].read_text(encoding="utf-8"))},
        str(tmp_path),
        {"id": "p1"},
        job_id=job_id,
    )
    saved = json.loads((tmp_path / report_data.filename(job_id)).read_text("utf-8"))
    service = FHIRExportService(MagicMock())
    return service._build_fhir_bundle(saved["data"], "run-1", {"id": "p1"})


MEDICATION_ASSESSED = FHIRExportService.LOINC_CODES["medication_assessed"]


def _medication_concepts(bundle):
    concepts = []
    for entry in bundle["entry"]:
        resource = entry["resource"]
        profiles = resource.get("meta", {}).get("profile", [])
        if terminology.PROFILE_THERAPEUTIC_IMPLICATION in profiles:
            for component in resource.get("component", []):
                codes = [c.get("code") for c in component["code"].get("coding", [])]
                if MEDICATION_ASSESSED in codes:
                    concepts.append(("implication", component["valueCodeableConcept"]))
        if terminology.PROFILE_MEDICATION_RECOMMENDATION in profiles:
            for item in resource.get("input", []):
                concepts.append(("task", item["valueCodeableConcept"]))
    return concepts


def test_the_generators_bundle_codes_its_drugs(monkeypatch, tmp_path):
    concepts = _medication_concepts(_bundle_from_the_generator(monkeypatch, tmp_path))
    kinds = {kind for kind, _ in concepts}
    assert kinds == {"implication", "task"}, kinds
    coded = [c for _, c in concepts if c.get("coding")]
    assert coded, "no drug in the bundle carries an RxNorm code"
    for concept in coded:
        [coding] = concept["coding"]
        assert coding["system"] == terminology.RXNORM
        row = next(r for r in ROWS.values() if r["rxcui"] == coding["code"])
        assert concept["text"] == row["name"]
