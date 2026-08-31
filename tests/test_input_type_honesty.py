"""Two input types the upload lane accepted and then failed on: gVCF and BCF.

``upload_router``'s own docstring said it: "GVCF/BCF: accepted today, but main.nf has no
branch for either, so the job fails at workflow definition." Both were typed, given a
workflow with ``needs_pypgx``, given a patient row, a job row and a queue slot, and then
killed at ``pipelines/pgx/main.nf``'s ``error "Unsupported input type"``. They were both
refused at ingest for a while; they have since diverged, and the difference is the whole
point of this module:

* **gVCF would run, and lie.** A gVCF is not a VCF with extra rows. Its ``<NON_REF>``
  symbolic allele and ``##GVCFBlock`` records assert reference confidence over whole
  spans, and PharmCAT has not been validated against them. Routed onto the vcf lane it
  would not error — it would emit star alleles nobody has checked. A wrong answer
  delivered confidently is worse than the failed job it replaces. It is still refused.

  Detection has to look past the extension. GATK writes gVCFs as ``sample.g.vcf[.gz]``,
  whose last suffix is ``.vcf``; and the stored name is sanitised, so
  ``образец.gvcf`` reaches disk as ``upload_gvcf`` with no recognisable extension at
  all. Name *and* header are consulted, the header last and unconditionally — including
  the *binary* header of a BCF, now that BCFs are analysed.

* **BCF would not even run, so it is converted instead.** Carrying it on the vcf branch
  was tried and reverted, and that remains wrong: the branch stages the upload verbatim
  as ``upload_sample.bcf``, and ``docker/pharmcat/pharmcat.py``'s ``/genotype`` rejects
  any filename that does not end ``.vcf``/``.vcf.gz``/``.vcf.bgz`` with a 400.
  ``main.nf``'s PharmCAT curl ends in ``|| true``, so that 400 is swallowed and the run
  "completes" with no PharmCAT output — a *silent* failure, strictly worse than the loud
  ``error "Unsupported input type"`` it replaced.

  What changed is not the naming but the bytes: ``main.nf`` now has a ``bcf`` branch
  whose first step POSTs the file to gatk-api's ``/bcf-to-vcf`` (``bcftools view -O z``),
  and everything downstream sees the converted ``.vcf.gz``. The sidecar guard is
  deliberately left alone and still pinned below, because it is precisely what makes the
  conversion have to be real. The tests here assert that what reaches PharmCAT on the
  BCF lane is a converted VCF and never the ``.bcf`` itself.

Also pinned here: ``/upload/inspect-header`` saved its temp file under a suffix built from
the raw client-supplied ``file.filename``, putting a client-controlled string into a
filesystem path.
"""

import asyncio
import gzip
import re
import uuid
from pathlib import Path

import pytest

import app.api.routes.upload_router as ur

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_NF = REPO_ROOT / "pipelines" / "pgx" / "main.nf"

# A minimal single-sample VCF header plus one record. Enough for the extension sniffers
# and for the ##GVCFBlock reader, and it is never handed to a real caller.
VCF_HEADER = b"##fileformat=VCFv4.2\n"
VCF_COLUMNS = b"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tNA12878\n"
PLAIN_VCF_BYTES = VCF_HEADER + VCF_COLUMNS

# What GATK HaplotypeCaller -ERC GVCF writes into the header, and what no plain VCF
# carries: one record per reference-confidence band.
GVCF_BYTES = (
    VCF_HEADER
    + b"##GVCFBlock0-1=minGQ=0(inclusive),maxGQ=1(exclusive)\n"
    + b"##GVCFBlock1-2=minGQ=1(inclusive),maxGQ=2(exclusive)\n"
    + b'##ALT=<ID=NON_REF,Description="Represents any possible alternative allele">\n'
    + VCF_COLUMNS
)

# BCF2's container magic, followed by a zero-length header: `BCF`, a major and a minor
# version byte, then a little-endian uint32 length. Enough for the magic-byte sniffer,
# and deliberately NOT a real BCF -- the tests that need one build it with pysam (see
# `bcf` below), because a BCF that is analysed rather than refused has to survive an
# actual header read.
BCF_BYTES = b"BCF\x02\x02" + (0).to_bytes(4, "little")
BAM_BYTES = b"BAM\x01" + b"\x00" * 32

# GRCh38 and GRCh37 lengths for chr10, which carries CYP2C9/CYP2C19. One correctly-sized
# contig is decisive evidence of a build (see CONTIG_LENGTH_ASSEMBLIES in
# header_inspector.py), so a one-contig test file is enough to drive build detection.
CHR10_GRCH38_LENGTH = 133797422
CHR10_GRCH37_LENGTH = 135534747


