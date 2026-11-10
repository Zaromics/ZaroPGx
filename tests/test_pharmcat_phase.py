"""What else the same genotypes could have been called, for a call PharmCAT made.

PharmCAT reads an unphased file. Where a gene has two or more heterozygous positions
among those its allele definitions are written over, the diplotype it prints is the one
its definitions explain most specifically, and another reading is available. 82 of the
1,132 two-allele calls its matcher made over the 61 GeT-RM genomes carry one, and 42 of
those a different phenotype -- TPMT ``*1/*3A`` (Intermediate Metabolizer) reads as
``*3B/*3C`` (Poor Metabolizer) if the two variants are on opposite chromosomes.

The definitions themselves are ClinPGx's (CC-BY-SA-4.0) and are not committed: the
pharmcat image publishes them out of its own jar (docker/pharmcat/definitions.py). So the
digests here are written by hand, small and in the real shape, from the cases that taught
the rules: UGT1A1's repeat for cis-against-trans, TPMT for the phenotype, NUDT15 for a
position nobody read, CYP4F2 for a tie, CYP2C19 for an ambiguity code.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

from app.reports import pharmcat_phase as phase

BUILDER = Path(__file__).resolve().parents[1] / "docker" / "pharmcat" / "definitions.py"
EXAMPLE_JSON = (
    Path(__file__).resolve().parents[1] / "test_data" / "pharmcat.example.report.json"
)


def _builder():
    """docker/pharmcat/definitions.py, loaded by path as the image lays it out."""
    spec = importlib.util.spec_from_file_location("service_definitions", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# UGT1A1, two of its four positions: rs887829 (*80) and the TA repeat rs3064744 (*28).
# *80+*28 is a named allele of its own, which is why the cis reading scores 4 and the
# trans reading 2, and why PharmCAT prints *1/*80+*28.
UGT1A1 = {
    "version": "2026-07-13",
    "loci": [
        {"c": "chr2", "p": 233757013, "r": "rs887829"},
        {"c": "chr2", "p": 233760233, "r": "rs3064744"},
    ],
    "alleles": [
        {"n": "*1", "s": 2, "ref": True, "w": {"0": "C", "1": "CAT"}},
        {"n": "*28", "s": 1, "w": {"1": "CATAT"}},
        {"n": "*80", "s": 1, "w": {"0": "T"}},
        {"n": "*80+*28", "s": 2, "w": {"0": "T", "1": "CATAT"}},
    ],
    "phenotypes": {
        "*1/*80+*28": "Intermediate Metabolizer",
        "*28/*80": "Indeterminate",
        "*1/*1": "Normal Metabolizer",
    },
}

# TPMT's two defining positions, 8,310 bp apart: both variants on one chromosome is
# *3A, one each is *3B/*3C -- Intermediate Metabolizer against Poor Metabolizer.
TPMT = {
    "version": "2026-07-13",
    "loci": [
        {"c": "chr6", "p": 18130918, "r": "rs1800460"},
        {"c": "chr6", "p": 18139228, "r": "rs1142345"},
    ],
    "alleles": [
        {"n": "*1", "s": 2, "ref": True, "w": {"0": "C", "1": "T"}},
        {"n": "*3A", "s": 2, "w": {"0": "T", "1": "C"}},
        {"n": "*3B", "s": 1, "w": {"0": "T"}},
        {"n": "*3C", "s": 1, "w": {"1": "C"}},
    ],
    "phenotypes": {
        "*1/*3A": "Intermediate Metabolizer",
        "*3B/*3C": "Poor Metabolizer",
    },
}


def _digest(tmp_path: Path, genes: dict, version: str = "3.4.0") -> Path:
    path = tmp_path / "allele-definitions.json.gz"
    payload = {
        "format": phase.FORMAT,
        "source": "PharmCAT",
        "pharmcat_version": version,
        "genes": genes,
    }
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh)
    phase._cache.clear()
    return path


def _reference_base(definition: dict, index: int) -> str:
    """The base the gene's reference allele carries at one of its positions."""
    return next(
        allele["w"][str(index)] for allele in definition["alleles"] if allele.get("ref")
    )


