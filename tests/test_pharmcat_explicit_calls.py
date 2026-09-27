"""PharmCAT gets a called genotype at each of its positions, or none -- never an assumed one.

PharmCAT reads a position absent from its VCF as a no-call. Its preprocessor's
--absent-to-ref / --unspecified-to-ref turn such positions into 0/0, and PharmCAT's
own documentation marks both DANGEROUS: use them only when the data really is
reference there rather than unreadable. For an alignment the pipeline used to hand
PharmCAT PyPGx's create-input-vcf output, which is variants-only, so every reference
position was absent -- and with this deployment's flags on, every UNSEQUENCED
position read as reference too. Measured 2026-09-27 on a real targeted panel: 422 of
PharmCAT's 1,226 positions had no read, including every RYR1, CACNA1S, CFTR, F2 and F5
position, and the report called all five genes normal.

The alignment lanes now force-call every PharmCAT position from the reads
(genotype_pharmcat_positions in the pypgx wrapper) and give PharmCAT that file with
the flags off. A gVCF already had explicit calls (GVCFToVCF) and gets the flags off
too. Validated on the live stack against a 30x WGS: all 21 PharmCAT genes identical
to the old path; on the panel the five unsequenced genes became "not called".
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from app.utils.pharmcat_assume_ref import (
    ALIGNMENT_INPUT_TYPES,
    EXPLICIT_CALL_INPUT_TYPES,
    explicit_calls_paragraph,
    pharmcat_flags_for_input,
)

REPO = Path(__file__).resolve().parents[1]
MAIN_NF = REPO / "pipelines" / "pgx" / "main.nf"
WRAPPER = REPO / "docker" / "pypgx" / "pypgx_wrapper.py"


def _nf() -> str:
    return MAIN_NF.read_text(encoding="utf-8")


def _workflow() -> str:
    src = _nf()
    return src[src.index("\nworkflow {") :]


def _process(name: str) -> str:
    src = _nf()
    start = src.index(f"process {name} {{")
    return src[start : src.index("\n}\n", start)]


def _names(listing: str) -> set:
    return {s.strip().strip("'\"") for s in listing.split(",")}


# --------------------------------------------------------------------------
# One rule, two copies: main.nf applies it, the report describes it
# --------------------------------------------------------------------------


def test_main_nf_and_the_report_agree_on_which_inputs_are_explicit():
    """If these drift, the report states flags PharmCAT did not run with."""
    m = re.search(r"explicit_call_input_types = \[([^\]]*)\]", _workflow())
    assert m, "main.nf no longer declares explicit_call_input_types"
    assert _names(m.group(1)) == set(EXPLICIT_CALL_INPUT_TYPES)


def test_alignment_inputs_hand_pharmcat_the_force_called_vcf():
    m = re.search(
        r"pharmcat_vcf_ch = \(params\.input_type in \[([^\]]*)\]\) \? "
        r"PyPGxBam2Vcf\.out\.pharmcat_vcf : vcf_ch",
        _workflow(),
    )
    assert m, "PharmCAT's input for alignments is no longer PyPGxBam2Vcf's pharmcat_vcf"
    assert _names(m.group(1)) == set(ALIGNMENT_INPUT_TYPES)


def test_every_pharmcat_run_reads_that_vcf_and_the_effective_flags():
    wf = _workflow()
    calls = re.findall(r"PharmCATRun\(\s*(.*?)\s*\)\n", wf, re.S)
    assert len(calls) == 2, "expected the skip_pypgx and the PyPGx PharmCATRun calls"
    for call in calls:
        args = [a.strip() for a in call.split(",")]
        assert args[0] in ("pharmcat_vcf_ch", "pypgx_complete_ch")
        assert args[-2:] == ["pharmcat_absent_ch", "pharmcat_unspecified_ch"]
    # The PyPGx arm waits on PyPGx, then hands PharmCAT the explicit VCF, not PyPGx's.
    assert "combine(pharmcat_vcf_ch)" in wf
    assert "combine(vcf_ch)" not in wf


def test_the_effective_flags_are_off_for_explicit_inputs():
    wf = _workflow()
    assert (
        "pharmcat_absent_ch = Channel.value(explicit_calls ? 'false' : "
        "params.pharmcat_absent_to_ref)" in wf
    )
    assert (
        "pharmcat_unspecified_ch = Channel.value(explicit_calls ? 'false' : "
        "params.pharmcat_unspecified_to_ref)" in wf
    )


def test_pharmcat_run_always_sends_the_effective_flags():
    """Always sent, even as 'false': the sidecar falls back to the .env when the
    field is missing, which is how an alignment run would get them back on."""
    body = _process("PharmCATRun")
    assert "!{params.pharmcat_absent_to_ref}" not in body
    assert "!{params.pharmcat_unspecified_to_ref}" not in body
    assert "-F pharmcat_absent_to_ref=!{absent_to_ref}" in body
    assert "-F pharmcat_unspecified_to_ref=!{unspecified_to_ref}" in body


def test_the_mtdna_call_keeps_the_users_consent():
    """For MT-RNR1 the flag is a consent read by its own rule (app/mtdna/mt_rnr1.py);
    the override is PharmCAT's alone."""
    wf = _workflow()
    block = wf[
        wf.index("mtdna_result = MtdnaCall(") : wf.index("mtdna_outside = mtdna_result")
    ]
    assert "Channel.value(params.pharmcat_absent_to_ref)" in block


