"""gatk-api /liftover-vcf: real GRCh37->GRCh38 conversion, safely and honestly.

What is pinned here, and why each pin exists:

* **Contig-prefix normalisation.** UCSC's hg19ToHg38 chain names its source
  contigs `chr1`-style; a b37/GRCh37 VCF says `1`. Picard LiftoverVcf does not
  error on the mismatch -- it just finds no chain interval for any record and
  rejects everything. This is exactly what killed the repo's first liftover
  attempt, so the detection (`vcf_contig_naming`), the rename map, and the
  "rename runs BEFORE gatk" ordering are all asserted.
* **The reject-rate guard.** A majority-rejected run means the chain never
  matched the input; silently returning the survivors would feed a clinical
  report a fraction of the patient's variants. The guard must 500, not 200.
* **Command-injection hardening.** `file.filename` and `source_build` are
  attacker-controlled multipart fields that end up near subprocess argv. Same
  rules as tests/test_command_injection_hardening.py: argv lists only, never
  shell=True, names rebuilt by safe_upload_name, form values allowlisted.
* **The workflow registry step.** main.nf's LiftoverVCF process reports
  step_name=liftover; a step the registry does not mint 404s its updates and
  hangs [pending] forever (the HLA lane hit this exact trap under another name).

The gatk_api module is imported out-of-container exactly the way
tests/test_gatk_api_no_mock_bam.py does (stubbed psutil/job_client, temp data
tree), and every external tool -- gatk, bcftools -- is a recorded fake, because
neither GATK nor htslib exists in the unit-test environment.
"""

import gzip
import importlib.util
import logging
import os
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
GATK_API_SOURCE = REPO_ROOT / "docker" / "gatk-api" / "gatk_api.py"

PLAIN_GRCH37_VCF = (
    "##fileformat=VCFv4.2\n"
    "##reference=file:///refs/human_g1k_v37.fasta\n"
    "##contig=<ID=1,length=249250621>\n"
    "##contig=<ID=10,length=135534747>\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
    "10\t96541616\trs4244285\tG\tA\t100\tPASS\t.\tGT\t0/1\n"
)

CHR_GRCH37_VCF = (
    "##fileformat=VCFv4.2\n"
    "##reference=file:///refs/hg19.fasta\n"
    "##contig=<ID=chr1,length=249250621>\n"
    "##contig=<ID=chr10,length=135534747>\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n"
    "chr10\t96541616\trs4244285\tG\tA\t100\tPASS\t.\tGT\t0/1\n"
)


def _fake_psutil():
    module = types.ModuleType("psutil")
    module.virtual_memory = lambda: types.SimpleNamespace(total=16 * 1024**3)
    module.Process = lambda *a, **k: types.SimpleNamespace()
    return module


def _fake_job_client():
    module = types.ModuleType("job_client")

    class JobClient:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("no job server in tests")

    module.JobClient = JobClient
    module.create_job_client = lambda *a, **k: JobClient()
    return module


@pytest.fixture(scope="module")
def gatk_api(tmp_path_factory):
    """Import the sidecar module out-of-container, pointed at a temp data tree."""
    root = tmp_path_factory.mktemp("gatk_api_liftover_home")
    before_handlers = list(logging.root.handlers)

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATA_DIR", str(root / "data"))
        mp.setenv("TMPDIR", str(root / "tmp"))
        mp.setenv("REFERENCE_DIR", str(root / "reference"))
        mp.setitem(sys.modules, "psutil", _fake_psutil())
        mp.setitem(sys.modules, "job_client", _fake_job_client())

        spec = importlib.util.spec_from_file_location(
            "zaropgx_gatk_api_liftover_under_test", GATK_API_SOURCE
        )
        module = importlib.util.module_from_spec(spec)
        mp.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        yield module

    for handler in list(logging.root.handlers):
        if handler not in before_handlers:
            logging.root.removeHandler(handler)
    for handler in getattr(module, "_log_handlers", []):
        handler.close()


@pytest.fixture()
def client(gatk_api):
    return TestClient(gatk_api.app)