def _gene(
    symbol: str,
    calls: dict,
    printed: tuple,
    phenotype: str = "",
    definition: dict | None = None,
    **extra,
) -> dict:
    """One gene as PharmCAT's report JSON carries it: a panel with calls, and the call.

    The panel is built from the same definition the digest carries, because that is the
    real relation: PharmCAT genotypes the positions its definitions are written over.
    """
    definition = definition or {"UGT1A1": UGT1A1, "TPMT": TPMT}[symbol]
    gene = {
        "geneSymbol": symbol,
        "callSource": "MATCHER",
        "phased": False,
        "effectivelyPhased": False,
        "variants": [
            {
                "gene": symbol,
                "chromosome": locus["c"],
                "position": locus["p"],
                "dbSnpId": locus["r"],
                # PharmCAT 3 writes referenceAllele and 2.x wrote wildtypeAllele;
                # both are here because a panel entry carries one or the other and
                # this module reads neither -- only the call.
                "referenceAllele": _reference_base(definition, index),
                "wildtypeAllele": _reference_base(definition, index),
                "call": calls.get(locus["p"]),
                "alleles": [],
                "phased": False,
                "hasUndocumentedVariations": False,
                "warnings": [],
            }
            for index, locus in enumerate(definition["loci"])
        ],
        "sourceDiplotypes": [
            {
                # The report shows "label" where a report carries one
                # (app/pharmcat/report_json.py's extract_source_call); this module
                # reads the two allele names. A real report carries both.
                "label": "/".join(printed),
                "allele1": {"name": printed[0]},
                "allele2": {"name": printed[1]},
                "phenotypes": [phenotype] if phenotype else [],
            }
        ],
    }
    gene.update(extra)
    return gene


def _report(*genes: dict) -> dict:
    return {"genes": {gene["geneSymbol"]: gene for gene in genes}}


UGT1A1_HETS = {233757013: "C/T", 233760233: "CAT/CATAT"}
TPMT_HETS = {18130918: "C/T", 18139228: "T/C"}


# --- the three rules the GeT-RM runs taught -------------------------------------


def test_an_ambiguity_code_is_a_choice_of_bases():
    """CYP2C19 *2 wants R at chr10:94775507. Comparing bases literally rejects every
    *2 there is, which is why 27 of the 61 runs' CYP2C19 calls went unnamed at first."""
    assert phase.accepts("R", "A")
    assert phase.accepts("R", "G")
    assert not phase.accepts("R", "C")
    assert phase.accepts("N", "T")


def test_a_repeat_the_definition_takes_either_way():
    """NUDT15 *3 is written "AGGAGTC or AGGAGTCGGAGTC": the 6 bp repeat in either
    state, which is the nomenclature gap that cost us a NUDT15 rename."""
    assert phase.accepts("AGGAGTC or AGGAGTCGGAGTC", "AGGAGTCGGAGTC")
    assert phase.accepts("AGGAGTC or AGGAGTCGGAGTC", "AGGAGTC")
    assert not phase.accepts("AGGAGTC or AGGAGTCGGAGTC", "A")


def test_an_indel_matches_its_own_sequence():
    assert phase.accepts("CATAT", "CATAT")
    assert not phase.accepts("CATAT", "CAT")


def test_a_position_nobody_read_is_no_evidence_for_the_allele_over_it():
    """NUDT15 *5 is defined by one position; in HG00277 that position has no call, so
    *5 cannot be ruled out -- and PharmCAT printed *6, whose variant was seen. An
    allele's score is its evidence, so *6 (1) beats *5 (0)."""
    definition = phase.Definition(
        {
            "loci": [{"c": "chr13", "p": 48037798}, {"c": "chr13", "p": 48037782}],
            "alleles": [
                {"n": "*5", "s": 1, "w": {"0": "A"}},
                {"n": "*6", "s": 1, "w": {"1": "AGGAGTCGGAGTC"}},
            ],
        }
    )
    # The first position was not read; the second carries *6's variant.
    assert definition.name([None, "AGGAGTCGGAGTC"]) == ("*6", 1)
    # With nothing read at all, *5 is possible and unproven: named, with no evidence.
    assert definition.name([None, None]) == ("*5", 0)