def test_the_force_called_vcf_is_a_required_output_outside_the_vcf_glob():
    body = _process("PyPGxBam2Vcf")
    line = next(l for l in body.splitlines() if "emit: pharmcat_vcf" in l)
    assert 'path "pharmcat/pharmcat_positions.vcf.gz"' in line
    assert "optional" not in line
    # The subdirectory keeps PyPGx's `*.vcf.gz` output from matching it as well.
    assert 'path "*.vcf.gz", emit: vcf' in body


@pytest.mark.parametrize("input_type", sorted(EXPLICIT_CALL_INPUT_TYPES))
def test_the_flags_never_reach_pharmcat_for_explicit_inputs(input_type):
    assert pharmcat_flags_for_input(input_type, True, True) == (False, False)
    assert pharmcat_flags_for_input(input_type.upper(), True, False) == (False, False)


@pytest.mark.parametrize("input_type", ["vcf", "bcf", None])
def test_the_flags_pass_through_for_a_variants_only_vcf(input_type):
    assert pharmcat_flags_for_input(input_type, True, False) == (True, False)
    assert pharmcat_flags_for_input(input_type, False, True) == (False, True)


# --------------------------------------------------------------------------
# The report's paragraph
# --------------------------------------------------------------------------

STATS = {
    "n_pharmcat_positions": 1226,
    "n_reference": 800,
    "n_variant": 4,
    "n_no_reads": 422,
    "n_uncertain": 0,
    "min_reference_depth": 7,
}


def test_the_paragraph_states_what_the_force_call_found():
    text = explicit_calls_paragraph("fastq", False, False, STATS)
    assert "1,226" in text and "800 reference" in text and "4 variant" in text
    assert "422 had no reads" in text
    assert "not reference calls" in text
    assert "not called" in text
    assert "not applied" not in text


def test_the_paragraph_says_when_requested_flags_were_set_aside():
    text = explicit_calls_paragraph("bam", True, True, STATS)
    assert "assume-reference flags were set for this run and not applied" in text


def test_a_gvcf_gets_only_the_set_aside_note():
    """Its coverage counts are gvcf_provenance_paragraph's to state."""
    assert explicit_calls_paragraph("gvcf", False, False, None) is None
    text = explicit_calls_paragraph("gvcf", False, True, None)
    assert "not applied" in text and "no reads" not in text


def test_a_plain_vcf_gets_no_paragraph():
    assert explicit_calls_paragraph("vcf", True, True, STATS) is None


