"""A gene a VCF has no genotype in gets no PyPGx call -- not a reference one.

Found 2026-10-20, running PyPGx 0.27 from the shipped image on the app's own demo VCF
(app/static/demo/pharmcat.example.vcf, PharmCAT's example: records at PharmCAT's
positions only). Genes with no record anywhere in the file came back called:

    CYP2E1  *7/*7      ABCB1   *2/*2      GSTM1   *A/*A
    NAT1    *4/*4      APOE    Reference/Reference
    SLCO1B3 Reference/Reference

PyPGx reads "no record" as "matches the reference genome" (pypgx#142). The alignment
lane already declines such genes: /create-input-vcf measures coverage on the reads and
writes ##ZaroPGx_uncovered_genes= into the VCF (tests/test_pypgx_uncovered_genes.py).
An uploaded VCF has no such line, so only a gene on a chromosome with no record at all
escaped, through PyPGx's "invalid contig". With "Assume absent sites = reference" off
(the default) PharmCAT reads the same absences as no-calls: the two tools disagreed.

Now /genotype, given a VCF without coverage evidence and the job's absent_to_ref off,
gives no call to a gene with no called genotype in PyPGx's own region for it. A ./.
does not count: PyPGx hands it to Beagle, which fills it in (measured: the demo's 37
NAT2 records, all 0/0, set to ./. gave NAT2 *5/*5, against *12/*12). main.nf sends the
run's effective flag. A gVCF's converted VCF keeps no reference blocks, so gatk-api
/gvcf-to-vcf writes the uncovered-genes line from the blocks (tests in
tests/test_gvcf_to_vcf_endpoint.py), and its genes are called as an alignment's are.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import importlib.util
import json
import logging
import re
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pysam = pytest.importorskip("pysam")

REPO = Path(__file__).resolve().parents[1]
WRAPPER = REPO / "docker" / "pypgx" / "pypgx_wrapper.py"
GATK_API = REPO / "docker" / "gatk-api" / "gatk_api.py"
GENE_TABLE = REPO / "lexicon" / "gene-table.csv"
MAIN_NF = REPO / "pipelines" / "pgx" / "main.nf"


def _fake_psutil():
    module = types.ModuleType("psutil")
    module.virtual_memory = lambda: types.SimpleNamespace(
        total=16 * 1024**3, available=8 * 1024**3, used=8 * 1024**3, percent=50.0
    )
    module.Process = lambda *a, **k: types.SimpleNamespace()
    module.pid_exists = lambda pid: False
    module.NoSuchProcess = type("NoSuchProcess", (Exception,), {})
    module.AccessDenied = type("AccessDenied", (Exception,), {})
    module.TimeoutExpired = type("TimeoutExpired", (Exception,), {})
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
def pypgx(tmp_path_factory):
    """The sidecar module, imported out of its source (as in
    tests/test_pypgx_structural_variants.py)."""
    root = tmp_path_factory.mktemp("pypgx_no_genotype_home")
    before = list(logging.root.handlers)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATA_DIR", str(root / "data"))
        mp.setenv("TMPDIR", str(root / "tmp"))
        mp.setenv("REFERENCE_DIR", str(root / "reference"))
        mp.setenv("PYPGX_PROGRESS_LOG", str(root / "pypgx_progress.log"))
        mp.setitem(sys.modules, "psutil", _fake_psutil())
        mp.setitem(sys.modules, "job_client", _fake_job_client())
        spec = importlib.util.spec_from_file_location(
            "zaropgx_pypgx_no_genotype_test", WRAPPER
        )
        module = importlib.util.module_from_spec(spec)
        mp.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        module.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        yield module
    for handler in list(logging.root.handlers):
        if handler not in before:
            logging.root.removeHandler(handler)
    for handler in getattr(module, "_log_handlers", []):
        handler.close()


# --------------------------------------------------------------------------
# VCFs
# --------------------------------------------------------------------------

# Inside PyPGx's GRCh38 regions (lexicon/gene-table.csv): CYP2D6 22:42116498-42155810,
# CYP2E1 10:133517362-133549123, GSTM1 1:109684816-109696745.
CYP2D6_38 = ("22", 42130692)
CYP2E1_38 = ("10", 133530000)
GSTM1_38 = ("1", 109690000)
# CYP2E1's GRCh37 region is 10:135330866-135362620.
CYP2E1_37 = ("10", 135340000)


def _vcf(path, records, prefix="chr", samples=("S",), form="plain"):
    """Write records (contig, pos, genotype[, ref]) as a VCF; return its path.

    form: "plain", "bgzip" (BGZF, no index) or "indexed" (BGZF and a .tbi), the
    forms /genotype can be handed and the one it prepares."""
    contigs = sorted({c for c, *_ in records}, key=str)
    lines = ["##fileformat=VCFv4.2"]
    lines += [f"##contig=<ID={prefix}{c}>" for c in contigs]
    lines.append('##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">')
    lines.append('##FORMAT=<ID=DP,Number=1,Type=Integer,Description="Depth">')
    lines.append(
        "\t".join(
            ["#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO"]
            + (["FORMAT", *samples] if samples else [])
        )
    )
    for contig, pos, genotype, *ref in sorted(records, key=lambda r: (str(r[0]), r[1])):
        ref = ref[0] if ref else "G"
        row = [f"{prefix}{contig}", str(pos), ".", ref, "A", "50", "PASS", "."]
        if samples:
            fmt, value = ("DP", "12") if genotype is None else ("GT", genotype)
            row += [fmt, value]
        lines.append("\t".join(row))
    text = "\n".join(lines) + "\n"
    plain = Path(path)
    plain.write_text(text, encoding="utf-8")
    if form == "plain":
        return str(plain)
    gz = str(plain) + ".gz"
    pysam.tabix_compress(str(plain), gz, force=True)
    if form == "indexed":
        pysam.tabix_index(gz, preset="vcf", force=True)
    return gz


REGIONS_38 = {
    "CYP2D6": "22:42116498-42155810",
    "CYP2E1": "10:133517362-133549123",
    "GSTM1": "1:109684816-109696745",
}

FORMS = ["plain", "bgzip", "indexed"]


# --------------------------------------------------------------------------
# PyPGx's regions
# --------------------------------------------------------------------------


def test_the_regions_are_pypgx_own_for_each_assembly(pypgx):
    grch38 = pypgx.load_gene_regions(GENE_TABLE, "GRCh38")
    grch37 = pypgx.load_gene_regions(GENE_TABLE, "GRCh37")
    assert grch38["CYP2D6"] == "22:42116498-42155810"
    assert grch37["CYP2D6"] == "22:42512500-42551883"
    assert grch38["GSTM1"] == "1:109684816-109696745"


def test_genes_pypgx_never_calls_from_a_vcf_are_left_to_say_so_themselves(pypgx):
    """GSTT1 and UGT2B17 have copy-number alleles only: PyPGx answers "No SNV/indel-
    based star alleles" for them whatever the VCF holds. The paralogs and the control
    genes are not called at all."""
    regions = pypgx.load_gene_regions(GENE_TABLE, "GRCh38")
    for gene in ("GSTT1", "UGT2B17", "CYP2D7", "CYP2A7", "CYP2B7", "EGFR", "VDR"):
        assert gene not in regions
    assert len(regions) == 85


def test_the_lexicon_copy_is_pypgx_0_27_0s_table():
    """gatk-api has no PyPGx and reads lexicon/gene-table.csv instead (Dockerfile.gatk-api
    copies it in). Measured 2026-10-20: identical to the table inside the pypgx image's
    PyPGx 0.27.0 but for MT-RNR1. A PyPGx bump must refresh the copy and this digest."""
    dockerfile = (REPO / "docker" / "pypgx" / "Dockerfile.pypgx").read_text()
    assert "pypgx==0.27.0" in dockerfile
    with open(GENE_TABLE, newline="", encoding="utf-8") as fh:
        rows = [
            (r["Gene"], r["Variants"], r["GRCh37Region"], r["GRCh38Region"])
            for r in csv.DictReader(fh)
        ]
    assert len(rows) == 92
    assert (
        hashlib.sha256(repr(rows).encode()).hexdigest()
        == "8599d92c84af46a5a791c2e0a1ee26994d20c14bd2312c1cc0a320c42f90ce77"
    )
    gatk_dockerfile = (REPO / "docker" / "gatk-api" / "Dockerfile.gatk-api").read_text()
    assert "COPY lexicon/gene-table.csv /app/pypgx-gene-table.csv" in gatk_dockerfile


def test_both_sidecars_spell_the_header_keys_alike():
    for key in ("UNCOVERED_GENES_HEADER_KEY", "COVERAGE_MEASURED_ON_HEADER_KEY"):
        values = [
            re.search(rf"^{key} = ['\"]([^'\"]+)['\"]", path.read_text(), re.M)
            for path in (WRAPPER, GATK_API)
        ]
        assert all(values), key
        assert values[0].group(1) == values[1].group(1), key


# --------------------------------------------------------------------------
# Which genes have a genotype
# --------------------------------------------------------------------------


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("prefix", ["chr", ""])
def test_a_gene_with_a_genotype_is_found_in_every_form(pypgx, tmp_path, form, prefix):
    vcf = _vcf(tmp_path / "in.vcf", [(*CYP2D6_38, "0/1")], prefix=prefix, form=form)
    assert pypgx.genes_without_genotypes(vcf, REGIONS_38) == ["CYP2E1", "GSTM1"]


@pytest.mark.parametrize("form", FORMS)
def test_a_no_call_is_not_a_genotype(pypgx, tmp_path, form):
    """./. is filled in by Beagle from its panel, not read from the sample."""
    vcf = _vcf(
        tmp_path / "in.vcf",
        [(*CYP2D6_38, "./."), (*CYP2E1_38, "./."), (*GSTM1_38, ".")],
        form=form,
    )
    assert pypgx.genes_without_genotypes(vcf, REGIONS_38) == [
        "CYP2D6",
        "CYP2E1",
        "GSTM1",
    ]


@pytest.mark.parametrize("form", FORMS)
def test_no_calls_are_told_apart_from_no_record(pypgx, tmp_path, form):
    """The option covers absence, not no-calls: /genotype needs to know which."""
    vcf = _vcf(
        tmp_path / "in.vcf",
        [(*CYP2D6_38, "0/1"), (*CYP2E1_38, "./."), (*CYP2E1_38[:1], 133530100, "./.")],
        form=form,
    )
    assert pypgx.genotype_evidence(vcf, REGIONS_38) == {
        "CYP2D6": "genotype",
        "CYP2E1": "no-calls",
        "GSTM1": "no record",
    }


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("genotype", ["0/0", "0|0", "./1", "1"])
def test_reference_half_and_haploid_calls_are_genotypes(
    pypgx, tmp_path, form, genotype
):
    vcf = _vcf(tmp_path / "in.vcf", [(*CYP2E1_38, genotype)], form=form)
    assert "CYP2E1" not in pypgx.genes_without_genotypes(vcf, REGIONS_38)


@pytest.mark.parametrize("form", FORMS)
def test_a_record_without_gt_or_a_vcf_without_samples_is_no_genotype(
    pypgx, tmp_path, form
):
    no_gt = _vcf(tmp_path / "a.vcf", [(*CYP2E1_38, None)], form=form)
    assert "CYP2E1" in pypgx.genes_without_genotypes(no_gt, REGIONS_38)
    sites = _vcf(tmp_path / "b.vcf", [(*CYP2E1_38, "0/1")], samples=(), form=form)
    assert pypgx.genes_without_genotypes(sites, REGIONS_38) == sorted(REGIONS_38)


def test_only_the_first_sample_counts(pypgx, tmp_path):
    """PyPGx's result is read from its first row (_first_pypgx_row), the first sample."""
    path = tmp_path / "two.vcf"
    text = _vcf_text_two_samples(*CYP2E1_38, "./.", "0/1")
    path.write_text(text, encoding="utf-8")
    assert "CYP2E1" in pypgx.genes_without_genotypes(str(path), REGIONS_38)