def test_a_tie_goes_to_the_allele_the_definitions_list_first():
    """CYP4F2 *3 and *17 are each defined by one position. PharmCAT prints *3; sorting
    the names as text prints *17, which is how 23 CYP4F2 calls went wrong at first."""
    definition = phase.Definition(
        {
            "loci": [{"c": "chr19", "p": 15879621}],
            "alleles": [
                {"n": "*3", "s": 1, "w": {"0": "T"}},
                {"n": "*17", "s": 1, "w": {"0": "T"}},
            ],
        }
    )
    assert definition.name(["T"])[0] == "*3"


# --- naming the arrangements ----------------------------------------------------


def test_the_printed_call_is_the_most_specific_arrangement():
    """UGT1A1 with both positions heterozygous: *1/*80+*28 scores 4, *80/*28 scores 2,
    and PharmCAT prints the first. Both are readings of the same genotypes."""
    definition = phase.Definition(UGT1A1)
    found, hets = phase.arrangements(
        definition, _gene("UGT1A1", UGT1A1_HETS, ("*1", "*80+*28"))
    )
    assert len(hets) == 2
    scored = {entry["shown"]: entry["score"] for entry in found}
    assert scored == {"*1/*80+*28": 4, "*28/*80": 2}


def test_a_homozygous_position_sits_on_both_chromosomes():
    """It is not an arrangement question, and leaving it out would make every allele
    defined over it unfittable."""
    definition = phase.Definition(UGT1A1)
    gene = _gene(
        "UGT1A1", {233757013: "T/T", 233760233: "CAT/CATAT"}, ("*80", "*80+*28")
    )
    found, hets = phase.arrangements(definition, gene)
    assert len(hets) == 1
    assert {entry["shown"] for entry in found} == {"*80/*80+*28"}


# --- what the report says -------------------------------------------------------


def test_the_other_reading_is_named_with_its_phenotype(tmp_path):
    path = _digest(tmp_path, {"TPMT": TPMT})
    result = phase.unmeasured(
        _report(_gene("TPMT", TPMT_HETS, ("*1", "*3A"), "Intermediate Metabolizer")),
        path,
    )
    finding = result["genes"]["TPMT"]
    assert finding["printed"] == "*1/*3A"
    assert finding["alternatives"] == [
        {"diplotype": "*3B/*3C", "phenotype": "Poor Metabolizer"}
    ]
    assert finding["closest"] == 8310
    assert finding["within_reach"] is False
    assert [position["rsid"] for position in finding["positions"]] == [
        "rs1800460",
        "rs1142345",
    ]
    assert result["counts"]["differs"] == 1


def test_the_note_is_one_short_sentence(tmp_path):
    """It prints in the gene table's Diplotype column, 19% of the width: #213 cut the
    read-phase note to 99 characters for the same reason."""
    path = _digest(tmp_path, {"TPMT": TPMT})
    result = phase.unmeasured(
        _report(_gene("TPMT", TPMT_HETS, ("*1", "*3A"), "Intermediate Metabolizer")),
        path,
    )
    note = phase.call_note(result["genes"]["TPMT"])
    assert note == (
        "Pairing not measured; the other reading is *3B/*3C, Poor Metabolizer."
    )
    assert len(note) <= 99
    assert note.count(".") == 1