def test_missing_counts_still_say_what_the_calls_rest_on():
    text = explicit_calls_paragraph("cram", False, False, None)
    assert "called from the alignment" in text
    assert "no-call, not a reference call" in text


# --------------------------------------------------------------------------
# The force-call itself (pypgx wrapper), loaded without its FastAPI/PyPGx imports
# --------------------------------------------------------------------------


def _load_wrapper(names):
    tree = ast.parse(WRAPPER.read_text(encoding="utf-8"))
    wanted = [
        node
        for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in names)
        or (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id in names for t in node.targets)
        )
    ]
    ns: Dict[str, Any] = {
        "os": os,
        "subprocess": subprocess,
        "tempfile": tempfile,
        "shutil": shutil,
        "Path": Path,
        "Dict": Dict,
        "Any": Any,
        "REFERENCE_DIR": Path("/reference"),
    }
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(WRAPPER), "exec"), ns)
    return ns


CLASSIFY = [
    "PHARMCAT_MIN_VARIANT_GQ",
    "PHARMCAT_MIN_REF_DEPTH",
    "classify_position_call",
]


@pytest.mark.parametrize(
    "gt, dp, gq, expected",
    [
        ((0, 0), 7, None, "ref"),  # 0.5**7 < 1%: a het would have shown an alt read
        ((0, 0), 6, None, "uncertain"),
        ((0, 0), None, None, "uncertain"),
        ((0, 1), 30, 20, "variant"),
        ((0, 1), 30, 19, "uncertain"),
        ((1, 1), 30, None, "uncertain"),
        ((None, None), 30, 99, "uncertain"),
        (None, 30, 99, "uncertain"),
    ],
)
def test_what_counts_as_a_call(gt, dp, gq, expected):
    classify = _load_wrapper(CLASSIFY)["classify_position_call"]
    assert classify(gt, dp, gq) == expected


def test_every_subprocess_in_the_force_call_is_an_argv_list():
    tree = ast.parse(WRAPPER.read_text(encoding="utf-8"))
    fn = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "genotype_pharmcat_positions"
    )
    calls = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in ("run", "Popen")
    ]
    assert len(calls) >= 5
    for call in calls:
        assert isinstance(call.args[0], ast.List), "argv must be a list literal"
        assert not any(kw.arg == "shell" for kw in call.keywords)


needs_htslib = pytest.mark.skipif(
    not (shutil.which("bcftools") and shutil.which("tabix")),
    reason="needs bcftools and tabix on PATH (present in the pypgx image, not in CI)",
)