@pytest.fixture()
def hg38_reference(gatk_api):
    path = Path(gatk_api.REFERENCE_PATHS["hg38"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(">chr1\nACGTACGTACGT\n", encoding="utf-8")
    return path


@pytest.fixture()
def chain_file(gatk_api):
    path = Path(gatk_api.LIFTOVER_CHAIN_PATHS[("GRCh37", "GRCh38")])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        gzip.compress(b"chain 1 chr1 249250621 + 0 1 chr1 248956422 + 0 1 1\n")
    )
    yield path
    path.unlink(missing_ok=True)


class FakeTools:
    """Stands in for the module's `subprocess` binding, recording every argv.

    Emulates just enough of bcftools/gatk for the pipeline to complete:
    `bcftools annotate` copies its input to the -o path (gzipped), `gatk
    LiftoverVcf` writes configurable lifted/reject VCFs at -O/--REJECT, and
    `bcftools index -t` touches a .tbi. Everything else exits 0 silently.
    """

    SubprocessError = subprocess.SubprocessError
    CalledProcessError = subprocess.CalledProcessError
    TimeoutExpired = subprocess.TimeoutExpired
    PIPE = subprocess.PIPE
    STDOUT = subprocess.STDOUT

    VCF_HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"

    def __init__(self):
        self.calls = []
        # (chrom, pos, filter) rows the fake LiftoverVcf writes
        self.lifted_rows = [("chr10", 94781859, "PASS")]
        self.reject_rows = []

    def argvs(self):
        return [list(c[0]) for c in self.calls]

    def ran(self, *prefix):
        want = list(prefix)
        return [argv for argv in self.argvs() if argv[: len(want)] == want]

    def _flag_value(self, argv, flag):
        return argv[argv.index(flag) + 1]

    def _write_vcf(self, path, rows):
        body = self.VCF_HEADER + "".join(
            f"{chrom}\t{pos}\trsX\tG\tA\t100\t{filt}\t.\n" for chrom, pos, filt in rows
        )
        with open(path, "wb") as handle:
            handle.write(gzip.compress(body.encode("utf-8")))

    def run(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        tool = argv[0] if argv else ""
        sub = argv[1] if len(argv) > 1 else ""

        if tool == "bcftools" and sub == "annotate":
            source = argv[-1]
            with open(source, "rb") as handle:
                blob = handle.read()
            text = (
                gzip.decompress(blob).decode()
                if blob[:2] == b"\x1f\x8b"
                else blob.decode()
            )
            with open(self._flag_value(argv, "-o"), "wb") as handle:
                handle.write(gzip.compress(text.encode("utf-8")))
        elif tool == "gatk" and sub == "LiftoverVcf":
            self._write_vcf(self._flag_value(argv, "-O"), self.lifted_rows)
            self._write_vcf(self._flag_value(argv, "--REJECT"), self.reject_rows)
        elif tool == "bcftools" and sub == "index":
            Path(f"{argv[-1]}.tbi").write_bytes(b"TBI\x01")

        return subprocess.CompletedProcess(argv, 0, b"", b"")


@pytest.fixture()
def fake_tools(gatk_api, monkeypatch):
    tools = FakeTools()
    monkeypatch.setattr(gatk_api, "subprocess", tools)
    return tools


def _post_liftover(
    client, vcf_text, filename="sample.vcf", source_build="GRCh37", **extra
):
    data = {"source_build": source_build, "reference_genome": "hg38", **extra}
    return client.post(
        "/liftover-vcf",
        files={"file": (filename, vcf_text.encode("utf-8"), "text/plain")},
        data=data,
    )


# ---------------------------------------------------------------------------
# Chain-path resolution and source_build allowlist
# ---------------------------------------------------------------------------


def test_chain_path_constant_resolves_under_the_reference_mount(gatk_api):
    chain = gatk_api.LIFTOVER_CHAIN_PATHS[("GRCh37", "GRCh38")]
    assert chain.startswith(gatk_api.REFERENCE_DIR)
    assert chain.replace(os.sep, "/").endswith("chain/hg19ToHg38.over.chain.gz")


def test_source_build_allowlist_accepts_both_spellings(gatk_api):
    assert gatk_api.LIFTOVER_SOURCE_BUILDS["grch37"] == "GRCh37"
    assert gatk_api.LIFTOVER_SOURCE_BUILDS["hg19"] == "GRCh37"


def test_hostile_source_build_is_refused_before_any_tool_runs(
    client, fake_tools, hg38_reference, chain_file
):
    resp = _post_liftover(client, CHR_GRCH37_VCF, source_build="GRCh37; rm -rf /")

    assert resp.status_code == 400, resp.text
    assert "source_build" in resp.json()["detail"]
    assert fake_tools.calls == [], "a refused build must never reach a subprocess"


def test_grch38_as_source_build_is_refused(
    client, fake_tools, hg38_reference, chain_file
):
    # Lifting a GRCh38 file "to GRCh38" would double-shift coordinates.
    resp = _post_liftover(client, CHR_GRCH37_VCF, source_build="GRCh38")
    assert resp.status_code == 400, resp.text


def test_target_build_must_be_grch38(client, fake_tools, hg38_reference, chain_file):
    resp = _post_liftover(client, CHR_GRCH37_VCF, reference_genome="hg19")
    assert resp.status_code == 400, resp.text
    assert "GRCh38" in resp.json()["detail"]


def test_missing_chain_file_is_a_clear_400(
    client, fake_tools, hg38_reference, gatk_api
):
    chain = Path(gatk_api.LIFTOVER_CHAIN_PATHS[("GRCh37", "GRCh38")])
    chain.unlink(missing_ok=True)

    resp = _post_liftover(client, CHR_GRCH37_VCF)

    assert resp.status_code == 400, resp.text
    assert "chain" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Contig-prefix detection and normalisation (the part that killed attempt #1)
# ---------------------------------------------------------------------------


def test_plain_contig_naming_is_detected(gatk_api, tmp_path):
    vcf = tmp_path / "plain.vcf"
    vcf.write_text(PLAIN_GRCH37_VCF, encoding="utf-8")
    assert gatk_api.vcf_contig_naming("t", str(vcf)) == "plain"


def test_chr_contig_naming_is_detected(gatk_api, tmp_path):
    vcf = tmp_path / "chr.vcf"
    vcf.write_text(CHR_GRCH37_VCF, encoding="utf-8")
    assert gatk_api.vcf_contig_naming("t", str(vcf)) == "chr"


def test_naming_detection_reads_gzipped_vcfs(gatk_api, tmp_path):
    vcf = tmp_path / "plain.vcf.gz"
    vcf.write_bytes(gzip.compress(PLAIN_GRCH37_VCF.encode("utf-8")))
    assert gatk_api.vcf_contig_naming("t", str(vcf)) == "plain"


def test_naming_detection_falls_back_to_data_lines(gatk_api, tmp_path):
    # No ##contig records at all: the CHROM column is the only evidence.
    vcf = tmp_path / "bare.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "10\t96541616\trs4244285\tG\tA\t100\tPASS\t.\n",
        encoding="utf-8",
    )
    assert gatk_api.vcf_contig_naming("t", str(vcf)) == "plain"


def test_mixed_contig_naming_is_refused(gatk_api, tmp_path):
    vcf = tmp_path / "mixed.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=chr1,length=249250621>\n"
        "##contig=<ID=10,length=135534747>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n",
        encoding="utf-8",
    )
    with pytest.raises(HTTPException) as excinfo:
        gatk_api.vcf_contig_naming("t", str(vcf))
    assert excinfo.value.status_code == 400
    assert "mixes" in excinfo.value.detail


def test_rename_map_covers_the_primary_assembly(gatk_api, tmp_path):
    map_path = gatk_api.write_chr_rename_map(str(tmp_path))
    entries = dict(
        line.split("\t")
        for line in Path(map_path).read_text(encoding="utf-8").strip().splitlines()
    )
    assert entries["1"] == "chr1"
    assert entries["22"] == "chr22"
    assert entries["X"] == "chrX"
    assert entries["Y"] == "chrY"
    # Both b37's MT and a bare M must land on UCSC's chrM: the chain has no MT.
    assert entries["MT"] == "chrM"
    assert entries["M"] == "chrM"


def test_plain_input_is_renamed_before_liftover(
    client, fake_tools, hg38_reference, chain_file
):
    resp = _post_liftover(client, PLAIN_GRCH37_VCF)

    assert resp.status_code == 200, resp.text
    annotate_calls = fake_tools.ran("bcftools", "annotate")
    assert len(annotate_calls) == 1, "unprefixed input must be renamed exactly once"
    annotate_argv = annotate_calls[0]
    assert "--rename-chrs" in annotate_argv

    gatk_calls = fake_tools.ran("gatk", "LiftoverVcf")
    assert len(gatk_calls) == 1
    gatk_input = gatk_calls[0][gatk_calls[0].index("-I") + 1]
    renamed_output = annotate_argv[annotate_argv.index("-o") + 1]
    assert gatk_input == renamed_output, "LiftoverVcf must consume the RENAMED file"

    # The ordering is the point: rename first, lift second.
    tools_in_order = [(argv[0], argv[1]) for argv in fake_tools.argvs()]
    assert tools_in_order.index(("bcftools", "annotate")) < tools_in_order.index(
        ("gatk", "LiftoverVcf")
    )
    assert resp.json()["renamed_contigs"] is True


def test_chr_input_skips_the_rename(client, fake_tools, hg38_reference, chain_file):
    resp = _post_liftover(client, CHR_GRCH37_VCF)

    assert resp.status_code == 200, resp.text
    assert (
        fake_tools.ran("bcftools", "annotate") == []
    ), "chr-named input needs no rename"
    assert len(fake_tools.ran("gatk", "LiftoverVcf")) == 1
    assert resp.json()["renamed_contigs"] is False


def test_liftover_argv_shape(gatk_api):
    """Pin the exact flag set so a GATK bump that renames one is a loud failure."""
    argv = gatk_api.build_liftover_argv(
        "in.vcf", "out.vcf.gz", "c.chain.gz", "rej.vcf.gz", "ref.fa"
    )
    assert argv[:2] == ["gatk", "LiftoverVcf"]
    for flag, value in (
        ("-I", "in.vcf"),
        ("-O", "out.vcf.gz"),
        ("-C", "c.chain.gz"),
        ("--REJECT", "rej.vcf.gz"),
        ("-R", "ref.fa"),
        ("--WARN_ON_MISSING_CONTIG", "true"),
        ("--RECOVER_SWAPPED_REF_ALT", "true"),
    ):
        assert argv[argv.index(flag) + 1] == value, flag


# ---------------------------------------------------------------------------
# Stats, reject reasons, and the reject-rate guard
# ---------------------------------------------------------------------------


def test_response_carries_counts_and_reject_reasons(
    client, fake_tools, hg38_reference, chain_file
):
    fake_tools.lifted_rows = [
        ("chr10", 94761900, "PASS"),
        ("chr10", 94781859, "PASS"),
        ("chr12", 21178615, "PASS"),
    ]
    fake_tools.reject_rows = [("chr10", 96500000, "NoTarget")]

    resp = _post_liftover(client, CHR_GRCH37_VCF)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["n_lifted"] == 3
    assert body["n_rejected"] == 1
    assert body["reject_reasons"] == {"NoTarget": 1}
    assert body["vcf_path"].endswith(".grch38.vcf.gz")
    assert body["reject_path"].endswith(".reject.vcf.gz")
    assert body["vcf_index"] == f"{body['vcf_path']}.tbi"
    assert body["source_build"] == "GRCh37"
    assert body["target_build"] == "GRCh38"
    # The output must actually exist where the response says it does.
    assert os.path.exists(body["vcf_path"])


def test_reject_reason_summary_ranks_most_common_first(gatk_api, tmp_path):
    rows = (
        [("chr1", 1, "NoTarget")] * 3
        + [("chr1", 2, "MismatchedRefAllele")] * 2
        + [("chr1", 3, "IndelStraddlesMultipleIntervals")]
    )
    reject = tmp_path / "rej.vcf.gz"
    body = FakeTools.VCF_HEADER + "".join(
        f"{c}\t{p}\t.\tG\tA\t.\t{f}\t.\n" for c, p, f in rows
    )
    reject.write_bytes(gzip.compress(body.encode("utf-8")))

    summary = gatk_api.summarise_reject_reasons(str(reject))
    assert list(summary.items())[0] == ("NoTarget", 3)
    assert summary["MismatchedRefAllele"] == 2


def test_implausible_reject_rate_fails_loudly(
    client, fake_tools, hg38_reference, chain_file
):
    # 9 of 10 rejected: the signature of a chain/prefix mismatch, not of data.
    fake_tools.lifted_rows = [("chr10", 94781859, "PASS")]
    fake_tools.reject_rows = [("chr10", 96500000 + i, "NoTarget") for i in range(9)]

    resp = _post_liftover(client, CHR_GRCH37_VCF)

    assert resp.status_code == 500, resp.text
    detail = resp.json()["detail"]
    assert "rejected 9 of 10" in detail
    assert "NoTarget" in detail


def test_zero_total_records_is_an_error_not_an_empty_success(
    client, fake_tools, hg38_reference, chain_file
):
    fake_tools.lifted_rows = []
    fake_tools.reject_rows = []

    resp = _post_liftover(client, CHR_GRCH37_VCF)

    assert resp.status_code == 500, resp.text
    assert "no records" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Command-injection hardening
# ---------------------------------------------------------------------------


def test_hostile_filename_cannot_inject_into_any_argv(
    client, fake_tools, hg38_reference, chain_file
):
    resp = _post_liftover(client, PLAIN_GRCH37_VCF, filename="x;touch pwned;.vcf")

    assert resp.status_code == 200, resp.text
    assert fake_tools.calls, "the pipeline must actually have run"
    for argv, kwargs in fake_tools.calls:
        assert isinstance(argv, list), f"shell-string command: {argv!r}"
        assert kwargs.get("shell") is not True, "a call still runs with shell=True"
        for element in argv:
            assert ";" not in str(
                element
            ), f"metacharacter survived into argv: {element!r}"
    # The stored name is rebuilt from allowlisted parts, not sanitised in place.
    gatk_argv = fake_tools.ran("gatk", "LiftoverVcf")[0]
    stored = os.path.basename(gatk_argv[gatk_argv.index("-I") + 1])
    assert re.fullmatch(r"[A-Za-z0-9_-]+\.vcf(\.gz)?", stored), stored


def test_outputs_land_on_the_shared_data_volume(
    client, fake_tools, hg38_reference, chain_file, gatk_api
):
    # The caller is a Nextflow process in another container; a path on this
    # container's private /tmp would be unreadable there (the exact defect the
    # BAM conversions were fixed for).
    resp = _post_liftover(client, CHR_GRCH37_VCF)

    assert resp.status_code == 200, resp.text
    results_root = os.path.join(gatk_api.DATA_DIR, "results")
    assert resp.json()["vcf_path"].startswith(results_root)
    assert resp.json()["reject_path"].startswith(results_root)


# ---------------------------------------------------------------------------
# Workflow registry: the step the pipeline reports must exist to be reported to
# ---------------------------------------------------------------------------


def test_registry_mints_liftover_step_when_needed():
    from app.api.models import WorkflowOptions
    from app.services.workflow_registry import resolve_steps

    steps = resolve_steps("genomic_analysis", WorkflowOptions(needs_liftover=True))
    names = [s.step_name for s in steps]
    assert "liftover" in names
    # The lift happens before analysis proper: right after header inspection.
    assert names.index("liftover") == names.index("header_analysis") + 1

    by_name = {s.step_name: s for s in steps}
    assert by_name["liftover"].container_name == "gatk-api"


def test_registry_omits_liftover_step_by_default():
    from app.api.models import WorkflowOptions
    from app.services.workflow_registry import resolve_steps

    names = [s.step_name for s in resolve_steps("genomic_analysis", WorkflowOptions())]
    assert "liftover" not in names


def test_liftover_step_maps_to_the_gatk_stage():
    # An unmapped step falls through to the ANALYSIS default; the lift runs in
    # the gatk-api container and must surface under the GATK stage in the UI.
    from app.services.workflow_stages import WorkflowStage, stage_from_step

    assert stage_from_step("liftover") is WorkflowStage.GATK


# ---------------------------------------------------------------------------
# Runner plumbing: --source_build must survive the app -> runner -> argv trip
# ---------------------------------------------------------------------------


def _load_runner():
    """Import docker/nextflow/runner.py by path - docker/ is not a package.

    Same module name as tests/test_sample_identifier_injection.py uses, so the
    two share one cached import instead of double-initialising its logging.
    """
    import importlib.util as _ilu
    import tempfile

    name = "zaropgx_nextflow_runner"
    if name in sys.modules:
        return sys.modules[name]
    os.environ.setdefault(
        "NEXTFLOW_PROGRESS_LOG",
        str(Path(tempfile.gettempdir()) / "zaropgx_nextflow_progress_test.log"),
    )
    spec = _ilu.spec_from_file_location(
        name, REPO_ROOT / "docker" / "nextflow" / "runner.py"
    )
    module = _ilu.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_runner_emits_source_build_only_when_set():
    # Declared-but-not-emitted was exactly how skip_gatk/skip_report got lost
    # (406); the liftover trigger must not repeat that failure shape.
    runner = _load_runner()
    base = dict(
        input_path="/data/x.vcf",
        input_type="vcf",
        patient_id="p",
        report_id="r",
        reference="hg38",
        outdir="/data/out",
    )
    cmd = runner.build_nextflow_command(**base, source_build="GRCh37")
    assert cmd[cmd.index("--source_build") + 1] == "GRCh37"

    for absent in ("", "   ", None):
        cmd = runner.build_nextflow_command(**base, source_build=absent or "")
        assert "--source_build" not in cmd


def test_runner_request_allowlists_source_build():
    # source_build is interpolated into LiftoverVCF's shell block, so the
    # request model must hold it to the same alphabet as reference.
    import pydantic

    runner = _load_runner()
    base = dict(input="/data/x.vcf", input_type="vcf", patient_id="p")

    assert (
        runner.NextflowRunRequest(**base, source_build="GRCh37").source_build
        == "GRCh37"
    )
    assert runner.NextflowRunRequest(**base, source_build="  ").source_build == ""
    assert runner.NextflowRunRequest(**base).source_build == ""

    with pytest.raises(pydantic.ValidationError):
        runner.NextflowRunRequest(**base, source_build='GRCh37"; touch /tmp/pwned; "')
