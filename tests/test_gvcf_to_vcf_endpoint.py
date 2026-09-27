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

    Rows are written as GenotypeGVCFs writes them (measured on a panel gVCF): a 0/0 is
    a reference-block row, `REF ALT=.` with GT:DP:RGQ and a confident RGQ --
    rewrite_homref_to_pharmcat_alleles no-calls a 0/0 row it cannot stand behind -- and
    a position the gVCF had no block for is a bare `GT ./.`.
    """
    lines = []
    for row in rows:
        chrom, pos = row[0], row[1]
        genotype = row[2] if len(row) > 2 else "0/0"
        if genotype == "0/0":
            lines.append(f"{chrom}\t{pos}\t.\tG\t.\t.\t.\t.\tGT:DP:RGQ\t0/0:30:99\n")
        else:
            lines.append(f"{chrom}\t{pos}\t.\tG\t.\t.\t.\t.\tGT\t{genotype}\n")
    return VCF_HEADER + "".join(lines)


def _positions_text(rows):
    """PharmCAT's position list: one SNV record per position."""
    return VCF_HEADER + "".join(
        f"{chrom}\t{pos}\trsX\tG\tA\t.\tPASS\t.\tGT\t0/0\n" for chrom, pos in rows
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
        _positions_text(PHARMCAT_POSITION_ROWS)
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
    * `bcftools view -H -R BED ... -o OUT STAGED` (the gVCF's own records at
      PharmCAT's positions) writes the text lines in `span_records`.
    * `bcftools view -T ^ROWS ... -o OUT IN` writes IN's rows minus those at a position
      ROWS lists.
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
        self.span_records = []
        self.span_beds = []
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
            side = [
                argv[argv.index(flag) + 1].lstrip("^")
                for flag in ("-R", "-T")
                if flag in argv
            ]
            return side + [argv[-1]]
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
            source = argv[-1]
            if "-R" in argv:
                self.span_beds.append(
                    Path(self._flag_value(argv, "-R")).read_text(encoding="utf-8")
                )
                Path(self._flag_value(argv, "-o")).write_text(
                    "".join(self.span_records), encoding="utf-8"
                )
            elif "-T" in argv:
                listed = Path(self._flag_value(argv, "-T").lstrip("^")).read_text(
                    encoding="utf-8"
                )
                drop = {tuple(line.split("\t")) for line in listed.splitlines()}
                self._write(
                    self._flag_value(argv, "-o"),
                    [
                        row
                        for row in self.rows_by_path.get(source, [])
                        if (row[0], str(row[1])) not in drop
                    ],
                )
            elif source.endswith((".pharmcat_alleles.vcf", ".general.vcf")):
                # The PharmCAT-alleles rewrite of the PGx pass: carry its rows, so the
                # coverage count downstream reads what the rewrite actually wrote.
                rows = []
                for line in Path(source).read_text(encoding="utf-8").splitlines():
                    if line and not line.startswith("#"):
                        f = line.split("\t")
                        rows.append((f[0], int(f[1]), f[9].split(":")[0]))
                self._write(self._flag_value(argv, "-o"), rows)
            else:
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


def test_only_the_pgx_pass_runs_at_calling_threshold_zero(client, tools):
    """At GATK's default of 30, a variant that fails the threshold is not no-called: it
    is written as a reference-block row, 0/0 with the het's GQ as RGQ. Measured on a
    panel gVCF: CYP4F2 rs4020346 (0/1, 5 of 34 reads alt, QUAL 15.6) came out
    `T . 0.12 GT:DP:RGQ 0/0:34:23`, which no rule applied afterwards can tell from
    covered reference. At 0 the row keeps the caller's genotype, AD and QUAL, and
    rewrite_homref_to_pharmcat_alleles makes it a no-call. The variant pass is the
    ordinary calling step and keeps GATK's default."""
    resp = _post(client)

    # The status assertion is not decoration: a bare `for argv in ...: assert` over an
    # EMPTY list passes. Pointing PHARMCAT_POSITIONS_PATH at the regions BED 400s this
    # endpoint before a single tool runs, and this test and three of its neighbours
    # went on passing over nothing.
    assert resp.status_code == 200, resp.text
    assert len(tools.genotype_calls()) == 2, tools.argvs()

    flag = "--standard-min-confidence-threshold-for-calling"
    for argv in tools.genotype_calls():
        assert "-stand-call-conf" not in argv, argv
        if "--include-non-variant-sites" in argv:
            assert argv[argv.index(flag) + 1] == "0", argv
        else:
            assert flag not in argv, argv


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
    # And it merges THE TWO PASSES, not two paths that happen to be named plausibly --
    # the PGx pass by way of its PharmCAT-alleles rewrite (see
    # rewrite_homref_to_pharmcat_alleles), which is written from that pass's output.
    # The variant pass by way of the copy that lost the PGx pass's positions (step 3b).
    merged = concat[0][concat[0].index("-o") + 2 :]
    outputs = [argv[argv.index("-O") + 1] for argv in tools.genotype_calls()]
    pgx_pass = next(o for o in outputs if o.endswith("pgx_positions.vcf.gz"))
    variant_pass = next(o for o in outputs if o != pgx_pass)
    trimmed = [argv for argv in tools.ran("bcftools", "view") if "-T" in argv]
    assert len(trimmed) == 1 and trimmed[0][-1] == variant_pass, tools.argvs()
    assert sorted(merged) == sorted(
        [
            trimmed[0][trimmed[0].index("-o") + 1],
            pgx_pass.replace(".vcf.gz", ".general.vcf.gz"),
        ]
    ), merged


def test_every_concat_input_is_indexed_first(client, tools):
    """`concat -a` opens its inputs through their indexes: an unindexed one fails the
    conversion outright (measured: "could not load index" on every gVCF, when the
    trimmed variant pass was first added without one). GATK indexes what it writes;
    anything bcftools writes has to be indexed before the concat."""
    resp = _post(client)
    assert resp.status_code == 200, resp.text

    order = tools.argvs()
    concat = tools.ran("bcftools", "concat")[0]
    gatk_outputs = {argv[argv.index("-O") + 1] for argv in tools.genotype_calls()}
    for source in concat[concat.index("-o") + 2 :]:
        if source in gatk_outputs:
            continue
        indexed = [
            i
            for i, argv in enumerate(order)
            if argv[:2] == ["bcftools", "index"] and argv[-1] == source
        ]
        assert indexed and indexed[0] < order.index(concat), source


def test_the_upload_is_staged_and_indexed_before_genotyping(client, tools):
    """GenotypeGVCFs needs an index and a filename extension it recognises, and the
    stored upload name is guaranteed to be neither: safe_upload_name() has no `.gvcf`
    in SAFE_UPLOAD_EXTENSIONS, so `sample.gvcf` reaches disk with no extension at all.
    The unconditional `bcftools view -Oz` is also what lets a gVCF written as a BCF use
    this lane rather than needing one of its own."""
    resp = _post(client, filename="sample.gvcf")

    assert resp.status_code == 200, resp.text
    # The other `bcftools view`s re-encode the two judged copies of the PGx pass,
    # read the staged gVCF's records at PharmCAT's positions (-R) and trim the variant
    # pass (-T).
    staged = [
        argv
        for argv in tools.ran("bcftools", "view")
        if not argv[-1].endswith((".pharmcat_alleles.vcf", ".general.vcf"))
        and "-R" not in argv
        and "-T" not in argv
    ]
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


# --------------------------------------------------------------------------
# PharmCAT's alleles at reference positions (rewrite_homref_to_pharmcat_alleles)
# --------------------------------------------------------------------------

_POSITIONS_HEADER = (
    "##fileformat=VCFv4.2\n"
    "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPharmCAT\n"
)
_INDEL = ("chr13", 48037782, "rs746071566", "AGGAGTC", "A,AGGAGTCGGAGTC")
_SNV = ("chr19", 15878920, "rs4020346", "T", "C")


def _positions(tmp_path, rows):
    path = tmp_path / "pharmcat_positions.vcf"
    path.write_text(
        _POSITIONS_HEADER
        + "".join(
            f"{c}\t{p}\t{i}\t{r}\t{a}\t.\tPASS\tPX=G\tGT\t0/0\n"
            for c, p, i, r, a in rows
        ),
        encoding="utf-8",
    )
    return str(path)


def _called(rows):
    """Rows are (chrom, pos, ref, alt, FORMAT, sample) or, with QUAL, (..., qual)."""
    return [
        "##fileformat=VCFv4.2\n",
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\n",
    ] + [
        f"{c}\t{p}\t.\t{r}\t{a}\t{rest[0] if rest else '.'}\t.\t.\t{fmt}\t{s}\n"
        for c, p, r, a, fmt, s, *rest in rows
    ]


def _indel_rows(at_anchor, sample="0/0:21:60", fmt="GT:DP:RGQ"):
    """GenotypeGVCFs emits one row per base of a multi-base interval: the anchor row
    given, then reference-block rows for the other six bases of _INDEL's REF."""
    return [at_anchor] + [
        ("chr13", 48037782 + offset, "G", ".", fmt, sample) for offset in range(1, 7)
    ]


def _body(lines):
    return [line for line in lines if not line.startswith("#")]


def _genotype(line):
    return line.rstrip("\n").split("\t")[9].split(":", 1)[0]


def test_a_confident_reference_indel_position_gets_pharmcats_alleles(
    gatk_api, tmp_path
):
    """GenotypeGVCFs writes it REF=anchor ALT=., which PharmCAT discards; measured on a
    panel gVCF, that made NUDT15 *3/*6/*9, UGT1A1 *28/*36/*37 and DPYD *3/*7
    uncallable though the positions were covered and reference."""
    lines, rewritten, demoted = gatk_api.rewrite_homref_to_pharmcat_alleles(
        _called(_indel_rows(("chr13", 48037782, "A", ".", "GT:DP:RGQ", "0/0:21:60"))),
        _positions(tmp_path, [_INDEL]),
    )
    assert (rewritten, demoted) == (1, 0)
    assert _body(lines)[0] == (
        "chr13\t48037782\trs746071566\tAGGAGTC\tA,AGGAGTCGGAGTC\t.\t.\t.\tGT:DP\t0/0:21\n"
    )


@pytest.mark.parametrize(
    "fmt, sample, why",
    [
        ("GT:DP:RGQ", "0/0:21:12", "reference confidence below 20"),
        ("GT:DP:RGQ", "0/0:6:60", "fewer than 7 reads"),
        ("GT:DP", "0/0:21", "no confidence evidence at all"),
    ],
)
def test_a_reference_row_it_cannot_stand_behind_becomes_a_no_call(
    gatk_api, tmp_path, fmt, sample, why
):
    lines, rewritten, demoted = gatk_api.rewrite_homref_to_pharmcat_alleles(
        _called(_indel_rows(("chr13", 48037782, "A", ".", fmt, sample))),
        _positions(tmp_path, [_INDEL]),
    )
    assert (rewritten, demoted) == (0, 1), why
    assert _genotype(_body(lines)[0]) == "./.", why


@pytest.mark.parametrize(
    "row, why",
    [
        # CYP4F2 rs4020346 exactly as GenotypeGVCFs writes it at threshold 0, from the
        # panel gVCF's `0/1:29,5,0:34:23` (the WGS shows the site uncertain).
        (
            (
                "chr19",
                15878920,
                "T",
                "C",
                "GT:AD:DP:GQ:PL",
                "0/1:29,5:34:23:23,0,887",
                "15.63",
            ),
            "a het below QUAL 30",
        ),
        (
            (
                "chr19",
                15878920,
                "T",
                "C",
                "GT:AD:DP:GQ:PL",
                "1/1:0,3:3:9:99,9,0",
                "95.0",
            ),
            "a hom-alt below GQ 20",
        ),
        # The same gVCF's chr19:15878886, a 0/0 on a variant record: 2 of 31 reads alt.
        (
            ("chr19", 15878920, "T", "C", "GT:AD:DP:RGQ", "0/0:29,2:31:18", "0"),
            "a 0/0 with reads for another allele",
        ),
        (
            ("chr19", 15878920, "T", "C", "GT:AD:DP:RGQ", "0/0:29,1:30:12", "0"),
            "a 0/0 below reference confidence 20",
        ),
    ],
)
def test_a_row_the_caller_was_not_sure_of_becomes_a_no_call(
    gatk_api, tmp_path, row, why
):
    """Neither a call nor reference: GenotypeGVCFs runs this pass at threshold 0, so
    the caller's own genotype reaches this function and the lane applies the bar."""
    lines, rewritten, demoted = gatk_api.rewrite_homref_to_pharmcat_alleles(
        _called([row]), _positions(tmp_path, [_SNV])
    )
    assert (rewritten, demoted) == (0, 1), why
    assert _genotype(_body(lines)[0]) == "./.", why


def test_one_stray_read_at_depth_is_still_reference(gatk_api, tmp_path):
    """The alignment lanes' allowance: from 20 reads up, 5% may show something else."""
    lines, rewritten, demoted = gatk_api.rewrite_homref_to_pharmcat_alleles(
        _called(
            _indel_rows(
                ("chr13", 48037782, "A", "C", "GT:AD:DP:RGQ", "0/0:29,1:30:60", "0")
            )
        ),
        _positions(tmp_path, [_INDEL]),
    )
    assert (rewritten, demoted) == (1, 0)


def test_every_base_of_a_multi_base_record_must_be_reference(gatk_api, tmp_path):
    """A deletion's REF spans seven bases; one of them not confidently reference means
    the deletion's absence is not known either."""
    rows = _indel_rows(("chr13", 48037782, "A", ".", "GT:DP:RGQ", "0/0:21:60"))
    rows[4] = ("chr13", 48037786, "G", ".", "GT:DP:RGQ", "0/0:21:5")
    lines, rewritten, demoted = gatk_api.rewrite_homref_to_pharmcat_alleles(
        _called(rows), _positions(tmp_path, [_INDEL])
    )
    assert (rewritten, demoted) == (0, 1)
    assert _genotype(_body(lines)[0]) == "./."


def test_a_base_missing_from_the_record_span_is_not_reference(gatk_api, tmp_path):
    lines, rewritten, demoted = gatk_api.rewrite_homref_to_pharmcat_alleles(
        _called([("chr13", 48037782, "A", ".", "GT:DP:RGQ", "0/0:21:60")]),
        _positions(tmp_path, [_INDEL]),
    )
    assert (rewritten, demoted) == (0, 1)


def test_a_reference_row_beside_a_no_call_row_is_a_no_call(gatk_api, tmp_path):
    """Review: a 0/0 row and a `./.` row at one position skipped the check."""
    lines, rewritten, demoted = gatk_api.rewrite_homref_to_pharmcat_alleles(
        _called(
            [
                ("chr19", 15878920, "T", ".", "GT:DP:RGQ", "0/0:30:99"),
                ("chr19", 15878920, "T", "C", "GT", "./."),
            ]
        ),
        _positions(tmp_path, [_SNV]),
    )
    assert (rewritten, demoted) == (0, 1)
    assert _body(lines) == [
        "chr19\t15878920\trs4020346\tT\tC\t.\t.\t.\tGT:DP\t./.:30\n"
    ]


def test_a_confident_call_is_left_as_gatk_wrote_it(gatk_api, tmp_path):
    called = _called(
        [
            (
                "chr13",
                48037782,
                "A",
                "AGGAGTCGGAGTC",
                "GT:AD:DP:GQ:PL",
                "0/1:10,10:20:99:300,0,300",
                "300.64",
            )
        ]
    )
    lines, rewritten, demoted = gatk_api.rewrite_homref_to_pharmcat_alleles(
        called, _positions(tmp_path, [_INDEL])
    )
    assert (rewritten, demoted) == (0, 0)
    assert lines == called


def test_an_uncovered_position_is_pharmcats_record_uncalled(gatk_api, tmp_path):
    """No block in the gVCF: a no-call, and absent rather than uncertain."""
    lines, rewritten, demoted = gatk_api.rewrite_homref_to_pharmcat_alleles(
        _called([("chr13", 48037782, "A", ".", "GT", "./.")]),
        _positions(tmp_path, [_INDEL]),
    )
    assert (rewritten, demoted) == (0, 0)
    assert _body(lines) == [
        "chr13\t48037782\trs746071566\tAGGAGTC\tA,AGGAGTCGGAGTC\t.\t.\t.\tGT\t./.\n"
    ]


def test_a_position_with_a_variant_row_keeps_every_row(gatk_api, tmp_path):
    """A second record at the position saying non-reference means it is not reference."""
    called = _called(
        [
            ("chr2", 233760233, "C", ".", "GT:DP:RGQ", "0/0:30:99"),
            (
                "chr2",
                233760233,
                "CAT",
                "CATAT",
                "GT:AD:DP:GQ",
                "0/1:15,15:30:99",
                "250.6",
            ),
        ]
    )
    lines, rewritten, demoted = gatk_api.rewrite_homref_to_pharmcat_alleles(
        called,
        _positions(tmp_path, [("chr2", 233760233, "rs3064744", "CAT", "C,CATAT")]),
    )
    assert (rewritten, demoted) == (0, 0)
    assert lines == called


def test_rows_outside_pharmcats_list_are_untouched_but_for_weak_calls(
    gatk_api, tmp_path
):
    """The other bases of a multi-base record come through the pass too. A reference
    row or a confident call there is left alone; a variant the caller was not sure of
    is a no-call, as it would have been dropped at GATK's default threshold rather than
    reaching PyPGx as a call."""
    called = _called(
        [
            ("chr1", 101, "G", ".", "GT:DP", "0/0:3"),
            (
                "chr1",
                102,
                "G",
                "A",
                "GT:AD:DP:GQ:PL",
                "0/1:15,15:30:99:300,0,300",
                "300.6",
            ),
            (
                "chr1",
                103,
                "G",
                "A",
                "GT:AD:DP:GQ:PL",
                "0/1:29,5:34:23:23,0,887",
                "15.63",
            ),
        ]
    )
    lines, rewritten, demoted = gatk_api.rewrite_homref_to_pharmcat_alleles(
        called, _positions(tmp_path, [("chr1", 100, "rs1", "C", "T")])
    )
    assert (rewritten, demoted) == (0, 0)
    assert _body(lines)[:2] == _body(called)[:2]
    assert _genotype(_body(lines)[2]) == "./."


# --------------------------------------------------------------------------
# A deletion called upstream of a PharmCAT position (review, verified with GATK 4.7.0.0
# and PharmCAT end to end: DPYD rs72549303 came out TG/TG inside a het TTGTCTG>T)
# --------------------------------------------------------------------------
_DPYD = ("chr1", 97450065, "rs72549303", "TG", "T")


def _qual(line):
    return line.split("\t")[5]


def test_a_position_inside_an_upstream_deletion_is_not_reference(gatk_api, tmp_path):
    """GenotypeGVCFs with -L emits only records STARTING in an interval, so the
    deletion never reaches the PGx pass; the position comes out as a plain confident
    reference-block row. Only the gVCF's own record says otherwise."""
    rows = [
        ("chr1", 97450065, "T", ".", "GT:DP:RGQ", "0/0:51:99"),
        ("chr1", 97450066, "G", ".", "GT:DP:RGQ", "0/0:51:99"),
    ]
    contested = [("chr1", 97450060, 97450066, False)]
    lines, rewritten, demoted = gatk_api.rewrite_homref_to_pharmcat_alleles(
        _called(rows), _positions(tmp_path, [_DPYD]), contested
    )
    assert (rewritten, demoted) == (0, 1)
    assert _genotype(_body(lines)[0]) == "./."

    # Without the gVCF's record it would have been reference -- the finding itself.
    lines, rewritten, demoted = gatk_api.rewrite_homref_to_pharmcat_alleles(
        _called(rows), _positions(tmp_path, [_DPYD])
    )
    assert (rewritten, demoted) == (1, 0)


def test_a_forced_row_is_decided_by_the_gvcfs_own_records(gatk_api, tmp_path):
    """GATK's shape where a position's only other allele is a spanning deletion's `*`:
    ALT `.`, QUAL `inf`, RGQ the deletion's own GQ. Under a deletion the caller called,
    the record contests the position; under one it genotyped 0/0 with no reads for it
    -- HaplotypeCaller writes such records in repeats -- the position is reference, and
    review measured it no-called by the QUAL heuristic this replaced."""
    row = ("chr19", 15878920, "T", ".", "GT:DP:RGQ", "0/0:32:90", "inf")

    lines, rewritten, demoted = gatk_api.rewrite_homref_to_pharmcat_alleles(
        _called([row]),
        _positions(tmp_path, [_SNV]),
        [("chr19", 15878917, 15878920, False)],
    )
    assert (rewritten, demoted) == (0, 1)
    assert _genotype(_body(lines)[0]) == "./."

    lines, rewritten, demoted = gatk_api.rewrite_homref_to_pharmcat_alleles(
        _called([row]), _positions(tmp_path, [_SNV])
    )
    assert (rewritten, demoted) == (1, 0)
    assert _genotype(_body(lines)[0]) == "0/0"
    assert _qual(_body(lines)[0]) == "."


def test_a_no_call_never_carries_qual_inf(gatk_api, tmp_path):
    """PharmCAT stops on it: "Error parsing data: QUAL 'inf' is not a number"
    (review, with the demoted row `T . inf GT:DP:RGQ 0/0:28:15`)."""
    lines, _, _ = gatk_api.rewrite_homref_to_pharmcat_alleles(
        _called(
            [
                ("chr19", 15878920, "T", ".", "GT:DP:RGQ", "0/0:28:15", "inf"),
                ("chr19", 15878921, "G", ".", "GT:DP:RGQ", "0/0:51:99", "inf"),
            ]
        ),
        _positions(tmp_path, [_SNV]),
    )
    assert [_qual(line) for line in _body(lines)] == [".", "."]


def test_a_covered_position_gatk_left_uncalled_is_not_absent(gatk_api, tmp_path):
    """Measured on the panel: chrX 154536168 `GT:DP:RGQ ./.:11:0` -- 11 reads, no call.
    It stays `./.`, but counts as covered-and-uncertain, not as "not covered"."""
    called = _called([("chr19", 15878920, "T", ".", "GT:DP:RGQ", "./.:11:0")])
    lines, rewritten, demoted = gatk_api.rewrite_homref_to_pharmcat_alleles(
        called, _positions(tmp_path, [_SNV])
    )
    assert (rewritten, demoted) == (0, 1)
    assert _genotype(_body(lines)[0]) == "./."


@pytest.mark.parametrize(
    "record, contests",
    [
        # HaplotypeCaller's het deletion upstream of DPYD rs72549303.
        (
            "chr1\t97450060\t.\tTTGTCTG\tT,<NON_REF>\t500\t.\t.\tGT:AD:DP:GQ\t0/1:30,21,0:51:99",
            True,
        ),
        # A 0/0 with 2 of 31 reads for the deletion: beyond the 5% allowance.
        (
            "chr1\t97450060\t.\tTTGTCTG\tT,<NON_REF>\t0\t.\t.\tGT:AD:DP:GQ\t0/0:29,2,0:31:18",
            True,
        ),
        # One stray read in 30 is within it.
        (
            "chr1\t97450060\t.\tTTGTCTG\tT,<NON_REF>\t0\t.\t.\tGT:AD:DP:GQ\t0/0:29,1,0:30:60",
            False,
        ),
        # Hemizygous.
        (
            "chrX\t154532040\t.\tCAG\tC,<NON_REF>\t90\t.\t.\tGT:AD:DP:GQ\t1:0,9,0:9:90",
            True,
        ),
        # A reference block contests nothing, whatever it spans.
        (
            "chr1\t97450000\t.\tA\t<NON_REF>\t.\t.\tEND=97450100\tGT:DP:GQ\t0/0:40:99",
            False,
        ),
        # Reads for <NON_REF> are not reads for an allele the caller saw.
        (
            "chr1\t97450060\t.\tTTGTCTG\tT,<NON_REF>\t0\t.\t.\tGT:AD:DP:GQ\t0/0:29,0,9:38:30",
            False,
        ),
    ],
)
def test_which_gvcf_records_contest_reference(gatk_api, record, contests):
    spans = gatk_api.contested_spans([record + "\n"])
    f = record.split("\t")
    expected = [(f[0], int(f[1]), int(f[1]) + len(f[3]) - 1, False)] if contests else []
    assert spans == expected


def test_the_conversion_reads_the_gvcfs_own_records_across_pharmcats_spans(
    client, tools, gatk_api
):
    """By overlap, so a record starting upstream is returned; through the index."""
    resp = _post(client)
    assert resp.status_code == 200, resp.text

    reads = [argv for argv in tools.ran("bcftools", "view") if "-R" in argv]
    assert len(reads) == 1, tools.argvs()
    argv = reads[0]
    assert argv[argv.index("--regions-overlap") + 1] == "record"
    assert argv[-1].endswith("staged.vcf.gz")
    assert tools.span_beds[0].splitlines() == [
        f"{c}\t{p - 1}\t{p}" for c, p in PHARMCAT_POSITION_ROWS
    ]


def test_a_contested_position_reaches_the_merged_vcf_as_a_no_call(client, tools):
    chrom, pos = PHARMCAT_POSITION_ROWS[0]
    tools.span_records = [
        f"{chrom}\t{pos - 3}\t.\tGTCA\tG,<NON_REF>\t400\t.\t.\tGT:AD:DP:GQ\t0/1:15,15,0:30:99\n"
    ]

    body = _post(client).json()

    merged = tools.ran("bcftools", "concat")[0]
    rows = tools.rows_by_path[merged[merged.index("-o") + 1]]
    assert (chrom, pos, "./.") in rows, rows
    assert body["n_positions_uncertain"] == 1


def test_the_pgx_pass_has_the_last_word_at_its_positions(client, tools):
    """Measured: a record starting at a PharmCAT position and running past its interval
    comes out of both passes, and `concat -D` kept the variant pass's `1/1 GQ 15` over
    the PGx pass's no-call. The variant pass now loses every record at a position the
    PGx pass wrote a row for, before the concat."""
    chrom, pos = PHARMCAT_POSITION_ROWS[0]
    tools.variant_rows = [(chrom, pos, "1/1"), ("chr1", 1000)]

    resp = _post(client)
    assert resp.status_code == 200, resp.text

    merged = tools.ran("bcftools", "concat")[0]
    rows = tools.rows_by_path[merged[merged.index("-o") + 1]]
    assert [r for r in rows if (r[0], r[1]) == (chrom, pos)] == [(chrom, pos, "0/0")]
    assert ("chr1", 1000) in rows


def test_other_bases_inside_a_called_deletion_are_not_reference(gatk_api, tmp_path):
    """Measured through the endpoint: GATK's forced row at 97450066, a base PharmCAT
    does not list, sat inside a called het TTGTCTG>T as `0/0:51:99`. PharmCAT ignores
    the base; PyPGx reads the same file, so it is a no-call too."""
    lines, rewritten, demoted = gatk_api.rewrite_homref_to_pharmcat_alleles(
        _called(
            [
                ("chr1", 97450065, "T", ".", "GT:DP:RGQ", "0/0:51:99"),
                ("chr1", 97450066, "G", ".", "GT:DP:RGQ", "0/0:51:99", "inf"),
                ("chr1", 97450067, "C", ".", "GT:DP:RGQ", "0/0:51:99"),
            ]
        ),
        _positions(tmp_path, [_DPYD]),
        [("chr1", 97450060, 97450066, False)],
    )
    assert (rewritten, demoted) == (0, 1)
    assert [_genotype(line) for line in _body(lines)] == ["./.", "./.", "0/0"]
    assert [_qual(line) for line in _body(lines)] == [".", ".", "."]


# --------------------------------------------------------------------------
# `*` at a PharmCAT position (review, verified with GATK 4.7.0.0 and PharmCAT 3.4.0:
# rs17376848 came out A/A under a het GGAA>G anchored one base upstream)
# --------------------------------------------------------------------------
_RS17376848 = ("chr1", 97450068, "rs17376848", "A", "G")


@pytest.mark.parametrize(
    "row",
    [
        (
            "chr1",
            97450068,
            "A",
            "*",
            "GT:AD:DP:GQ:PL",
            "0/1:20,18:38:99:0,99,900",
            "45",
        ),
        ("chr1", 97450068, "A", "*", "GT:AD:DP:GQ:PL", "./.:20,18:38:99:0,99,900"),
    ],
)
def test_a_star_allele_never_reaches_pharmcat(gatk_api, tmp_path, row):
    """Beside a `*` row -- called or `./.` -- PharmCAT's preprocessor adds its own 0/0
    and reports reference. PharmCAT's own record with `./.` reads as missing."""
    lines, rewritten, demoted = gatk_api.rewrite_homref_to_pharmcat_alleles(
        _called([row]), _positions(tmp_path, [_RS17376848])
    )
    assert (rewritten, demoted) == (0, 1)
    assert _body(lines) == [
        "chr1\t97450068\trs17376848\tA\tG\t.\t.\t.\tGT:DP\t./.:38\n"
    ]


def test_a_call_covered_by_a_record_starting_upstream_is_a_no_call(gatk_api, tmp_path):
    """GATK drops the `*` of `C *,G 1/2` and writes `C G 0/1` with AD 0,15: the other
    haplotype is deleted, not reference. PharmCAT discarded it only because GT and AD
    disagree; the lane does not rely on that."""
    row = (
        "chr1",
        97078993,
        "C",
        "G",
        "GT:AD:DP:GQ:PL",
        "0/1:0,15:15:45:450,0,45",
        "400",
    )
    positions = _positions(tmp_path, [("chr1", 97078993, "rs1", "C", "G")])

    lines, rewritten, demoted = gatk_api.rewrite_homref_to_pharmcat_alleles(
        _called([row]), positions, [("chr1", 97078991, 97078994, False)]
    )
    assert (rewritten, demoted) == (0, 1)
    assert _genotype(_body(lines)[0]) == "./."

    # A record starting AT the position is the row itself: the call stands.
    called = _called([row])
    lines, rewritten, demoted = gatk_api.rewrite_homref_to_pharmcat_alleles(
        called, positions, [("chr1", 97078993, 97078993, False)]
    )
    assert (rewritten, demoted) == (0, 0)
    assert lines == called


# --------------------------------------------------------------------------
# What contests reference, allele by allele (review's D1 and D2)
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "record, expected",
    [
        # No genotype at all on a record with a real allele: the caller could not say.
        (
            "chr1\t97079003\t.\tTACG\tT,<NON_REF>\t0\t.\t.\tGT:AD:DP:GQ\t./.:20,5,0:25:0",
            [("chr1", 97079003, 97079006, False)],
        ),
        # Only the insertion of a multi-allelic record carried: insertion_only.
        (
            "chr1\t97079119\t.\tGGTG\tG,GGTGT,<NON_REF>\t500\t.\t.\tGT:AD:DP:GQ\t0/2:15,0,14,0:29:99",
            [("chr1", 97079119, 97079122, True)],
        ),
        # The deletion carried as well: not.
        (
            "chr1\t97079119\t.\tGGTG\tG,GGTGT,<NON_REF>\t500\t.\t.\tGT:AD:DP:GQ\t1/2:0,15,14,0:29:99",
            [("chr1", 97079119, 97079122, False)],
        ),
        # A plain insertion record.
        (
            "chr1\t97079119\t.\tG\tGT,<NON_REF>\t500\t.\t.\tGT:AD:DP:GQ\t0/1:15,14,0:29:99",
            [("chr1", 97079119, 97079119, True)],
        ),
    ],
)
def test_contested_spans_by_allele(gatk_api, record, expected):
    assert gatk_api.contested_spans([record + "\n"]) == expected


def test_an_insertion_does_not_contest_an_snv_under_it(gatk_api, tmp_path):
    """An insertion changes the identity of no reference base. Review: `GGTG G,GGTGT
    0/2` no-called the SNV at 97079121 inside the GGTG."""
    rows = [("chr1", 97079121, "T", ".", "GT:DP:RGQ", "0/0:30:60")]
    spans = [("chr1", 97079119, 97079122, True)]

    lines, rewritten, demoted = gatk_api.rewrite_homref_to_pharmcat_alleles(
        _called(rows),
        _positions(tmp_path, [("chr1", 97079121, "rs1", "T", "C")]),
        spans,
    )
    assert (rewritten, demoted) == (1, 0)
    assert _genotype(_body(lines)[0]) == "0/0"


def test_an_insertion_still_contests_an_indel_record(gatk_api, tmp_path):
    """In a repeat an insertion can be written at any point of it, so a multi-base
    PharmCAT record under one is not reference."""
    rows = [
        ("chr1", 97079121, "T", ".", "GT:DP:RGQ", "0/0:30:60"),
        ("chr1", 97079122, "G", ".", "GT:DP:RGQ", "0/0:30:60"),
    ]
    spans = [("chr1", 97079119, 97079122, True)]

    lines, rewritten, demoted = gatk_api.rewrite_homref_to_pharmcat_alleles(
        _called(rows),
        _positions(tmp_path, [("chr1", 97079121, "rs1", "TG", "T")]),
        spans,
    )
    assert (rewritten, demoted) == (0, 1)
    assert _genotype(_body(lines)[0]) == "./."


# --------------------------------------------------------------------------
# Two readers: PharmCAT's file, and the general one PyPGx reads
# --------------------------------------------------------------------------
def test_a_confident_call_no_called_for_pharmcat_stays_for_pypgx(gatk_api, tmp_path):
    """Review measured the G of `C *,G 1/2` (GATK: `C G 0/1:0,15`) lost from what PyPGx
    reads. It matters most for CYP2D6: 157 PharmCAT positions, called by PyPGx."""
    row = (
        "chr1",
        97078993,
        "C",
        "G",
        "GT:AD:DP:GQ:PL",
        "0/1:0,15:15:45:450,0,45",
        "400",
    )
    called = _called([row])

    pharmcat, general, rewritten, demoted = gatk_api.judge_pgx_pass(
        called,
        _positions(tmp_path, [("chr1", 97078993, "rs1", "C", "G")]),
        [("chr1", 97078991, 97078994, False)],
    )
    assert (rewritten, demoted) == (0, 1)
    assert _genotype(_body(pharmcat)[0]) == "./."
    assert _body(general) == _body(called)


@pytest.mark.parametrize(
    "row",
    [
        # A weak call: nobody should read it as a call.
        (
            "chr19",
            15878920,
            "T",
            "C",
            "GT:AD:DP:GQ:PL",
            "0/1:29,5:34:23:23,0,887",
            "15.63",
        ),
        # A `*` row the caller was not sure of.
        (
            "chr19",
            15878920,
            "T",
            "*",
            "GT:AD:DP:GQ:PL",
            "0/1:20,18:38:99:0,99,900",
            "0",
        ),
    ],
)
def test_an_evidence_no_call_is_a_no_call_for_everyone(gatk_api, tmp_path, row):
    pharmcat, general, _, demoted = gatk_api.judge_pgx_pass(
        _called([row]), _positions(tmp_path, [_SNV])
    )
    assert demoted == 1
    assert _body(general) == _body(pharmcat)
    assert _genotype(_body(general)[0]) == "./."


def test_the_conversion_returns_pharmcats_own_file(client, tools):
    """Beside the output (the work dir is gone once the request returns), holding the
    judged PGx pass alone; genotyped.vcf.gz is built from the general copy."""
    body = _post(client).json()

    path = body["pharmcat_vcf_path"]
    assert path.endswith(".genotyped.pharmcat.vcf.gz"), path
    assert os.path.dirname(path) == os.path.dirname(body["vcf_path"])
    written = [
        argv
        for argv in tools.ran("bcftools", "view")
        if argv[argv.index("-o") + 1] == path
    ]
    assert len(written) == 1 and written[0][-1].endswith(".pharmcat_alleles.vcf")
    assert [r[:2] for r in tools.rows_by_path[path]] == PHARMCAT_POSITION_ROWS