def test_the_phenotype_is_named_only_where_it_differs(tmp_path):
    """The printed call's own phenotype is in the same row's Phenotype cell, two
    columns over, so repeating it would spend the narrowest column saying nothing."""
    same = dict(
        TPMT, phenotypes={"*1/*3A": "Poor Metabolizer", "*3B/*3C": "Poor Metabolizer"}
    )
    path = _digest(tmp_path, {"TPMT": same})
    result = phase.unmeasured(
        _report(_gene("TPMT", TPMT_HETS, ("*1", "*3A"), "Poor Metabolizer")), path
    )
    assert phase.call_note(result["genes"]["TPMT"]) == (
        "Pairing not measured; the other reading is *3B/*3C."
    )
    assert result["counts"]["differs"] == 0


def test_a_call_within_one_fragment_is_counted_as_checkable(tmp_path):
    """UGT1A1's two positions are 3,220 bp apart in this digest's coordinates; the real
    pair is 309 bp, which reads could settle. Only the counting is tested here: nothing
    in this module reads any reads."""
    close = dict(
        UGT1A1,
        loci=[
            {"c": "chr2", "p": 233760000, "r": "rs887829"},
            {"c": "chr2", "p": 233760233, "r": "rs3064744"},
        ],
    )
    path = _digest(tmp_path, {"UGT1A1": close})
    gene = _gene(
        "UGT1A1",
        {233760000: "C/T", 233760233: "CAT/CATAT"},
        ("*1", "*80+*28"),
        "Intermediate Metabolizer",
        definition=close,
    )
    result = phase.unmeasured(_report(gene), path)
    assert result["genes"]["UGT1A1"]["within_reach"] is True
    assert result["counts"]["within_reach"] == 1


# --- when it says nothing -------------------------------------------------------


def test_nothing_is_said_where_the_printed_call_is_not_reproduced(tmp_path):
    """An alternative to a call this module could not reproduce would be an
    alternative to the wrong thing, which is worse than silence -- the rule
    read_phase.unreached_alternative applies for the same reason. 54 of the 1,132
    GeT-RM calls are in this class."""
    path = _digest(tmp_path, {"TPMT": TPMT})
    gene = _gene("TPMT", TPMT_HETS, ("*2", "*9"), "Poor Metabolizer")
    result = phase.unmeasured(_report(gene), path)
    assert result["genes"] == {}
    assert result["counts"]["unphased"] == 1
    assert result["counts"]["named"] == 0


def test_nothing_is_said_where_phase_could_not_have_changed_the_call(tmp_path):
    """PharmCAT's own effectivelyPhased: 990 of its 1,159 GeT-RM calls."""
    path = _digest(tmp_path, {"TPMT": TPMT})
    gene = _gene(
        "TPMT",
        TPMT_HETS,
        ("*1", "*3A"),
        "Intermediate Metabolizer",
        effectivelyPhased=True,
    )
    result = phase.unmeasured(_report(gene), path)
    assert result["genes"] == {}
    assert result["counts"]["unphased"] == 0


def test_nothing_is_said_for_a_phased_file(tmp_path):
    """A phased VCF settles the question, and PharmCAT reads FORMAT/PS since 3.0."""
    path = _digest(tmp_path, {"TPMT": TPMT})
    gene = _gene(
        "TPMT",
        {18130918: "C|T", 18139228: "T|C"},
        ("*1", "*3A"),
        "Intermediate Metabolizer",
        phased=True,
    )
    assert phase.unmeasured(_report(gene), path)["genes"] == {}


def test_nothing_is_said_about_an_outside_call(tmp_path):
    """CYP2D6 comes from PyPGx and HLA from OptiType; neither rests on PharmCAT's
    definitions, and app/reports/read_phase.py speaks for the PyPGx ones."""
    path = _digest(tmp_path, {"TPMT": TPMT})
    gene = _gene(
        "TPMT",
        TPMT_HETS,
        ("*1", "*3A"),
        "Intermediate Metabolizer",
        callSource="OUTSIDE",
    )
    result = phase.unmeasured(_report(gene), path)
    assert result["genes"] == {}
    assert result["counts"]["calls"] == 0