def _vcf_text_two_samples(contig, pos, first, second):
    return (
        "##fileformat=VCFv4.2\n"
        f"##contig=<ID=chr{contig}>\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tA\tB\n"
        f"chr{contig}\t{pos}\t.\tG\tA\t50\tPASS\t.\tGT\t{first}\t{second}\n"
    )


@pytest.mark.parametrize("form", FORMS)
def test_grch37_is_read_against_grch37_regions(pypgx, tmp_path, form):
    """A b37 VCF ("10", not "chr10") reaching /genotype unlifted, as hg19."""
    vcf = _vcf(tmp_path / "in.vcf", [(*CYP2E1_37, "0/1")], prefix="", form=form)
    grch37 = pypgx.load_gene_regions(GENE_TABLE, "GRCh37")
    grch38 = pypgx.load_gene_regions(GENE_TABLE, "GRCh38")
    assert "CYP2E1" not in pypgx.genes_without_genotypes(
        vcf, {"CYP2E1": grch37["CYP2E1"]}
    )
    # The same position is outside CYP2E1 on GRCh38.
    assert pypgx.genes_without_genotypes(vcf, {"CYP2E1": grch38["CYP2E1"]}) == [
        "CYP2E1"
    ]


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("prefix", ["chr", ""])
def test_gstt1_is_looked_for_on_its_alt_contig(pypgx, tmp_path, form, prefix):
    """PyPGx's GRCh38 GSTT1 is on 22_KI270879v1_alt; a GATK VCF names it
    chr22_KI270879v1_alt. (Left out of /genotype's regions, as PyPGx calls GSTT1
    from copy number only; the gVCF lane measures it, see gatk-api.)"""
    region = {"GSTT1": "22_KI270879v1_alt:267307-281486"}
    vcf = _vcf(
        tmp_path / "in.vcf",
        [("22_KI270879v1_alt", 270000, "0/1")],
        prefix=prefix,
        form=form,
    )
    assert pypgx.genes_without_genotypes(vcf, region) == []
    primary = _vcf(tmp_path / "p.vcf", [("22", 270000, "0/1")], form=form)
    assert pypgx.genes_without_genotypes(primary, region) == ["GSTT1"]


