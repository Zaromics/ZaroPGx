"""The FHIR bundle says what kind of input produced its genotypes.

Before this a bundle from a 23andMe export -- most genes declined, no copy number, a
fifth of PharmCAT's rs-identified positions present -- was indistinguishable, to a
receiving system, from one built from a 30x genome. The facts now go in
``Observation.method`` on every genotype and variant (app/services/fhir/input_method.py).

The last two tests run the real report generator over real job rows and export what
it saved, because a test that hands the bundle builder a dictionary assembled by hand
cannot discover what the generator actually stores.
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import app.reports.generator as generator_module
from app.services.fhir import input_method, report_data, terminology, xml_format
from app.services.fhir_export_service import FHIRExportService

REPO = Path(__file__).resolve().parents[1]
IG_PACKAGE = (
    REPO
    / "docker"
    / "fhir-server"
    / "packages"
    / "hl7.fhir.uv.genomics-reporting-3.0.0.tgz"
)
REPORT_JSON = REPO / "test_data" / "pharmcat.unphased.example.report.json"

# What /array-to-vcf returns (gatk_api.py), with test_array_report_provenance's counts.
ARRAY_STEP = {
    "vendor_shape": "23andme",
    "n_rows_read": 8,
    "n_genotypes_usable": 8,
    "n_no_calls": 0,
    "n_pharmcat_positions_rsid": 1018,
    "n_positions_carried": 8,
}
GVCF_STEP = {
    "n_pharmcat_positions": 1226,
    "n_pgx_positions_called": 1180,
    "positions_expressed_in": "GRCh37",
}
LIFTOVER_STEP = {
    "source_build": "GRCh37",
    "target_build": "GRCh38",
    "n_lifted": 4210,
    "n_rejected": 12,
}


def _method(file_type, **facts):
    return input_method.method_concept(
        {"input_method": dict(facts, file_type=file_type)}
    )


def _codes(concept):
    return [c["code"] for c in (concept or {}).get("coding", [])]


# --- what each input says ---------------------------------------------------------


def test_an_array_is_a_microarray_and_says_how_little_it_carried():
    concept = _method("23andme", array=ARRAY_STEP)
    assert _codes(concept) == ["microarray"]
    text = concept["text"]
    assert "23andMe genotyping array export" in text
    assert "by rs identifier" in text
    assert "8 of the 1,018 rs-identified positions" in text
    assert "no copy-number or structural calls" in text


def test_ancestrydna_is_named_as_the_reader_names_it():
    concept = _method("ancestry", array=dict(ARRAY_STEP, vendor_shape="ancestrydna"))
    assert concept["text"].startswith("AncestryDNA genotyping array export")


@pytest.mark.parametrize("file_type", ["bam", "cram", "sam"])
def test_an_alignment_is_sequencing_genotyped_from_its_reads(file_type):
    concept = _method(file_type)
    assert _codes(concept) == ["ngs-mps"]
    assert f"Aligned sequencing reads ({file_type.upper()})" in concept["text"]


def test_fastq_says_it_was_aligned_here():
    concept = _method("fastq")
    assert _codes(concept) == ["ngs-mps"]
    assert "aligned to GRCh38 with bwa-mem" in concept["text"]


def test_a_gvcf_is_sequencing_and_says_what_it_covered_and_in_which_build():
    concept = _method("gvcf", gvcf=GVCF_STEP)
    assert _codes(concept) == ["ngs-mps"]
    assert "GATK GenotypeGVCFs" in concept["text"]
    assert "1,180 of 1,226 positions carried a call" in concept["text"]
    assert "genotyped in GRCh37 and lifted back to GRCh38" in concept["text"]


@pytest.mark.parametrize("file_type", ["vcf", "bcf"])
def test_a_vcf_gets_no_code_because_the_file_does_not_say_how_it_was_made(file_type):
    """A VCF comes from a chip as easily as from a sequencer."""
    concept = _method(file_type, absent_to_ref=False, unspecified_to_ref=False)
    assert "coding" not in concept
    assert f"supplied as {file_type.upper()}" in concept["text"]
    assert "the submitting caller's" in concept["text"]
    assert "not assumed reference" in concept["text"]


def test_a_vcf_run_that_assumed_the_reference_says_so():
    text = _method("vcf", absent_to_ref=True, unspecified_to_ref=True)["text"]
    assert (
        "absent from the file were assumed reference (PharmCAT --absent-to-ref)" in text
    )
    assert "--unspecified-to-ref" in text


def test_a_lifted_vcf_says_what_the_lift_cost():
    text = _method("vcf", absent_to_ref=False, liftover=LIFTOVER_STEP)["text"]
    assert (
        "lifted over from GRCh37 to GRCh38 (4,210 lifted, 12 dropped as unliftable)"
        in text
    )


def test_a_count_the_step_did_not_record_is_left_out_not_zeroed():
    text = _method("23andme", array={"vendor_shape": "23andme"})["text"]
    assert "of the" not in text
    assert " 0 " not in text and "None" not in text


def test_flags_not_read_say_nothing_about_assuming_reference():
    """None means the job's flags could not be read: neither claim is made."""
    text = _method("vcf")["text"]
    assert "assumed reference" not in text


@pytest.mark.parametrize("data", [{}, {"file_type": "unknown"}, {"file_type": "fasta"}])
def test_no_method_where_the_input_is_not_known(data):
    assert input_method.method_concept(data) is None


def test_the_file_type_alone_still_gives_the_method():
    """A bundle saved before the facts were: it has the file type only."""
    assert _codes(input_method.method_concept({"file_type": "cram"})) == ["ngs-mps"]


