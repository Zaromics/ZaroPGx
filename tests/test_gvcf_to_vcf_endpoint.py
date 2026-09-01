"""gatk-api /gvcf-to-vcf: a real genotyping, or a loud failure. Never a quiet success.

Why this endpoint exists is pinned in tests/test_input_type_honesty.py: PharmCAT 3.4.0
DETECTS a gVCF and refuses it, and main.nf's PharmCAT curl ends in ``|| true``, so a
gVCF carried downstream produces no PharmCAT output and no error. This module pins the
three things that make the conversion trustworthy:

* **The command is the conversion.** Two ``GenotypeGVCFs`` passes as argv lists -- the
  input path derives from an uploaded filename -- one over PharmCAT's own position list
  with ``--include-non-variant-sites`` and one over its complement, joined with
  ``bcftools concat -a``. The first pass is the whole point of the lane: its rows are
  called reference data, not the ``--absent-to-ref`` fabrication a plain VCF needs.
  ``-L pharmcat_regions.bed`` is measurably the wrong interval list (350x the file for
  identical PharmCAT results, and ~30x the PyPGx runtime per gene) and must not appear.

* **The prerequisite is checked, loudly.** ``pharmcat_positions.vcf`` is staged under
  the ``/reference`` bind mount, not shipped in the image, and a long-lived deployment
  will not have it -- genome-downloader short-circuits on
  ``/reference/.download_complete``. Absent, the endpoint answers 400 NAMING THE PATH,
  rather than genotyping against nothing.

* **The result is vouched for**, including its NAME. PharmCAT condemns a
  ``*.g.vcf[.gz]`` filename before reading a byte, so a correct conversion published
  under the wrong name is still refused at the far end of the run.

The module is imported out-of-container the way tests/test_bcf_to_vcf_endpoint.py does
(stubbed psutil/job_client, temp data and reference trees), and gatk/bcftools are a
recorded fake, because neither exists in the unit-test environment.
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
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
GATK_API_SOURCE = REPO_ROOT / "docker" / "gatk-api" / "gatk_api.py"

# Not a real gVCF: every tool that would read one is faked below, so the body only has
# to be bytes the endpoint streams to disk and hands to bcftools.
GVCF_BYTES = (
    b"##fileformat=VCFv4.2\n"
    b"##GVCFBlock0-1=minGQ=0(inclusive),maxGQ=1(exclusive)\n"
    b'##ALT=<ID=NON_REF,Description="Represents any possible alternative allele">\n'
    b"#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tNA12878\n"
)

VCF_HEADER = (
    "##fileformat=VCFv4.2\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tNA12878\n"
)

# The staged position list. Four distinct positions, so "covered N of 4" is a number
# this module controls rather than one it has to know PharmCAT's release by heart for.
PHARMCAT_POSITION_ROWS = [
    ("chr10", 94761900),
    ("chr10", 94781859),
    ("chr22", 42126611),
    ("chr22", 42127941),
]


def _vcf_text(rows):
    """Rows are (chrom, pos) or (chrom, pos, genotype); the genotype defaults to 0/0.

    A sample column is written because the endpoint's coverage count reads the genotype
    -- ``--include-non-variant-sites`` emits a row at EVERY interval position, including
    ones the gVCF had no block for, and those come back ``./.``. Counting them would
    report full coverage for a file that covered nothing.
    """
    lines = []
    for row in rows:
        chrom, pos = row[0], row[1]
        genotype = row[2] if len(row) > 2 else "0/0"
        lines.append(f"{chrom}\t{pos}\trsX\tG\tA\t100\tPASS\t.\tGT:DP\t{genotype}:30\n")
    return VCF_HEADER + "".join(lines)


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
    """Import the sidecar module out-of-container, pointed at temp data/reference trees.

    The GRCh38 FASTA and PharmCAT's position list are written for real (as ordinary
    files -- nothing reads their contents except count_vcf_positions, which is
    production code and is exercised here), because both are existence-checked before
    any subprocess runs and their absence is its own tested 400.
    """
    root = tmp_path_factory.mktemp("gatk_api_gvcf_home")
    reference = root / "reference"
    (reference / "hg38").mkdir(parents=True)
    (reference / "hg38" / "Homo_sapiens_assembly38.fasta").write_text(">chr10\nACGT\n")
    (reference / "pharmcat").mkdir(parents=True)
    (reference / "pharmcat" / "pharmcat_positions.vcf").write_text(
        _vcf_text(PHARMCAT_POSITION_ROWS)
    )

    before_handlers = list(logging.root.handlers)

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATA_DIR", str(root / "data"))
        mp.setenv("TMPDIR", str(root / "tmp"))
        mp.setenv("REFERENCE_DIR", str(reference))
        mp.setitem(sys.modules, "psutil", _fake_psutil())
        mp.setitem(sys.modules, "job_client", _fake_job_client())

        spec = importlib.util.spec_from_file_location(
            "zaropgx_gatk_api_gvcf_under_test", GATK_API_SOURCE
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


class FakeTools:
    """Stands in for the module's `subprocess` binding, recording every argv.

    Models just enough of gatk and bcftools for the four-step conversion:

    * `bcftools view -O z -o OUT IN` writes a gzipped VCF (the staging re-encode).
    * `gatk GenotypeGVCFs -V IN ... -O OUT` writes `pgx_rows` when the argv carries
      `--include-non-variant-sites`, and `variant_rows` otherwise.
    * `bcftools concat -a ... -o OUT A B` writes the union of what A and B hold.
    * `bcftools index -t X` touches `X.tbi`.

    EVERY INPUT PATH IS OPENED, and a missing one fails the call the way the real tool
    would (exit 2, stderr naming the file). Without that the fake decided its output
    purely from the subcommand and never looked at what it was pointed at: rewriting
    every `-V` and every positional input to `/nonexistent` left 30 of this module's 31
    tests green, so nothing here pinned that the four steps are actually chained -- that
    GenotypeGVCFs reads the STAGED, indexed copy rather than the raw upload, or that
    concat reads the two files the passes just wrote.

    `failing`, `payload` and the row lists exist so a non-zero exit, a
    not-actually-BGZF output and an empty result can each be provoked.
    """

    SubprocessError = subprocess.SubprocessError
    CalledProcessError = subprocess.CalledProcessError
    TimeoutExpired = subprocess.TimeoutExpired
    PIPE = subprocess.PIPE
    STDOUT = subprocess.STDOUT

    def __init__(self):
        self.calls = []
        self.pgx_rows = list(PHARMCAT_POSITION_ROWS)
        self.variant_rows = [("chr1", 1000), ("chrM", 1555)]
        self.rows_by_path = {}
        # (tool, subcommand-or-None) -> exit code, for provoking one failure at a time.
        self.failing = {}
        self.stderr = b""
        # None means "write real gzip"; bytes are written verbatim by concat instead.
        self.payload = None
        self.index_writes_tbi = True

    def argvs(self):
        return [list(call) for call in self.calls]

    def ran(self, *prefix):
        want = list(prefix)
        return [argv for argv in self.argvs() if argv[: len(want)] == want]

    def genotype_calls(self):
        return [argv for argv in self.argvs() if argv[:2] == ["gatk", "GenotypeGVCFs"]]

    @staticmethod
    def _flag_value(argv, flag):
        return argv[argv.index(flag) + 1]

    def _write(self, path, rows):
        self.rows_by_path[path] = list(rows)
        Path(path).write_bytes(gzip.compress(_vcf_text(rows).encode("utf-8")))

    @staticmethod
    def _inputs(argv):
        """The paths this command READS, so a broken chain cannot go unnoticed."""
        tool, sub = argv[0], (argv[1] if len(argv) > 1 else "")
        if tool == "gatk" and sub == "GenotypeGVCFs":
            return [FakeTools._flag_value(argv, flag) for flag in ("-R", "-V")]
        if tool == "bcftools" and sub == "view":
            return [argv[-1]]
        if tool == "bcftools" and sub == "concat":
            return argv[argv.index("-o") + 2 :]
        if tool == "bcftools" and sub == "index":
            return [argv[-1]]
        return []

    def run(self, argv, **kwargs):
        self.calls.append(argv)
        tool = argv[0]
        sub = argv[1] if len(argv) > 1 else ""

        for path in self._inputs(argv):
            if not os.path.exists(path):
                return subprocess.CompletedProcess(
                    argv,
                    2,
                    b"",
                    f"{tool}: {path}: No such file or directory".encode("utf-8"),
                )

        code = self.failing.get((tool, sub), self.failing.get((tool, None), 0))
        if code:
            return subprocess.CompletedProcess(argv, code, b"", self.stderr)

        if tool == "gatk" and sub == "GenotypeGVCFs":
            out = self._flag_value(argv, "-O")
            rows = (
                self.pgx_rows
                if "--include-non-variant-sites" in argv
                else self.variant_rows
            )
            self._write(out, rows)
        elif tool == "bcftools" and sub == "view":
            self._write(self._flag_value(argv, "-o"), [("chr1", 1)])
        elif tool == "bcftools" and sub == "concat":
            out = self._flag_value(argv, "-o")
            if self.payload is not None:
                Path(out).write_bytes(self.payload)
                self.rows_by_path[out] = []
            else:
                merged = []
                for source in argv[argv.index("-o") + 2 :]:
                    merged.extend(self.rows_by_path.get(source, []))
                self._write(out, merged)
        elif tool == "bcftools" and sub == "index" and self.index_writes_tbi:
            Path(f"{argv[-1]}.tbi").write_bytes(b"TBI\x01")

        return subprocess.CompletedProcess(argv, 0, b"", self.stderr)


@pytest.fixture()
def tools(gatk_api, monkeypatch):
    fake = FakeTools()
    monkeypatch.setattr(gatk_api, "subprocess", fake)
    return fake


def _post(client, filename="sample.g.vcf.gz", body=GVCF_BYTES, **extra):
    return client.post(
        "/gvcf-to-vcf",
        files={"file": (filename, body, "application/octet-stream")},
        data={"reference_genome": "hg38", **extra},
    )


# ---------------------------------------------------------------------------
# The conversion itself: two passes, exact complements, joined
# ---------------------------------------------------------------------------
def test_the_pgx_pass_emits_reference_calls_over_pharmcats_positions(
    client, tools, gatk_api
):
    """The reason the lane exists at all.

    Without --include-non-variant-sites the output is variant rows only and every PGx
    reference position is a no-call -- measured at ONE record on the probe gVCF, versus
    1,362 with it. That is the difference between called reference data and PharmCAT's
    --absent-to-ref fabrication.
    """
    resp = _post(client)

    assert resp.status_code == 200, resp.text
    passes = tools.genotype_calls()
    assert len(passes) == 2, tools.argvs()

    pgx = [argv for argv in passes if "--include-non-variant-sites" in argv]
    assert len(pgx) == 1, "exactly one pass may emit non-variant sites"
    assert pgx[0][pgx[0].index("-L") + 1] == gatk_api.PHARMCAT_POSITIONS_PATH
    assert "-XL" not in pgx[0], "the reference pass is bounded by -L, never by -XL"


def test_the_variant_pass_covers_everything_the_pgx_pass_excluded(
    client, tools, gatk_api
):
    """-XL of the same file, so the two passes are exact complements.

    That is what makes `bcftools concat -a` of the pair duplicate-free by construction,
    and it is what keeps PyPGx and the mtDNA sidecar supplied: PharmCAT's position list
    carries no chrM at all, so every chrM variant lands in this pass.
    """
    _post(client)

    variant = [
        argv
        for argv in tools.genotype_calls()
        if "--include-non-variant-sites" not in argv
    ]
    assert len(variant) == 1, tools.argvs()
    assert variant[0][variant[0].index("-XL") + 1] == gatk_api.PHARMCAT_POSITIONS_PATH
    assert "-L" not in variant[0]


def test_both_passes_disable_the_calling_confidence_cutoff(client, tools):
    """GenotypeGVCFs re-genotypes from the PLs; at the default -stand-call-conf of 30
    a call the original caller emitted can come back as ./. . Zero keeps the
    re-genotyping faithful rather than silently dropping sites the uploader never chose
    a threshold for. The report copy still says the re-derivation itself remains."""
    resp = _post(client)

    # The status assertion is not decoration: a bare `for argv in ...: assert` over an
    # EMPTY list passes. Pointing PHARMCAT_POSITIONS_PATH at the regions BED 400s this
    # endpoint before a single tool runs, and this test and three of its neighbours
    # went on passing over nothing.
    assert resp.status_code == 200, resp.text
    assert len(tools.genotype_calls()) == 2, tools.argvs()

    for argv in tools.genotype_calls():
        flag = "--standard-min-confidence-threshold-for-calling"
        assert flag in argv, argv
        assert argv[argv.index(flag) + 1] == "0", argv


def test_the_regions_bed_is_not_the_interval_list(client, tools):
    """Measured: identical PharmCAT results (21/21 genes both ways), 350x the file
    (4.8 MB vs 13 KB) and ~30x the PyPGx runtime per gene (2m29s vs 5.1s) -- which over
    ZaroPGx's ~20 genes is roughly 45 minutes of nothing. The BED is not a more
    thorough alternative; it is the same answer, slower."""
    resp = _post(client)

    assert resp.status_code == 200, resp.text
    assert tools.argvs(), "nothing ran, so this loop would assert over nothing"

    for argv in tools.argvs():
        assert not any("pharmcat_regions" in str(token) for token in argv), argv


def test_the_two_passes_are_merged_into_one_file(client, tools):
    concat = tools.ran("bcftools", "concat")
    assert concat == [], "nothing should have run yet"

    resp = _post(client)

    assert resp.status_code == 200, resp.text
    concat = tools.ran("bcftools", "concat")
    assert len(concat) == 1, tools.argvs()
    assert "-a" in concat[0], "-a is what allows the two passes' interleaved positions"
    assert "-D" in concat[0], (
        "-D is load-bearing, not tidiness: GATK selects records for a VariantWalker by "
        "OVERLAP, so a record starting outside a PharmCAT interval and extending into "
        "it satisfies both -L and -XL and is emitted by both passes. `concat -a` alone "
        "keeps both copies and the duplicate reaches PyPGx and PharmCAT."
    )
    # And it merges THE TWO PASSES, not two paths that happen to be named plausibly.
    merged = concat[0][concat[0].index("-o") + 2 :]
    assert sorted(merged) == sorted(
        argv[argv.index("-O") + 1] for argv in tools.genotype_calls()
    ), merged


def test_the_upload_is_staged_and_indexed_before_genotyping(client, tools):
    """GenotypeGVCFs needs an index and a filename extension it recognises, and the
    stored upload name is guaranteed to be neither: safe_upload_name() has no `.gvcf`
    in SAFE_UPLOAD_EXTENSIONS, so `sample.gvcf` reaches disk with no extension at all.
    The unconditional `bcftools view -Oz` is also what lets a gVCF written as a BCF use
    this lane rather than needing one of its own."""
    resp = _post(client, filename="sample.gvcf")

    assert resp.status_code == 200, resp.text
    staged = tools.ran("bcftools", "view")
    assert len(staged) == 1, tools.argvs()
    output = staged[0][staged[0].index("-o") + 1]
    assert output.endswith(".vcf.gz"), output

    indexed = [argv[-1] for argv in tools.ran("bcftools", "index", "-t", "-f")]
    assert output in indexed, indexed

    # And GATK is handed THAT file, not the raw upload. This was the unpinned half:
    # every assertion here was about the staging command, none about what the passes
    # were then pointed at, so handing GenotypeGVCFs the unindexed, extension-less
    # upload would have passed.
    upload = staged[0][-1]
    assert upload != output
    for argv in tools.genotype_calls():
        assert argv[argv.index("-V") + 1] == output, argv

    # Order matters: the index has to exist before GATK reads the file.
    order = tools.argvs()
    assert order.index(staged[0]) < order.index(tools.genotype_calls()[0])


def test_every_call_is_an_argv_list(client, tools):
    """The input path derives from an uploaded filename; no shell may re-parse it."""
    resp = _post(client, filename="x;touch pwned;.g.vcf.gz")

    assert resp.status_code == 200, resp.text
    assert tools.argvs(), "nothing ran, so this loop would assert over nothing"

    for argv in tools.argvs():
        assert isinstance(argv, list), argv


# ---------------------------------------------------------------------------
# The output, and what its name promises
# ---------------------------------------------------------------------------
def test_the_output_is_never_named_like_a_gvcf(client, tools):
    """PharmCAT condemns `*.g.vcf[.gz]` by filename before reading a byte
    (pcat/utilities.py:is_gvcf_file), and main.nf's PharmCAT curl swallows the refusal,
    so a correct conversion under the wrong name ends the run with no output and no
    error."""
    resp = _post(client, filename="sample.g.vcf.gz")

    assert resp.status_code == 200, resp.text
    name = Path(resp.json()["vcf_path"]).name
    assert not re.search(r"\.(g|genomic)\.vcf(\.b?gz)?$", name, re.IGNORECASE), name
    assert name.endswith(".genotyped.vcf.gz"), name


def test_the_upload_name_is_sanitised_and_the_output_stem_derived_from_it(
    client, tools
):
    resp = _post(client, filename="../../evil name;.g.vcf.gz")

    assert resp.status_code == 200, resp.text
    stored = Path(tools.ran("bcftools", "view")[0][-1]).name
    assert stored.startswith("evilnameg_"), stored
    assert Path(resp.json()["vcf_path"]).name.startswith("evilnameg_")


def test_the_output_lands_on_the_shared_volume(client, tools, gatk_api):
    """The caller is a Nextflow process in another container; /tmp is invisible to it."""
    resp = _post(client, job_id="job-1")

    vcf_path = resp.json()["vcf_path"]
    assert vcf_path.startswith(os.path.join(gatk_api.DATA_DIR, "results")), vcf_path
    assert not vcf_path.startswith(gatk_api.TEMP_DIR), vcf_path


def test_the_result_is_tabix_indexed(client, tools):
    resp = _post(client)

    assert resp.json()["vcf_index"].endswith(".genotyped.vcf.gz.tbi")


def test_a_failed_index_is_a_warning_not_a_lost_run(client, tools):
    """The pipeline re-indexes when no .tbi travels with the VCF, so this is survivable.

    Only the FINAL index is best effort. The staging index is not: GenotypeGVCFs cannot
    read an unindexed gVCF, so that failure is fatal and is tested separately.
    """
    tools.index_writes_tbi = False

    resp = _post(client)

    assert resp.status_code == 200, resp.text
    assert resp.json()["vcf_index"] is None


def test_the_input_copy_is_not_left_on_the_container(client, tools, gatk_api):
    """A whole-genome gVCF arrives here, and it is re-encoded into a second copy."""
    resp = _post(client)

    # A run that never got as far as writing anything would leave nothing behind
    # either, and this assertion would be about that instead.
    assert resp.status_code == 200, resp.text

    leftovers = list(Path(gatk_api.TEMP_DIR).glob("**/*.vcf.gz"))
    assert leftovers == [], leftovers


def test_the_response_says_how_much_of_pharmcats_list_was_covered(client, tools):
    """A gVCF that omits a region has no reference block there, so those positions are
    absent, not reference. The report reads these counts off the step row."""
    tools.pgx_rows = PHARMCAT_POSITION_ROWS[:3]

    body = _post(client).json()

    assert body["n_pharmcat_positions"] == len(PHARMCAT_POSITION_ROWS)
    assert body["n_pgx_positions_called"] == 3
    assert body["n_positions_absent"] == 1
    assert body["target_build"] == "GRCh38"


def test_the_count_is_an_intersection_not_a_row_count(client, tools):
    """ "Covered 6 of 4 positions" is not a sentence the report may ever print.

    The all-sites pass emits MORE positions than PharmCAT's list contains, and not by a
    little: GATK derives an interval from a VCF record as `start..start+len(REF)-1`, so
    each multi-base-REF record in pharmcat_positions.vcf becomes a run of per-base rows
    at positions that are not themselves in the list. That is why the measured run
    produced 1,362 rows from a 1,226-record list.

    The earlier version of this test fed DUPLICATE rows at positions already in the
    list, which is the one case GenotypeGVCFs cannot produce -- it emits one row per
    site -- so it passed against the row count it was named for.
    """
    padding = [("chr10", 94781860), ("chr10", 94781861)]
    tools.pgx_rows = PHARMCAT_POSITION_ROWS + padding

    body = _post(client).json()

    assert body["n_pharmcat_positions"] == len(PHARMCAT_POSITION_ROWS)
    assert body["n_pgx_positions_called"] == len(PHARMCAT_POSITION_ROWS)
    assert body["n_positions_absent"] == 0


def test_the_inflated_rows_cannot_hide_real_missing_coverage(client, tools):
    """The failure a clamped row count conceals, isolated.

    A file that covers only half of PharmCAT's positions can still emit more rows than
    the list has entries, so `max(0, total - rows)` reports "0 not covered" for a file
    that missed half of them. The intersection reports the two it missed.
    """
    padding = [("chr10", 94781860), ("chr10", 94781861), ("chr10", 94781862)]
    tools.pgx_rows = PHARMCAT_POSITION_ROWS[:2] + padding

    body = _post(client).json()

    assert body["n_pgx_positions_called"] == 2
    assert body["n_positions_absent"] == 2


def test_the_step_row_carries_the_same_counts_the_response_does(
    client, tools, gatk_api, monkeypatch
):
    """The JobStep's output_data is the report's ONLY source for these numbers.

    ``app/utils/gvcf_provenance.py`` reads n_pharmcat_positions / n_pgx_positions_called
    / n_positions_absent off the step row -- never off this response, which no one
    stores. The endpoint built a second literal dict for ``complete_step`` and nothing
    executed it: the module-level ``job_client`` stub raises on construction, so every
    test in this file ran with ``job_client = None`` and skipped the branch entirely.
    Renaming a key there would have deleted the coverage sentence from every gVCF report
    with the suite green. This drives the branch for real.
    """
    completed = {}

    class RecordingJobClient:
        def __init__(self, *args, **kwargs):
            pass

        async def start_step(self, *args, **kwargs):
            return None

        async def log_progress(self, *args, **kwargs):
            return None

        async def complete_step(self, message, output_data=None):
            completed["message"] = message
            completed["output_data"] = output_data

    monkeypatch.setattr(gatk_api, "JobClient", RecordingJobClient)
    tools.pgx_rows = PHARMCAT_POSITION_ROWS[:3]

    body = _post(client, job_id="job-42").json()

    assert completed, "complete_step was never called; the branch is still unexercised"
    output_data = completed["output_data"]
    for key in (
        "n_pharmcat_positions",
        "n_pgx_positions_called",
        "n_positions_absent",
        "target_build",
    ):
        assert output_data[key] == body[key], key
    assert output_data["n_pgx_positions_called"] == 3
    assert output_data["n_positions_absent"] == 1


def test_a_no_call_row_is_not_counted_as_coverage(client, tools):
    """--include-non-variant-sites emits a row at EVERY interval position, including
    ones the gVCF had no reference block for; those come back `./.`. Counting them
    would claim full coverage for a file that covered nothing -- the same fabrication
    PharmCAT's --absent-to-ref makes, arriving through the lane built to avoid it.

    A hom-ref `0/0` row IS coverage; that is the whole point of the pass.
    """
    tools.pgx_rows = [
        (chrom, pos, "0/0" if index < 2 else "./.")
        for index, (chrom, pos) in enumerate(PHARMCAT_POSITION_ROWS)
    ]

    body = _post(client).json()

    assert body["n_pgx_positions_called"] == 2
    assert body["n_positions_absent"] == 2


# ---------------------------------------------------------------------------
# The prerequisite: PharmCAT's position list
# ---------------------------------------------------------------------------
def test_a_missing_position_list_is_a_400_that_names_the_path(
    client, tools, gatk_api, monkeypatch, tmp_path
):
    """It is staged under the /reference bind mount, not shipped in the image, and a
    long-lived deployment will not have it: genome-downloader short-circuits on
    /reference/.download_complete. Verified absent on the maintainer's own machine.
    Without it there is nothing to emit reference calls over, so the run must stop here
    rather than genotype against nothing."""
    missing = str(tmp_path / "pharmcat" / "pharmcat_positions.vcf")
    monkeypatch.setattr(gatk_api, "PHARMCAT_POSITIONS_PATH", missing)

    resp = _post(client)

    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert missing in detail, "the operator must be told which path to stage"
    assert "pgx_pharmcat" in detail, "and where a correct copy already exists"
    assert "re-staged on every PharmCAT" in detail
    assert tools.argvs() == [], "nothing may run before the prerequisite is checked"


def test_a_position_list_that_is_present_but_unusable_is_a_400(
    client, tools, gatk_api, monkeypatch, tmp_path
):
    """Present is not usable, and the difference is reachable rather than theoretical.

    genome-downloader's download_file() writes whatever the server returns; its URL is
    templated on PHARMCAT_VERSION, so a bump to a release whose asset is named
    differently 404s and the error body lands at exactly this path -- and because
    gz_path == fasta_path for that entry, nothing extracts or indexes and the status is
    reported "ready". Without a content check GATK gets an unparseable interval list and
    the operator never sees the 400 that names the file and the fix.
    """
    empty = tmp_path / "pharmcat_positions.vcf"
    empty.write_text("404: Not Found\n")
    monkeypatch.setattr(gatk_api, "PHARMCAT_POSITIONS_PATH", str(empty))

    resp = _post(client)

    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "no variant records" in detail
    assert "pgx_pharmcat" in detail, "the operator needs somewhere to get a good copy"
    assert tools.argvs() == [], "nothing may run against an unusable interval list"


def test_a_missing_reference_fasta_is_a_400(client, tools, gatk_api, monkeypatch):
    monkeypatch.setitem(
        gatk_api.REFERENCE_PATHS, "hg38", "/reference/hg38/absent.fasta"
    )

    resp = _post(client)

    assert resp.status_code == 400, resp.text
    assert "reference FASTA" in resp.json()["detail"]
    assert tools.argvs() == []


@pytest.mark.parametrize("reference_genome", ["hg19", "grch37", "chm13"])
def test_only_grch38_is_accepted(client, tools, reference_genome):
    """PharmCAT's position list exists in GRCh38 coordinates only, so there is no
    interval list to run the reference pass over for anything else. A GRCh37 gVCF is
    refused at upload; this is the server-side half of that decision."""
    resp = _post(client, reference_genome=reference_genome)

    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "GRCh38" in detail
    assert "position list" in detail
    assert tools.argvs() == []


def test_grch38_is_accepted_by_either_spelling(client, tools):
    assert _post(client, reference_genome="grch38").status_code == 200
    assert _post(client, reference_genome="hg38").status_code == 200


# ---------------------------------------------------------------------------
# Vouching: never a quiet success
# ---------------------------------------------------------------------------
def test_an_empty_conversion_is_a_loud_error(client, tools):
    """A header-only VCF reads downstream as "no variants found". It is not that."""
    tools.pgx_rows = []
    tools.variant_rows = []

    resp = _post(client)

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"].lower()
    assert "empty" in detail
    assert "no variants found" in detail


def test_a_discarded_output_does_not_survive_the_refusal(client, tools):
    """A refused conversion must leave nothing a later step could pick up by path.

    Asked of the exact path the concat was told to write, not of a glob over the data
    tree: the module-scoped sidecar shares one DATA_DIR across this file's tests.
    """
    tools.pgx_rows = []
    tools.variant_rows = []

    _post(client)

    written = tools.ran("bcftools", "concat")[0]
    output = Path(written[written.index("-o") + 1])
    assert not output.exists(), output


def test_an_uncompressed_output_is_refused(client, tools):
    """Everything downstream opens this file as bgzip; plain text is not a conversion.

    Named for what it pins. As `test_a_non_bgzf_output_is_refused` it claimed more than
    the code checks: `_looks_gzipped` reads the two gzip magic bytes and nothing else,
    so a plain-gzip (non-BGZF) VCF would pass. Both callers hand it a `bcftools -O z`
    output, which is genuinely BGZF, so what is actually guarded against -- and what is
    pinned here -- is plain text or an error document where the VCF belongs.
    """
    tools.payload = b"##fileformat=VCFv4.2\nnot compressed at all\n"

    resp = _post(client)

    assert resp.status_code == 500, resp.text
    assert "not compressed at all" in resp.json()["detail"].lower()


def test_an_empty_output_file_is_refused(client, tools):
    tools.payload = b""

    resp = _post(client)

    assert resp.status_code == 500, resp.text
    assert "wrote no VCF" in resp.json()["detail"]


def test_a_failed_genotyping_pass_is_refused_with_the_tools_own_complaint(
    client, tools
):
    """The `<*>`-flavoured gVCF failure arrives here if one slips past the upload gate,
    and the operator needs GATK's own sentence to recognise it."""
    tools.failing[("gatk", "GenotypeGVCFs")] = 3
    tools.stderr = (
        b"A USER ERROR has occurred: The list of input alleles must contain "
        b"<NON_REF> as an allele"
    )

    resp = _post(client)

    assert resp.status_code == 500, resp.text
    detail = resp.json()["detail"]
    assert "exit code 3" in detail
    assert "must contain <NON_REF>" in detail, "the tool's own complaint must get out"


def test_a_failed_staging_index_is_fatal_not_ignored(client, tools):
    """Unlike the final index, this one is load-bearing: GenotypeGVCFs cannot read an
    unindexed gVCF, so continuing past it would fail later and further away."""
    tools.failing[("bcftools", "index")] = 1

    resp = _post(client)

    assert resp.status_code == 500, resp.text
    assert "Indexing the uploaded gVCF" in resp.json()["detail"]
    assert (
        tools.genotype_calls() == []
    ), "nothing may be genotyped from an unindexed file"


def test_a_failed_concat_is_refused(client, tools):
    tools.failing[("bcftools", "concat")] = 2

    resp = _post(client)

    assert resp.status_code == 500, resp.text
    assert "Merging the two genotyped passes" in resp.json()["detail"]