def test_nothing_is_said_where_pharmcat_printed_the_alternatives_itself(tmp_path):
    """26 of the 169 GeT-RM calls where phase mattered: the report already shows them
    joined with "or" (app/pharmcat/report_json.py's unresolved_call)."""
    path = _digest(tmp_path, {"TPMT": TPMT})
    gene = _gene("TPMT", TPMT_HETS, ("*1", "*3A"), "Intermediate Metabolizer")
    gene["sourceDiplotypes"].append(
        {"allele1": {"name": "*3B"}, "allele2": {"name": "*3C"}, "phenotypes": []}
    )
    assert phase.unmeasured(_report(gene), path)["genes"] == {}


def test_nothing_is_said_where_pharmcat_warned_about_it_itself(tmp_path):
    """PharmCAT raises its own ambiguity message -- "The assigned genotype is *1/*6;
    however, *4/*9 cannot be ruled out" -- and the report prints it under the call. It
    does so for 68 of the 82 arrangements this module can name, so saying it again
    would be a second copy of one fact."""
    path = _digest(tmp_path, {"TPMT": TPMT})
    gene = _gene("TPMT", TPMT_HETS, ("*1", "*3A"), "Intermediate Metabolizer")
    gene["messages"] = [
        {
            "rule_name": "TPMT *1/*3A warning",
            "exception_type": "ambiguity",
            "message": (
                "The assigned genotype is *1/*3A; however, *3B/*3C cannot be ruled "
                "out without phased data."
            ),
        }
    ]
    result = phase.unmeasured(_report(gene), path)
    assert result["genes"] == {}
    assert result["counts"]["pharmcat_warned"] == 1
    assert result["counts"]["named"] == 0


def test_a_message_that_rules_nothing_out_does_not_silence_the_note(tmp_path):
    """Most of PharmCAT's messages are about something else: a reference allele, a
    reverse-complement footnote, a drug's own text."""
    path = _digest(tmp_path, {"TPMT": TPMT})
    gene = _gene("TPMT", TPMT_HETS, ("*1", "*3A"), "Intermediate Metabolizer")
    gene["messages"] = [
        {
            "rule_name": "TPMT reverse complement footnote",
            "exception_type": "footnote",
            "message": "TPMT is on the negative chromosomal strand.",
        }
    ]
    assert "TPMT" in phase.unmeasured(_report(gene), path)["genes"]


def test_no_published_definitions_means_no_note(tmp_path):
    """Every release before this one, and any stack whose pharmcat service has not
    started yet."""
    result = phase.unmeasured(
        _report(_gene("TPMT", TPMT_HETS, ("*1", "*3A"))), tmp_path / "absent.json.gz"
    )
    assert result["status"] == "none"
    assert phase.coverage_note(result) is None


def test_a_digest_from_a_different_format_is_refused(tmp_path):
    path = tmp_path / "allele-definitions.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump({"format": phase.FORMAT + 1, "genes": {"TPMT": TPMT}}, fh)
    phase._cache.clear()
    assert phase.digest(path) is None


# --- the rows and the paragraph -------------------------------------------------


def test_only_a_row_pharmcat_called_is_marked(tmp_path):
    path = _digest(tmp_path, {"TPMT": TPMT})
    report = _report(
        _gene("TPMT", TPMT_HETS, ("*1", "*3A"), "Intermediate Metabolizer")
    )
    rows = [
        # app/reports/provenance.py reads call_source, and a tool marker for an
        # outside call: MATCHER is PharmCAT's own, which is what this speaks for.
        {"gene": "TPMT", "call_source": "MATCHER", "diplotype": "*1/*3A"},
        {
            "gene": "TPMT",
            "call_source": "OUTSIDE",
            "source": "PyPGx",
            "diplotype": "*1/*3A",
        },
    ]
    phase.annotate_genes(rows, report, path)
    assert "Pairing not measured" in rows[0]["pharmcat_phase"]["call_note"]
    assert "pharmcat_phase" not in rows[1]


