"""Unit tests for the unified PharmCAT report.json walkers (Wave 4 / 355)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.pharmcat.report_json import (
    detect_format,
    extract_matcher_metadata,
    extract_source_call,
    iter_gene_blocks,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = REPO_ROOT / "test_data"
NESTED_V2 = TEST_DATA / "pharmcat.example.nested.v2.report.json"
FLAT_V340 = TEST_DATA / "pharmcat.example.v340.report.json"

# A report directory is /data/reports/{patient_id}/{job_id} and every artifact in
# it is named from the job id (upload_router.py:246-256).
JOB_ID = "0357a4a0-5cfe-4a7b-b764-2b2c009152db"


def _make_report_dir(tmp_path: Path, job_id: str = JOB_ID) -> Path:
    """Reproduce a real report directory, verified against ``data/reports/``.

    Filenames come from the production writers (docker/pharmcat/pharmcat.py:877,
    :913, :930 and upload_router.py:252-256); the JSON payload is the real
    checked-in PharmCAT 3.4.0 fixture, not a hand-rolled dict.
    """
    report_dir = tmp_path / "reports" / "patient-1" / job_id
    report_dir.mkdir(parents=True)

    payload = FLAT_V340.read_bytes()
    for suffix in ("_pgx_pharmcat.json", "_raw_report.json", "_pgx_report.json"):
        (report_dir / f"{job_id}{suffix}").write_bytes(payload)

    # Non-JSON siblings, present in every real directory.
    for suffix in (
        "_pgx_pharmcat.html",
        "_pgx_pharmcat.tsv",
        "_pgx_report.html",
        "_pgx_report.pdf",
        "_pgx_report_interactive.html",
        "_workflow.svg",
    ):
        (report_dir / f"{job_id}{suffix}").write_text("stub", encoding="utf-8")

    return report_dir


def _genes(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["genes"]


def test_extract_source_call_prefers_source_when_lists_diverge():
    gene_data = {
        "sourceDiplotypes": [
            {
                "label": "*1/*3",
                "phenotypes": ["Intermediate Metabolizer"],
                "activityScore": "1.0",
            }
        ],
        "recommendationDiplotypes": [
            {
                "label": "Unknown/Unknown",
                "phenotypes": ["No Result"],
                "activityScore": None,
            }
        ],
    }
    call = extract_source_call(gene_data)
    assert call["diplotype"] == "*1/*3"
    assert call["phenotype"] == "Intermediate Metabolizer"
    assert float(call["activity_score"]) == 1.0


def test_extract_source_call_defaults_when_absent():
    call = extract_source_call({})
    assert call["diplotype"] == "Unknown/Unknown"
    assert call["phenotype"] == "Unknown"
    assert call["activity_score"] is None


def test_detect_format_nested_v2():
    assert detect_format(_genes(NESTED_V2)) == "nested"


def test_detect_format_flat_v340():
    assert detect_format(_genes(FLAT_V340)) == "flat"


def test_detect_format_empty():
    assert detect_format({}) == "empty"
    assert detect_format(None) == "empty"


def test_iter_gene_blocks_nested_emits_source_and_symbol():
    blocks = list(iter_gene_blocks(_genes(NESTED_V2)))
    keys = {(b.source, b.gene_symbol) for b in blocks}
    assert ("CPIC", "CYP2D6") in keys
    assert ("DPWG", "CYP2D6") in keys
    assert ("CPIC", "CYP2C19") in keys

    cyp = next(b for b in blocks if b.source == "CPIC" and b.gene_symbol == "CYP2D6")
    call = extract_source_call(cyp.gene_data)
    assert call["diplotype"] == "*1/*3"
    assert call["phenotype"] == "Intermediate Metabolizer"
    assert float(call["activity_score"]) == 1.0


def test_iter_gene_blocks_flat_reports_an_absent_source_as_none():
    """PharmCAT 3.x emits ``phenotypeSource: null``; that is not a CPIC claim.

    This assertion previously read ``== "CPIC"``, locking in the ``or "CPIC"``
    default that fabricated a guideline attribution for every 3.4.0 gene
    (BACKLOG 28 + 216).
    """
    blocks = list(iter_gene_blocks(_genes(FLAT_V340)))
    by_gene = {b.gene_symbol: b for b in blocks}
    assert set(by_gene) == {"ABCG2", "CYP2C19", "CYP2D6", "SLCO1B1", "VKORC1"}
    assert by_gene["CYP2C19"].source is None

    call = extract_source_call(by_gene["CYP2C19"].gene_data)
    assert call["diplotype"] == "*38/*38"
    assert call["phenotype"] == "Normal Metabolizer"

    no_result = extract_source_call(by_gene["CYP2D6"].gene_data)
    assert no_result["diplotype"] == "Unknown/Unknown"
    assert no_result["phenotype"] == "No Result"


def test_flat_blocks_carry_real_call_source_and_no_invented_guideline():
    blocks = {b.gene_symbol: b for b in iter_gene_blocks(_genes(FLAT_V340))}

    assert len(blocks) == 5
    # phenotypeSource is None across the whole 3.4.0 fixture -- do not invent "CPIC".
    assert all(b.source is None for b in blocks.values())
    assert blocks["CYP2C19"].call_source == "MATCHER"
    assert blocks["CYP2D6"].call_source == "NONE"


def test_nested_blocks_keep_the_guideline_bucket_and_carry_call_source():
    blocks = list(iter_gene_blocks(_genes(NESTED_V2)))

    assert {b.source for b in blocks} == {"CPIC", "DPWG"}
    cyp2d6 = [b for b in blocks if b.gene_symbol == "CYP2D6"]
    assert cyp2d6
    assert all(b.call_source == "OUTSIDE" for b in cyp2d6)


def test_detect_format_prefers_guideline_keys_over_gene_like_values():
    """A CPIC/DPWG/FDA top key is nested even if values look gene-shaped."""
    genes = {
        "CPIC": {
            "CYP2D6": {
                "geneSymbol": "CYP2D6",
                "sourceDiplotypes": [],
            }
        }
    }
    assert detect_format(genes) == "nested"


def test_detect_format_flat_when_gene_fields_present():
    genes = {
        "CYP2D6": {
            "geneSymbol": "CYP2D6",
            "alleleDefinitionVersion": "2026-01-01",
            "recommendationDiplotypes": [{"label": "*1/*1", "phenotypes": ["NM"]}],
        }
    }
    assert detect_format(genes) == "flat"


# ---------------------------------------------------------------------------
# 159 -- run-derived provenance (matcherMetadata + dataVersion)
# ---------------------------------------------------------------------------


def test_extract_matcher_metadata_from_v340_shape():
    report = {
        "pharmcatVersion": "3.4.0",
        "dataVersion": "2026-07-13-11-40",
        "matcherMetadata": {
            "namedAlleleMatcherVersion": "2.0.0",
            "genomeBuild": "GRCh38.p14",
            "inputFilename": "pharmcat.example.v340.preprocessed.vcf.bgz",
        },
    }
    meta = extract_matcher_metadata(report)
    assert meta["genome_build"] == "GRCh38.p14"
    assert meta["named_allele_matcher_version"] == "2.0.0"
    assert meta["data_version"] == "2026-07-13-11-40"


def test_extract_matcher_metadata_v2_shape_keeps_data_version_only():
    """v2 report.json has dataVersion but no matcherMetadata -> partial render."""
    meta = extract_matcher_metadata({"dataVersion": "2023-10-05-13-00"})
    assert meta["genome_build"] is None
    assert meta["named_allele_matcher_version"] is None
    assert meta["data_version"] == "2023-10-05-13-00"


def test_extract_matcher_metadata_handles_absent_and_malformed():
    for payload in (None, {}, {"matcherMetadata": None}, {"matcherMetadata": "nope"}):
        meta = extract_matcher_metadata(payload)
        assert meta == {
            "genome_build": None,
            "named_allele_matcher_version": None,
            "data_version": None,
        }


def test_extract_matcher_metadata_blank_strings_become_none():
    meta = extract_matcher_metadata(
        {"dataVersion": "  ", "matcherMetadata": {"genomeBuild": ""}}
    )
    assert meta["genome_build"] is None
    assert meta["data_version"] is None


def test_probe_matcher_metadata_reads_a_real_report_dir_layout(tmp_path):
    """The shape production actually produces: nested dir, job-id-named artifacts."""
    from app.reports.generator import probe_matcher_metadata

    report_dir = _make_report_dir(tmp_path, JOB_ID)
    meta = probe_matcher_metadata(str(report_dir), JOB_ID)
    assert meta["genome_build"] == "GRCh38.p14"
    assert meta["named_allele_matcher_version"] == "2.0.0"
    assert meta["data_version"] == "2026-07-13-11-40"


def test_probe_matcher_metadata_missing_dir_is_all_none(tmp_path):
    from app.reports.generator import probe_matcher_metadata

    meta = probe_matcher_metadata(str(tmp_path / "nope"), JOB_ID)
    assert meta == {
        "genome_build": None,
        "named_allele_matcher_version": None,
        "data_version": None,
    }


def test_probe_suffixes_are_filenames_production_actually_writes():
    """159 regression guardrail -- the reason F2 shipped green.

    The first probe searched ``{report_id}.report.json`` and ``*.report.json``.
    That is PharmCAT's *native* output name, produced only inside the PharmCAT
    service's own temp workdir; the service deliberately renames it on the way
    out ("to avoid colliding with our own JSON export"). No report directory has
    ever contained one, so the probe always returned all-``None`` and the
    provenance block never rendered -- yet the unit test passed, because the test
    hand-created a file under the fictional name.

    So: assert the probe's filename knowledge against the *writers*, not against
    a name a test chose.
    """
    from app.reports.generator import _MATCHER_METADATA_FILE_SUFFIXES

    service_src = (REPO_ROOT / "docker" / "pharmcat" / "pharmcat.py").read_text(
        encoding="utf-8"
    )
    router_src = (REPO_ROOT / "app" / "api" / "routes" / "upload_router.py").read_text(
        encoding="utf-8"
    )

    # Every suffix the probe looks for is emitted by a real writer.
    assert _MATCHER_METADATA_FILE_SUFFIXES, "probe has no filename patterns"
    for suffix in _MATCHER_METADATA_FILE_SUFFIXES:
        assert (
            suffix in service_src
        ), f"probe looks for {suffix!r}, which no writer in pharmcat.py emits"

    # The canonical artifact is the one upload_router reconciles.
    assert "_pgx_pharmcat.json" in _MATCHER_METADATA_FILE_SUFFIXES
    assert 'f"{job_id}_pgx_pharmcat.json"' in router_src

    # ...and the name the broken probe used is written by nobody. Every JSON the
    # PharmCAT service lands in a patient directory carries a known suffix.
    landed = set(re.findall(r'patient_dir / f"\{[^}]+\}([^"]*\.json)"', service_src))
    assert landed, "no patient_dir JSON writes found -- did the writer move?"
    assert ".report.json" not in landed
    assert landed <= set(_MATCHER_METADATA_FILE_SUFFIXES), (
        f"PharmCAT service writes JSON suffixes the probe does not know about: "
        f"{landed - set(_MATCHER_METADATA_FILE_SUFFIXES)}"
    )


def test_generate_report_probes_the_directory_it_writes_into():
    """The other half of F2: the probe was aimed one directory too deep.

    ``generate_report`` does ``report_dir = output_dir`` because both callers
    already pass the nested ``/data/reports/{patient_id}/{job_id}`` path, so
    joining a patient id back onto ``output_dir`` invents a level that is not
    there. The probe must use ``output_dir`` as given.
    """
    generator_src = (REPO_ROOT / "app" / "reports" / "generator.py").read_text(
        encoding="utf-8"
    )
    router_src = (REPO_ROOT / "app" / "api" / "routes" / "upload_router.py").read_text(
        encoding="utf-8"
    )

    assert "report_dir = output_dir" in generator_src
    assert "probe_matcher_metadata(output_dir, _report_id_for_meta)" in generator_src
    assert "probe_matcher_metadata(\n        os.path.join(" not in generator_src

    # The caller really does pass an already-nested directory.
    assert "output_dir=str(patient_dir)" in router_src


def test_probe_matcher_metadata_ignores_pharmcats_native_report_json_name(tmp_path):
    """A directory holding only ``{id}.report.json`` is not a real layout."""
    from app.reports.generator import probe_matcher_metadata

    (tmp_path / f"{JOB_ID}.report.json").write_bytes(FLAT_V340.read_bytes())
    meta = probe_matcher_metadata(str(tmp_path), JOB_ID)
    assert meta == {
        "genome_build": None,
        "named_allele_matcher_version": None,
        "data_version": None,
    }


def test_probe_matcher_metadata_reads_raw_report_json_alternate(tmp_path):
    """``_raw_report.json`` is the verbatim copy; use it when the canonical one
    is absent."""
    from app.reports.generator import probe_matcher_metadata

    (tmp_path / f"{JOB_ID}_raw_report.json").write_bytes(FLAT_V340.read_bytes())
    meta = probe_matcher_metadata(str(tmp_path), JOB_ID)
    assert meta["genome_build"] == "GRCh38.p14"


def test_probe_matcher_metadata_globs_when_basename_is_not_the_report_id(tmp_path):
    """The PharmCAT service names artifacts from its own ``name_base``, which is
    not always the job id (see the same fallback at generator.py:1150)."""
    from app.reports.generator import probe_matcher_metadata

    (tmp_path / "Sample_1_pgx_pharmcat.json").write_bytes(FLAT_V340.read_bytes())
    meta = probe_matcher_metadata(str(tmp_path), JOB_ID)
    assert meta["genome_build"] == "GRCh38.p14"
    assert meta["named_allele_matcher_version"] == "2.0.0"


def test_probe_matcher_metadata_skips_a_json_carrying_no_provenance(tmp_path):
    """A parseable but provenance-free JSON must not shadow a later candidate."""
    from app.reports.generator import probe_matcher_metadata

    # Canonical name, but not a PharmCAT report payload.
    (tmp_path / f"{JOB_ID}_pgx_pharmcat.json").write_text(
        json.dumps({"some": "other export"}), encoding="utf-8"
    )
    (tmp_path / f"{JOB_ID}_raw_report.json").write_bytes(FLAT_V340.read_bytes())

    meta = probe_matcher_metadata(str(tmp_path), JOB_ID)
    assert meta["genome_build"] == "GRCh38.p14"
    assert meta["data_version"] == "2026-07-13-11-40"
