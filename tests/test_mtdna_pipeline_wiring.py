"""The pipeline calls the service, and an HTTP error fails the run."""

from pathlib import Path

MAIN_NF = Path(__file__).resolve().parent.parent / "pipelines/pgx/main.nf"


def _process() -> str:
    source = MAIN_NF.read_text(encoding="utf-8")
    start = source.index("process MtdnaCall")
    return source[start : source.index("process ", start + 10)]


def test_the_process_exists():
    assert "process MtdnaCall" in MAIN_NF.read_text(encoding="utf-8")


def test_an_http_error_fails_the_run():
    """Same rule as OptiType: a failing service must not read as 'no variants'."""
    assert "--fail-with-body" in _process()


def test_it_posts_a_step_name_the_registry_knows():
    """An unregistered step name 404s and hangs the UI at [pending]."""
    assert "step_name=mtdna_analysis" in _process()


def test_it_receives_the_detected_source_build():
    """Never the reference_genome form field, which defaults to hg38."""
    assert "source_build" in _process()


def test_the_outside_call_reaches_pharmcat():
    source = MAIN_NF.read_text(encoding="utf-8")
    pharmcat = source[source.index("process PharmCATRun") :]
    assert "mtdna_outside.tsv" in pharmcat
