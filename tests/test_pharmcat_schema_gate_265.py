"""BACKLOG 265 -- PharmCAT report.json version tracking + structure validation.

PharmCAT versions emit structurally different JSON.  The walkers in
``app.pharmcat.report_json`` are defensive (``isinstance`` guards, ``.get``
defaults), so a shape change never raises -- it yields *nothing*, the parser
stores zero rows, and the report renders as though the sample simply had no
findings.  That is the failure this gate exists to make impossible.

Every assertion here is on real parser behaviour against real payloads: the
three tracked fixtures, and (when present) the real report directories under
``data/reports``.
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Optional
from unittest.mock import MagicMock

import pytest

from app.pharmcat import report_json
from app.pharmcat.pharmcat_parser import PharmCATParser
from app.pharmcat.report_json import (
    SUPPORTED_VERSION_SERIES,
    GeneBlock,
    PharmCATSchemaError,
    parse_pharmcat_version,
    validate_report,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = REPO_ROOT / "test_data"

# The three *tracked* fixtures.  A git worktree has no gitignored files, so
# dev-notes/pharmcat-json-postgres/example_pgx_pharmcat.json (3.0.1) must never
# be a test dependency -- its shape is covered by NESTED_V2 instead.
V2_REPORT = TEST_DATA / "pharmcat.example.report.json"  # v2.15.4-*, nested
NESTED_V2 = TEST_DATA / "pharmcat.example.nested.v2.report.json"  # v2.15.4-*, nested
FLAT_V340 = TEST_DATA / "pharmcat.example.v340.report.json"  # 3.4.0, flat

TRACKED_FIXTURES = (V2_REPORT, NESTED_V2, FLAT_V340)

REAL_REPORTS = sorted((REPO_ROOT / "data" / "reports").glob("*/*_pgx_pharmcat.json"))


def _load(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _codes(result) -> set:
    return {issue.code for issue in result.issues}


def _mock_parser() -> tuple:
    added: list = []
    session = MagicMock()
    session.add.side_effect = lambda obj: added.append(obj)
    parser = PharmCATParser(db_session=session)
    parser.db_session.query.return_value.filter.return_value.first.return_value = None
    return parser, added


# ---------------------------------------------------------------------------
# Version parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("3.4.0", (3, 4, 0)),
        ("v2.15.4-20-g7f763d7c", (2, 15, 4)),  # the real string in the v2 fixtures
        ("3.0.1", (3, 0, 1)),
        ("3.1.1", (3, 1, 1)),
        ("  3.4.0  ", (3, 4, 0)),
        ("3.4", (3, 4, 0)),
        ("v3.5.0-SNAPSHOT", (3, 5, 0)),
        (None, None),
        ("", None),
        ("unreleased", None),
        (3.4, None),
    ],
)
def test_parse_pharmcat_version(raw, expected):
    assert parse_pharmcat_version(raw) == expected


def test_supported_series_is_evidence_backed():
    """Every entry must name the payload that proves the parser handles it."""
    assert SUPPORTED_VERSION_SERIES
    for series, evidence in SUPPORTED_VERSION_SERIES.items():
        assert isinstance(series, tuple) and len(series) == 2
        assert evidence and isinstance(evidence, str)

    # The version compose.yml deploys today must be supported.
    assert (3, 4) in SUPPORTED_VERSION_SERIES


# ---------------------------------------------------------------------------
# The gate accepts everything the repo genuinely supports
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", TRACKED_FIXTURES, ids=lambda p: p.name)
def test_tracked_fixtures_pass_the_gate(path: Path):
    result = validate_report(_load(path))
    assert result.ok, result.summary()
    assert not result.errors, [i.message for i in result.errors]
    assert result.version_supported, result.summary()
    assert result.gene_block_count > 0


def test_the_two_live_shapes_are_both_recognised():
    assert validate_report(_load(NESTED_V2)).detected_format == "nested"
    assert validate_report(_load(FLAT_V340)).detected_format == "flat"


@pytest.mark.skipif(not REAL_REPORTS, reason="no data/reports/ in this checkout")
@pytest.mark.parametrize("path", REAL_REPORTS, ids=lambda p: p.parent.name[:8])
def test_real_report_directories_pass_the_gate(path: Path):
    """Production PharmCAT output must never trip the gate."""
    result = validate_report(_load(path))
    assert result.ok, result.summary()
    assert result.version_supported, result.summary()


def test_a_payload_with_genes_but_no_drugs_is_fine():
    """The router's synthetic activity-score payload has no ``drugs`` key at
    all, and ``parse_and_load`` handles that -- so the gate must too."""
    result = validate_report(
        {
            "title": "activity-score-run",
            "pharmcatVersion": "3.4.0",
            "genes": {"CYP2D6": {"geneSymbol": "CYP2D6", "sourceDiplotypes": []}},
        }
    )
    assert result.ok, result.summary()
    assert not result.issues


def test_a_run_that_called_no_genes_warns_rather_than_failing():
    """``genes: {}`` is a thin run, not a broken schema."""
    result = validate_report({"pharmcatVersion": "3.4.0", "genes": {}})
    assert result.ok
    assert "genes.empty" in _codes(result)


# ---------------------------------------------------------------------------
# Unknown version: surfaced loudly, never blocking
# ---------------------------------------------------------------------------


def test_unknown_version_warns_and_still_parses(caplog):
    report = _load(FLAT_V340)
    report["pharmcatVersion"] = "4.1.0"

    result = validate_report(report)
    assert result.ok, "an unknown version must not block a structurally sound report"
    assert not result.errors
    assert result.version_supported is False
    assert "version.unsupported" in _codes(result)
    # The message has to be actionable: it names the version and the set.
    warning = next(i for i in result.issues if i.code == "version.unsupported")
    assert "4.1.0" in warning.message
    assert "3.4" in warning.message

    parser, added = _mock_parser()
    with caplog.at_level(logging.WARNING, logger="app.pharmcat.pharmcat_parser"):
        parser.parse_and_load(report)
    assert added, "an unknown version must not stop the parse"
    assert any("4.1.0" in r.getMessage() for r in caplog.records)


def test_patch_bump_inside_a_supported_series_is_silent():
    report = _load(FLAT_V340)
    report["pharmcatVersion"] = "3.4.7"
    result = validate_report(report)
    assert result.version_supported
    assert not result.issues


def test_missing_version_is_a_warning_not_an_error():
    report = _load(FLAT_V340)
    report.pop("pharmcatVersion")
    result = validate_report(report)
    assert result.ok
    assert result.version_supported is False
    assert "version.missing" in _codes(result)


def test_unparseable_version_is_a_warning():
    report = _load(FLAT_V340)
    report["pharmcatVersion"] = "unreleased-nightly"
    result = validate_report(report)
    assert result.ok
    assert "version.unparseable" in _codes(result)


# ---------------------------------------------------------------------------
# Structural rejection -- the teeth
# ---------------------------------------------------------------------------


def test_report_that_is_not_an_object_is_rejected():
    result = validate_report(["not", "a", "report"])
    assert not result.ok
    assert "report.not_an_object" in _codes(result)


def test_missing_genes_section_is_rejected():
    result = validate_report({"pharmcatVersion": "3.4.0", "drugs": {}})
    assert not result.ok
    assert "genes.missing" in _codes(result)


def test_genes_as_a_list_is_rejected():
    """A plausible future change: ``genes`` becomes an array of gene objects."""
    flat = _load(FLAT_V340)["genes"]
    result = validate_report(
        {"pharmcatVersion": "4.0.0", "genes": list(flat.values())},
    )
    assert not result.ok
    assert "genes.not_a_mapping" in _codes(result)


def test_genes_wrapped_in_an_unknown_container_is_rejected():
    """The exact silent-degradation shape: data is there, none of it walkable."""
    flat = _load(FLAT_V340)["genes"]
    result = validate_report(
        {
            "pharmcatVersion": "4.0.0",
            "genes": {"results": {"calls": list(flat.values())}},
        }
    )
    assert not result.ok
    assert "genes.unrecognised_shape" in _codes(result)
    assert result.gene_block_count == 0


def test_renamed_gene_fields_are_rejected():
    """If PharmCAT renamed every field the walker keys on, the gene entries stop
    looking like genes -- and the walker would yield source-bucket garbage."""
    genes = {
        "CYP2D6": {"symbol": "CYP2D6", "calls": [{"diplotype": "*1/*1"}]},
        "CYP2C19": {"symbol": "CYP2C19", "calls": [{"diplotype": "*38/*38"}]},
    }
    result = validate_report({"pharmcatVersion": "4.0.0", "genes": genes})
    assert not result.ok
    assert "genes.unrecognised_shape" in _codes(result)


def test_partially_unrecognised_genes_warn_and_name_the_keys():
    genes = dict(_load(FLAT_V340)["genes"])
    genes["SOMETHING_NEW"] = "not a mapping"
    result = validate_report({"pharmcatVersion": "3.4.0", "genes": genes})
    assert "genes.partial_shape" in _codes(result)
    issue = next(i for i in result.issues if i.code == "genes.partial_shape")
    assert "SOMETHING_NEW" in issue.message


def test_drugs_spine_type_violation_is_rejected():
    """``drugs[src][drug].guidelines[].annotations[]`` is the walk the parser
    depends on; a type change anywhere on that spine is silent data loss."""
    report = _load(FLAT_V340)
    src = next(iter(report["drugs"]))
    drug = next(iter(report["drugs"][src]))
    report["drugs"][src][drug]["guidelines"] = {"0": {"annotations": []}}

    result = validate_report(report)
    assert not result.ok
    assert "drugs.guidelines_not_a_list" in _codes(result)
    assert drug in next(
        i.message for i in result.errors if i.code == "drugs.guidelines_not_a_list"
    )


def test_annotations_type_change_is_rejected():
    report = _load(FLAT_V340)
    src = next(iter(report["drugs"]))
    drug = next(iter(report["drugs"][src]))
    report["drugs"][src][drug]["guidelines"][0]["annotations"] = {"a": {}}

    result = validate_report(report)
    assert not result.ok
    assert "drugs.annotations_not_a_list" in _codes(result)


def test_drugs_as_a_list_is_rejected():
    report = _load(FLAT_V340)
    report["drugs"] = [{"name": "abacavir"}]
    result = validate_report(report)
    assert not result.ok
    assert "drugs.not_a_mapping" in _codes(result)


def test_unannotated_gene_calls_must_be_a_list():
    """``_parse_unannotated_gene_calls`` iterates it and calls ``.get`` on each
    element; a mapping there yields str keys and an AttributeError."""
    report = _load(FLAT_V340)
    report["unannotatedGeneCalls"] = {"CYP3A5": {}}
    result = validate_report(report)
    assert not result.ok
    assert "unannotatedGeneCalls.not_a_list" in _codes(result)


# ---------------------------------------------------------------------------
# The 2.x -> 3.4.0 transition: would this gate have caught it?
# ---------------------------------------------------------------------------


def _nested_only_walker(genes_section: Optional[Mapping[str, Any]]) -> Iterator:
    """The walker ZaroPGx shipped before flat support (commit 8cd53b8).

    ``for source, genes in genes_data.items(): for symbol, data in genes.items()``
    -- with the ``isinstance`` guards the current code has, so this yields
    silently rather than raising, which is the *charitable* reconstruction.
    The original had no guards and died with ``AttributeError`` instead; either
    way the run is broken.
    """
    if not genes_section:
        return
    for source, genes in genes_section.items():
        if not isinstance(genes, dict):
            continue
        for gene_symbol, gene_data in genes.items():
            if not isinstance(gene_data, dict):
                continue
            yield GeneBlock(gene_symbol, gene_data, source, gene_data.get("callSource"))


def _flat_only_walker(genes_section: Optional[Mapping[str, Any]]) -> Iterator:
    """The mirror mistake: a flat-only walker handed a nested 2.x report."""
    if not genes_section:
        return
    for gene_symbol, gene_data in genes_section.items():
        if not isinstance(gene_data, dict):
            continue
        yield GeneBlock(gene_symbol, gene_data, None, gene_data.get("callSource"))


def test_gate_catches_a_nested_only_walker_handed_the_flat_3_4_0_report(monkeypatch):
    """This is the 2.x -> 3.4.0 transition, reproduced.

    The structural scan is deliberately independent of ``iter_gene_blocks``, so
    it still sees five gene entries in the real 3.4.0 fixture.  The old
    nested-only walker extracts none of them -- a flat gene block has no
    dict-valued fields, so the inner loop finds nothing to yield.  Data present,
    nothing extracted: hard error.
    """
    report = _load(FLAT_V340)

    # Sanity: unpatched, this fixture is clean.
    assert validate_report(report).ok

    monkeypatch.setattr(report_json, "iter_gene_blocks", _nested_only_walker)
    result = validate_report(report)

    assert not result.ok
    assert "genes.not_walkable" in _codes(result)
    assert result.gene_entry_count == 5
    assert result.gene_block_count == 0
    assert "5" in next(
        i.message for i in result.errors if i.code == "genes.not_walkable"
    )


def test_gate_catches_a_flat_only_walker_handed_the_nested_v2_report(monkeypatch):
    """The same transition in the other direction: a flat-only walker reads the
    two guideline buckets as if they were two genes, losing every real gene."""
    report = _load(NESTED_V2)
    assert validate_report(report).ok

    monkeypatch.setattr(report_json, "iter_gene_blocks", _flat_only_walker)
    result = validate_report(report)

    assert not result.ok
    assert "genes.undercounted" in _codes(result)
    assert result.gene_block_count == 2  # "CPIC" and "DPWG", read as genes
    assert result.gene_entry_count > 2


def test_gate_catches_a_nested_only_walker_on_every_real_3_x_report(monkeypatch):
    """Not just the fixture -- the same regression against production output."""
    if not REAL_REPORTS:
        pytest.skip("no data/reports/ in this checkout")

    monkeypatch.setattr(report_json, "iter_gene_blocks", _nested_only_walker)
    for path in REAL_REPORTS:
        result = validate_report(_load(path))
        assert "genes.not_walkable" in _codes(result), path


# ---------------------------------------------------------------------------
# Wiring: the parser refuses to load a structurally broken report
# ---------------------------------------------------------------------------


def test_parse_and_load_rejects_a_broken_report_before_writing_anything():
    parser, added = _mock_parser()
    with pytest.raises(PharmCATSchemaError) as excinfo:
        parser.parse_and_load({"pharmcatVersion": "4.0.0", "genes": ["a", "list"]})

    assert not added, "nothing may be written for a rejected report"
    assert "genes.not_a_mapping" in str(excinfo.value)
    assert excinfo.value.result.errors


def test_parse_and_load_accepts_every_tracked_fixture():
    for path in TRACKED_FIXTURES:
        parser, added = _mock_parser()
        parser.parse_and_load(_load(path))
        assert added, path.name


def test_parse_and_load_rejects_a_silently_empty_shape_change():
    """The end-to-end shape of the bug: a full report the walker cannot read."""
    report = copy.deepcopy(_load(FLAT_V340))
    report["genes"] = {"CPIC": {"payload": {"gene": "CYP2D6"}}}

    parser, added = _mock_parser()
    with pytest.raises(PharmCATSchemaError):
        parser.parse_and_load(report)
    assert not added


# ---------------------------------------------------------------------------
# Second pass (independent review).  Counting gene *containers* is not enough:
# these are the shape changes that matched container-for-container and still
# produced an empty parse, plus two false positives on shapes the repo builds.
# ---------------------------------------------------------------------------


def _diplotype_rows(report: dict) -> int:
    """Diplotype rows the real parser would actually store."""
    from app.pharmcat.pharmcat_parser import PharmCATDiplotype

    parser, added = _mock_parser()
    parser.parse_and_load(report)
    return sum(1 for o in added if isinstance(o, PharmCATDiplotype))


def test_a_renamed_sourceDiplotypes_field_is_rejected():
    """The likeliest upstream change of all, and the old gate waved it through.

    Container counts still matched (5 entries, 5 blocks) because ``geneSymbol``
    alone keeps a block gene-like -- but every diplotype vanished.
    """
    clean = _load(FLAT_V340)
    assert _diplotype_rows(clean) > 0

    renamed = _load(FLAT_V340)
    for gene in renamed["genes"].values():
        gene["diplotypes"] = gene.pop("sourceDiplotypes")

    result = validate_report(renamed)
    assert not result.ok, "a renamed diplotype field must not pass"
    assert "genes.no_source_diplotypes" in _codes(result)
    # The container counts still agree -- which is exactly why this needed its
    # own check rather than more counting.
    assert result.gene_entry_count == result.gene_block_count == 5


def test_source_diplotypes_retyped_to_an_object_is_rejected():
    """List -> object made the parser die with AttributeError mid-write."""
    report = _load(FLAT_V340)
    for gene in report["genes"].values():
        gene["sourceDiplotypes"] = {"0": {"label": "*1/*1"}}

    result = validate_report(report)
    assert not result.ok
    assert "genes.source_diplotypes_not_a_list" in _codes(result)


def test_one_gene_missing_source_diplotypes_only_warns():
    """A single odd gene is not a schema change; do not fail the analysis."""
    report = _load(FLAT_V340)
    report["genes"]["ABCG2"].pop("sourceDiplotypes")
    result = validate_report(report)
    assert result.ok
    assert "genes.some_source_diplotypes_missing" in _codes(result)


def test_an_extra_nesting_level_is_rejected():
    """``genes -> symbol -> {"call": gene_data}`` matched container-for-container
    and stored 126 rows under the literal gene symbol ``"call"``."""
    report = _load(FLAT_V340)
    report["genes"] = {sym: {"call": data} for sym, data in report["genes"].items()}

    result = validate_report(report)
    assert not result.ok
    assert "genes.symbol_mismatch" in _codes(result)


def test_gene_symbol_disagreeing_with_its_key_is_reported():
    report = _load(FLAT_V340)
    report["genes"]["ABCG2"]["geneSymbol"] = "NOT_ABCG2"
    result = validate_report(report)
    assert result.ok, "one disagreement is not a schema change"
    assert "genes.some_symbols_mismatch" in _codes(result)


@pytest.mark.parametrize(
    "key,code",
    [
        ("drugs", "drugs.null"),
        ("unannotatedGeneCalls", "unannotatedGeneCalls.null"),
    ],
)
def test_explicit_null_top_level_containers_are_rejected(key, code):
    """``data.get(k, {})`` returns None when the key exists holding null, so the
    parser crashes on ``.items()`` / iteration.  No real payload has one."""
    report = _load(FLAT_V340)
    report[key] = None
    result = validate_report(report)
    assert not result.ok
    assert code in _codes(result)


@pytest.mark.parametrize(
    "field,code",
    [
        ("guidelines", "drugs.guidelines_null"),
        ("annotations", "drugs.annotations_null"),
    ],
)
def test_explicit_null_inside_the_drugs_spine_is_rejected(field, code):
    report = _load(FLAT_V340)
    src = next(iter(report["drugs"]))
    drug = next(iter(report["drugs"][src]))
    if field == "guidelines":
        report["drugs"][src][drug]["guidelines"] = None
    else:
        report["drugs"][src][drug]["guidelines"][0]["annotations"] = None

    result = validate_report(report)
    assert not result.ok
    assert code in _codes(result)


def test_an_all_empty_nested_genes_section_is_a_warning_not_an_error():
    """``{"genes": {"CPIC": {}}}`` is a shape ZaroPGx builds itself as the TSV
    fallback seed (upload_router.py:588).  A nested-era run that called no genes
    is the same fact as ``genes: {}``, which is already only a warning."""
    result = validate_report(
        {"pharmcatVersion": "v2.15.4-20-g7f763d7c", "genes": {"CPIC": {}, "DPWG": {}}}
    )
    assert result.ok, result.summary()
    assert "genes.empty" in _codes(result)


def test_one_empty_guideline_bucket_alongside_real_genes_is_clean():
    """An empty DPWG bucket is a recognised bucket holding nothing -- not an
    unparsed entry.  The old message claimed both, and both were false."""
    report = _load(NESTED_V2)
    report["genes"]["DPWG"] = {}
    result = validate_report(report)
    assert result.ok
    assert not result.issues, [i.message for i in result.issues]


def test_a_non_string_version_is_not_silently_accepted():
    """``pharmcatVersion: 3.4`` as a JSON *number* used to stringify into a
    supported series, contradicting parse_pharmcat_version's own contract."""
    report = _load(FLAT_V340)
    report["pharmcatVersion"] = 3.4
    result = validate_report(report)
    assert result.version_supported is False
    assert "version.unparseable" in _codes(result)