@pytest.mark.parametrize("form", FORMS)
def test_overlap_is_decided_as_pysams_fetch_decides_it(pypgx, tmp_path, form):
    """PyPGx slices with fetch(contig, start, end), start 0-based: a deletion starting
    before the region and reaching into it is in; a record ending at its start is not.
    """
    start = 133517362  # CYP2E1's GRCh38 start, as the table gives it
    reaching = _vcf(tmp_path / "a.vcf", [("10", start - 2, "0/1", "GGGG")], form=form)
    assert "CYP2E1" not in pypgx.genes_without_genotypes(reaching, REGIONS_38)
    short = _vcf(tmp_path / "b.vcf", [("10", start - 2, "0/1", "GGG")], form=form)
    assert "CYP2E1" in pypgx.genes_without_genotypes(short, REGIONS_38)


# --------------------------------------------------------------------------
# What a gene without a call says
# --------------------------------------------------------------------------


def test_the_note_offers_the_option_except_where_it_would_call_a_deletion(pypgx):
    note = pypgx.no_genotype_note("CYP2E1")
    assert note.startswith("No genotype in the VCF over this gene")
    assert "reference sequence" in note
    assert '"Assume absent sites = reference"' in note
    deleted = pypgx.no_genotype_note("GSTM1")
    assert deleted.startswith("No genotype in the VCF over this gene")
    assert "both copies deleted (common for GSTM1)" in deleted
    assert "Assume absent" not in deleted
    # The option does not cover no-calls, so it is not offered for them.
    for gene in ("CYP2E1", "GSTM1"):
        no_calls = pypgx.no_genotype_note(gene, no_calls=True)
        assert no_calls.startswith("Only no-calls (./.) in the VCF over this gene")
        assert "reference panel" in no_calls and "Assume absent" not in no_calls
    assert "deleted" in pypgx.no_genotype_note("GSTM1", no_calls=True)