def test_the_paragraph_does_not_promise_a_note_no_row_carries(tmp_path):
    """A real NA18526 render turned this up: its NUDT15 row already carried the
    caller-disagreement note, so the finding reached no row -- and the paragraph still
    said "for 1 more this report names it beside the call"."""
    path = _digest(tmp_path, {"TPMT": TPMT})
    report = _report(_gene("TPMT", TPMT_HETS, ("*1", "*3A"), "Intermediate Metabolizer"))
    rows = [
        {
            "gene": "TPMT",
            "call_source": "MATCHER",
            "diplotype": "*1/*3A",
            "caller_disagreement": {"call_note": "PyPGx read this as *3B/*3C."},
        }
    ]
    result = phase.annotate_genes(rows, report, path)
    assert "pharmcat_phase" not in rows[0]
    assert result["counts"]["named"] == 0
    assert result["counts"]["could_name"] == 1
    note = phase.coverage_note(result)
    assert "this report names it beside the call" not in note
    # Nor does it claim the definitions allow no other reading, because they do.
    assert "no other reading" not in note


def test_a_row_the_callers_already_disagree_on_is_left_alone(tmp_path):
    """caller_agreement.py has already said the printed call is one of two readings;
    a second note of the same shape would read as a second doubt."""
    path = _digest(tmp_path, {"TPMT": TPMT})
    report = _report(
        _gene("TPMT", TPMT_HETS, ("*1", "*3A"), "Intermediate Metabolizer")
    )
    rows = [
        {
            "gene": "TPMT",
            "call_source": "MATCHER",
            "diplotype": "*1/*3A",
            "caller_disagreement": {"call_note": "PyPGx read this as *3B/*3C."},
        }
    ]
    phase.annotate_genes(rows, report, path)
    assert "pharmcat_phase" not in rows[0]


def test_the_paragraph_counts_them_and_says_where_the_definitions_came_from(tmp_path):
    path = _digest(tmp_path, {"TPMT": TPMT})
    report = _report(
        _gene("TPMT", TPMT_HETS, ("*1", "*3A"), "Intermediate Metabolizer")
    )
    rows = [{"gene": "TPMT", "call_source": "MATCHER", "diplotype": "*1/*3A"}]
    note = phase.coverage_note(phase.annotate_genes(rows, report, path))
    assert "1 of the 1 calls it made here rests on such an arrangement" in note
    assert "this report names it beside the call" in note
    assert "only how they are paired" in note
    assert "PharmCAT 3.4.0's, from ClinPGx" in note
    assert note.endswith(".")


def test_the_paragraph_credits_pharmcat_for_the_ones_it_warned_about(tmp_path):
    """The reader is told the whole state of affairs: what PharmCAT said itself, what
    this report adds, and that the alleles are not in question either way."""
    path = _digest(tmp_path, {"TPMT": TPMT, "UGT1A1": UGT1A1})
    warned = _gene("TPMT", TPMT_HETS, ("*1", "*3A"), "Intermediate Metabolizer")
    warned["messages"] = [
        {"exception_type": "ambiguity", "message": "*3B/*3C cannot be ruled out."}
    ]
    ours = _gene("UGT1A1", UGT1A1_HETS, ("*1", "*80+*28"), "Intermediate Metabolizer")
    rows = [
        {"gene": "TPMT", "call_source": "MATCHER", "diplotype": "*1/*3A"},
        {"gene": "UGT1A1", "call_source": "MATCHER", "diplotype": "*1/*80+*28"},
    ]
    note = phase.coverage_note(phase.annotate_genes(rows, _report(warned, ours), path))
    assert "2 of the 2 calls it made here rest on such an arrangement" in note
    assert "PharmCAT names the other reading itself for 1 of them" in note
    assert "For 1 more, read from the same definitions" in note


def test_the_paragraph_is_silent_when_every_call_settled_its_own_phase(tmp_path):
    path = _digest(tmp_path, {"TPMT": TPMT})
    gene = _gene(
        "TPMT",
        TPMT_HETS,
        ("*1", "*3A"),
        "Intermediate Metabolizer",
        effectivelyPhased=True,
    )
    assert phase.coverage_note(phase.unmeasured(_report(gene), path)) is None


