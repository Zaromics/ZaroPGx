"""Task 8: surface an ambiguous genome-build header to the user.

Task 6's header_inspector rework made `detect_reference_assembly` report
`assembly=None` with `ambiguous=True` when a file's own header evidence
contradicts itself about the genome build (contig lengths naming two
assemblies, or two conflicting ``##reference=`` lines). But
`file_processor.analyze_file` collapsed that `None` straight to the string
``"unknown"``, and `determine_workflow`'s reference-genome branch only ever
warns when the build is a *named*, unsupported build (e.g. GRCh37) --
`reference != "unknown"`. A self-contradicting header landed on exactly the
same "unknown" string as a header with no evidence at all, so it fell through
every branch and produced no warning whatsoever: a file that lies about
itself proceeded in total silence.

These tests pin:
  1. A contradictory header produces exactly one new warning, through the
     same `workflow["warnings"]` channel/shape the GRCh37 warnings use, and
     does NOT block the job (`unsupported` stays False).
  2. A header with no evidence at all (the pre-existing, legitimate
     "unknown") gets no new warning -- silence there is correct, not a bug.
  3. `inspect_header`'s metadata dict carries the same key set regardless of
     file format (VCF/alignment vs. FASTA/FASTQ/unknown), so a caller can
     index `metadata["reference_genome_ambiguous"]` without `.get`.
"""

import types
import uuid

from app.api.models import FileType, SequencingProfile, VCFHeaderInfo
from app.api.utils.file_processor import FileAnalysis, FileProcessor
from app.api.utils.header_inspector import inspect_header


def _vcf_info(
    reference_genome: str = "unknown",
    ambiguous: bool = False,
    candidates=None,
) -> VCFHeaderInfo:
    return VCFHeaderInfo(
        reference_genome=reference_genome,
        sequencing_platform="Illumina",
        sequencing_profile=SequencingProfile.WGS,
        has_index=True,
        is_bgzipped=True,
        contigs=["chr1", "chr2"],
        sample_count=1,
        variant_count=1000,
        reference_genome_ambiguous=ambiguous,
        reference_genome_candidates=candidates or [],
    )


def _analysis(vcf_info: VCFHeaderInfo) -> FileAnalysis:
    return FileAnalysis(
        file_type=FileType.VCF,
        is_compressed=True,
        has_index=True,
        vcf_info=vcf_info,
    )


def test_ambiguous_header_gets_exactly_one_new_warning():
    baseline = FileProcessor().determine_workflow(_analysis(_vcf_info()))
    workflow = FileProcessor().determine_workflow(
        _analysis(_vcf_info(ambiguous=True, candidates=["GRCh37", "GRCh38"]))
    )

    new_warnings = [w for w in workflow["warnings"] if w not in baseline["warnings"]]
    assert (
        len(new_warnings) == 1
    ), f"expected exactly one new warning, got {new_warnings}"

    warning = new_warnings[0].lower()
    assert "contradicts itself" in warning
    assert "grch37" in warning and "grch38" in warning
    assert "could not be verified" in warning
    assert "caller-declared build" in warning

    # The channel matches the GRCh37/provisional warnings' shape: an HTML
    # <p> string appended to workflow["warnings"], not a new key/flag.
    assert new_warnings[0].startswith("<p>") and new_warnings[0].endswith("</p>")


def test_ambiguous_header_does_not_block_the_job():
    workflow = FileProcessor().determine_workflow(
        _analysis(_vcf_info(ambiguous=True, candidates=["GRCh37", "GRCh38"]))
    )
    assert workflow["unsupported"] is False
    assert workflow["unsupported_reason"] is None


def test_no_evidence_header_adds_no_new_warning():
    # candidates == [] and ambiguous == False is the pre-existing "genuinely
    # undetectable" case, distinct from a header that contradicts itself.
    baseline = FileProcessor().determine_workflow(_analysis(_vcf_info()))
    workflow = FileProcessor().determine_workflow(
        _analysis(_vcf_info(ambiguous=False, candidates=[]))
    )

    assert workflow["warnings"] == baseline["warnings"]
    assert workflow["unsupported"] is False


def test_ambiguous_header_without_named_candidates_still_warns():
    # Metadata is not guaranteed to carry candidate names; the warning must
    # still fire (without naming builds it doesn't have).
    workflow = FileProcessor().determine_workflow(
        _analysis(_vcf_info(ambiguous=True, candidates=[]))
    )
    ambiguity_warnings = [w for w in workflow["warnings"] if "contradicts itself" in w]
    assert len(ambiguity_warnings) == 1


_EXPECTED_METADATA_KEYS = {
    "version",
    "created_by",
    "reference_genome",
    "reference_genome_path",
    "reference_genome_source",
    "reference_genome_ambiguous",
    "reference_genome_candidates",
}


def test_fasta_metadata_keys_match_vcf_shape(tmp_path):
    path = tmp_path / "sample.fasta"
    path.write_text(">chr1 test sequence\nACGTACGTACGT\n", encoding="utf-8")

    metadata = inspect_header(str(path))["metadata"]

    assert set(metadata.keys()) == _EXPECTED_METADATA_KEYS
    assert metadata["reference_genome_ambiguous"] is False
    assert metadata["reference_genome_candidates"] == []


def test_fastq_metadata_keys_match_vcf_shape(tmp_path):
    path = tmp_path / "sample.fastq"
    path.write_text("@read1\nACGTACGT\n+\nIIIIIIII\n", encoding="utf-8")

    metadata = inspect_header(str(path))["metadata"]

    assert set(metadata.keys()) == _EXPECTED_METADATA_KEYS
    assert metadata["reference_genome_ambiguous"] is False
    assert metadata["reference_genome_candidates"] == []