def test_a_gvcfs_no_reads_note_names_the_gvcf(pypgx, tmp_path):
    assert (
        pypgx.no_reads_note("CYP2E1", "gVCF") == "No reads over this gene in the gVCF"
    )
    assert pypgx.no_reads_note("CYP2E1") == "No reads over this gene in the alignment"
    measured = tmp_path / "m.vcf"
    measured.write_text(
        "##fileformat=VCFv4.2\n##ZaroPGx_coverage_measured_on=gVCF\n#CHROM\n"
    )
    assert pypgx.coverage_measured_on(str(measured)) == "gVCF"
    plain = tmp_path / "p.vcf"
    plain.write_text("##fileformat=VCFv4.2\n#CHROM\n")
    assert pypgx.coverage_measured_on(str(plain)) == "alignment"


def test_the_step_counts_genes_without_a_genotype_apart(pypgx):
    """And genotyped means a diplotype came back: PyPGx's own no-call for GSTT1 (no
    small-variant alleles) is a successful result, and the demo VCF read "66 of 66
    genes genotyped" of 40 calls."""
    panel = ["CYP2D6", "CYP2E1", "GSTM1", "GSTT1", "NAT1"]
    no_genotype = {g: {"success": True, "diplotype": None} for g in ("CYP2E1", "GSTM1")}
    results = dict(no_genotype)
    results["CYP2D6"] = {"success": True, "diplotype": "*1/*1"}
    results["GSTT1"] = {"success": True, "diplotype": None}
    results["NAT1"] = {"success": False, "error": "boom"}
    assert pypgx.step_gene_counts(panel, results, {}, no_genotype) == {
        "total_genes": 5,
        "successful_genes": 1,
        "not_sequenced_genes": 0,
        "no_genotype_genes": 2,
        "failed_genes": 1,
    }


