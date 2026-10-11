"""/cram-to-bam must refuse, up front, a CRAM this install's FASTA cannot decode.

A 1000 Genomes 30x CRAM (GRCh38_full_analysis_set_plus_decoy_hla) carries M5s that
differ from the UCSC hg38 the genome-downloader stages on 18 primary chromosomes,
plus 2,911 contigs that FASTA lacks. htslib only noticed at chr1:248,747,869, five
minutes into the decode, and Nextflow's retry spent five more. The check compares
the M5 of every contig that holds reads before decoding anything.

Like tests/test_gatk_api_no_mock_bam.py, the sidecar module is imported with psutil
and the job client stubbed; samtools is stubbed here too, since CI has none.
"""

import importlib.util
import logging
import subprocess
import sys
import types
from pathlib import Path

import pytest
from fastapi import HTTPException

SOURCE = Path(__file__).resolve().parent.parent / "docker" / "gatk-api" / "gatk_api.py"

DICT = (
    "@HD\tVN:1.0\tSO:unsorted\n"
    "@SQ\tSN:chr1\tLN:248956422\tM5:2648ae1bacce4ec4b6cf337dcae37816\n"
    "@SQ\tSN:chr4\tLN:190214555\tM5:23dccd106897542ad87d2765d28a19a1\n"
)
UCSC_HEADER = DICT  # written against this install's own FASTA
GRCH38_HEADER = (
    "@SQ\tSN:chr1\tLN:248956422\tM5:6aef897c3d6ff0c78aff06ac189178dd\n"
    "@SQ\tSN:chr4\tLN:190214555\tM5:23dccd106897542ad87d2765d28a19a1\n"
    "@SQ\tSN:HLA-A*01:01:01:01\tLN:3503\tM5:7e2bfcdfb4b5c8dcbe8ff8b0e9e8a75e\n"
)


@pytest.fixture(scope="module")
def gatk_api(tmp_path_factory):
    root = tmp_path_factory.mktemp("gatk_api_home")
    before = list(logging.root.handlers)
    psutil = types.ModuleType("psutil")
    psutil.virtual_memory = lambda: types.SimpleNamespace(total=16 * 1024**3)
    psutil.Process = lambda *a, **k: types.SimpleNamespace()
    job_client = types.ModuleType("job_client")
    job_client.JobClient = object
    job_client.create_job_client = lambda *a, **k: None
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATA_DIR", str(root / "data"))
        mp.setenv("TMPDIR", str(root / "tmp"))
        mp.setenv("REFERENCE_DIR", str(root / "reference"))
        mp.setitem(sys.modules, "psutil", psutil)
        mp.setitem(sys.modules, "job_client", job_client)
        spec = importlib.util.spec_from_file_location("zaropgx_gatk_api_md5", SOURCE)
        module = importlib.util.module_from_spec(spec)
        mp.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        yield module
    for handler in list(logging.root.handlers):
        if handler not in before:
            logging.root.removeHandler(handler)
    for handler in getattr(module, "_log_handlers", []):
        handler.close()


def test_a_differing_m5_on_a_contig_with_reads_conflicts(gatk_api):
    cram = gatk_api._sq_m5s(GRCH38_HEADER)
    ref = gatk_api._sq_m5s(DICT)
    conflicts = gatk_api.cram_reference_conflicts(cram, ["chr1", "chr4"], ref)
    assert conflicts == ["chr1 (M5 6aef897c, reference 2648ae1b)"]


def test_a_contig_without_reads_is_never_compared(gatk_api):
    cram = gatk_api._sq_m5s(GRCH38_HEADER)
    ref = gatk_api._sq_m5s(DICT)
    # A targeted CRAM with reads only on chr4 decodes fine today; keep it working.
    assert gatk_api.cram_reference_conflicts(cram, ["chr4"], ref) == []


def test_reads_on_a_contig_the_reference_lacks_conflict(gatk_api):
    cram = gatk_api._sq_m5s(GRCH38_HEADER)
    ref = gatk_api._sq_m5s(DICT)
    conflicts = gatk_api.cram_reference_conflicts(cram, ["HLA-A*01:01:01:01"], ref)
    assert conflicts == ["HLA-A*01:01:01:01 (not in the reference)"]


def test_a_missing_m5_is_not_a_conflict(gatk_api):
    cram = {"chr1": None}
    ref = gatk_api._sq_m5s(DICT)
    assert gatk_api.cram_reference_conflicts(cram, ["chr1"], ref) == []


def _reference(tmp_path, with_dict=True):
    fasta = tmp_path / "ref.fasta"
    fasta.write_text(">chr1\nN\n")
    if with_dict:
        (tmp_path / "ref.dict").write_text(DICT)
    return str(fasta)


def _stub_samtools(monkeypatch, gatk_api, header, idxstats, fail=()):
    calls = []

    def run(argv, capture_output, check):
        calls.append(argv[1])
        if argv[1] in fail:
            return subprocess.CompletedProcess(argv, 1, b"", b"boom")
        out = {"view": header, "index": "", "idxstats": idxstats}[argv[1]]
        return subprocess.CompletedProcess(argv, 0, out.encode(), b"")

    monkeypatch.setattr(gatk_api.subprocess, "run", run)
    return calls


IDXSTATS = "chr1\t248956422\t63199231\t75828\nchr4\t190214555\t0\t0\n*\t0\t0\t1017844\n"


def test_a_cram_from_another_grch38_fasta_is_refused_before_decoding(
    gatk_api, tmp_path, monkeypatch
):
    _stub_samtools(monkeypatch, gatk_api, GRCH38_HEADER, IDXSTATS)
    with pytest.raises(HTTPException) as err:
        gatk_api.verify_cram_reference_md5("t", "in.cram", _reference(tmp_path))
    assert err.value.status_code == 400
    assert "chr1 (M5 6aef897c, reference 2648ae1b)" in err.value.detail
    assert "upload the BAM" in err.value.detail


def test_a_cram_from_this_installs_fasta_passes(gatk_api, tmp_path, monkeypatch):
    _stub_samtools(monkeypatch, gatk_api, UCSC_HEADER, IDXSTATS)
    gatk_api.verify_cram_reference_md5("t", "in.cram", _reference(tmp_path))


@pytest.mark.parametrize("fail", ["view", "index", "idxstats"])
def test_an_inconclusive_check_lets_htslib_decide(
    gatk_api, tmp_path, monkeypatch, fail
):
    _stub_samtools(monkeypatch, gatk_api, GRCH38_HEADER, IDXSTATS, fail=(fail,))
    gatk_api.verify_cram_reference_md5("t", "in.cram", _reference(tmp_path))


def test_no_dict_skips_the_check(gatk_api, tmp_path, monkeypatch):
    calls = _stub_samtools(monkeypatch, gatk_api, GRCH38_HEADER, IDXSTATS)
    gatk_api.verify_cram_reference_md5(
        "t", "in.cram", _reference(tmp_path, with_dict=False)
    )
    assert calls == []


def test_cram_to_bam_runs_the_check_before_converting():
    source = SOURCE.read_text(encoding="utf-8")
    endpoint = source[source.index("async def cram_to_bam(") :]
    endpoint = endpoint[: endpoint.index("\n@app.")]
    assert endpoint.index("verify_cram_reference_md5") < endpoint.index(
        "convert_to_indexed_bam"
    )
