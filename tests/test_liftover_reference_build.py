"""The analysis build for a lifted VCF must be GRCh38, not the uploader's pick.

A GRCh37/hg19 VCF is lifted to GRCh38 before PyPGx/PharmCAT run. If the pipeline
then analysed it against the reference_genome the uploader chose for the ORIGINAL
file (e.g. "hg19", the file's real build and a perfectly natural selection),
PyPGx would apply its GRCh37 assembly to already-lifted GRCh38 coordinates and
emit wrong star alleles with nothing in the output saying so. `analysis_reference`
is the single choke point that decides Nextflow's params.reference; these tests
pin it to hg38 whenever a liftover is planned, and to a pass-through otherwise.
"""

from app.api.routes.upload_router import analysis_reference


def test_lifted_grch37_is_analysed_against_hg38_even_if_user_picked_hg19():
    # The dangerous case: the uploader selected their file's real build.
    workflow = {"needs_liftover": True, "source_build": "GRCh37", "reference": "hg19"}
    assert analysis_reference(workflow) == "hg38"


def test_lifted_file_is_hg38_even_if_reference_key_is_absent():
    assert analysis_reference({"needs_liftover": True}) == "hg38"


def test_lifted_file_ignores_a_grch37_spelled_reference():
    workflow = {"needs_liftover": True, "reference": "GRCh37"}
    assert analysis_reference(workflow) == "hg38"


def test_non_lifted_file_passes_its_reference_through_unchanged():
    # A native GRCh38 VCF (no lift) keeps its declared build.
    assert analysis_reference({"needs_liftover": False, "reference": "hg38"}) == "hg38"


def test_non_lifted_file_with_no_reference_defaults_to_hg38():
    assert analysis_reference({}) == "hg38"


def test_non_lifted_file_keeps_a_non_hg38_reference():
    # A BAM aligned to something else would carry that through; only a lift forces hg38.
    assert analysis_reference({"needs_liftover": False, "reference": "hg19"}) == "hg19"
