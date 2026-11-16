"""OptiType's result table carries the calls and what they rest on; keep both.

Each row is one solution -- the six alleles, `Reads` (how many reads it explains) and
`Objective` (the value the ILP maximised). The wrapper read the first row's alleles and
dropped the two numbers, so a report could say a gene was typed and never how well. On a
whole genome that is a detail; on a sparse input it is the difference between a call and
a guess, and it is the only evidence OptiType offers.

The alleles themselves are unchanged: still the best solution, still the first row.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ZAROHLA = REPO / "docker" / "zarohla" / "app.py"

HEADER = "\tA1\tA2\tB1\tB2\tC1\tC2\tReads\tObjective\n"
ROW = "0\tA*23:01\tA*23:01\tB*18:01\tB*35:28\tC*01:02\tC*07:01\t95.0\t93.28\n"
SECOND = "1\tA*23:01\tA*23:01\tB*35:28\tB*35:28\tC*01:02\tC*07:01\t91.0\t90.171\n"


@pytest.fixture(scope="module")
def zarohla(tmp_path_factory):
    """Imported by path, the way tests/test_zarohla_no_hla_reads.py does it: the module
    lives in an image, with psutil and job_client beside it there and not here."""
    root = tmp_path_factory.mktemp("zarohla_home")
    psutil = types.ModuleType("psutil")
    psutil.virtual_memory = lambda: types.SimpleNamespace(total=16 * 1024**3)
    psutil.Process = lambda *a, **k: types.SimpleNamespace()
    job_client = types.ModuleType("job_client")
    job_client.JobClient = object
    job_client.create_job_client = lambda *a, **k: None

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATA_DIR", str(root / "data"))
        mp.setitem(sys.modules, "psutil", psutil)
        mp.setitem(sys.modules, "job_client", job_client)
        spec = importlib.util.spec_from_file_location(
            "zarohla_optitype_under_test", ZAROHLA
        )
        module = importlib.util.module_from_spec(spec)
        mp.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        yield module


def _write(tmp_path, text):
    path = tmp_path / "2026_result.tsv"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_calls_are_the_best_solution(zarohla, tmp_path):
    results, _ = zarohla.read_optitype_result(_write(tmp_path, HEADER + ROW))

    assert results == {
        "HLA-A": "A*23:01,A*23:01",
        "HLA-B": "B*18:01,B*35:28",
        "HLA-C": "C*01:02,C*07:01",
    }


def test_the_numbers_behind_the_call_are_kept(zarohla, tmp_path):
    """95 reads and an objective of 93.28 is the whole of OptiType's evidence."""
    _, evidence = zarohla.read_optitype_result(_write(tmp_path, HEADER + ROW))

    assert evidence["reads"] == 95
    assert evidence["objective"] == pytest.approx(93.28)


def test_more_solutions_do_not_change_the_call(zarohla, tmp_path):
    """`--enumerate N` writes N rows, best first. Nothing here asks for them, and if
    something does one day, the call stays the first row."""
    results, evidence = zarohla.read_optitype_result(
        _write(tmp_path, HEADER + ROW + SECOND)
    )

    assert results["HLA-B"] == "B*18:01,B*35:28"
    assert evidence["solutions"] == 2


def test_a_single_solution_is_not_called_a_choice(zarohla, tmp_path):
    """One row is the normal case; reporting "1 solution" would imply alternatives
    were weighed when none were asked for."""
    _, evidence = zarohla.read_optitype_result(_write(tmp_path, HEADER + ROW))

    assert "solutions" not in evidence


def test_an_empty_table_types_nothing(zarohla, tmp_path):
    assert zarohla.read_optitype_result(_write(tmp_path, HEADER)) == ({}, {})


def test_a_table_without_the_numbers_still_types(zarohla, tmp_path):
    """An OptiType that writes different columns must cost the evidence, not the call."""
    header = "\tA1\tA2\tB1\tB2\tC1\tC2\n"
    row = "0\tA*23:01\tA*23:01\tB*18:01\tB*35:28\tC*01:02\tC*07:01\n"

    results, evidence = zarohla.read_optitype_result(_write(tmp_path, header + row))

    assert results["HLA-A"] == "A*23:01,A*23:01"
    assert evidence == {}


def test_an_unreadable_number_is_left_out_rather_than_guessed(zarohla, tmp_path):
    row = "0\tA*23:01\tA*23:01\tB*18:01\tB*35:28\tC*01:02\tC*07:01\tn/a\t93.28\n"

    _, evidence = zarohla.read_optitype_result(_write(tmp_path, HEADER + row))

    assert "reads" not in evidence and evidence["objective"] == pytest.approx(93.28)


def test_a_gene_with_no_call_is_left_out(zarohla, tmp_path):
    """PharmCAT has no HLA-C and OptiType sometimes types none of a gene; an empty
    pair must not reach the report as an allele named ","."""
    row = "0\tA*23:01\tA*23:01\t\t\tC*01:02\tC*07:01\t95.0\t93.28\n"

    results, _ = zarohla.read_optitype_result(_write(tmp_path, HEADER + row))

    assert "HLA-B" not in results
    assert set(results) == {"HLA-A", "HLA-C"}