@pytest.fixture
def bcf(tmp_path):
    """Write a real, readable single-sample BCF and return its path.

    Real, not a stub: BCF is now converted and analysed rather than refused, so the
    ingest path reads its binary header for a sample count, a build and a
    ``##GVCFBlock`` record. Hand-rolled bytes would be refused for the wrong reason
    (zero samples) and prove nothing about any of that.
    """
    pysam = pytest.importorskip("pysam")

    def _write(
        name="sample.bcf",
        samples=("NA12878",),
        header_lines=(),
        contig="chr10",
        contig_length=CHR10_GRCH38_LENGTH,
    ):
        header = pysam.VariantHeader()
        for line in header_lines:
            header.add_line(line)
        header.contigs.add(contig, length=contig_length)
        for sample in samples:
            header.add_sample(sample)
        path = tmp_path / name
        with pysam.VariantFile(str(path), "wb", header=header) as out:
            out.write(
                out.new_record(
                    contig=contig, start=94781858, stop=94781859, alleles=("G", "A")
                )
            )
        return path

    return _write


# What GATK writes into a gVCF header, kept as header lines a pysam VariantHeader will
# accept, so the same declaration can be put inside a *binary* BCF.
GVCF_HEADER_LINES = (
    "##GVCFBlock0-1=minGQ=0(inclusive),maxGQ=1(exclusive)",
    '##ALT=<ID=NON_REF,Description="Represents any possible alternative allele">',
)


# ---------------------------------------------------------------------------
# The real endpoint, driven through the real FileProcessor
# ---------------------------------------------------------------------------
@pytest.fixture
def upload(client, monkeypatch, tmp_path):
    """POST real bytes at ``/upload/genomic-data``, through the real FileProcessor.

    Same shape as tests/test_upload_story_coherence.py: only what would reach Postgres,
    the filesystem outside ``tmp_path`` or a sibling container is replaced. The file-type
    verdict, the workflow dict and the refusal all come from production code.
    """
    monkeypatch.setattr(ur.file_processor, "temp_dir", tmp_path / "uploads")

    created_patients = []
    monkeypatch.setattr(
        ur,
        "create_patient",
        lambda db, identifier: (created_patients.append(identifier) or uuid.uuid4()),
    )
    monkeypatch.setattr(
        ur,
        "register_genetic_data",
        lambda db, patient_id, file_type, file_path, is_supplementary: uuid.uuid4(),
    )

    class _FakeJob:
        def __init__(self):
            self.id = uuid.uuid4()
            self.status = "pending"
            self.job_metadata = {}

    class _FakeJobService:
        def __init__(self, db):
            self.job = _FakeJob()

        def create_job(self, job_create):
            return self.job

        def update_job(self, job_id, job_update):
            return self.job

    monkeypatch.setattr(ur, "JobService", _FakeJobService)

    async def _noop_background(*args, **kwargs):
        return None

    monkeypatch.setattr(
        ur, "process_file_nextflow_background_with_db", _noop_background
    )

    def _post(name, payload):
        return client.post(
            "/upload/genomic-data",
            files=[("files", (name, payload, "application/octet-stream"))],
            data={"reference_genome": "hg38"},
        )

    _post.created_patients = created_patients
    return _post


# ---------------------------------------------------------------------------
# gVCF: refused, by name and by header
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name,payload",
    [
        ("sample.gvcf", GVCF_BYTES),
        ("sample.gvcf.gz", gzip.compress(GVCF_BYTES)),
        # The GATK spelling. Its last suffix is ".vcf", so the extension table used to
        # type it VCF and hand it to the vcf lane as an ordinary variant call set.
        ("sample.g.vcf", GVCF_BYTES),
        ("sample.g.vcf.gz", gzip.compress(GVCF_BYTES)),
    ],
    ids=["gvcf", "gvcf.gz", "g.vcf", "g.vcf.gz"],
)
def test_a_gvcf_is_refused_before_a_job_exists(upload, name, payload):
    resp = upload(name, payload)

    assert resp.status_code == 400, resp.text
    assert "gvcf" in resp.json()["detail"].lower()
    assert upload.created_patients == [], "refusal must precede any patient/job row"


def test_a_gvcf_named_as_a_plain_vcf_is_caught_by_its_header(upload):
    """Naming is a convention, ``##GVCFBlock`` is a declaration. Trust the declaration."""
    resp = upload("sample.vcf", GVCF_BYTES)

    assert resp.status_code == 400, resp.text
    assert "gvcf" in resp.json()["detail"].lower()


def test_the_gvcf_refusal_names_the_reason_and_a_way_out(upload):
    detail = upload("sample.g.vcf", GVCF_BYTES).json()["detail"].lower()

    # the reason: reference-confidence blocks, and that nobody has validated them
    assert "reference" in detail
    assert "non_ref" in detail
    assert "##gvcfblock" in detail
    assert "pharmcat" in detail
    # the way out: convert to a plain VCF, or hand over the alignment it came from
    assert "convert" in detail
    assert "plain single-sample" in detail
    for accepted in ("bam", "cram", "sam"):
        assert accepted in detail


