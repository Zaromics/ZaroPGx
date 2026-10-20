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

Now /genotype reads what the file holds at each gene's own allele-defining positions
(PyPGx's allele table), not anywhere in its region: UGT1A4's and UGT1A6's regions span
UGT1A1's exon 1, and UGT1A1's records alone had made them *1/*1 and Reference/
Reference. Given a VCF without coverage evidence and the job's absent_to_ref off, a
gene with no genotype at any of its positions gets no call, and a gene with some gets a
call that says how many of the rest PyPGx read as the reference. A ./. does not count:
PyPGx hands it to Beagle, which fills it in (measured: the demo's 37 NAT2 records, all
0/0, set to ./. gave NAT2 *5/*5, against *12/*12), so no-calls are dropped from the
file PyPGx reads, in every lane. main.nf sends the run's effective flag. A gVCF's
converted VCF keeps no reference blocks, so gatk-api /gvcf-to-vcf writes the
uncovered-genes line from the blocks (tests in tests/test_gvcf_to_vcf_endpoint.py),
and its genes are called as an alignment's are.
"""

from __future__ import annotations

import ast
import csv
import gzip
import hashlib
import importlib.util
import json
import logging
import re
import shutil
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
ALLELE_TABLE = REPO / "lexicon" / "allele-table.csv"
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

# Allele-defining positions in PyPGx 0.27's GRCh38 table (lexicon/allele-table.csv):
# CYP2D6 *4 (22-42128945-C-T) and 22-42126611-C-G, CYP2E1 10-133526101-G-C, GSTM1 *B
# (1-109690516-G-C, its only one), UGT1A1 2-233759924-C-T. CYP2E1's GRCh37 position
# for the same variant is 10-135339605. 10:133530000 is inside CYP2E1's region and is
# none of its positions.
CYP2D6_38 = ("22", 42128945)
CYP2D6_38_B = ("22", 42126611)
CYP2E1_38 = ("10", 133526101)
CYP2E1_REGION_ONLY = ("10", 133530000)
GSTM1_38 = ("1", 109690516)
UGT1A1_38 = ("2", 233759924)
CYP2E1_37 = ("10", 135339605)


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


FORMS = ["plain", "bgzip", "indexed"]

# PyPGx's step on the demo VCF (app/static/demo/pharmcat.example.vcf), genes=ALL,
# input_type vcf, the option off, measured in the shipped image on 2026-10-20: genes
# called, of them some positions read as reference, and genes with no genotype.
DEMO_CALLED = 2
DEMO_PARTIAL = 1
DEMO_NO_GENOTYPE = 62


@pytest.fixture(scope="module")
def positions38(pypgx):
    return pypgx.load_gene_positions(GENE_TABLE, ALLELE_TABLE, "GRCh38")


@pytest.fixture(scope="module")
def three(positions38):
    return {g: positions38[g] for g in ("CYP2D6", "CYP2E1", "GSTM1")}


# --------------------------------------------------------------------------
# PyPGx's genes and positions
# --------------------------------------------------------------------------


def test_the_regions_are_pypgx_own_for_each_assembly(pypgx):
    grch38 = pypgx.load_gene_regions(GENE_TABLE, "GRCh38")
    grch37 = pypgx.load_gene_regions(GENE_TABLE, "GRCh37")
    assert grch38["CYP2D6"] == "22:42116498-42155810"
    assert grch37["CYP2D6"] == "22:42512500-42551883"
    assert grch38["GSTM1"] == "1:109684816-109696745"


def test_genes_pypgx_never_calls_from_a_vcf_are_left_to_say_so_themselves(
    pypgx, positions38
):
    """GSTT1 and UGT2B17 have copy-number alleles only: PyPGx answers "No SNV/indel-
    based star alleles" for them whatever the VCF holds. The paralogs and the control
    genes are not called at all. Every other gene has a defining variant."""
    regions = pypgx.load_gene_regions(GENE_TABLE, "GRCh38")
    for gene in ("GSTT1", "UGT2B17", "CYP2D7", "CYP2A7", "CYP2B7", "EGFR", "VDR"):
        assert gene not in regions and gene not in positions38
    assert len(regions) == 85
    assert set(positions38) == set(regions)


def test_the_positions_are_the_defining_variants_ref_spans(pypgx, positions38):
    """0-based half-open, one per position: a deletion's span is its REF."""
    assert positions38["GSTM1"] == [("1", 109690515, 109690516)]
    assert positions38["UGT1A6"] == [("2", 233693630, 233693631)]
    # CYP2D6 *?: 22-42128198-CTCAG-C covers five bases.
    assert ("22", 42128197, 42128202) in positions38["CYP2D6"]
    # UGT1A4's twelve are its own, none of UGT1A1's, though its region spans UGT1A1.
    ugt1a1 = set(positions38["UGT1A1"])
    assert len(positions38["UGT1A4"]) == 12
    assert not ugt1a1 & set(positions38["UGT1A4"])
    # IFNL4's one position is also IFNL3's (rs12979860): data at it is data for both.
    assert set(positions38["IFNL4"]) <= set(positions38["IFNL3"])
    grch37 = pypgx.load_gene_positions(GENE_TABLE, ALLELE_TABLE, "GRCh37")
    assert ("10", 135339604, 135339605) in grch37["CYP2E1"]


def test_the_lexicon_copies_are_pypgx_0_27_0s_tables():
    """gatk-api has no PyPGx and reads lexicon/gene-table.csv instead (Dockerfile.gatk-api
    copies it in); the tests read lexicon/allele-table.csv. Measured 2026-10-20: both
    identical to the tables inside the pypgx image's PyPGx 0.27.0 but for MT-RNR1 (the
    allele table after refreshing three rows). A PyPGx bump must refresh the copies and
    these digests."""
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
    with open(ALLELE_TABLE, newline="", encoding="utf-8-sig") as fh:
        rows = [
            (r["Gene"], r["StarAllele"], r["GRCh37Core"], r["GRCh38Core"])
            for r in csv.DictReader(fh)
        ]
    assert len(rows) == 1338
    assert (
        hashlib.sha256(repr(rows).encode()).hexdigest()
        == "963e859276edce6e49a0448c6d4e567a2bf5daba7ec20ec3df086cdac172509d"
    )
    gatk_dockerfile = (REPO / "docker" / "gatk-api" / "Dockerfile.gatk-api").read_text()
    assert "COPY lexicon/gene-table.csv /app/pypgx-gene-table.csv" in gatk_dockerfile


def test_both_sidecars_spell_the_header_keys_alike():
    for key in (
        "UNCOVERED_GENES_HEADER_KEY",
        "COVERAGE_MEASURED_ON_HEADER_KEY",
        "OFF_REFERENCE_HEADER_KEY",
        "POSITIONS_WITHOUT_READS_HEADER_KEY",
    ):
        values = [
            re.search(rf"^{key} = ['\"]([^'\"]+)['\"]", path.read_text(), re.M)
            for path in (WRAPPER, GATK_API)
        ]
        assert all(values), key
        assert values[0].group(1) == values[1].group(1), key


# --------------------------------------------------------------------------
# What the file holds at each gene's positions
# --------------------------------------------------------------------------


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("prefix", ["chr", ""])
def test_a_gene_with_a_genotype_is_found_in_every_form(
    pypgx, tmp_path, three, form, prefix
):
    vcf = _vcf(tmp_path / "in.vcf", [(*CYP2D6_38, "0/1")], prefix=prefix, form=form)
    assert pypgx.genes_without_genotypes(vcf, three) == ["CYP2E1", "GSTM1"]


@pytest.mark.parametrize("form", FORMS)
def test_a_record_in_the_region_but_at_none_of_its_positions_is_no_data(
    pypgx, tmp_path, three, form
):
    vcf = _vcf(tmp_path / "in.vcf", [(*CYP2E1_REGION_ONLY, "0/1")], form=form)
    assert "CYP2E1" in pypgx.genes_without_genotypes(vcf, three)


@pytest.mark.parametrize("form", FORMS)
def test_a_neighbours_records_do_not_genotype_a_gene(
    pypgx, tmp_path, positions38, form
):
    """Found on the demo VCF: UGT1A1's promoter and exon-1 records made UGT1A4 *1/*1
    and UGT1A6 Reference/Reference through their regions alone."""
    genes = {g: positions38[g] for g in ("UGT1A1", "UGT1A4", "UGT1A6")}
    vcf = _vcf(tmp_path / "in.vcf", [(*UGT1A1_38, "0/0")], form=form)
    assert pypgx.genes_without_genotypes(vcf, genes) == ["UGT1A4", "UGT1A6"]


@pytest.mark.parametrize("form", FORMS)
def test_the_positions_are_counted(pypgx, tmp_path, three, form):
    vcf = _vcf(
        tmp_path / "in.vcf",
        [(*CYP2D6_38, "0/1"), (*CYP2D6_38_B, "./."), (*GSTM1_38, ".")],
        form=form,
    )
    counts = pypgx.position_evidence(vcf, three)
    total = len(three["CYP2D6"])
    assert counts["CYP2D6"] == {
        "positions": total,
        "genotyped": 1,
        "no_calls": 1,
        "no_reads": 0,
    }
    assert counts["CYP2E1"] == {
        "positions": 7,
        "genotyped": 0,
        "no_calls": 0,
        "no_reads": 0,
    }
    assert counts["GSTM1"] == {
        "positions": 1,
        "genotyped": 0,
        "no_calls": 1,
        "no_reads": 0,
    }


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("genotype", ["0/0", "0|0", "./1", "1"])
def test_reference_half_and_haploid_calls_are_genotypes(
    pypgx, tmp_path, three, form, genotype
):
    vcf = _vcf(tmp_path / "in.vcf", [(*CYP2E1_38, genotype)], form=form)
    assert "CYP2E1" not in pypgx.genes_without_genotypes(vcf, three)


@pytest.mark.parametrize("form", FORMS)
def test_a_record_without_gt_or_a_vcf_without_samples_is_no_genotype(
    pypgx, tmp_path, three, form
):
    no_gt = _vcf(tmp_path / "a.vcf", [(*CYP2E1_38, None)], form=form)
    assert "CYP2E1" in pypgx.genes_without_genotypes(no_gt, three)
    sites = _vcf(tmp_path / "b.vcf", [(*CYP2E1_38, "0/1")], samples=(), form=form)
    assert pypgx.genes_without_genotypes(sites, three) == sorted(three)


def test_only_the_first_sample_counts(pypgx, tmp_path, three):
    """PyPGx's result is read from its first row (_first_pypgx_row), the first sample."""
    path = tmp_path / "two.vcf"
    path.write_text(_vcf_text_two_samples(*CYP2E1_38, "./.", "0/1"), encoding="utf-8")
    assert "CYP2E1" in pypgx.genes_without_genotypes(str(path), three)


def _vcf_text_two_samples(contig, pos, first, second):
    return (
        "##fileformat=VCFv4.2\n"
        f"##contig=<ID=chr{contig}>\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tA\tB\n"
        f"chr{contig}\t{pos}\t.\tG\tA\t50\tPASS\t.\tGT\t{first}\t{second}\n"
    )


@pytest.mark.parametrize("form", FORMS)
def test_grch37_is_read_at_grch37_positions(pypgx, tmp_path, positions38, form):
    """A b37 VCF ("10", not "chr10") reaching /genotype unlifted, as hg19."""
    vcf = _vcf(tmp_path / "in.vcf", [(*CYP2E1_37, "0/1")], prefix="", form=form)
    grch37 = pypgx.load_gene_positions(GENE_TABLE, ALLELE_TABLE, "GRCh37")
    assert pypgx.genes_without_genotypes(vcf, {"CYP2E1": grch37["CYP2E1"]}) == []
    # The same position is none of CYP2E1's on GRCh38.
    assert pypgx.genes_without_genotypes(vcf, {"CYP2E1": positions38["CYP2E1"]}) == [
        "CYP2E1"
    ]


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("prefix", ["chr", ""])
def test_an_alt_contig_matches_under_either_naming(pypgx, tmp_path, form, prefix):
    """PyPGx's GRCh38 GSTT1 is on 22_KI270879v1_alt; a GATK VCF names it
    chr22_KI270879v1_alt. (GSTT1 itself is left to PyPGx, which calls it from copy
    number only; the gVCF and alignment lanes measure it.)"""
    position = {"GSTT1": [("22_KI270879v1_alt", 269999, 270000)]}
    vcf = _vcf(
        tmp_path / "in.vcf",
        [("22_KI270879v1_alt", 270000, "0/1")],
        prefix=prefix,
        form=form,
    )
    assert pypgx.genes_without_genotypes(vcf, position) == []
    primary = _vcf(tmp_path / "p.vcf", [("22", 270000, "0/1")], form=form)
    assert pypgx.genes_without_genotypes(primary, position) == ["GSTT1"]


@pytest.mark.parametrize("form", FORMS)
def test_overlap_is_decided_as_pysams_fetch_decides_it(pypgx, tmp_path, three, form):
    """A deletion starting before a position and covering it is at it; one ending just
    before it is not."""
    pos = CYP2E1_38[1]
    reaching = _vcf(tmp_path / "a.vcf", [("10", pos - 2, "0/1", "GGG")], form=form)
    assert "CYP2E1" not in pypgx.genes_without_genotypes(reaching, three)
    short = _vcf(tmp_path / "b.vcf", [("10", pos - 2, "0/1", "GG")], form=form)
    assert "CYP2E1" in pypgx.genes_without_genotypes(short, three)


def test_a_vcf_without_genotypes_keeps_its_records(pypgx, tmp_path):
    """drop_no_calls runs bcftools only where there are genotypes to test."""
    sites = _vcf(tmp_path / "s.vcf", [(*CYP2E1_38, "0/1")], samples=(), form="indexed")
    assert pypgx.drop_no_calls(sites) == sites


@pytest.mark.skipif(shutil.which("bcftools") is None, reason="bcftools not installed")
def test_no_calls_are_dropped_and_half_calls_kept(pypgx, tmp_path, three):
    vcf = _vcf(
        tmp_path / "in.vcf",
        [
            (*CYP2D6_38, "./."),
            (*CYP2D6_38_B, ".|."),
            (*CYP2E1_38, "./1"),
            (*GSTM1_38, "."),
        ],
        form="indexed",
    )
    kept = pypgx.drop_no_calls(vcf)
    with pysam.VariantFile(kept) as handle:
        assert [r.pos for r in handle] == [CYP2E1_38[1]]


# --------------------------------------------------------------------------
# What a gene without a call, or with part of one, says
# --------------------------------------------------------------------------


def test_the_note_offers_the_option_except_where_it_would_not_help(pypgx):
    note = pypgx.no_genotype_note("CYP2E1", 7)
    assert note.startswith(
        "No genotype in the VCF at any of PyPGx's positions for this gene"
    )
    assert "reference sequence" in note
    assert '"Assume absent sites = reference"' in note
    deleted = pypgx.no_genotype_note("GSTM1", 1)
    assert deleted.startswith(
        "No genotype in the VCF at any of PyPGx's positions for this gene"
    )
    assert "both copies deleted (common for GSTM1)" in deleted
    assert "Assume absent" not in deleted
    # The option does not cover no-calls: where every position is one, no offer.
    for gene, n in (("CYP2E1", 7), ("GSTM1", 1)):
        no_calls = pypgx.no_genotype_note(gene, n, no_calls=n)
        assert no_calls.startswith(
            "Only no-calls (./.) in the VCF at PyPGx's positions for this gene"
        )
        assert "Assume absent" not in no_calls
    assert "deleted" in pypgx.no_genotype_note("GSTM1", 1, no_calls=1)
    # Some no-calls, some absent: the absent ones are the option's to vouch for.
    assert "Assume absent" in pypgx.no_genotype_note("CYP2E1", 7, no_calls=3)


def test_a_partial_call_says_how_many_positions_were_read_as_reference(pypgx):
    assert pypgx.positions_read_as_reference_note(120, 121) == (
        "120 of 121 positions had no genotype in the file and were read as reference"
    )


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


def test_a_gene_off_the_reference_is_not_called_a_possible_deletion(pypgx, tmp_path):
    """GSTT1 on a GRCh38 without ALT contigs: no reads because none could map there."""
    line = pypgx.genes_off_reference_header_line({"GSTT1": "22_KI270879v1_alt"})
    assert line == "##ZaroPGx_genes_off_reference=GSTT1:22_KI270879v1_alt"
    assert pypgx.genes_off_reference_header_line({}).endswith("=.")
    path = tmp_path / "h.vcf"
    path.write_text(f"##fileformat=VCFv4.2\n{line}\n#CHROM\n")
    assert pypgx.genes_off_reference_from_vcf(str(path)) == {
        "GSTT1": "22_KI270879v1_alt"
    }
    note = pypgx.off_reference_note("22_KI270879v1_alt", "gVCF")
    assert note.startswith("The gVCF's reference has no 22_KI270879v1_alt contig")
    assert "deleted" not in note


def test_the_alignment_lane_names_the_genes_off_its_reference(pypgx, monkeypatch):
    rows = [("22_KI270879v1_alt", "GSTT1"), ("22", "CYP2D6"), ("1", "GSTM1")]

    class _Frame:
        def __getitem__(self, columns):
            return self

        def itertuples(self, index=False):
            return iter(rows)

    utils = types.ModuleType("pypgx.api.utils")
    utils.create_regions_bed = lambda assembly, add_chr_prefix: types.SimpleNamespace(
        gr=types.SimpleNamespace(df=_Frame())
    )
    api = types.ModuleType("pypgx.api")
    api.utils = utils
    root = types.ModuleType("pypgx")
    root.api = api
    for name, module in (
        ("pypgx", root),
        ("pypgx.api", api),
        ("pypgx.api.utils", utils),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    no_alt = ["chr1", "chr22"]
    assert pypgx.genes_off_reference(no_alt, "GRCh38") == {"GSTT1": "22_KI270879v1_alt"}
    assert pypgx.genes_off_reference(no_alt + ["chr22_KI270879v1_alt"], "GRCh38") == {}


def test_the_step_counts_genes_without_a_genotype_apart(pypgx):
    """And genotyped means a diplotype came back: PyPGx's own no-call for GSTT1 (no
    small-variant alleles) is a successful result, and the demo VCF read "66 of 66
    genes genotyped" of 40 calls."""
    panel = ["CYP2D6", "CYP2E1", "GSTM1", "GSTT1", "NAT1", "NAT2"]
    no_genotype = {g: {"success": True, "diplotype": None} for g in ("CYP2E1", "GSTM1")}
    results = dict(no_genotype)
    results["CYP2D6"] = {
        "success": True,
        "diplotype": "*1/*1",
        "details": {"positions": 121, "positions_without_genotype": 120},
    }
    results["NAT2"] = {"success": True, "diplotype": "*4/*4", "details": {}}
    results["GSTT1"] = {"success": True, "diplotype": None}
    results["NAT1"] = {"success": False, "error": "boom"}
    assert pypgx.step_gene_counts(panel, results, {}, no_genotype) == {
        "total_genes": 6,
        "successful_genes": 2,
        "not_sequenced_genes": 0,
        "no_genotype_genes": 2,
        "partially_genotyped_genes": 1,
        "positions_without_genotype": 120,
        "failed_genes": 1,
    }


# --------------------------------------------------------------------------
# /genotype
# --------------------------------------------------------------------------


@pytest.fixture
def genotype(pypgx, monkeypatch):
    """POST /genotype with PyPGx itself faked: records what reaches the batches.

    bgzip, tabix and bcftools are not on the test host; pysam does their jobs."""
    seen = {"genes": [], "vcf": None, "prepared": [], "dropped": []}

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

    def fake_drop(path):
        seen["dropped"].append(path)
        return path

    monkeypatch.setattr(pypgx, "process_gene_batch_parallel", fake_batch)
    monkeypatch.setattr(pypgx, "prepare_vcf_for_pypgx", fake_prepare)
    monkeypatch.setattr(pypgx, "drop_no_calls", fake_drop)
    monkeypatch.setattr(
        pypgx,
        "pypgx_gene_positions",
        lambda assembly: pypgx.load_gene_positions(GENE_TABLE, ALLELE_TABLE, assembly),
    )

    def post(records, header="", genes="CYP2D6,CYP2E1,GSTM1", **data):
        path = Path(pypgx.TEMP_DIR) / "upload.vcf"
        _vcf(path, records)
        body = path.read_text(encoding="utf-8")
        if header:
            body = body.replace(
                "##fileformat=VCFv4.2\n", "##fileformat=VCFv4.2\n" + header
            )
        fields = {"genes": genes, "reference_genome": "hg38", **data}
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
        assert result["details"]["note"].startswith("No genotype in the VCF at")
    assert "deleted" in body["results"]["GSTM1"]["details"]["note"]
    # The batches read the prepared file, without its no-calls.
    assert seen["vcf"] == seen["dropped"][0] and seen["vcf"].endswith(".gz")


def test_a_partial_call_says_how_many_positions_were_read_as_reference(genotype):
    """CYP2D6 from one genotyped position of its 121: called, and the call says so."""
    seen, post = genotype
    body = post([(*CYP2D6_38, "0/1")], absent_to_ref="false")
    details = body["results"]["CYP2D6"]["details"]
    total = details["positions"]
    assert details["positions_without_genotype"] == total - 1
    assert details["positions_note"] == (
        f"{total - 1} of {total} positions had no genotype in the file and were read "
        "as reference"
    )


def test_a_neighbours_records_do_not_call_a_gene(genotype):
    seen, post = genotype
    body = post([(*UGT1A1_38, "0/0")], genes="UGT1A1,UGT1A4,UGT1A6")
    assert seen["genes"] == ["UGT1A1"]
    assert body["results"]["UGT1A4"]["diplotype"] is None
    assert body["results"]["UGT1A6"]["diplotype"] is None


def test_without_the_field_the_option_is_off(genotype):
    seen, post = genotype
    post([(*CYP2D6_38, "0/1")])
    assert seen["genes"] == ["CYP2D6"]


def test_with_the_option_on_absent_genes_are_called_as_before(genotype):
    """The uploader said the file lists every variant: absence is reference, and a call
    resting on it needs no note."""
    seen, post = genotype
    body = post([(*CYP2D6_38, "0/1")], absent_to_ref="true")
    assert sorted(seen["genes"]) == ["CYP2D6", "CYP2E1", "GSTM1"]
    assert "positions_note" not in body["results"]["CYP2D6"].get("details", {})


def test_with_the_option_on_no_calls_are_still_not_vouched_for(genotype):
    """A ./. is not an absence: the option says nothing about it, and PyPGx would fill
    it in from its panel (NAT2 *5/*5 from 37 ./. records on the demo VCF)."""
    seen, post = genotype
    body = post(
        [(*CYP2D6_38, "0/1"), (*CYP2D6_38_B, "./."), (*GSTM1_38, "./.")],
        absent_to_ref="true",
    )
    assert sorted(seen["genes"]) == ["CYP2D6", "CYP2E1"]
    note = body["results"]["GSTM1"]["details"]["note"]
    assert note.startswith("Only no-calls (./.) in the VCF at PyPGx's positions")
    assert body["results"]["CYP2D6"]["details"]["positions_without_genotype"] == 1


@pytest.mark.parametrize(
    "header",
    [
        "##ZaroPGx_uncovered_genes=.\n",
        "##ZaroPGx_uncovered_genes=.\n##ZaroPGx_coverage_measured_on=gVCF\n",
    ],
)
def test_a_vcf_that_says_what_was_covered_is_not_second_guessed(genotype, header):
    """An alignment's VCF and a gVCF's: absence there is reference where reads were.
    Its no-calls are not (the gVCF lane writes one wherever the gVCF was not sure):
    counted, and dropped before PyPGx."""
    seen, post = genotype
    body = post(
        [(*CYP2D6_38, "0/1"), (*CYP2D6_38_B, "./.")],
        header=header,
        absent_to_ref="false",
    )
    assert sorted(seen["genes"]) == ["CYP2D6", "CYP2E1", "GSTM1"]
    assert body["results"]["CYP2D6"]["details"]["positions_without_genotype"] == 1
    assert seen["vcf"] == seen["dropped"][0]


def test_a_gvcfs_uncovered_gene_says_gvcf(genotype):
    seen, post = genotype
    body = post(
        [(*CYP2D6_38, "0/1")],
        header="##ZaroPGx_uncovered_genes=CYP2E1\n##ZaroPGx_coverage_measured_on=gVCF\n",
    )
    assert sorted(seen["genes"]) == ["CYP2D6", "GSTM1"]
    note = body["results"]["CYP2E1"]["details"]["note"]
    assert note == "No reads over this gene in the gVCF"


def test_a_gene_off_the_inputs_reference_gets_that_note(genotype):
    seen, post = genotype
    body = post(
        [(*CYP2D6_38, "0/1")],
        header=(
            "##ZaroPGx_uncovered_genes=GSTT1\n##ZaroPGx_coverage_measured_on=gVCF\n"
            "##ZaroPGx_genes_off_reference=GSTT1:22_KI270879v1_alt\n"
        ),
        genes="CYP2D6,GSTT1",
    )
    assert seen["genes"] == ["CYP2D6"]
    note = body["results"]["GSTT1"]["details"]["note"]
    assert note.startswith("The gVCF's reference has no 22_KI270879v1_alt contig")


# --------------------------------------------------------------------------
# Reads at a gene's own positions, not anywhere in its region
# --------------------------------------------------------------------------
# A read anywhere in its region made a gene covered, and its positions with no record
# were read as the reference. On a panel over UGT1A1, UGT1A4 and UGT1A6 (whose regions
# span UGT1A1's exon 1) were called *1/*1 and Reference/Reference from no read at any
# position of their own. The producers now name the positions with no reads.


def _unread(positions38, genes, measured_on=None):
    """The header an alignment's (or, with measured_on, a gVCF's) VCF carries when
    ``genes`` -> [index into the gene's positions, or None for all] had no reads."""
    listed = ",".join(
        gene
        + ":"
        + ";".join(
            str(span[1] + 1)
            for i, span in enumerate(positions38[gene])
            if which is None or i in which
        )
        for gene, which in genes.items()
    )
    header = "##ZaroPGx_uncovered_genes=.\n"
    if measured_on:
        header += f"##ZaroPGx_coverage_measured_on={measured_on}\n"
    return header + f"##ZaroPGx_positions_without_reads={listed}\n"


def test_a_neighbours_reads_do_not_call_a_gene(genotype, positions38):
    """The panel case: reads over UGT1A1's exon 1 only."""
    seen, post = genotype
    body = post(
        [(*UGT1A1_38, "0/0")],
        header=_unread(positions38, {"UGT1A4": None, "UGT1A6": None}),
        genes="UGT1A1,UGT1A4,UGT1A6",
    )
    assert seen["genes"] == ["UGT1A1"]
    for gene in ("UGT1A4", "UGT1A6"):
        result = body["results"][gene]
        assert result["success"] is True and result["diplotype"] is None
        assert result["details"]["note"] == (
            "No reads at any of PyPGx's positions for this gene in the alignment"
        )
    assert "positions_note" not in body["results"]["UGT1A1"].get("details", {})


def test_a_gene_with_reads_at_some_positions_is_called_with_a_count(
    genotype, positions38
):
    seen, post = genotype
    body = post(
        [(*CYP2D6_38, "0/1")],
        header=_unread(positions38, {"CYP2D6": [0, 1, 2]}),
        genes="CYP2D6",
    )
    assert seen["genes"] == ["CYP2D6"]
    details = body["results"]["CYP2D6"]["details"]
    total = len(positions38["CYP2D6"])
    assert details["positions_without_genotype"] == 3
    assert details["positions_note"] == (
        f"3 of {total} positions had no reads in the alignment and were read as "
        "reference"
    )


def test_a_gvcfs_no_call_with_no_reads_is_counted_once(genotype, positions38):
    """gatk-api writes a ./. at a PharmCAT position the gVCF had no reads over, and
    names the position: one position without reads, not two unread."""
    seen, post = genotype
    b = next(i for i, s in enumerate(positions38["CYP2D6"]) if s[1] + 1 == 42126611)
    body = post(
        [(*CYP2D6_38, "0/1"), (*CYP2D6_38_B, "./.")],
        header=_unread(positions38, {"CYP2D6": [b]}, measured_on="gVCF"),
        genes="CYP2D6",
    )
    details = body["results"]["CYP2D6"]["details"]
    assert details["positions_without_genotype"] == 1
    assert "had no reads in the gVCF" in details["positions_note"]


def test_no_calls_where_there_were_reads_keep_the_files_wording(genotype, positions38):
    seen, post = genotype
    body = post(
        [(*CYP2D6_38, "0/1"), (*CYP2D6_38_B, "./.")],
        header=_unread(positions38, {"CYP2D6": [5]}),
        genes="CYP2D6",
    )
    details = body["results"]["CYP2D6"]["details"]
    assert details["positions_without_genotype"] == 2
    assert "had no genotype in the file" in details["positions_note"]


def test_with_whole_genome_depth_a_copy_number_gene_is_left_to_pypgx(
    genotype, positions38, monkeypatch, pypgx
):
    """CYP2D6 *5/*5 has no reads at any of its positions, and reads in its region's
    flanks. With whole-genome depth PyPGx's copy-number model reads that as a deletion;
    judged by positions it would have had no call."""
    monkeypatch.setattr(pypgx, "sv_inputs_from_vcf", lambda *a: ("d.zip", "c.zip"))
    monkeypatch.setattr(pypgx, "sv_target_genes", lambda: frozenset({"CYP2D6"}))
    seen, post = genotype
    body = post(
        [(*UGT1A1_38, "0/0")],
        header=_unread(positions38, {"CYP2D6": None, "UGT1A4": None}),
        genes="CYP2D6,UGT1A1,UGT1A4",
    )
    assert sorted(seen["genes"]) == ["CYP2D6", "UGT1A1"]
    assert "positions_note" not in body["results"]["CYP2D6"].get("details", {})
    assert body["results"]["UGT1A4"]["diplotype"] is None


def test_the_positions_header_round_trips(pypgx, tmp_path):
    line = pypgx.positions_without_reads_header_line(
        {"UGT1A6": [233693631], "CYP2D6": [42126611, 42126578], "NAT1": []}
    )
    assert line == (
        "##ZaroPGx_positions_without_reads=CYP2D6:42126578;42126611,UGT1A6:233693631"
    )
    assert pypgx.positions_without_reads_header_line({}) == (
        "##ZaroPGx_positions_without_reads=."
    )
    vcf = tmp_path / "h.vcf"
    vcf.write_text(f"##fileformat=VCFv4.2\n{line}\n#CHROM\n", encoding="utf-8")
    assert pypgx.positions_without_reads_from_vcf(str(vcf)) == {
        "CYP2D6": {42126578, 42126611},
        "UGT1A6": {233693631},
    }
    vcf.write_text("##fileformat=VCFv4.2\n#CHROM\n", encoding="utf-8")
    assert pypgx.positions_without_reads_from_vcf(str(vcf)) is None


def test_both_sidecars_read_the_same_positions(pypgx):
    """gatk-api has no PyPGx: it reads the lexicon's copy of PyPGx's allele table with
    its own copy of load_gene_positions."""
    tree = ast.parse(GATK_API.read_text(encoding="utf-8"))
    fn = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "pypgx_gene_positions_grch38"
    )
    ns = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(GATK_API), "exec"), ns)
    assert ns["pypgx_gene_positions_grch38"](
        GENE_TABLE, ALLELE_TABLE
    ) == pypgx.load_gene_positions(GENE_TABLE, ALLELE_TABLE, "GRCh38")


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
        "ABCB1": {
            "success": True,
            "diplotype": "*1/*2",
            "details": {
                "positions": 3,
                "positions_without_genotype": 2,
                "positions_note": "2 of 3 positions had no genotype in the file and "
                "were read as reference",
            },
        },
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


@pytest.mark.parametrize("lane", ["write_html", "write_interactive_html"])
def test_a_partial_call_carries_its_note_beside_the_diplotype(
    monkeypatch, tmp_path, lane
):
    rows, page = _report(monkeypatch, tmp_path, lane)
    assert rows["ABCB1"]["positions_note"].startswith("2 of 3 positions")
    for tr in re.findall(r"<tr>(.*?)</tr>", page, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
        if cells and _text(cells[0]).strip() == "ABCB1":
            assert "2 of 3 positions had no genotype in the file" in _text(cells[1])
            break
    else:
        raise AssertionError("no ABCB1 row")


def test_an_outside_call_pypgx_made_carries_its_note(monkeypatch, tmp_path):
    """CYP2D6 from a VCF reaches PharmCAT as PyPGx's outside call: PharmCAT's row is
    PyPGx's call, and its note goes with it."""
    note = "120 of 121 positions had no genotype in the file and were read as reference"
    pypgx = {
        "results": {
            "CYP2D6": {
                "success": True,
                "diplotype": "*1/*4",
                "details": {"positions_note": note},
            }
        }
    }
    genes = [{"gene": "CYP2D6", "diplotype": "*1/*4", "call_source": "OUTSIDE"}]
    rows, _ = _report(monkeypatch, tmp_path, "write_html", genes, pypgx)
    assert rows["CYP2D6"]["positions_note"] == note


def test_the_workflow_diagram_counts_them_apart():
    from app.reports.generator import pypgx_gene_counts

    output = {
        "total_genes": 66,
        "successful_genes": DEMO_CALLED,
        "not_sequenced_genes": 0,
        "no_genotype_genes": DEMO_NO_GENOTYPE,
        "partially_genotyped_genes": DEMO_PARTIAL,
        "failed_genes": 0,
    }
    assert pypgx_gene_counts(output) == (
        DEMO_CALLED,
        66,
        0,
        DEMO_NO_GENOTYPE,
        0,
        DEMO_PARTIAL,
    )


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
