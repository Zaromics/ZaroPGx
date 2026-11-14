"""A reader that stops finding anything must say so, not return an empty section.

The failure this sweep exists for is silent by nature: PharmCAT 2.x nested gene symbols
differently, the variants traversal found none, and every report shipped without its
evidence table until a person read one. Nothing raised, nothing failed, and the fixtures
the tests ran against still had the old shape.

So the sweep's only hard assertion is about silence: a run where a reader found nothing
is reported and exits non-zero. The counts beside it are for reading, not for passing --
"4 genes" means nothing without the 9 the rest of the corpus shows.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "getrm" / "report_sanity.py"


@pytest.fixture(scope="module")
def sweep():
    spec = importlib.util.spec_from_file_location("getrm_report_sanity", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["getrm_report_sanity"] = module
    spec.loader.exec_module(module)
    return module


def _run(sweep, tmp_path, report, name="HG00000"):
    sample = tmp_path / name
    sample.mkdir(parents=True, exist_ok=True)
    (sample / "pharmcat.json").write_text(json.dumps(report), encoding="utf-8")
    return sample


def _gene_block(gene="CYP2C19", call="C/T"):
    return {
        "geneSymbol": gene,
        "chr": "chr10",
        "callSource": "MATCHER",
        "sourceDiplotypes": [
            {
                "allele1": {"gene": gene, "name": "*1"},
                "allele2": {"gene": gene, "name": "*2"},
                "phenotypes": ["Intermediate Metabolizer"],
            }
        ],
        "variants": [
            {
                "gene": gene,
                "chromosome": "chr10",
                "position": 94781859,
                "dbSnpId": "rs4244285",
                "call": call,
                "alleles": ["*2"],
                "referenceAllele": "C",
            }
        ],
    }


def test_a_run_with_evidence_passes_quietly(sweep, tmp_path, capsys, monkeypatch):
    _run(sweep, tmp_path, {"genes": {"CYP2C19": _gene_block()}})
    monkeypatch.setattr(sys, "argv", ["report_sanity.py", str(tmp_path)])

    assert sweep.main() == 0
    assert "Every reader found something" in capsys.readouterr().out


def test_a_reader_that_finds_nothing_is_named(sweep, tmp_path, capsys, monkeypatch):
    """The #232 shape: the report is there, the traversal comes back empty, and
    nothing anywhere raises."""
    _run(sweep, tmp_path, {"genes": {}})
    monkeypatch.setattr(sys, "argv", ["report_sanity.py", str(tmp_path)])

    assert sweep.main() == 1
    out = capsys.readouterr().out
    assert "nothing found in 1 run" in out
    assert "HG00000" in out


def test_a_homozygous_reference_run_is_not_silence(
    sweep, tmp_path, capsys, monkeypatch
):
    """A gene read at every position and differing nowhere has no variants to list,
    which is a result, not a broken reader -- the call is still there."""
    block = _gene_block(call="C/C")
    _run(sweep, tmp_path, {"genes": {"CYP2C19": block}})
    monkeypatch.setattr(sys, "argv", ["report_sanity.py", str(tmp_path)])

    code = sweep.main()
    out = capsys.readouterr().out

    assert "calls: nothing found" not in out
    assert code in (0, 1)  # the variants reader may be empty; the calls reader is not


def test_the_spread_reads_as_a_range(sweep):
    assert "min      1" in sweep._spread("x", [1, 5, 9])
    assert "median" in sweep._spread("x", [1, 5, 9])
    assert "nothing to measure" in sweep._spread("x", [])