def test_the_gvcf_refusal_promises_no_analysis(upload):
    """The old copy said gVCFs "will be processed through PyPGx and PharmCAT"."""
    detail = upload("sample.gvcf", GVCF_BYTES).json()["detail"].lower()

    for promise in ("will be processed", "treated as vcf", "may require conversion"):
        assert promise not in detail, promise


def test_the_gvcf_refusal_carries_no_markup_of_its_own(upload):
    """One string, two sinks, and the sinks want opposite things.

    ``unsupported_reason``/``refusal_reason`` is the 400's plain-text ``detail`` *and*
    the red alert in index.html, which builds that alert by interpolating the reason
    into a template string and assigning it with innerHTML (see the ``wf.refused``
    branch there). So a literal ``<NON_REF>`` in the sentence is parsed as an unknown
    tag and vanishes — the user reads "blocks (the  allele and ...)". Escaping it
    instead would leave ``&lt;NON_REF&gt;`` sitting in the API error. Writing the allele
    without its angle brackets is what reads correctly in both, and this pins it.

    (Asserted on the string rather than through tests/js/render_workflow_panel.js: that
    harness's DOM shim stores innerHTML as a plain string and never parses it, so it
    cannot see a tag being swallowed. Recommendations are a different case — they are
    HTML by design, so the ``&lt;NON_REF&gt;`` in the bcftools bullet is correct there.)
    """
    detail = upload("sample.g.vcf", GVCF_BYTES).json()["detail"]

    assert "<" not in detail and ">" not in detail, detail
    assert "&lt;" not in detail and "&amp;" not in detail, detail


def test_a_gvcf_workflow_plans_no_steps_it_cannot_run():
    """No needs_* flag may promise an analysis that is refused at the door."""
    from app.api.models import FileType
    from app.api.utils.file_processor import FileAnalysis, FileProcessor

    workflow = FileProcessor(temp_dir="/tmp").determine_workflow(
        FileAnalysis(
            file_type=FileType.GVCF,
            is_compressed=False,
            has_index=False,
            file_size=1,
            vcf_info=None,
            is_valid=True,
            validation_errors=[],
        )
    )

    assert workflow["unsupported"] is True
    assert workflow["is_provisional"] is False
    for flag in ("needs_alignment", "needs_gatk", "needs_hla", "needs_pypgx"):
        assert workflow[flag] is False, flag


def test_the_preview_marks_a_gvcf_as_refused(client):
    """The header preview and the upload gate must reach the same verdict."""
    resp = client.post(
        "/upload/inspect-header",
        files={"file": ("sample.g.vcf", GVCF_BYTES, "application/octet-stream")},
    )

    assert resp.status_code == 200, resp.text
    workflow = resp.json()["compat"]["workflow"]
    assert workflow["refused"] is True
    assert "gvcf" in (workflow["refusal_reason"] or "").lower()


@pytest.mark.parametrize(
    "name,payload",
    [
        ("sample.vcf", PLAIN_VCF_BYTES),
        ("sample.vcf.gz", gzip.compress(PLAIN_VCF_BYTES)),
    ],
    ids=["vcf", "vcf.gz"],
)
def test_a_plain_vcf_is_not_mistaken_for_a_gvcf(tmp_path, name, payload):
    """The negative control, at the detector.

    Driven through ``_detect_file_type`` rather than the endpoint because
    FileProcessor's VCF path calls the header inspector for a sample count, which needs
    pysam or bcftools and has neither on a bare host — every VCF there reads as
    zero-sample and is refused for a reason that has nothing to do with this change.
    """
    from app.api.models import FileType
    from app.api.utils.file_processor import FileProcessor

    path = tmp_path / name
    path.write_bytes(payload)

    assert FileProcessor(temp_dir=str(tmp_path))._detect_file_type(path) is FileType.VCF


def test_an_unreadable_file_is_not_guessed_into_a_gvcf(tmp_path):
    """The header read is best effort: a truncated gzip must not become a refusal."""
    from app.api.models import FileType
    from app.api.utils.file_processor import FileProcessor

    path = tmp_path / "broken.vcf.gz"
    path.write_bytes(b"\x1f\x8b\x08\x00truncated")

    assert FileProcessor(temp_dir=str(tmp_path))._detect_file_type(path) is FileType.VCF


def test_the_name_rule_does_not_need_a_gvcf_header(tmp_path):
    """``sample.g.vcf`` is refused on its name alone, header or no header.

    Belt and braces on purpose: a gVCF that has been filtered or re-headered can lose
    its ``##GVCFBlock`` records while still carrying reference blocks in the body. The
    name the caller chose is evidence too, and it is the cheaper of the two.
    """
    from app.api.models import FileType
    from app.api.utils.file_processor import FileProcessor

    path = tmp_path / "sample.g.vcf"
    path.write_bytes(PLAIN_VCF_BYTES)

    assert (
        FileProcessor(temp_dir=str(tmp_path))._detect_file_type(path) is FileType.GVCF
    )


