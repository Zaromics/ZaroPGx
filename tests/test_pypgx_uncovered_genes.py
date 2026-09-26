"""A gene the alignment never covered gets no PyPGx call -- not a reference one.

Found on the first end-to-end panel run (2026-09-26). A targeted panel over ~20
pharmacogenes, aligned and genotyped, came back with confident PyPGx diplotypes for
genes it never sequenced -- measured in the aligned BAM:

    ABCB1   0 reads  -> *2/*2
    CYP2E1  0 reads  -> *7/*7
    GSTM1   0 reads  -> *A/*A
    APOE    0 reads  -> Reference/Reference
    CYP2D6  329 reads -> *1/*4   (control)

"No reads" was being read as "matches the reference genome", and where GRCh38 itself
carries a non-*1 haplotype that surfaces as *2/*2 or *7/*7. The wrapper already meant
to handle sparse panels, but only per CHROMOSOME ("invalid contig" when the gene's
chromosome has no variants at all). ABCB1 shares chr7 with CYP3A, GSTM1 shares chr1 with
DPYD, CYP2E1 shares chr10 with CYP2C -- so the chromosome check never fired for them.

This is the failure the consumer-array refusal exists to prevent: PyPGx calls are handed
to PharmCAT as OVERRIDING outside calls, so a panel without CYP2D6 would report CYP2D6
as a normal metabolizer from zero reads.

The fix measures per-gene coverage where it can actually be observed -- on the
alignment, in /create-input-vcf -- and carries the answer INSIDE the VCF header, so it
travels with the file through Nextflow without new plumbing. The genotyper reads the
header and records those genes as no-data instead of calling them. An uploaded VCF has
no such header line and keeps the old chromosome-level behaviour: it carries no coverage
evidence to act on.
"""

from __future__ import annotations

import ast
import gzip
from pathlib import Path

WRAPPER = Path(__file__).resolve().parents[1] / "docker" / "pypgx" / "pypgx_wrapper.py"


def _load(names):
    tree = ast.parse(WRAPPER.read_text(encoding="utf-8"))
    wanted, found = [], set()
    for node in tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in names
        ):
            wanted.append(node)
            found.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in names:
                    wanted.append(node)
                    found.add(t.id)
    assert not set(names) - found, f"missing: {sorted(set(names) - found)}"
    ns = {"gzip": gzip, "Optional": __import__("typing").Optional}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(WRAPPER), "exec"), ns)
    return ns


NS = _load(
    [
        "UNCOVERED_GENES_HEADER_KEY",
        "uncovered_genes_header_line",
        "uncovered_genes_from_vcf",
    ]
)


def _vcf(tmp_path, header_extra="", gz=True):
    body = (
        "##fileformat=VCFv4.2\n"
        + header_extra
        + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n"
        + "chr22\t42127941\t.\tG\tA\t50\tPASS\t.\tGT\t0/1\n"
    )
    path = tmp_path / ("in.vcf.gz" if gz else "in.vcf")
    if gz:
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            fh.write(body)
    else:
        path.write_text(body, encoding="utf-8")
    return path


def test_the_header_line_names_the_uncovered_genes():
    line = NS["uncovered_genes_header_line"](["GSTM1", "ABCB1", "CYP2E1"])
    assert line == "##ZaroPGx_uncovered_genes=ABCB1,CYP2E1,GSTM1"


def test_full_coverage_is_recorded_not_omitted():
    """Measured and complete is different from never measured, and the genotyper has
    to be able to tell them apart."""
    assert NS["uncovered_genes_header_line"]([]) == "##ZaroPGx_uncovered_genes=."


def test_the_uncovered_set_round_trips_through_a_bgzipped_header(tmp_path):
    line = NS["uncovered_genes_header_line"](["ABCB1", "GSTM1"])
    vcf = _vcf(tmp_path, header_extra=line + "\n")
    assert NS["uncovered_genes_from_vcf"](str(vcf)) == {"ABCB1", "GSTM1"}


def test_a_measured_full_coverage_reads_as_an_empty_set(tmp_path):
    vcf = _vcf(tmp_path, header_extra="##ZaroPGx_uncovered_genes=.\n")
    assert NS["uncovered_genes_from_vcf"](str(vcf)) == set()


def test_an_uploaded_vcf_without_the_line_reads_as_unknown(tmp_path):
    """None, not an empty set: an uploaded VCF carries no coverage evidence, and
    reading its silence as "everything covered" would change nothing today but would
    be the wrong claim for the next caller."""
    assert NS["uncovered_genes_from_vcf"](str(_vcf(tmp_path))) is None
    assert NS["uncovered_genes_from_vcf"](str(_vcf(tmp_path, gz=False))) is None


# --------------------------------------------------------------------------
# Wiring (the pysam/bcftools halves run only in the container; validated live)
# --------------------------------------------------------------------------


def _src():
    return WRAPPER.read_text(encoding="utf-8")


def test_create_input_vcf_measures_and_records_coverage():
    src = _src()
    body = src[src.index("def run_pypgx_create_input_vcf") :]
    body = body[: body.index("\ndef ", 1)]
    assert "uncovered_genes_in_alignment(" in body
    assert "annotate_uncovered_genes(" in body


def test_the_genotyper_skips_uncovered_genes_before_batching():
    src = _src()
    body = src[src.index("async def genotype(") :]
    read_at = body.index("uncovered_genes_from_vcf(")
    batch_at = body.index("gene_batches = chunk_list(")
    assert read_at < batch_at, "uncovered genes must be removed before batching"


def test_an_uncovered_gene_gets_no_diplotype():
    src = _src()
    body = src[src.index("async def genotype(") :]
    block = body[
        body.index("uncovered_genes_from_vcf(") : body.index(
            "gene_batches = chunk_list("
        )
    ]
    assert "'diplotype': None" in block or '"diplotype": None' in block