# --------------------------------------------------------------------------
# /genotype
# --------------------------------------------------------------------------


@pytest.fixture
def genotype(pypgx, monkeypatch):
    """POST /genotype with PyPGx itself faked: records what reaches the batches.

    bgzip and tabix are not on the test host; pysam's own do the same job."""
    seen = {"genes": [], "vcf": None, "prepared": []}

    async def fake_batch(genes, vcf_path, job_dir, reference_genome, **kwargs):
        seen["genes"].extend(genes)
        seen["vcf"] = vcf_path
        return {g: {"success": True, "diplotype": "*1/*1"} for g in genes}

    def fake_prepare(path):
        seen["prepared"].append(path)
        gz = path if path.endswith(".gz") else f"{path}.gz"
        pysam.tabix_compress(path, gz, force=True)
        pysam.tabix_index(gz, preset="vcf", force=True)
        return gz

    monkeypatch.setattr(pypgx, "process_gene_batch_parallel", fake_batch)
    monkeypatch.setattr(pypgx, "prepare_vcf_for_pypgx", fake_prepare)
    monkeypatch.setattr(
        pypgx,
        "pypgx_gene_regions",
        lambda assembly: pypgx.load_gene_regions(GENE_TABLE, assembly),
    )

    def post(records, header="", **data):
        path = Path(pypgx.TEMP_DIR) / "upload.vcf"
        _vcf(path, records)
        body = path.read_text(encoding="utf-8")
        if header:
            body = body.replace(
                "##fileformat=VCFv4.2\n", "##fileformat=VCFv4.2\n" + header
            )
        fields = {"genes": "CYP2D6,CYP2E1,GSTM1", "reference_genome": "hg38", **data}
        resp = TestClient(pypgx.app).post(
            "/genotype", files={"file": ("in.vcf", body, "text/plain")}, data=fields
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    return seen, post


def test_with_the_option_off_a_gene_without_a_genotype_is_not_called(genotype):
    seen, post = genotype
    body = post([(*CYP2D6_38, "0/0")], absent_to_ref="false")

    assert seen["genes"] == ["CYP2D6"]
    assert body["success"] is True
    for gene in ("CYP2E1", "GSTM1"):
        result = body["results"][gene]
        assert result["success"] is True and result["diplotype"] is None
        assert result["details"]["note"].startswith(
            "No genotype in the VCF over this gene"
        )
    assert "deleted" in body["results"]["GSTM1"]["details"]["note"]
    # The batches read the file the genotypes were looked for in.
    assert seen["vcf"].endswith(".gz") and seen["prepared"]


def test_without_the_field_the_option_is_off(genotype):
    seen, post = genotype
    post([(*CYP2D6_38, "0/1")])
    assert seen["genes"] == ["CYP2D6"]


def test_with_the_option_on_absent_genes_are_called_as_before(genotype):
    """The uploader said the file lists every variant: absence is reference."""
    seen, post = genotype
    post([(*CYP2D6_38, "0/1")], absent_to_ref="true")
    assert sorted(seen["genes"]) == ["CYP2D6", "CYP2E1", "GSTM1"]


def test_with_the_option_on_a_gene_of_no_calls_is_still_not_called(genotype):
    """A ./. is not an absence: the option says nothing about it, and PyPGx would fill
    it in from its panel (NAT2 *5/*5 from 37 ./. records on the demo VCF)."""
    seen, post = genotype
    body = post([(*CYP2D6_38, "0/1"), (*CYP2E1_38, "./.")], absent_to_ref="true")
    assert sorted(seen["genes"]) == ["CYP2D6", "GSTM1"]
    note = body["results"]["CYP2E1"]["details"]["note"]
    assert note.startswith("Only no-calls (./.) in the VCF over this gene")


@pytest.mark.parametrize(
    "header",
    [
        "##ZaroPGx_uncovered_genes=.\n",
        "##ZaroPGx_uncovered_genes=.\n##ZaroPGx_coverage_measured_on=gVCF\n",
    ],
)
def test_a_vcf_that_says_what_was_covered_is_not_second_guessed(genotype, header):
    """An alignment's VCF and a gVCF's: absence there is reference where reads were."""
    seen, post = genotype
    post([(*CYP2D6_38, "0/1")], header=header, absent_to_ref="false")
    assert sorted(seen["genes"]) == ["CYP2D6", "CYP2E1", "GSTM1"]
    assert seen["prepared"] == []


def test_a_gvcfs_uncovered_gene_says_gvcf(genotype):
    seen, post = genotype
    body = post(
        [(*CYP2D6_38, "0/1")],
        header="##ZaroPGx_uncovered_genes=CYP2E1\n##ZaroPGx_coverage_measured_on=gVCF\n",
    )
    assert sorted(seen["genes"]) == ["CYP2D6", "GSTM1"]
    note = body["results"]["CYP2E1"]["details"]["note"]
    assert note == "No reads over this gene in the gVCF"


# --------------------------------------------------------------------------
# main.nf: the run's effective flag reaches /genotype
# --------------------------------------------------------------------------


def _process(name):
    src = MAIN_NF.read_text(encoding="utf-8")
    start = src.index(f"process {name} {{")
    return src[start : src.index("\n}\n", start)]


def test_pypgx_is_sent_the_effective_flag_always():
    """Always sent, even as 'false', so the field decides; and the effective value,
    forced 'false' for alignments and gVCFs, never params.pharmcat_absent_to_ref."""
    body = _process("PyPGxGenotypeAll")
    assert re.search(r"^\s*val absent_to_ref$", body, re.M)
    assert "-F absent_to_ref=!{absent_to_ref}" in body
    assert "params.pharmcat_absent_to_ref" not in body
    src = MAIN_NF.read_text(encoding="utf-8")
    wf = src[src.index("\nworkflow {") :]
    call = re.search(r"PyPGxGenotypeAll\(\s*(.*?)\s*\)\n", wf, re.S)
    args = [a.strip() for a in call.group(1).split(",")]
    inputs = re.findall(r"^\s*(?:path|val) (\w+)", body[: body.index("output:")], re.M)
    assert len(args) == len(inputs)
    assert args[inputs.index("absent_to_ref")] == "pharmcat_absent_ch"


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------

NO_CALLS = {
    "results": {
        "CYP2E1": {
            "success": True,
            "diplotype": None,
            "details": {
                "note": "No genotype in the VCF over this gene, which PyPGx would "
                "read as the reference sequence. If the file is a whole-genome "
                'variant list, run it again with "Assume absent sites = reference" on'
            },
        },
        "NAT1": {
            "success": True,
            "diplotype": None,
            "details": {
                "note": "No genotype in the VCF over this gene, which PyPGx would "
                "read as the reference sequence. If the file is a whole-genome "
                'variant list, run it again with "Assume absent sites = reference" on'
            },
        },
        "GSTM1": {
            "success": True,
            "diplotype": None,
            "details": {"note": "No genotype in the VCF over this gene: GSTM1 note"},
        },
        "ABCB1": {"success": True, "diplotype": "*1/*2", "details": {}},
    }
}


def _report(monkeypatch, tmp_path, lane, genes=(), pypgx=NO_CALLS):
    from app.reports import generator as generator_module

    for key in (
        "write_pdf",
        "write_html",
        "write_interactive_html",
        "write_json",
        "write_tsv",
        "write_workflow_svg",
        "write_workflow_png",
        "show_pharmcat_html_report",
        "show_pharmcat_json_report",
        "show_pharmcat_tsv_report",
    ):
        monkeypatch.setitem(generator_module.REPORT_CONFIG, key, key == lane)
    (tmp_path / "pypgx_result.json").write_text(json.dumps(pypgx))
    result = generator_module.generate_report(
        {"data": {"genes": list(genes), "drugRecommendations": []}},
        str(tmp_path),
        {"id": "p1"},
        job_id=None,
    )
    rows = {g["gene"]: g for g in result["processed_data"]["genes"] if g.get("gene")}
    key = "html_path" if lane == "write_html" else "interactive_html_path"
    page = (tmp_path / result[key].split("/")[-1]).read_text(encoding="utf-8")
    return rows, page


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).replace("&#34;", '"')