# --- the digest the pharmcat image publishes ------------------------------------


def _jar(path: Path) -> Path:
    """A jar with one gene's definitions in it, as pharmcat.jar lays them out."""
    definition = {
        "gene": "TPMT",
        "version": "2026-07-13",
        "variants": [
            {"chromosome": "chr6", "position": 18130918, "rsid": "rs1800460"},
            {"chromosome": "chr6", "position": 18139228, "rsid": "rs1142345"},
        ],
        "namedAlleles": [
            {"name": "*1", "alleles": ["C", "T"], "score": 2, "reference": True},
            {"name": "*3A", "alleles": ["T", "C"], "score": 2},
            {"name": "*3B", "alleles": ["T", None], "score": 1},
            {"name": "*3C", "alleles": [None, "C"], "score": 1},
        ],
    }
    phenotypes = {
        "gene": "TPMT",
        "diplotypes": [
            {
                "diplotypekey": {"*1": 1, "*3A": 1},
                "phenotype": "Intermediate Metabolizer",
            },
            {"diplotypekey": {"*3B": 1, "*3C": 1}, "phenotype": "Poor Metabolizer"},
            {"diplotypekey": {"*1": 2}, "generesult": "Normal Metabolizer"},
        ],
    }
    with zipfile.ZipFile(path, "w") as jar:
        jar.writestr(
            "org/pharmgkb/pharmcat/definition/alleles/TPMT_translation.json",
            json.dumps(definition),
        )
        jar.writestr(
            "org/pharmgkb/pharmcat/phenotype/TPMT.json", json.dumps(phenotypes)
        )
    return path


def test_the_published_digest_is_what_the_report_reads(tmp_path, monkeypatch):
    """The builder's output and the reader's expectations are one contract, so the two
    are tested against each other rather than against two hand-written shapes."""
    builder = _builder()
    monkeypatch.setenv("PHARMCAT_VERSION", "3.4.0")
    out = tmp_path / "allele-definitions.json.gz"
    assert builder.publish(_jar(tmp_path / "pharmcat.jar"), out) == out
    phase._cache.clear()

    published = phase.digest(out)
    assert published["pharmcat_version"] == "3.4.0"
    assert "CC-BY-SA" in published["licence"]
    report = _report(
        _gene("TPMT", TPMT_HETS, ("*1", "*3A"), "Intermediate Metabolizer")
    )
    result = phase.unmeasured(report, out)
    assert result["genes"]["TPMT"]["alternatives"] == [
        {"diplotype": "*3B/*3C", "phenotype": "Poor Metabolizer"}
    ]


def test_only_the_positions_an_allele_is_specified_at_are_published(tmp_path):
    """Writing the others as nulls on every allele of every gene is most of the 5 MB."""
    builder = _builder()
    digest = builder.build(_jar(tmp_path / "pharmcat.jar"), "3.4.0")
    alleles = {entry["n"]: entry for entry in digest["genes"]["TPMT"]["alleles"]}
    assert alleles["*3B"]["w"] == {"0": "T"}
    assert alleles["*3C"]["w"] == {"1": "C"}
    assert alleles["*1"].get("ref") is True
    assert alleles["*3A"].get("ref") is None
    assert digest["genes"]["TPMT"]["phenotypes"]["*1/*3A"] == "Intermediate Metabolizer"


def test_a_digest_already_published_for_this_pharmcat_is_not_rebuilt(
    tmp_path, monkeypatch
):
    builder = _builder()
    monkeypatch.setenv("PHARMCAT_VERSION", "3.4.0")
    jar = _jar(tmp_path / "pharmcat.jar")
    out = tmp_path / "allele-definitions.json.gz"
    builder.publish(jar, out)
    written = out.stat().st_mtime_ns
    assert builder.publish(jar, out) == out
    assert out.stat().st_mtime_ns == written
    # A digest from another PharmCAT is replaced.
    monkeypatch.setenv("PHARMCAT_VERSION", "3.5.0")
    builder.publish(jar, out)
    assert builder.stamp_of(out) == "3.5.0"