# The sanitiser that stores the upload keeps ASCII and drops the rest, so a name with no
# ASCII stem loses its extension entirely: secure_filename("образец.gvcf") == "gvcf",
# stored as "upload_gvcf". Neither ".gvcf" nor ".vcf" matches that, so the name rule
# cannot fire and the content sniff is the only thing left looking.
CYRILLIC_STEM = "образец"


@pytest.mark.parametrize(
    "name,payload",
    [
        (f"{CYRILLIC_STEM}.gvcf", GVCF_BYTES),
        (f"{CYRILLIC_STEM}.gvcf.gz", gzip.compress(GVCF_BYTES)),
    ],
    ids=["stem-stripped", "stem-stripped.gz"],
)
def test_a_gvcf_is_refused_even_when_its_name_does_not_survive(upload, name, payload):
    """ "Rejected whatever it is named" has to mean it.

    The stored path is what ``_detect_file_type`` sees, and sanitising the client's
    filename can leave it with no recognisable extension at all. The header is the only
    evidence that survives that, so it is consulted on the content-sniff path too — not
    just when the name already says "VCF".
    """
    resp = upload(name, payload)

    assert resp.status_code == 400, resp.text
    assert "gvcf" in resp.json()["detail"].lower()
    assert upload.created_patients == []


@pytest.mark.parametrize(
    "stored_name,payload",
    [
        ("upload_gvcf", GVCF_BYTES),
        ("upload_gvcf.gz", gzip.compress(GVCF_BYTES)),
    ],
    ids=["no-extension", "gz-only"],
)
def test_the_content_sniff_answers_gvcf_not_vcf(tmp_path, stored_name, payload):
    """The same defect at the detector, where the fix actually lives.

    ``upload_gvcf`` has no extension the table recognises, so detection falls through to
    the content sniff — which saw ``##fileformat=VCF`` on line one and stopped. Line one
    of a gVCF says exactly that too.
    """
    from app.api.models import FileType
    from app.api.utils.file_processor import FileProcessor

    path = tmp_path / stored_name
    path.write_bytes(payload)

    assert (
        FileProcessor(temp_dir=str(tmp_path))._detect_file_type(path) is FileType.GVCF
    )


# ---------------------------------------------------------------------------
# BCF: accepted, because it is genuinely converted before anything reads its name
# ---------------------------------------------------------------------------
PHARMCAT_SIDECAR = REPO_ROOT / "docker" / "pharmcat" / "pharmcat.py"


def _bcf_workflow(**analysis_kwargs):
    """determine_workflow's verdict for a BCF, driven by production code."""
    from app.api.models import FileType
    from app.api.utils.file_processor import FileAnalysis, FileProcessor

    return FileProcessor(temp_dir="/tmp").determine_workflow(
        FileAnalysis(
            file_type=FileType.BCF,
            is_compressed=True,
            has_index=False,
            file_size=1,
            is_valid=True,
            validation_errors=[],
            **analysis_kwargs,
        )
    )


def test_a_bcf_reaches_the_pipeline_instead_of_a_refusal(upload, bcf):
    """The replacement for the refusal pin: a BCF is accepted and gets a job."""
    resp = upload("sample.bcf", bcf().read_bytes())

    assert resp.status_code == 200, resp.text
    assert upload.created_patients, "an accepted upload must reach a patient row"


def test_a_bcf_is_typed_bcf_and_not_quietly_renamed_to_vcf(bcf):
    """What main.nf is told has to match the file that was staged for it.

    The alias broke exactly this. It is worth pinning at the detector as well as at
    the submitter: the lane is only honest while the type the pipeline branches on is
    the type the file actually is.
    """
    from app.api.models import FileType
    from app.api.utils.file_processor import FileProcessor

    path = bcf()
    assert FileProcessor(temp_dir=str(path.parent))._detect_file_type(path) is (
        FileType.BCF
    )


def test_a_bcf_workflow_plans_the_conversion_it_can_now_run():
    workflow = _bcf_workflow(vcf_info=None)

    assert workflow["unsupported"] is False
    assert workflow["needs_conversion"] is True
    # needs_gatk, not just needs_conversion: upload_router turns needs_gatk into
    # --skip_gatk, and main.nf refuses --skip_gatk on a bcf run. Left False, every
    # BCF job would be submitted in the one shape the pipeline rejects.
    assert workflow["needs_gatk"] is True
    assert workflow["needs_pypgx"] is True
    assert workflow["needs_mtdna"] is True
    assert workflow["needs_hla"] is False, "a VCF-shaped input cannot be HLA-typed"


def test_a_grch37_bcf_is_converted_and_then_lifted(bcf):
    """Both steps, not either: conversion makes it readable, liftover makes it GRCh38."""
    from app.api.utils.file_processor import FileProcessor

    path = bcf("old.bcf", contig="10", contig_length=CHR10_GRCH37_LENGTH)
    analysis = asyncio.run(FileProcessor(temp_dir=str(path.parent)).analyze_file(path))

    assert analysis.vcf_info is not None, (
        "a BCF must get vcf_info: without it the build is unknown and a GRCh37 file "
        "is analysed as GRCh38"
    )
    assert analysis.vcf_info.reference_genome == "GRCh37"

    workflow = _bcf_workflow(vcf_info=analysis.vcf_info)
    assert workflow["needs_conversion"] is True
    assert workflow["needs_liftover"] is True
    assert workflow["source_build"] == "GRCh37"