@needs_htslib
def test_the_force_call_writes_calls_and_leaves_the_rest_out(tmp_path):
    """Five PharmCAT positions, one of each fate:

    100  SNV       10 reads, all reference  -> 0/0, PharmCAT's own record
    150  deletion  10 reads, all reference  -> 0/0 with PharmCAT's multi-base REF
                                               (bcftools writes REF=anchor ALT=.,
                                               which PharmCAT discards)
    200  SNV       10 reads, 5 alt          -> the caller's 0/1 record
    250  SNV        3 reads                 -> left out: too few to call reference
    350  SNV        no reads                -> left out
    """
    pysam = pytest.importorskip("pysam")
    import random

    rng = random.Random(7)
    seq = "".join(rng.choice("ACGT") for _ in range(400))
    fasta = tmp_path / "ref.fa"
    fasta.write_text(">chr1\n" + seq + "\n", encoding="utf-8")
    pysam.faidx(str(fasta))

    def alt_of(base):
        return "A" if base != "A" else "C"

    positions = tmp_path / "pharmcat_positions.vcf"
    records = [
        (100, seq[99], alt_of(seq[99])),
        (150, seq[149:152], seq[149]),
        (200, seq[199], alt_of(seq[199])),
        (250, seq[249], alt_of(seq[249])),
        (350, seq[349], alt_of(seq[349])),
    ]
    positions.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chr1,length=400>\n"
        '##INFO=<ID=PX,Number=.,Type=String,Description="gene">\n'
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPharmCAT\n"
        + "".join(
            f"chr1\t{pos}\trs{pos}\t{ref}\t{alt}\t.\tPASS\tPX=TEST\tGT\t0/0\n"
            for pos, ref, alt in records
        ),
        encoding="utf-8",
    )

    header = {
        "HD": {"VN": "1.6", "SO": "coordinate"},
        "SQ": [{"SN": "chr1", "LN": 400}],
        "RG": [{"ID": "rg", "SM": "S1"}],
    }
    reads = []
    for start, n, alt_at in (
        (75, 10, None),
        (125, 10, None),
        (175, 10, 199),
        (225, 3, None),
    ):
        for i in range(n):
            s = list(seq[start : start + 50])
            if alt_at is not None and i % 2 == 0:
                s[alt_at - start] = alt_of(seq[alt_at])
            reads.append((start, "".join(s)))
    bam = tmp_path / "aln.bam"
    with pysam.AlignmentFile(str(bam), "wb", header=header) as out:
        for i, (start, s) in enumerate(sorted(reads)):
            r = pysam.AlignedSegment()
            r.query_name, r.query_sequence, r.flag = f"r{i}", s, 0
            r.reference_id, r.reference_start, r.mapping_quality = 0, start, 60
            r.cigarstring = "50M"
            r.query_qualities = pysam.qualitystring_to_array("I" * 50)
            r.set_tag("RG", "rg")
            out.write(r)
    pysam.index(str(bam))

    ns = _load_wrapper(
        CLASSIFY
        + ["tabix_index", "PHARMCAT_POSITIONS_VCF", "genotype_pharmcat_positions"]
    )
    out = tmp_path / "pharmcat.vcf.gz"
    stats = ns["genotype_pharmcat_positions"](
        str(bam), str(fasta), str(out), positions=str(positions)
    )

    with pysam.VariantFile(str(out)) as vf:
        got = {(r.pos, r.ref, r.alts, r.samples["S1"]["GT"]) for r in vf}
    assert got == {
        (100, seq[99], (alt_of(seq[99]),), (0, 0)),
        (150, seq[149:152], (seq[149],), (0, 0)),
        (200, seq[199], (alt_of(seq[199]),), (0, 1)),
    }
    assert stats["n_pharmcat_positions"] == 5
    assert stats["n_reference"] == 2
    assert stats["n_variant"] == 1
    assert stats["n_no_reads"] == 1
    assert stats["n_uncertain"] == 1


# --------------------------------------------------------------------------
# Partial coverage: a call PharmCAT made from some of a gene's positions
# --------------------------------------------------------------------------


def test_a_partly_covered_call_is_named_with_what_it_could_not_assess():
    """Measured on the panel: NUDT15 *1/*1 with *4, *15, *20, *21 unassessed."""
    from app.reports.generator import partial_coverage_alert

    text = partial_coverage_alert(
        [
            {
                "gene": "NUDT15",
                "diplotype": "*1/*1",
                "uncalled_haplotypes": ["*4", "*15", "*20", "*21"],
            },
            {"gene": "CYP2C19", "diplotype": "*1/*1", "uncalled_haplotypes": []},
            # No call at all: already named under "Genes examined without a call".
            {
                "gene": "RYR1",
                "diplotype": "Unknown/Unknown",
                "uncalled_haplotypes": ["Reference", "c.38T>G"],
            },
        ]
    )
    assert "NUDT15 (*4, *15, *20, *21)" in text
    assert "CYP2C19" not in text and "RYR1" not in text
    assert "could not be assessed" in text


def test_no_partial_coverage_means_no_alert():
    from app.reports.generator import partial_coverage_alert

    assert (
        partial_coverage_alert(
            [{"gene": "TPMT", "diplotype": "*1/*1", "uncalled_haplotypes": []}]
        )
        is None
    )
    assert partial_coverage_alert(None) is None