def test_the_text_is_plain_text():
    for concept in (
        _method("23andme", array=ARRAY_STEP),
        _method("gvcf", gvcf=GVCF_STEP),
        _method("vcf", absent_to_ref=True, liftover=LIFTOVER_STEP),
    ):
        assert "<" not in concept["text"] and "your" not in concept["text"].lower()


# --- the codes are the IG's ---------------------------------------------------------


def test_every_code_sent_is_in_the_shipped_ig_with_its_display():
    with tarfile.open(IG_PACKAGE) as package:
        member = package.extractfile(
            "package/CodeSystem-genomic-study-method-type-cs.json"
        )
        system = json.load(member)
    assert system["url"] == terminology.GENOMIC_STUDY_METHOD_TYPE
    assert system["content"] == "complete"
    displays = {c["code"]: c["display"] for c in system["concept"]}
    for code, display in terminology.GENOMIC_STUDY_METHOD_TYPE_DISPLAYS.items():
        assert displays.get(code) == display, code


# --- where it lands in the bundle ---------------------------------------------------


def _bundle(data):
    service = FHIRExportService(MagicMock())
    return service._build_fhir_bundle(data, "run-1", {"id": "p1"})


def _observations(bundle, profile):
    return [
        e["resource"]
        for e in bundle["entry"]
        if profile in e["resource"].get("meta", {}).get("profile", [])
    ]


DATA = {
    "file_type": "23andme",
    "input_method": {"file_type": "23andme", "array": ARRAY_STEP},
    "genes": [
        {
            "gene": "CYP2C19",
            "diplotype": "*1/*2",
            "phenotype": "Intermediate Metabolizer",
            "unlisted_flag": {
                "variants": [
                    {"chrom": "chr10", "pos": 94781859, "ref": "G", "alt": "A"}
                ]
            },
        }
    ],
    "drugRecommendations": [],
}


def test_every_genotype_and_variant_carries_it_and_no_implication_does():
    bundle = _bundle(DATA)
    measured = _observations(bundle, terminology.PROFILE_GENOTYPE) + _observations(
        bundle, terminology.PROFILE_VARIANT
    )
    assert len(measured) == 2
    assert all(_codes(o.get("method")) == ["microarray"] for o in measured)
    for profile in (
        terminology.PROFILE_THERAPEUTIC_IMPLICATION,
        terminology.PROFILE_DIAGNOSTIC_IMPLICATION,
    ):
        assert all("method" not in o for o in _observations(bundle, profile))


def test_no_method_element_when_the_input_is_not_known():
    data = {k: v for k, v in DATA.items() if k not in ("file_type", "input_method")}
    bundle = _bundle(data)
    assert all(
        "method" not in o for o in _observations(bundle, terminology.PROFILE_GENOTYPE)
    )


def test_the_xml_bundle_carries_it_too():
    xml = xml_format.bundle_to_xml(_bundle(DATA))
    assert "<method>" in xml and "microarray" in xml


# --- the real producer --------------------------------------------------------------


def _report(session_factory, monkeypatch, tmp_path, file_type, steps, flags=False):
    """Run generate_report over a real job row and its step rows; return the data it
    saved for the bundle."""
    from app.api.db import Job, JobStep
    from app.api.models import JobCreate
    from app.services.job_service import JobService

    monkeypatch.setattr(
        generator_module.PharmCATDataService,
        "get_pharmcat_data_for_workflow",
        lambda self, job: None,
    )
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG, key, key == "write_html"
            )
    monkeypatch.setattr(generator_module, "fhir_export_enabled", lambda: False)
    session = session_factory()
    try:
        job = JobService(session).create_job(
            JobCreate(workflow_type="genomic_analysis", name="fhir-input-method")
        )
        session.commit()
        row = session.query(Job).filter(Job.id == job.id).first()
        row.job_metadata = {
            "file_analysis": {"file_type": file_type},
            "workflow": {},
            "pharmcat_absent_to_ref": flags,
            "pharmcat_unspecified_to_ref": False,
        }
        for order, (name, output) in enumerate(steps.items()):
            session.add(
                JobStep(
                    job_id=job.id,
                    step_name=name,
                    step_order=order,
                    status="completed",
                    output_data=output,
                )
            )
        session.commit()
        generator_module.generate_report(
            {"data": json.loads(REPORT_JSON.read_text(encoding="utf-8"))},
            str(tmp_path),
            {"id": "p1"},
            job_id=str(job.id),
            db_session=session,
        )
        saved = json.loads(
            (tmp_path / report_data.filename(str(job.id))).read_text("utf-8")
        )
    finally:
        session.rollback()
        session.close()
    return saved["data"]


def test_the_generator_saves_an_arrays_facts_for_the_bundle(
    session_factory, monkeypatch, tmp_path
):
    data = _report(
        session_factory, monkeypatch, tmp_path, "23andme", {"array_to_vcf": ARRAY_STEP}
    )
    genotypes = _observations(_bundle(data), terminology.PROFILE_GENOTYPE)
    assert genotypes
    method = genotypes[0]["method"]
    assert _codes(method) == ["microarray"]
    assert "8 of the 1,018 rs-identified positions" in method["text"]


def test_the_generator_saves_a_lifted_vcfs_facts_for_the_bundle(
    session_factory, monkeypatch, tmp_path
):
    data = _report(
        session_factory,
        monkeypatch,
        tmp_path,
        "vcf",
        {"liftover": LIFTOVER_STEP},
        flags=True,
    )
    method = _observations(_bundle(data), terminology.PROFILE_GENOTYPE)[0]["method"]
    assert "coding" not in method
    assert "(PharmCAT --absent-to-ref)" in method["text"]
    assert "4,210 lifted, 12 dropped as unliftable" in method["text"]