def test_the_bcf_plan_states_the_conversion_and_keeps_the_vcf_caveats(bcf):
    """Honest in both directions: the re-encode is free, the VCF limits are not."""
    from app.api.utils.file_processor import FileProcessor

    path = bcf()
    analysis = asyncio.run(FileProcessor(temp_dir=str(path.parent)).analyze_file(path))
    workflow = _bcf_workflow(vcf_info=analysis.vcf_info)

    recommendations = " ".join(workflow["recommendations"]).lower()
    assert "bcftools" in recommendations
    assert "no accuracy" in recommendations, (
        "the plan must say the conversion itself costs nothing, or a reader assumes "
        "it does"
    )

    warnings = " ".join(workflow["warnings"]).lower()
    assert "hla typing can not be performed" in warnings
    assert "cyp2d6" in warnings
    assert "structural variants" in warnings


def _vcf_workflow(**analysis_kwargs):
    from app.api.models import FileType
    from app.api.utils.file_processor import FileAnalysis, FileProcessor

    return FileProcessor(temp_dir="/tmp").determine_workflow(
        FileAnalysis(
            file_type=FileType.VCF,
            is_compressed=True,
            has_index=False,
            file_size=1,
            is_valid=True,
            validation_errors=[],
            **analysis_kwargs,
        )
    )


# What a VCF upload was told before the VCF and BCF branches were made to share one
# planner, quoted verbatim. The sharing is what keeps two copies of clinical
# build-detection copy from drifting apart; the risk it introduces is that the VCF half
# changes while nobody is looking at it, which is what this list is here to catch.
VCF_WARNINGS_BEFORE_THE_SPLIT = [
    "<p>⚠️ VCF datafiles lack the necessary raw information to perform complete pharmacogenomic analysis.</p>",
    "<p>The analysis can proceed, however, the results will be incomplete and have degraded accuracy.</p>",
    "<p class='preflight'>If you have an upstream, or original, datafile, such as BAM/SAM/CRAM, please consider uploading it instead in order for the PGx analysis to yield complete results with optimal fidelity.</p>",
    "<p class='preflight'>Although significant computation and processing time is required, if possible, using an upstream datafile(s) is strongly recommended.</p>",
    "<p>⚠️ HLA typing can not be performed.</p>",
    "<p>⚠️ CYP2D6 typing will be performed with degraded accuracy.</p>",
    "<p>⚠️ All genes with phenotypes affected by structural variants and copy-number variants will be evaluated with degraded accuracy.</p>",
]

VCF_RECOMMENDATIONS_BEFORE_THE_SPLIT = [
    "<p>VCF files use the quick pipeline:</p>",
    "<p>Step 1: Run PyPGx for star allele calling on all available pharmacogenes.</p>",
    "<p>Step 2: Run PharmCAT with outside calls from PyPGx.</p>",
]


def test_sharing_the_planner_did_not_change_what_a_vcf_is_told():
    workflow = _vcf_workflow(vcf_info=None)

    assert workflow["warnings"] == VCF_WARNINGS_BEFORE_THE_SPLIT
    assert workflow["recommendations"] == VCF_RECOMMENDATIONS_BEFORE_THE_SPLIT


def test_a_bcf_gets_the_whole_vcf_plan_and_the_conversion_on_top(bcf):
    """Nothing may be dropped on the way through the shared planner.

    A BCF *is* a VCF once converted, so its plan has to be the VCF plan exactly, with
    the conversion stated in front of it — not a paraphrase that happens to mention
    most of the same caveats.
    """
    from app.api.utils.file_processor import FileProcessor

    path = bcf()
    analysis = asyncio.run(FileProcessor(temp_dir=str(path.parent)).analyze_file(path))

    vcf_workflow = _vcf_workflow(vcf_info=analysis.vcf_info)
    bcf_workflow = _bcf_workflow(vcf_info=analysis.vcf_info)

    assert bcf_workflow["warnings"] == vcf_workflow["warnings"]
    shared = vcf_workflow["recommendations"]
    assert bcf_workflow["recommendations"][-len(shared) :] == shared
    extra = " ".join(bcf_workflow["recommendations"][: -len(shared)]).lower()
    assert extra, "the BCF plan must say the file is converted first"
    assert "converted" in extra and "bcftools" in extra


