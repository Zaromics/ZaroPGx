"""The alignment path produces upstream's own report, or fails loudly."""

from pathlib import Path

APP_PY = Path(__file__).resolve().parent.parent / "docker/mtdna-server-2/app.py"


def _alignment_branch() -> str:
    source = APP_PY.read_text(encoding="utf-8")
    return source[source.index("async def _call_from_alignment") :]


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
    assert "MIN_MEAN_COVERAGE" in APP_PY.read_text(encoding="utf-8")