def test_unknown_format_metadata_keys_match_vcf_shape(tmp_path):
    path = tmp_path / "sample.xyz"
    path.write_text("not a recognised genomic format\n", encoding="utf-8")

    metadata = inspect_header(str(path))["metadata"]

    assert set(metadata.keys()) == _EXPECTED_METADATA_KEYS
    assert metadata["reference_genome_ambiguous"] is False
    assert metadata["reference_genome_candidates"] == []


def test_real_vcf_metadata_keys_match_the_expected_shape(tmp_path):
    # Closes the reviewer's note on the VCF branch itself: the three prior
    # tests exercise FASTA/FASTQ/unknown against the same expected set, but
    # never actually drove a real VCF through inspect_header to confirm the
    # branch that HAS always carried these keys (Task 6) still matches.
    path = tmp_path / "sample.vcf"
    path.write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=chr1,length=248956422>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE1\n",
        encoding="utf-8",
    )

    metadata = inspect_header(str(path))["metadata"]

    assert set(metadata.keys()) == _EXPECTED_METADATA_KEYS


# ---------------------------------------------------------------------------
# API response: analysis_info.vcf_info must carry the ambiguity evidence too
# ---------------------------------------------------------------------------
#
# Review finding (Important): upload_router.py rebuilds a fresh VCFHeaderInfo
# for the response's analysis_info.vcf_info from file_analysis.vcf_info, and
# that reconstruction did not pass reference_genome_ambiguous /
# reference_genome_candidates through -- they silently defaulted to False/[],
# so the API told callers a genuinely ambiguous file was unambiguous even
# though workflow["warnings"] (tested above) correctly flagged it. Fixed at
# app/api/routes/upload_router.py:1695-1706 (the VCFHeaderInfo(...) call now
# passes both fields through from file_analysis.vcf_info).
def _upload_ambiguous_vcf_and_capture_response(monkeypatch, tmp_path) -> dict:
    """POST a VCF through the real endpoint and return the JSON body.

    Mirrors tests/test_ui_workflow_flag_reads.py's
    _upload_grch37_and_capture_response: only the DB/Nextflow/file-IO edges
    are stubbed. file_processor.process_files is stubbed to return a
    FileAnalysis whose vcf_info is ambiguous, so the real upload_router code
    assembles the real response shape from it.
    """
    from fastapi.testclient import TestClient

    import app.main as main
    from app.api.routes import upload_router

    main.app.router.on_startup.clear()
    main.app.router.on_shutdown.clear()

    monkeypatch.setattr(
        upload_router, "create_patient", lambda db, identifier: uuid.uuid4()
    )
    monkeypatch.setattr(
        upload_router,
        "register_genetic_data",
        lambda db, patient_id, file_type, file_path, is_supplementary: uuid.uuid4(),
    )

    class _FakeJob:
        def __init__(self):
            self.id = str(uuid.uuid4())
            self.status = "running"
            self.job_metadata = {}

    class _FakeJobService:
        def __init__(self, db):
            self._job = _FakeJob()

        def create_job(self, job_create):
            return self._job

        def update_job(self, job_id, job_update):
            return self._job

    monkeypatch.setattr(upload_router, "JobService", _FakeJobService)

    class _FakeProgressCalc:
        def calculate_progress_from_steps(self, steps_dict, workflow_config, job_id):
            return types.SimpleNamespace(
                progress_percentage=0,
                stage=types.SimpleNamespace(value="header_analysis"),
                message="stubbed",
            )

    monkeypatch.setattr(upload_router, "WorkflowProgressCalculator", _FakeProgressCalc)

    async def _noop_background(*args, **kwargs):
        return None

    monkeypatch.setattr(
        upload_router, "process_file_nextflow_background_with_db", _noop_background
    )

    async def _fake_process_files(files, reference_genome, **kwargs):
        stored = []
        for f in files:
            p = tmp_path / f.filename
            p.write_bytes(await f.read())
            stored.append(str(p))

        analysis = _analysis(_vcf_info(ambiguous=True, candidates=["GRCh37", "GRCh38"]))
        workflow = FileProcessor().determine_workflow(analysis)
        workflow["file_type"] = analysis.file_type.value
        workflow["reference"] = reference_genome or "hg38"
        workflow["workflow_type"] = "genomic_analysis"
        return {
            "success": True,
            "file_paths": stored,
            "file_analysis": analysis,
            "workflow": workflow,
        }

    monkeypatch.setattr(
        upload_router.file_processor, "process_files", _fake_process_files
    )

    def _fake_get_db():
        yield object()

    monkeypatch.setitem(main.app.dependency_overrides, main.get_db, _fake_get_db)

    vcf = b"##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
    with TestClient(main.app) as client:
        resp = client.post(
            "/upload/genomic-data",
            files={"files": ("ambiguous.vcf", vcf, "text/plain")},
            data={"sample_identifier": "ambiguous_sample", "reference_genome": "hg38"},
        )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_ambiguous_upload_response_carries_ambiguity_through_the_api(
    monkeypatch, tmp_path
):
    payload = _upload_ambiguous_vcf_and_capture_response(monkeypatch, tmp_path)

    vcf_info = payload["analysis_info"]["vcf_info"]
    assert vcf_info is not None
    assert vcf_info["reference_genome_ambiguous"] is True
    assert vcf_info["reference_genome_candidates"] == ["GRCh37", "GRCh38"]