def test_pharmcat_3_3_is_supported():
    """ZaroPGx shipped PharmCAT 3.3.0 (bb47abc -> 87dda76), so a 3.3 run must
    not be flagged as unverified."""
    assert (3, 3) in SUPPORTED_VERSION_SERIES
    report = _load(FLAT_V340)
    report["pharmcatVersion"] = "3.3.0"
    result = validate_report(report)
    assert result.version_supported
    assert not result.issues


def test_a_3_1_shaped_payload_passes_without_needing_data_reports():
    """(3, 1) is claimed on the strength of 18 production runs that live only in
    a gitignored directory.  Pin the claim with a payload CI can actually see:
    the 3.1.1 shape is flat, with matcherMetadata and unannotatedGeneCalls."""
    report = {
        "title": "3.1.1-shaped",
        "pharmcatVersion": "3.1.1",
        "dataVersion": "2025-09-01-12-00",
        "matcherMetadata": {"genomeBuild": "GRCh38.p13"},
        "genes": {
            "CYP2C19": {
                "geneSymbol": "CYP2C19",
                "callSource": "MATCHER",
                "sourceDiplotypes": [{"label": "*1/*1", "phenotypes": ["Normal"]}],
            }
        },
        "unannotatedGeneCalls": [],
        "messages": [],
    }
    result = validate_report(report)
    assert result.ok and result.version_supported
    assert not result.issues
    assert result.detected_format == "flat"