def test_the_pdf_names_each_reason_once(monkeypatch, tmp_path):
    rows, page = _report(monkeypatch, tmp_path, "write_html")
    assert rows["CYP2E1"]["no_call_note"].startswith("No genotype in the VCF")
    assert "no_call_note" not in rows["ABCB1"]
    section = page[page.index('<div class="uncalled-genes">') :]
    section = section[: section.index("</div>")]
    listed = _text(section)
    # Two reasons, two footnotes, numbered in gene order; the shared one once.
    assert listed.count("which PyPGx would read as the reference sequence") == 1
    assert re.search(r"CYP2E1 \(P\)<sup>1</sup>", section)
    assert re.search(r"GSTM1 \(P\)<sup>2</sup>", section)
    assert re.search(r"NAT1 \(P\)<sup>1</sup>", section)
    assert re.search(
        r"<sup>1</sup> No genotype in the VCF over this gene, which", section
    )
    assert "<sup>2</sup> No genotype in the VCF over this gene: GSTM1 note." in section
    assert '"Assume absent sites = reference" on.' in listed


def test_the_interactive_report_gives_the_reason_in_the_row(monkeypatch, tmp_path):
    _, page = _report(monkeypatch, tmp_path, "write_interactive_html")
    for tr in re.findall(r"<tr>(.*?)</tr>", page, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if cells and _text(cells[0]).strip() == "CYP2E1":
            assert "which PyPGx would read as the reference sequence" in _text(cells[1])
            break
    else:
        raise AssertionError("no CYP2E1 row")


def test_pharmcats_uncalled_cyp2d6_gets_pypgxs_reason(monkeypatch, tmp_path):
    """PharmCAT takes CYP2D6 from PyPGx alone: its uncalled row gets PyPGx's reason.
    A row PharmCAT called keeps none."""
    note = "No genotype in the VCF over this gene, which PyPGx would read as the ref"
    pypgx = {
        "results": {
            "CYP2D6": {"success": True, "diplotype": None, "details": {"note": note}},
            "CYP2C19": {"success": True, "diplotype": None, "details": {"note": note}},
        }
    }
    genes = [
        {"gene": "CYP2D6", "diplotype": "Unknown/Unknown", "phenotype": ""},
        {"gene": "CYP2C19", "diplotype": "*1/*2", "call_source": "MATCHER"},
    ]
    rows, _ = _report(monkeypatch, tmp_path, "write_html", genes, pypgx)
    assert rows["CYP2D6"]["no_call_note"] == note
    assert "no_call_note" not in rows["CYP2C19"]


def test_the_workflow_diagram_counts_them_apart():
    from app.reports.generator import pypgx_gene_counts

    output = {
        "total_genes": 66,
        "successful_genes": 4,
        "not_sequenced_genes": 0,
        "no_genotype_genes": 62,
        "failed_genes": 0,
    }
    assert pypgx_gene_counts(output) == (4, 66, 0, 62, 0)


def test_gzip_header_helpers_read_the_same_line(pypgx, tmp_path):
    """The two header readers /genotype calls, on the bgzipped form gatk-api writes."""
    path = tmp_path / "h.vcf.gz"
    with gzip.open(path, "wt") as fh:
        fh.write(
            "##fileformat=VCFv4.2\n##ZaroPGx_uncovered_genes=NAT1\n"
            "##ZaroPGx_coverage_measured_on=gVCF\n#CHROM\n"
        )
    assert pypgx.uncovered_genes_from_vcf(str(path)) == {"NAT1"}
    assert pypgx.coverage_measured_on(str(path)) == "gVCF"