def test_a_long_list_is_cut_and_counted_and_escaped():
    from app.reports.generator import partial_coverage_alert

    many = [f"*{i}" for i in range(2, 14)] + ["<x>"]
    text = partial_coverage_alert(
        [{"gene": "G6PD", "diplotype": "B/B", "uncalled_haplotypes": many}]
    )
    assert "and 5 more" in text
    assert "<x>" not in text


def test_the_uncalled_haplotypes_reach_the_report_genes():
    """Stored in the gene's full JSON by the parser; read back and carried through."""
    from app.services.pharmcat_data_service import PharmCATDataService

    service = PharmCATDataService.__new__(PharmCATDataService)
    genes = service._transform_genes_for_reports(
        [
            {
                "gene_symbol": "NUDT15",
                "call_source": "MATCHER",
                "uncalled_haplotypes": ["*4"],
            }
        ],
        [
            {
                "gene_symbol": "NUDT15",
                "diplotype_label": "*1/*1",
                "phenotype": "Normal Metabolizer",
            }
        ],
    )
    assert genes[0]["uncalled_haplotypes"] == ["*4"]


def test_the_parser_reads_them_back_from_the_stored_gene_json():
    src = (REPO / "app" / "pharmcat" / "pharmcat_parser.py").read_text(encoding="utf-8")
    body = src[src.index("def get_gene_summary") :]
    body = body[: body.index("\n    def ", 1)]
    assert '(r.gene_full_data or {}).get("uncalledHaplotypes")' in body


def test_the_generator_appends_the_alert_to_the_rendered_warnings():
    src = (REPO / "app" / "reports" / "generator.py").read_text(encoding="utf-8")
    assert 'coverage_alert = partial_coverage_alert((data or {}).get("genes"))' in src
    assert "workflow_warnings.append(coverage_alert)" in src


# --------------------------------------------------------------------------
# Which genes PharmCAT calls itself, so PyPGx does not call them a second time
# --------------------------------------------------------------------------

GENES_JSON = REPO / "config" / "genes.json"
POSITIONS_VCF = REPO / "reference" / "pharmcat" / "pharmcat_positions.vcf"


def _gene_sets():
    import json

    return json.loads(GENES_JSON.read_text(encoding="utf-8"))["sets"]


def test_f2_and_f5_are_pharmcats_not_pypgxs():
    """PharmCAT 3.4.0 matches F2 (rs1799963) and F5 (rs6025) from its own positions.
    Listed as PyPGx-only, they went to PharmCAT as outside calls as well, and a
    covered sample's report carried two F2 rows and two F5 rows (seen on the live
    WGS run, 2026-09-27)."""
    sets = _gene_sets()
    for gene in ("F2", "F5"):
        assert gene in sets["pharmcat_can_call"]
        assert gene not in sets["pypgx_minus_pharmcat"]


def test_the_pharmcat_sets_are_consistent():
    sets = _gene_sets()
    assert set(sets["pharmcat_all"]) == set(sets["pharmcat_can_call"]) | set(
        sets["pharmcat_outside_callers"]
    )
    assert set(sets["pypgx_minus_pharmcat"]) == set(sets["pypgx"]) - set(
        sets["pharmcat_can_call"]
    )


@pytest.mark.skipif(
    not POSITIONS_VCF.exists(),
    reason="reference/pharmcat is staged locally, not in git",
)
def test_every_gene_with_a_pharmcat_position_is_one_pharmcat_calls():
    """The rule F2/F5 broke: a gene in PharmCAT's position list is matched by
    PharmCAT, so PyPGx must not send it as an outside call too."""
    genes = set()
    for line in POSITIONS_VCF.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            continue
        info = line.split("\t")[7]
        for field in info.split(";"):
            if field.startswith("PX="):
                genes.update(field[3:].split(","))
    sets = _gene_sets()
    callable_here = set(sets["pharmcat_can_call"]) | set(
        sets["pharmcat_outside_callers"]
    )
    assert genes - callable_here == set()
