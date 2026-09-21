"""``PharmCATRun`` must look for PharmCAT's report where PharmCAT actually writes it.

The process ran PharmCAT successfully, then published nothing, and said ✔ while
doing it. Both halves of that are needed to make it silent:

* ``main.nf`` copied from ``/data/reports/<patient_id>/<patient_id>_pgx_pharmcat.*``
* the pharmcat sidecar writes ``/data/reports/<patient_id>/<report_id>/<report_id>_pgx_pharmcat.*``
  (``docker/pharmcat/pharmcat.py`` builds the name from ``name_base``, which is the
  report id, inside a directory that carries the report id as well)

So the copy loop was wrong twice over -- one directory level too shallow AND the
wrong filename stem -- and since the loop ends in ``|| true`` and all three outputs
are ``optional: true``, the glob simply matched nothing and the process exited 0.
Observed on a live run: BCF and gVCF lanes both completed 3/3 with a real 1.5 MB
report sitting in ``/data/reports/<pid>/<rid>/`` that never reached ``publishDir``.

The app's own flow was never affected -- ``upload_router.py`` looks for
``{job_id}_pgx_pharmcat.*``, and the job id is the report id -- which is why this
survived: only a standalone ``nextflow run`` exercises the copy loop.

These are source-text assertions because the alternative is a live Nextflow run
against the whole stack, which ``tests/e2e`` owns.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN_NF = REPO_ROOT / "pipelines" / "pgx" / "main.nf"
PHARMCAT_SIDECAR = REPO_ROOT / "docker" / "pharmcat" / "pharmcat.py"


def _pharmcat_run_block() -> str:
    src = MAIN_NF.read_text(encoding="utf-8")
    start = src.index("process PharmCATRun")
    # up to the next top-level `process ` or the workflow block
    rest = src[start + 1 :]
    end = min(
        (i for i in (rest.find("\nprocess "), rest.find("\nworkflow ")) if i != -1),
        default=len(rest),
    )
    return rest[:end]


def test_copy_loop_includes_the_report_id_directory():
    """One directory level too shallow is the first half of the bug."""
    block = _pharmcat_run_block()
    assert "/data/reports/!{patient_id}/!{report_id}/" in block, (
        "PharmCATRun must descend into the report_id directory; the sidecar writes "
        "/data/reports/<patient_id>/<report_id>/"
    )


def test_copy_loop_uses_the_report_id_filename_stem():
    """The wrong stem is the second half, and it hides independently."""
    block = _pharmcat_run_block()
    assert "!{report_id}_pgx_pharmcat." in block
    assert (
        "!{patient_id}_pgx_pharmcat." not in block
    ), "the filename stem is the report id, not the patient id"


def test_declared_outputs_match_the_copied_names():
    """A right copy with wrong output globs publishes nothing just the same."""
    block = _pharmcat_run_block()
    for suffix in ("html", "json", "tsv"):
        assert f'path "${{report_id}}_pgx_pharmcat.{suffix}"' in block
        assert f'path "${{patient_id}}_pgx_pharmcat.{suffix}"' not in block


def test_the_sidecar_still_names_reports_after_the_report_id():
    """Pins the premise. If the sidecar's layout moves, this test says so."""
    src = PHARMCAT_SIDECAR.read_text(encoding="utf-8")
    assert 'f"{name_base}_pgx_pharmcat.html"' in src
    assert 'f"{name_base}_pgx_pharmcat.json"' in src
    assert 'f"{name_base}_pgx_pharmcat.tsv"' in src


def test_a_missing_report_is_reported_rather_than_passed_over():
    """Silence was the reason this went a year unnoticed.

    The copy loop stays tolerant -- PharmCAT genuinely may produce nothing -- but
    the process must say so on stderr instead of exiting 0 with no trace.
    """
    block = _pharmcat_run_block()
    assert re.search(
        r"published no report|no PharmCAT report", block
    ), "PharmCATRun should announce an empty publish rather than exit silently"