def test_a_gvcf_written_as_a_bcf_is_still_refused(upload, bcf):
    """The trap the conversion opened, closed.

    ``_header_declares_gvcf_blocks`` reads the file as gzip-or-text, so it answers
    False for every BCF whatever the header says. While BCF was refused outright that
    cost nothing; once it is converted and analysed, a gVCF-in-a-BCF would be
    re-encoded into a plain VCF and analysed as one — a silent wrong answer wearing a
    conversion step, which is exactly what the gVCF refusal exists to prevent.
    """
    resp = upload("sample.bcf", bcf(header_lines=GVCF_HEADER_LINES).read_bytes())

    assert resp.status_code == 400, resp.text
    assert "gvcf" in resp.json()["detail"].lower()
    assert upload.created_patients == [], "refusal must precede any patient/job row"


def test_a_multi_sample_bcf_is_refused(bcf):
    """The one-sample policy has to cover the door BCF comes in by.

    The pipeline converts a BCF to a VCF and analyses that, so a multi-sample BCF
    reaches PyPGx/PharmCAT as a multi-sample VCF — the case the VCF check already
    refuses, arriving unchecked.
    """
    from app.api.utils.file_processor import FileProcessor

    path = bcf(samples=("NA12878", "NA12891"))
    result = asyncio.run(
        FileProcessor(temp_dir=str(path.parent)).process_upload(str(path))
    )

    assert result["status"] == "error"
    assert "exactly one sample" in result["error"]
    assert (
        "BCF" in result["error"]
    ), "the message must name the format that was uploaded"


def test_the_preview_plans_a_conversion_for_a_bcf(client, bcf):
    """The preview and the upload gate must reach the same verdict.

    ``needs_conversion`` travels on the compat workflow for the same reason
    ``needs_liftover`` does: without it the preview panel draws a plan one step shorter
    than the plan that runs.
    """
    resp = client.post(
        "/upload/inspect-header",
        files={"file": ("sample.bcf", bcf().read_bytes(), "application/octet-stream")},
    )

    assert resp.status_code == 200, resp.text
    workflow = resp.json()["compat"]["workflow"]
    assert workflow["refused"] is False
    assert workflow["needs_conversion"] is True


def test_the_pharmcat_sidecar_still_refuses_a_non_vcf_filename():
    """Why the conversion has to be real, pinned where the reason actually lives.

    ``/genotype`` checks the *filename*, and main.nf stages what it is given — a
    ``.bcf`` carried downstream verbatim reaches the sidecar still called ``.bcf`` and
    is answered 400. Because that curl ends in ``|| true``, the 400 is swallowed and
    the run finishes with no PharmCAT output at all. That is what makes renaming a BCF
    dishonest and re-encoding it the only correct answer. If this guard is ever widened
    to accept BCF, the conversion step is what should be revisited.
    """
    source = PHARMCAT_SIDECAR.read_text(encoding="utf-8")
    guard = re.search(
        r"file\.filename\.endswith\(\(([^)]*)\)\)",
        source,
    )

    assert guard, "pharmcat.py no longer gates /genotype on the filename extension"
    accepted = set(re.findall(r"'([^']+)'|\"([^\"]+)\"", guard.group(1)))
    accepted = {a or b for a, b in accepted}
    assert ".bcf" not in accepted, (
        "the PharmCAT sidecar now accepts .bcf; revisit the conversion step in "
        "pipelines/pgx/main.nf, which exists only because it does not"
    )
    assert ".vcf" in accepted


# ---------------------------------------------------------------------------
# The BCF lane, pinned across the boundary it has to hold on both sides
# ---------------------------------------------------------------------------
def _bcf_branch() -> str:
    """The workflow body's vcf/bcf branch, from main.nf's own source."""
    text = MAIN_NF.read_text(encoding="utf-8")
    start = text.index("else if (params.input_type in ['vcf', 'bcf'])")
    return text[start : text.index("\n    else {", start)]


def _bcf_process() -> str:
    text = MAIN_NF.read_text(encoding="utf-8")
    start = text.index("process BcfToVCF")
    return text[start : text.index("\nprocess ", start + 10)]


def test_main_nf_has_a_bcf_branch_that_converts_first():
    """The replacement for "main.nf still has no bcf branch".

    That pin existed because the ingest refusal was only honest while nothing
    downstream could carry a BCF. Something can now, so what has to be pinned is the
    other half of the same contract: the branch must exist AND it must convert, not
    stage the upload onward.
    """
    text = MAIN_NF.read_text(encoding="utf-8")
    conditions = re.findall(r"params\.input_type (?:==|in) (\[[^\]]*\]|'[a-z]+')", text)
    branched_on = {
        token
        for condition in conditions
        for token in re.findall(r"'([a-z]+)'", condition)
    }

    assert "vcf" in branched_on, "main.nf no longer branches on input_type; re-audit"
    assert "bcf" in branched_on
    assert "process BcfToVCF" in text
    assert "BcfToVCF(input_ch" in _bcf_branch(), (
        "the bcf branch must feed the raw upload to BcfToVCF; anything else means the "
        "upload is being carried downstream unconverted"
    )


