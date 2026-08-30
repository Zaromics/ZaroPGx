"""The alignment path produces upstream's own report, or fails loudly."""

from pathlib import Path

from app.mtdna.builds import (
    MitoBuild,
    classify_build,
    classify_from_mito_contig,
    plan_for,
)

APP_PY = Path(__file__).resolve().parent.parent / "docker/mtdna-server-2/app.py"


def _alignment_branch() -> str:
    source = APP_PY.read_text(encoding="utf-8")
    return source[source.index("async def _call_from_alignment") :]


def _classify_as_call_from_alignment_would(name, length, build_label):
    """Mirrors _call_from_alignment's own classification block: header
    ground truth first, falling back to the label only when the header
    carries no usable mito @SQ line at all (build == UNSUPPORTED)."""
    build = classify_from_mito_contig(name, length, build_label)
    if build == MitoBuild.UNSUPPORTED:
        build = classify_build(build_label)
    return build


def test_it_runs_mutserve_against_the_vendored_rcrs():
    branch = _alignment_branch()
    assert "MUTSERVE_JAR" in branch
    assert "RCRS_FASTA" in branch


def test_it_runs_haplocheck_for_contamination():
    assert "HAPLOCHECK_JAR" in _alignment_branch()


def test_it_renders_upstreams_rmd():
    branch = _alignment_branch()
    assert "report.Rmd" in branch
    assert "Rscript" in branch


def test_hg19_alignment_input_is_refused():
    """No alignment-level liftover exists; a wrong haplogroup is worse than none."""
    branch = _alignment_branch()
    assert "hg19" in branch.lower()
    assert "422" in branch


def test_reference_is_coverage_gated_on_this_path():
    """Scoped to the alignment branch's actual gating expression, not a bare
    substring anywhere in the file -- MIN_MEAN_COVERAGE is also just a
    constant *definition*, which would stay green even if every use of it
    were deleted. The real behaviour (a real match always wins; without
    positive coverage evidence the call stays a no-call; an unresolved
    delins at 961 blocks the promotion even at high coverage) is exercised
    directly against real VcfRecord inputs in test_mt_rnr1_vocabulary.py's
    resolve_mt_rnr1_call tests -- see review round 1, finding 4 (2026-08-30).
    """
    branch = _alignment_branch()
    assert "coverage >= MIN_MEAN_COVERAGE" in branch
    # `records` (not just `matched`) has to reach resolve_mt_rnr1_call: it is
    # what lets that function's own has_unresolved_961_deletion check block
    # promotion for an unmatched delins overlapping 961, even at high
    # coverage -- see test_resolve_withholds_reference_for_an_unresolved_961_delins.
    assert "resolve_mt_rnr1_call(" in branch
    assert "matched, records, evidence_reason=evidence_reason" in branch


def test_it_reads_the_bam_header_for_ground_truth():
    """`reference_genome` alone cannot tell hg19 from b37/GRCh38 -- the same
    reason _call_from_vcf reads the VCF's own ##contig line instead of
    trusting the label."""
    branch = _alignment_branch()
    assert "_read_mito_sq_header" in branch
    assert "classify_from_mito_contig" in branch


def test_header_classification_runs_before_the_hg19_refusal():
    """Ordering matters: a mislabelled hg19 BAM must be caught on the
    header's own evidence before the label-driven refusal check runs."""
    branch = _alignment_branch()
    header_call = branch.index("_read_mito_sq_header(")
    hg19_check = branch.index("MitoBuild.HG19")
    assert header_call < hg19_check


def test_b37_header_classifies_as_b37_even_though_the_label_could_lie():
    build = _classify_as_call_from_alignment_would("MT", 16569, "GRCh37")
    assert build == MitoBuild.B37
    assert plan_for(build).supported


def test_mislabelled_hg19_bam_is_caught_by_the_header_not_the_label():
    """Label says GRCh37; header (LN:16571) says hg19. Ground truth wins."""
    build = _classify_as_call_from_alignment_would("chrM", 16571, "GRCh37")
    assert build == MitoBuild.HG19


def test_no_mito_sq_line_falls_back_to_the_label():
    build = _classify_as_call_from_alignment_would(None, None, "GRCh38")
    assert build == MitoBuild.GRCH38


def test_cram_without_a_staged_reference_is_refused():
    """CRAM stores no sequence of its own; decoding it needs the exact
    reference it was compressed against, which this image does not fetch on
    its own (no REF_CACHE/REF_PATH baked in). The FASTA-path constant sits
    just above the function (like MIN_MEAN_COVERAGE), so read the whole file
    rather than _alignment_branch(), which starts at the `def` line."""
    source = APP_PY.read_text(encoding="utf-8")
    branch = _alignment_branch()
    assert "cram" in branch.lower()
    assert "--reference" in branch
    assert "human_g1k_v37.fasta" in source
    assert "Homo_sapiens_assembly38.fasta" in source
    assert "422" in branch
