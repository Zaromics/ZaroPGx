"""HLA outside calls must reach PharmCAT as "*08:01/*56:01".

zarohla reports OptiType's pair as "B*08:01,B*56:01", and main.nf used to copy it
into the outside-call file as is. PharmCAT strips the first gene letter only, so the
NA12878 whole-genome run's report carried HLA-B "*08:01,B*56:01". The phenotypes were
right ("*57:01 negative"; tried with B*57:01 in either position, PharmCAT still found
it), but the call PharmCAT shows was not a diplotype.

These run the OptiTypeHLAFromBAM process's own Python, as Nextflow would hand it to
python3, on zarohla's result shapes.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

MAIN_NF = Path(__file__).resolve().parents[1] / "pipelines" / "pgx" / "main.nf"


def _writer_script() -> str:
    src = MAIN_NF.read_text(encoding="utf-8")
    start = src.index("process OptiTypeHLAFromBAM {")
    body = src[start : src.index("\n}\n", start)]
    script = body[body.index("python3 - <<'PY'\n") + len("python3 - <<'PY'\n") :]
    script = script[: script.index("\nPY\n")]
    # The shell block is a Groovy ''' string, so its backslash escapes are processed
    # before the script runs.
    return (
        script.replace("\\\\", "\0")
        .replace("\\t", "\t")
        .replace("\\n", "\n")
        .replace("\0", "\\")
    )


def _outside_calls(tmp_path: Path, results: dict) -> list[str] | None:
    (tmp_path / "hla_result.json").write_text(json.dumps({"results": results}))
    subprocess.run([sys.executable, "-c", _writer_script()], cwd=tmp_path, check=True)
    out = tmp_path / "pharmcat.hla_calls.tsv"
    return out.read_text(encoding="utf-8").splitlines() if out.exists() else None


def test_optitype_pairs_become_pharmcat_diplotypes(tmp_path):
    # NA12878, as zarohla returned it.
    assert _outside_calls(
        tmp_path,
        {
            "HLA-A": "A*01:01,A*11:01",
            "HLA-B": "B*08:01,B*56:01",
            "HLA-C": "C*01:02,C*07:01",
        },
    ) == [
        "HLA-A\t*01:01/*11:01",
        "HLA-B\t*08:01/*56:01",
        "HLA-C\t*01:02/*07:01",
    ]


@pytest.mark.parametrize(
    "call, written",
    [
        ("B*57:01,B*57:01", "*57:01/*57:01"),
        # zarohla drops an empty OptiType column; one allele is not made into two.
        ("B*57:01", "*57:01"),
    ],
)
def test_other_zarohla_shapes(tmp_path, call, written):
    assert _outside_calls(tmp_path, {"HLA-B": call}) == [f"HLA-B\t{written}"]


def test_no_calls_still_writes_no_file(tmp_path):
    assert _outside_calls(tmp_path, {"HLA-A": "", "HLA-B": ""}) is None