def test_what_reaches_pharmcat_on_the_bcf_lane_is_a_converted_vcf():
    """Never the ``.bcf`` itself — that is the whole reason the lane exists."""
    process = _bcf_process()

    assert "http://gatk-api:5000/bcf-to-vcf" in process
    assert (
        "--fail-with-body" in process
    ), "a swallowed conversion error would put an error document where the VCF goes"
    assert 'path "converted.vcf.gz", emit: vcf' in process

    branch = _bcf_branch()
    # vcf_ch is what PyPGx and PharmCAT are handed; on this lane it must come from the
    # conversion (directly, or through the liftover that reads the conversion).
    assert "vcf_ch = converted_vcf_ch" in branch
    assert re.search(
        r"LiftoverVCF\(\s*converted_vcf_ch", branch
    ), "the liftover must read the conversion's output, never the raw upload"


def test_the_mtdna_sidecar_gets_the_converted_but_unlifted_file():
    """A lifted chrM is the one thing the sidecar must never be handed.

    b37's MT is already rCRS, so pushing it through the hg19-sourced chain shifts
    MT-RNR1 positions by 2. The vcf lane sends the original upload for that reason; the
    bcf lane's equivalent of "the original upload" is the converted-but-unlifted VCF,
    because the conversion only re-encodes the same records.
    """
    text = MAIN_NF.read_text(encoding="utf-8")
    wiring = text[text.index("if (params.skip_mtdna)") :]

    assert re.search(
        r"params\.input_type == 'bcf'\)\s*\{\s*mtdna_variants_ch = converted_vcf_ch",
        wiring,
    ), "the bcf lane must send the sidecar the converted, unlifted VCF"
    assert "mtdna_input_type = (params.input_type in ['vcf', 'bcf']) ? 'vcf'" in wiring
    assert "mtdna_variants_ch = vcf_ch" not in wiring, (
        "vcf_ch may have been through LiftoverVCF; the sidecar must never see a lifted "
        "chrM (b37's MT is already rCRS)"
    )


def test_skip_gatk_is_refused_for_a_bcf_run():
    """The conversion runs in the gatk-api container, so skipping it starves the lane."""
    text = MAIN_NF.read_text(encoding="utf-8")
    guard = re.search(r"if \(params\.skip_gatk && \[([^\]]*)\]", text)

    assert guard, "main.nf no longer guards skip_gatk against the conversion inputs"
    assert "'bcf'" in guard.group(1)


def test_the_conversion_step_name_matches_what_main_nf_posts():
    """A step main.nf posts that the registry does not mint 404s and hangs at [pending].

    Both halves are read from source rather than from a hand-copied constant: the
    registry is the authority for what exists, main.nf for what is reported.
    """
    from app.api.models import WorkflowOptions
    from app.services.workflow_registry import resolve_steps
    from app.services.workflow_stages import STEP_TO_STAGE, WorkflowStage

    posted = re.search(r"step_name=([a-z_]+)", _bcf_process())
    assert posted, "BcfToVCF no longer posts a step_name"
    step_name = posted.group(1)

    steps = resolve_steps(
        "genomic_analysis", WorkflowOptions(needs_conversion=True, needs_pypgx=True)
    )
    names = [s.step_name for s in steps]

    assert (
        step_name in names
    ), f"main.nf posts {step_name!r}; the registry mints {names}"
    by_name = {s.step_name: s for s in steps}
    assert by_name[step_name].container_name == "gatk-api"
    # Ordered before the liftover it may be followed by: a GRCh37 BCF is converted
    # first, and the liftover only ever sees the VCF this step produced.
    ordered = [
        s.step_name
        for s in resolve_steps(
            "genomic_analysis",
            WorkflowOptions(
                needs_conversion=True, needs_liftover=True, needs_pypgx=True
            ),
        )
    ]
    assert ordered.index(step_name) < ordered.index("liftover")
    assert STEP_TO_STAGE[step_name] is WorkflowStage.GATK


def test_the_conversion_step_is_not_minted_for_a_plain_vcf():
    from app.api.models import WorkflowOptions
    from app.services.workflow_registry import resolve_steps

    names = [
        s.step_name
        for s in resolve_steps("genomic_analysis", WorkflowOptions(needs_pypgx=True))
    ]

    assert "bcf_to_vcf" not in names


def test_no_alias_silently_rewrites_the_submitted_input_type():
    """A rename at submission is how BCF got a lane it could not finish.

    The alias made the payload disagree with the file on disk: main.nf was told ``vcf``
    while the staged file was still ``upload_sample.bcf``. Nothing may reintroduce that
    without also making the lane real, so the module must carry no alias table.
    """
    assert not hasattr(ur, "NEXTFLOW_INPUT_TYPE_ALIASES"), (
        "upload_router grew an input-type alias table again; a rename here must be "
        "backed by a lane that can actually finish"
    )


