"""The plan shown before upload must be the plan that runs.

``/upload/inspect-header`` already calls the *same* ``determine_workflow`` the upload
calls. It then forwarded only a handful of that verdict's flags on its ``compat``
workflow, so ``buildPlannedWorkflowHTML`` in ``app/templates/index.html`` re-derived the
rest in JavaScript from the file extension -- a second copy of a decision that lives in
``FileProcessor``.

The two copies drifted, in both directions:

  * ``needs_hla`` was not set by the CRAM and SAM branches for a long time, while the
    panel's ``['bam','cram','sam'].includes(fileType)`` fallback drew the HLA step for
    them regardless. The picture was right and the flags were wrong.
  * ``needs_mtdna`` has no fallback at all, so the pre-upload plan silently omitted a
    step that runs on every VCF, BAM, CRAM and SAM job. The plan the user approved was
    one step shorter than the plan that ran.

So the endpoint now forwards the whole ``needs_*`` set, and this test pins the property
that makes the JavaScript fallbacks unnecessary: for a given file, the preview's flags
and the upload's flags agree.

It deliberately compares against ``determine_workflow`` rather than against a second
upload, because that function is the single source both surfaces are supposed to be
quoting -- if they ever stop agreeing with it, it should be this test that says so and
not a user noticing the bar draw a step that never arrives.
"""

import asyncio

import pytest

from app.api.utils.file_processor import FileProcessor

# Every needs_* flag buildPlannedWorkflowHTML reads. If the panel starts reading a new
# one, add it here: the failure this guards against is a flag the panel consults that
# the preview does not send, which reads on the client as `undefined` -- falsy, so the
# step silently disappears from the plan rather than erroring.
PANEL_FLAGS = (
    "needs_conversion",
    "needs_gvcf_genotyping",
    "needs_liftover",
    "needs_gatk",
    "needs_alignment",
    "needs_hla",
    "needs_pypgx",
    "needs_pypgx_bam2vcf",
    "needs_mtdna",
)

VCF_BYTES = (
    b"##fileformat=VCFv4.2\n"
    b"##contig=<ID=chr10,length=133797422>\n"
    b'##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
    b"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
    b"chr10\t94761900\trs12248560\tC\tT\t100\tPASS\t.\tGT\t0/1\n"
)

BAM_BYTES = b"BAM\x01" + b"\x00" * 64


def _preview(client, name, payload):
    resp = client.post(
        "/upload/inspect-header",
        files={"file": (name, payload, "application/octet-stream")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["compat"]["workflow"]


def _direct_verdict(tmp_path, name, payload):
    """What determine_workflow says about the same bytes, with no HTTP in the way."""
    path = tmp_path / name
    path.write_bytes(payload)
    processor = FileProcessor(temp_dir=str(tmp_path))
    result = asyncio.run(processor.process_upload(str(path)))
    assert result["status"] == "success", result
    return result["workflow"]


@pytest.mark.parametrize(
    "name,payload",
    [("sample.vcf", VCF_BYTES), ("sample.bam", BAM_BYTES)],
    ids=["vcf", "bam"],
)
def test_the_preview_forwards_every_flag_the_panel_reads(client, name, payload):
    """A flag the panel consults but the preview omits reads as undefined: falsy."""
    workflow = _preview(client, name, payload)

    missing = [flag for flag in PANEL_FLAGS if flag not in workflow]
    assert (
        not missing
    ), "the planned-workflow panel reads flags the preview omits: {}".format(missing)


@pytest.mark.parametrize(
    "name,payload",
    [("sample.vcf", VCF_BYTES), ("sample.bam", BAM_BYTES)],
    ids=["vcf", "bam"],
)
def test_the_preview_agrees_with_determine_workflow(client, tmp_path, name, payload):
    """Same bytes, same verdict -- the preview must not be a second opinion."""
    preview = _preview(client, name, payload)
    direct = _direct_verdict(tmp_path, name, payload)

    disagreements = {
        flag: (preview.get(flag), direct.get(flag, False))
        for flag in PANEL_FLAGS
        if bool(preview.get(flag)) != bool(direct.get(flag, False))
    }
    assert (
        not disagreements
    ), "preview vs determine_workflow (preview, actual): {}".format(disagreements)


def test_the_preview_draws_the_mtdna_step_for_a_vcf(client):
    """The regression that motivated this: mtDNA has no fallback in the panel.

    Every VCF, BAM, CRAM and SAM job runs the mtDNA sidecar, and the panel draws that
    row from ``wf.needs_mtdna`` alone -- so an omitted flag did not degrade the plan, it
    deleted a step from it.
    """
    workflow = _preview(client, "sample.vcf", VCF_BYTES)

    assert workflow["needs_mtdna"] is True


def test_a_refused_file_still_carries_the_flag_shape(client):
    """The default branch and the analysed branch must return the same keys.

    A file that cannot be inspected takes the pre-seeded compat dict rather than the one
    built from the verdict. If the two carry different keys, the panel's behaviour
    depends on which path the file took, which is the shape of bug this file exists for.
    """
    workflow = _preview(client, "notes.txt", b"this is not a genomic file at all\n")

    missing = [flag for flag in PANEL_FLAGS if flag not in workflow]
    assert not missing, "the refusal/default shape omits panel flags: {}".format(
        missing
    )