def test_a_missing_jar_costs_the_note_and_nothing_else(tmp_path):
    builder = _builder()
    assert builder.publish(tmp_path / "absent.jar", tmp_path / "out.json.gz") is None


def test_the_builder_imports_nothing_from_app():
    """The pharmcat image has no app package: a module it ships imports nothing from
    one, at any level (tests/test_sidecar_standalone_modules.py polices the class for
    the files COPYed out of app/; this one is the image's own)."""
    source = BUILDER.read_text(encoding="utf-8")
    assert "import app" not in source
    assert "from app" not in source


# --- the page and the PDF -------------------------------------------------------


def _unphased_example() -> dict:
    """The demo report, with TPMT rewritten into the two-heterozygote case.

    The demo has no call whose phase was in question -- every one of its matcher calls
    is effectivelyPhased -- so the case has to be put there. It is a PharmCAT 2.15
    report, whose genes are nested under their guideline source, which is the shape
    app/pharmcat/report_json.py's walker exists for: rewriting it in place is also the
    check that this module reads that shape and not only PharmCAT 3's.
    """
    data = json.loads(EXAMPLE_JSON.read_text(encoding="utf-8"))
    for source, genes in data["genes"].items():
        if not isinstance(genes, dict) or "TPMT" not in genes:
            continue
        case = _gene("TPMT", TPMT_HETS, ("*1", "*3A"), "Intermediate Metabolizer")
        # Into the demo's own block, so the gene keeps everything else that puts it in
        # the report at all -- its drugs, its versions, its chromosome.
        gene = dict(genes["TPMT"])
        gene.update(
            variants=case["variants"],
            sourceDiplotypes=case["sourceDiplotypes"],
            recommendationDiplotypes=case["sourceDiplotypes"],
            phased=False,
            effectivelyPhased=False,
            callSource="MATCHER",
            messages=[],
        )
        genes["TPMT"] = gene
    return data


def test_the_note_and_the_paragraph_reach_the_page_and_the_pdf(monkeypatch, tmp_path):
    """The PDF is the delivered artifact: a note that only renders in HTML is not
    delivered (and the Executive Summary is laid out to one page, so an added note
    must not push it over)."""
    import app.reports.generator as generator_module

    path = _digest(tmp_path, {"TPMT": TPMT})
    monkeypatch.setattr(phase, "DIGEST", path)
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG, key, key == "write_html"
            )
    result = generator_module.generate_report(
        {"data": _unphased_example()}, str(tmp_path), {"id": "p1"}, job_id=None
    )
    page = (tmp_path / result["html_path"].split("/")[-1]).read_text(encoding="utf-8")
    assert "Pairing not measured" in page
    assert "the other reading is *3B/*3C, Poor Metabolizer" in page
    assert "The file PharmCAT read carries no phase" in page
    assert "only how they are paired" in page

    document, _ = generator_module.render_pdf_document(page)
    # Joined with a space and re-collapsed: WeasyPrint breaks a sentence into one text
    # box per line, so "Pairing not" and "measured;" are separate boxes and
    # concatenating them without a separator invents "notmeasured".
    printed = " ".join(
        (getattr(box, "text", "") or "").strip()
        for pdf_page in document.pages
        for box in pdf_page._page_box.descendants()
    )
    printed = " ".join(printed.split())
    assert "Pairing not measured" in printed
    assert "The file PharmCAT read carries no phase" in printed
    # The Executive Summary is laid out to one page (app/reports/pdf_fit.py), so an
    # added note must not push it over.
    summary = [
        index
        for index, pdf_page in enumerate(document.pages)
        if any(
            getattr(box, "element_tag", "") == "div"
            and getattr(box, "element", None) is not None
            and box.element.get("id") == "exec-summary"
            for box in pdf_page._page_box.descendants()
        )
    ]
    assert len(summary) == 1, f"the Executive Summary ran to {len(summary)} pages"