def _submit(monkeypatch, tmp_path, workflow):
    """Run the real Nextflow submitter and return the payload it POSTs.

    Everything replaced here is a boundary — the job store, the header inspector, the
    HTTP call and the completion poll. The payload assembly under test is production
    code.
    """
    captured = {}

    class _FakeJob:
        def __init__(self):
            self.id = "job-1"
            self.status = "running"
            self.job_metadata = {}

    job = _FakeJob()

    class _FakeJobService:
        def __init__(self, db):
            pass

        def get_job(self, job_id):
            return job

        def update_job(self, job_id, update):
            return job

        def update_job_step(self, job_id, step_name, update):
            return None

        def log_job_event(self, job_id, log_data):
            return None

    monkeypatch.setattr(ur, "JobService", _FakeJobService)
    monkeypatch.setattr(ur, "inspect_header", lambda path: {"samples": ["NA12878"]})
    monkeypatch.setattr(ur, "save_genomic_header", lambda db, path, ft, header: 1)
    monkeypatch.setattr(ur, "extract_raw_header_text", lambda path: None)

    class _RunResponse:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"job_key": "nf-1", "outdir": "/data/reports/p/job-1"}

    def _post(url, json=None, timeout=None):
        captured["payload"] = json
        return _RunResponse()

    monkeypatch.setattr(ur.requests, "post", _post)

    async def _noop_wait(*args, **kwargs):
        return None

    monkeypatch.setattr(ur, "wait_for_nextflow_completion", _noop_wait)

    asyncio.run(
        ur.process_file_nextflow_background(
            str(tmp_path / "sample.in"),
            "patient-1",
            "data-1",
            workflow,
            None,
            None,
            "job-1",
        )
    )

    assert "payload" in captured, "the submitter never reached the Nextflow POST"
    return captured["payload"]


@pytest.mark.parametrize("file_type", ["vcf", "bcf", "bam", "cram", "sam"])
def test_the_submitted_input_type_is_the_type_that_was_detected(
    monkeypatch, tmp_path, file_type
):
    """What main.nf is told must match the file that was staged for it.

    The BCF alias broke exactly this: the payload said ``vcf`` while the staged file was
    still ``upload_sample.bcf``, and the mismatch surfaced as a swallowed 400 from the
    PharmCAT sidecar instead of a refusal the user could act on. ``bcf`` is in the list
    now precisely because it must arrive as ``bcf`` — that is what selects the branch
    that converts it.
    """
    payload = _submit(
        monkeypatch, tmp_path, {"file_type": file_type, "reference": "hg38"}
    )

    assert payload["input_type"] == file_type


# ---------------------------------------------------------------------------
# /upload/inspect-header: the client's filename must not shape the temp path
# ---------------------------------------------------------------------------
@pytest.fixture
def captured_temp_suffix(client, monkeypatch):
    """POST to /upload/inspect-header and return the suffix NamedTemporaryFile got."""
    real_named_temporary_file = ur.tempfile.NamedTemporaryFile
    seen = []

    def _spy(*args, **kwargs):
        seen.append(kwargs.get("suffix", ""))
        return real_named_temporary_file(*args, **kwargs)

    monkeypatch.setattr(ur.tempfile, "NamedTemporaryFile", _spy)

    def _inspect(name):
        resp = client.post(
            "/upload/inspect-header",
            files={"file": (name, PLAIN_VCF_BYTES, "application/octet-stream")},
        )
        assert resp.status_code == 200, resp.text
        assert seen, "NamedTemporaryFile was never called"
        return seen[-1]

    return _inspect


@pytest.mark.parametrize(
    "name",
    [
        "a/b;c.vcf",
        "../../etc/passwd.vcf",
        "..\\..\\windows\\evil.vcf",
        "$(touch pwned).vcf",
        "*.vcf",
        "a b\tc.vcf",
    ],
)
def test_a_hostile_filename_never_reaches_the_temp_file_suffix(
    captured_temp_suffix, name
):
    suffix = captured_temp_suffix(name)

    assert "/" not in suffix and "\\" not in suffix
    assert ".." not in suffix
    for hostile in (";", "|", "&", "$", "`", "*", "?", "<", ">", " ", "\t", "'", '"'):
        assert hostile not in suffix, f"{hostile!r} survived in {suffix!r}"


def test_the_extension_still_survives_into_the_suffix(captured_temp_suffix):
    """The inspector branches on the extension, so sanitising must not eat it."""
    assert captured_temp_suffix("sample.vcf").endswith(".vcf")
    assert captured_temp_suffix("sample.vcf.gz").endswith(".vcf.gz")


def test_a_pathological_filename_still_yields_a_usable_suffix(captured_temp_suffix):
    """The fence around the sanitiser, not a pin on the fix.

    ``secure_filename("...")`` returns ``""``, which would leave the suffix as a bare
    ``"_"`` — the exact shape that broke app/main.py's variant-call route, where the
    empty name turned ``os.path.join(temp_dir, "")`` back into the directory.
    ``safe_upload_basename`` substitutes a generated name for that case; this says any
    future replacement has to keep doing so.
    """
    suffix = captured_temp_suffix("...")

    assert suffix.strip("_"), f"empty suffix for a pathological name: {suffix!r}"
