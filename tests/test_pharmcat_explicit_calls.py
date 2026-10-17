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
import logging
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
        r"pharmcat_vcf_ch = \(params\.input_type in \[([^\]]*)\]\)\s*\?\s*"
        r"PyPGxBam2Vcf\.out\.pharmcat_vcf\s*:\s*"
        r"\(params\.input_type == 'gvcf' \? GVCFToVCF\.out\.pharmcat_vcf : vcf_ch\)",
        _workflow(),
    )
    assert m, "PharmCAT's input is no longer each lane's own PharmCAT file"
    assert _names(m.group(1)) == set(ALIGNMENT_INPUT_TYPES)


def test_a_gvcf_hands_pharmcat_its_own_file():
    """PharmCAT's positions alone, no-calls in its representation; PyPGx and the mtDNA
    sidecar read genotyped.vcf.gz, which keeps a confident call PharmCAT's file had to
    drop (review: the G of `C *,G 1/2` under an upstream deletion, at a CYP2D6-dense
    list of positions whose call comes from PyPGx)."""
    body = _process("GVCFToVCF")
    line = next(l for l in body.splitlines() if "emit: pharmcat_vcf" in l)
    assert 'path "pharmcat/pharmcat_positions.vcf.gz"' in line
    assert "optional" not in line
    assert "pharmcat_vcf_path" in body
    assert 'cp "$PHARMCAT_VCF_PATH" pharmcat/pharmcat_positions.vcf.gz' in body


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
    "min_reference_reads": 7,
}


def test_the_paragraph_states_what_the_force_call_found():
    text = explicit_calls_paragraph("fastq", False, False, STATS)
    assert "1,226" in text and "800 reference" in text and "4 variant" in text
    assert "422 with no reads" in text
    assert "at least 7 good reads for the reference" in text
    assert "is a no-call" in text
    assert "not called" in text
    assert "not applied" not in text


def test_the_paragraph_says_when_requested_flags_were_set_aside():
    text = explicit_calls_paragraph("bam", True, True, STATS)
    assert "assume-reference flags set for this run were not applied" in text


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
    assert "is a no-call" in text


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
        "logger": logging.getLogger("pypgx_wrapper"),
    }
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(WRAPPER), "exec"), ns)
    return ns


CLASSIFY = [
    "PHARMCAT_MIN_VARIANT_GQ",
    "PHARMCAT_MIN_REF_READS",
    "PHARMCAT_MAX_OTHER_FRACTION",
    "PHARMCAT_MIN_BASE_QUALITY",
    "PHARMCAT_PILEUP_PAD",
    "PHARMCAT_STATUS_COLUMNS",
    "PHARMCAT_STATUS_KINDS",
    "pharmcat_status_by_gene",
    "write_pharmcat_status",
    "allowed_other_reads",
    "classify_pharmcat_record",
]


def _classify(ref, reads=None, indels=(), calls=(), pos=100):
    """classify_pharmcat_record for one record at chr1:pos, evidence given per base."""
    fn = _load_wrapper(CLASSIFY)["classify_pharmcat_record"]
    base_reads = {("chr1", p): r for p, r in (reads or {}).items()}
    return fn("chr1", pos, ref, base_reads, list(indels), list(calls))


@pytest.mark.parametrize(
    "case, reads, indels, calls, expected",
    [
        # Reference needs >= 7 reads for it and none for anything else below 20 reads.
        ("10 reference reads", {100: (10, 0)}, (), (), "reference"),
        ("6 reference reads", {100: (6, 0)}, (), (), "uncertain"),
        # Review: bcftools called 0/0 with one alt read at 7-20 reads, and the first
        # version took that as reference. A het misses its other allele entirely in n
        # reads with probability 0.5**n; one read of it is not "none".
        ("1 other read in 10", {100: (9, 1)}, (), (), "uncertain"),
        ("1 other read in 20", {100: (19, 1)}, (), (), "reference"),
        ("2 other reads in 20", {100: (18, 2)}, (), (), "uncertain"),
        ("2 other reads in 40", {100: (38, 2)}, (), (), "reference"),
        # Review: an allele PharmCAT does not list (TPMT het A), called by the caller.
        (
            "unlisted allele, called",
            {100: (10, 10)},
            (),
            [("chr1", 100, 100, True)],
            "variant",
        ),
        # Review: 20 all-alt reads at low base quality never reach the pileup counts.
        ("all reads below the quality floor", {}, (), (), "no_reads"),
        # An indel anchored upstream whose deleted span covers the position.
        ("spanning deletion", {100: (10, 0)}, [("chr1", 95, 105)], (), "uncertain"),
        # A call PharmCAT's matcher cannot use with confidence is not reference either.
        (
            "low-confidence call",
            {100: (10, 3)},
            (),
            [("chr1", 100, 100, False)],
            "uncertain",
        ),
    ],
)
def test_what_counts_as_reference(case, reads, indels, calls, expected):
    assert _classify("A", reads, indels, calls) == expected, case


def test_every_base_of_a_multi_base_record_must_be_reference():
    """Review: a homozygous RYR1 TT>AA MNV (20 of 20 reads) was written 0/0, because
    the first version looked at the POS base's genotype alone."""
    assert _classify("TT", {100: (0, 20), 101: (0, 20)}) == "uncertain"
    assert _classify("TT", {100: (20, 0), 101: (0, 20)}) == "uncertain"
    assert _classify("TT", {100: (20, 0), 101: (20, 0)}) == "reference"
    assert _classify("CTT", {100: (12, 0), 101: (12, 0)}) == "uncertain"  # 102 unread


def test_the_per_gene_counts_follow_pharmcats_px():
    """Pure Python, so it runs where the htslib tests skip. A record whose PX names
    two genes counts under each; the POI record, with none, under none."""
    ns = _load_wrapper(CLASSIFY)
    by_gene = ns["pharmcat_status_by_gene"](
        {
            ("chr1", 1): "reference",
            ("chr1", 2): "uncertain",
            ("chr1", 3): "no_reads",
            ("chr10", 4): "reference",
        },
        {
            ("chr1", 1): ("A",),
            ("chr1", 2): ("A", "B"),
            ("chr1", 3): ("B",),
            ("chr10", 4): (),
        },
    )
    zero = {"reference": 0, "variant": 0, "no_reads": 0, "uncertain": 0}
    assert by_gene == {
        "A": {**zero, "positions": 2, "reference": 1, "uncertain": 1},
        "B": {**zero, "positions": 2, "uncertain": 1, "no_reads": 1},
    }


def test_the_status_file_lists_every_position_with_its_reads(tmp_path):
    ns = _load_wrapper(CLASSIFY)
    path = tmp_path / "status.tsv"
    records = [
        ("chr1", 100, "rs1", "A", ("A", "C")),
        ("chr1", 200, "rs2", "CTT", ("CTT", "C")),  # three bases: fewest, most
        ("chr1", 300, None, "G", ("G", "T")),
    ]
    ok = ns["write_pharmcat_status"](
        str(path),
        records,
        {("chr1", 100): ("A",), ("chr1", 200): ("A", "B"), ("chr1", 300): ()},
        {
            ("chr1", 100): "reference",
            ("chr1", 200): "uncertain",
            ("chr1", 300): "no_reads",
        },
        {("chr1", 100): (12, 0), ("chr1", 200): (9, 0), ("chr1", 201): (4, 2)},
    )
    assert ok
    assert path.read_text(encoding="utf-8").splitlines() == [
        "chrom\tposition\trsid\tgene\tstatus\treference_reads\tother_reads",
        "chr1\t100\trs1\tA\treference\t12\t0",
        "chr1\t200\trs2\tA,B\tuncertain\t4\t2",
        "chr1\t300\t\t\tno_reads\t\t",
    ]


def test_a_status_file_that_cannot_be_written_costs_only_the_file(tmp_path):
    """main.nf publishes it as optional: a failed write is logged, leaves nothing
    behind for the report to half-read, and the step goes on."""
    ns = _load_wrapper(CLASSIFY)
    path = tmp_path / "gone" / "status.tsv"  # its directory does not exist
    ok = ns["write_pharmcat_status"](
        str(path),
        [("chr1", 100, "rs1", "A", ("A", "C"))],
        {},
        {("chr1", 100): "reference"},
        {},
    )
    assert ok is False
    assert not path.exists() and not path.parent.exists()


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
    assert len(calls) >= 4
    for call in calls:
        assert isinstance(call.args[0], ast.List), "argv must be a list literal"
        assert not any(kw.arg == "shell" for kw in call.keywords)


def test_reference_is_never_decided_by_a_constrained_genotype():
    """The first version ran `bcftools call -C alleles`, which calls 0/0 whenever the
    reads show an allele it was not offered."""
    src = WRAPPER.read_text(encoding="utf-8")
    body = src[src.index("def genotype_pharmcat_positions") :]
    body = body[: body.index("\ndef ", 1)]
    assert '"-C"' not in body


needs_htslib = pytest.mark.skipif(
    not (shutil.which("bcftools") and shutil.which("tabix")),
    reason="needs bcftools and tabix on PATH (present in the pypgx image, not in CI)",
)


@needs_htslib
def test_the_force_call_writes_calls_and_leaves_the_rest_out(tmp_path):
    """Seven PharmCAT positions, each with its own fate:

    100  SNV       10 reads, all reference     -> 0/0, PharmCAT's own record
    150  deletion  10 reads, all reference     -> 0/0 with PharmCAT's multi-base REF
                                                  (bcftools writes REF=anchor ALT=.,
                                                  which PharmCAT discards)
    200  SNV       10 reads, 5 alt             -> the caller's 0/1 record
    250  SNV        3 reads                    -> left out: too few for reference
    280  SNV       10 reads, 1 alt             -> left out: not "none for anything else"
    330  MNV       20 reads, all carry the MNV -> the caller's records, never 0/0
    390  SNV        no reads                   -> left out
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

    mnv_alt = alt_of(seq[329]) + alt_of(seq[330])
    positions = tmp_path / "pharmcat_positions.vcf"
    records = [
        (100, seq[99], alt_of(seq[99])),
        (150, seq[149:152], seq[149]),
        (200, seq[199], alt_of(seq[199])),
        (250, seq[249], alt_of(seq[249])),
        (280, seq[279], alt_of(seq[279])),
        (330, seq[329:331], mnv_alt),
        (390, seq[389], alt_of(seq[389])),
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
    # (first base, reads, {position: alt-carrying read predicate})
    for start, n, alts in (
        (75, 10, {}),
        (125, 10, {}),
        (175, 10, {199: lambda i: i % 2 == 0}),
        (225, 3, {}),
        (256, 10, {279: lambda i: i == 0}),
        (310, 20, {329: lambda i: True, 330: lambda i: True}),
    ):
        for i in range(n):
            s = list(seq[start : start + 50])
            for at, carries in alts.items():
                if carries(i):
                    s[at - start] = alt_of(seq[at])
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
    status_tsv = tmp_path / "pharmcat.status.tsv"
    stats = ns["genotype_pharmcat_positions"](
        str(bam),
        str(fasta),
        str(out),
        positions=str(positions),
        status_tsv=str(status_tsv),
    )

    with pysam.VariantFile(str(out)) as vf:
        got = {(r.pos, r.ref, r.alts, r.samples["S1"]["GT"]) for r in vf}
    assert got == {
        (100, seq[99], (alt_of(seq[99]),), (0, 0)),
        (150, seq[149:152], (seq[149],), (0, 0)),
        (200, seq[199], (alt_of(seq[199]),), (0, 1)),
        (330, seq[329], (alt_of(seq[329]),), (1, 1)),
        (331, seq[330], (alt_of(seq[330]),), (1, 1)),
    }
    assert stats["n_pharmcat_positions"] == 7
    assert stats["n_reference"] == 2
    assert stats["n_variant"] == 2
    assert stats["n_no_reads"] == 1
    assert stats["n_uncertain"] == 2
    # Per gene, by PharmCAT's PX; and each position's fate, with its reads.
    assert stats["by_gene"] == {
        "TEST": {
            "positions": 7,
            "reference": 2,
            "variant": 2,
            "no_reads": 1,
            "uncertain": 2,
        }
    }
    rows = [l.split("\t") for l in status_tsv.read_text(encoding="utf-8").splitlines()]
    assert rows[0] == list(ns["PHARMCAT_STATUS_COLUMNS"])
    fate = {int(r[1]): (r[3], r[4], r[5], r[6]) for r in rows[1:]}
    assert fate == {
        100: ("TEST", "reference", "10", "0"),
        150: ("TEST", "reference", "10", "0"),
        200: ("TEST", "variant", "5", "5"),
        250: ("TEST", "uncertain", "3", "0"),
        280: ("TEST", "uncertain", "9", "1"),
        330: ("TEST", "variant", "0", "20"),
        390: ("TEST", "no_reads", "", ""),
    }


# 300 bp of GRCh38 around CYP2C19 rs28399504 (chr10:94762556-94762855); the
# position is index 150. Review's BAQ case needs real sequence: on random sequence
# BAQ left the alt reads alone and the test proved nothing.
_CYP2C19_CONTEXT = (
    "GTCAAAGTCCTTTCAGAAGGAGCATATAGTGGGCCTAGGTGATTGGCCACTTTATCCATCAAAGAGGCACACACACTTAATT"
    "AGCATGGAGTGTTATAAAAAGCTTGGAGTGCAAGCTCACGGTTGTCTTAACAAGAGGAGAAGGCTTCAATGGATCCTTTTG"
    "TGGTCCTTGTGCTCTGTCTCTCATGTTTGCTTCTCCTTTCAATCTGGAGACAGAGCTCTGGGAGAGGAAAACTCCCTCCTG"
    "GCCCCACTCCTCTCCCAGTGATTGGAAATATCCTACAGATAGATATTAAGGATGTC"
)


@needs_htslib
def test_evidence_the_caller_does_not_call_still_blocks_reference(tmp_path):
    """Two ways review found a false 0/0 with reads for something else in plain view:

    chr1:100  one read of 10 deletes the position. bcftools reports an indel candidate
              only from two gapped reads by default, and a deleted read counts for
              nothing at the bases it deletes, so the nine others read as reference.
              -> left out (the counting pileup runs with -m 1)
    chr2:151  CYP2C19 rs28399504 A>G, 10 of 20 reads, each also deleting 3 bases 1 bp
              downstream. BAQ drops every alt base beside the gap: the site counted
              10 reads, all reference, and the caller made no call.
              -> left out (the counting pileup runs with -B, and counts 10,10)

    Each assertion fails with its flag removed (checked by hand, bcftools 1.22).
    """
    pysam = pytest.importorskip("pysam")
    import random

    rng = random.Random(11)
    seq = "".join(rng.choice("ACGT") for _ in range(400))
    ctx = _CYP2C19_CONTEXT
    assert ctx[150] == "A"
    fasta = tmp_path / "ref.fa"
    fasta.write_text(f">chr1\n{seq}\n>chr2\n{ctx}\n", encoding="utf-8")
    pysam.faidx(str(fasta))

    alt = "A" if seq[99] != "A" else "C"
    positions = tmp_path / "pharmcat_positions.vcf"
    positions.write_text(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tPharmCAT\n"
        f"chr1\t100\trs1\t{seq[99]}\t{alt}\t.\tPASS\tPX=TEST\tGT\t0/0\n"
        "chr2\t151\trs28399504\tA\tG\t.\tPASS\tPX=CYP2C19\tGT\t0/0\n",
        encoding="utf-8",
    )

    reads = []  # (contig index, 0-based start, sequence, cigar)
    reads.append((0, 70, seq[70:99] + seq[101:132], "29M2D31M"))  # deletes 99-100
    reads += [(0, 70, seq[70:130], "60M")] * 9
    for i in range(20):
        s0 = 125 - (i * 3) % 45
        if i < 10:  # G at 150, then 152-154 deleted
            before = ctx[s0:150] + "G" + ctx[151:152]
            query = before + ctx[155 : 155 + 100 - len(before)]
            reads.append((1, s0, query, f"{len(before)}M3D{100 - len(before)}M"))
        else:
            reads.append((1, s0, ctx[s0 : s0 + 100], "100M"))

    header = {
        "HD": {"VN": "1.6", "SO": "coordinate"},
        "SQ": [{"SN": "chr1", "LN": len(seq)}, {"SN": "chr2", "LN": len(ctx)}],
        "RG": [{"ID": "rg", "SM": "S1"}],
    }
    bam = tmp_path / "aln.bam"
    with pysam.AlignmentFile(str(bam), "wb", header=header) as out:
        for i, (tid, start, s, cigar) in enumerate(sorted(reads)):
            r = pysam.AlignedSegment()
            r.query_name, r.query_sequence, r.flag = f"r{i}", s, 0
            r.reference_id, r.reference_start, r.mapping_quality = tid, start, 60
            r.cigarstring = cigar
            r.query_qualities = pysam.qualitystring_to_array("I" * len(s))
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
        got = {(r.chrom, r.pos): r.samples["S1"]["GT"] for r in vf}
    assert ("chr1", 100) not in got, got
    assert got.get(("chr2", 151)) != (0, 0), got
    assert stats["n_reference"] == 0, stats


# --------------------------------------------------------------------------
# Partial coverage: a call PharmCAT made from some of a gene's positions
# --------------------------------------------------------------------------


def _missing(*positions):
    """missing_positions as pharmcat_parser.position_coverage gives them."""
    return [
        {"position": pos, "rsid": f"rs{i}", "alleles": list(alleles)}
        for i, (pos, alleles) in enumerate(positions)
    ]


def test_a_partly_covered_call_is_named_with_what_it_could_not_assess():
    """Measured on the panel: NUDT15 *1/*1 with *4, *15, *20, *21 unassessed."""
    from app.reports.generator import partial_coverage_alert

    text = partial_coverage_alert(
        [
            {
                "gene": "NUDT15",
                "diplotype": "*1/*1",
                "uncalled_haplotypes": ["*4", "*15", "*20", "*21"],
                "n_positions": 20,
                "missing_positions": _missing(
                    ("chr13:1", ["*4"]),
                    ("chr13:2", ["*15"]),
                    ("chr13:3", ["*20"]),
                    ("chr13:4", ["*21"]),
                    ("chr13:5", ["*4"]),
                ),
            },
            {
                "gene": "CYP2C19",
                "diplotype": "*1/*1",
                "uncalled_haplotypes": [],
                "n_positions": 35,
                "missing_positions": [],
            },
            # No call, and no position missing: already named under "Genes examined
            # without a call", with nothing about positions to add.
            {
                "gene": "RYR1",
                "diplotype": "Unknown/Unknown",
                "uncalled_haplotypes": ["Reference", "c.38T>G"],
            },
        ],
        "bam",
    )
    assert (
        "<li><strong>NUDT15</strong>: 5 of 20 positions not read; alleles not "
        "assessable: *4, *15, *20, *21</li>"
    ) in text
    assert "CYP2C19" not in text and "RYR1" not in text
    assert "could not be assessed" in text
    # No statuses were kept, so no reasons are claimed.
    assert "no reads" not in text.lower()


def test_each_line_says_why_the_positions_were_missing():
    """The alignment's own per-position statuses give the reasons; an allele PharmCAT
    still assessed from its other positions is named as such, not as assessed."""
    from app.reports.generator import partial_coverage_alert

    text = partial_coverage_alert(
        [
            {
                "gene": "CYP2B6",
                "diplotype": "*1/*6",
                "uncalled_haplotypes": ["*30"],
                "n_positions": 48,
                "missing_positions": _missing(
                    ("chr19:100", ["*30"]), ("chr19:200", ["*6", "*7"])
                ),
            },
            {
                "gene": "VKORC1",
                "diplotype": "Unknown/Unknown",
                "uncalled_haplotypes": ["rs9923231 variant (T)"],
                "n_positions": 1,
                "missing_positions": _missing(("chr16:300", ["rs9923231 variant (T)"])),
            },
            {
                "gene": "TPMT",
                "diplotype": "*1/*1",
                "uncalled_haplotypes": [],
                "n_positions": 45,
                "missing_positions": _missing(("chr6:400", ["*3B"])),
            },
        ],
        "cram",
        {
            "chr19:100": "no_reads",
            "chr19:200": "uncertain",
            "chr16:300": "no_reads",
            "chr6:400": "variant",
            "chr6:999": "uncertain",  # read as missing by no gene PharmCAT called
        },
    )
    assert (
        "<li><strong>CYP2B6</strong>: 2 of 48 positions not read (no reads 1, "
        "uncertain 1); alleles not assessable: *30; assessed on their other "
        "positions only: *6, *7</li>"
    ) in text
    # A gene with no call gets its count: that may be why.
    assert (
        "<li><strong>VKORC1</strong>: 1 of 1 position not read (no reads 1); "
        "no call</li>"
    ) in text
    # The reads showed something there: not "no reads", and not nothing.
    assert (
        "<li><strong>TPMT</strong>: 1 of 45 positions not read (variant 1); "
        "assessed on their other positions only: *3B</li>"
    ) in text
    # Each reason used is explained once, and only those.
    assert (
        "In brackets, <em>no reads</em>: no read of mapping quality 1 or more" in text
    )
    assert "<em>uncertain</em>: reads did, but did not settle the genotype" in text
    assert (
        "<em>variant</em>: the reads showed a non-reference allele that PharmCAT"
        in text
    )
    assert "<em>other</em>" not in text and "position list" not in text


def test_the_reasons_always_add_up_to_the_count():
    """A position the status file does not have (its copy of PharmCAT's positions
    was stale: an upgrade without restaging reference/pharmcat/) and a status the
    alert does not name each get a bucket, never a silent shortfall."""
    from app.reports.generator import partial_coverage_alert

    text = partial_coverage_alert(
        [
            {
                "gene": "NUDT15",
                "diplotype": "*1/*1",
                "uncalled_haplotypes": ["*4"],
                "n_positions": 20,
                "missing_positions": _missing(
                    *[(f"chr13:{i}", ["*4"]) for i in range(1, 6)]
                ),
            }
        ],
        "bam",
        {"chr13:1": "no_reads", "chr13:2": "no_reads", "chr13:3": "reference"},
    )
    assert (
        "<strong>NUDT15</strong>: 5 of 20 positions not read (no reads 2, other 1, "
        "not in this run's position list 2)"
    ) in text
    assert "<em>other</em>: another status" in text
    assert (
        "<em>not in this run's position list</em>: the alignment was genotyped" in text
    )


def test_a_plain_vcf_says_not_in_the_vcf_and_leaves_uncalled_genes_alone():
    """A variants-only VCF leaves most of PharmCAT's panel uncalled; those are
    explained under "Genes examined without a call", not listed again here."""
    from app.reports.generator import partial_coverage_alert

    text = partial_coverage_alert(
        [
            {
                "gene": "CYP2C19",
                "diplotype": "*1/*2",
                "uncalled_haplotypes": ["*35"],
                "n_positions": 35,
                "missing_positions": _missing(("chr10:1", ["*35"])),
            },
            {
                "gene": "CFTR",
                "diplotype": "Unknown/Unknown",
                "uncalled_haplotypes": [],
                "n_positions": 93,
                "missing_positions": _missing(*[(f"chr7:{i}", []) for i in range(93)]),
            },
        ],
        "vcf",
        {"chr10:1": "no_reads"},  # not an alignment's: never claimed as reads
    )
    assert "Positions not in the VCF" in text
    assert "1 of 35 positions not in the VCF" in text
    assert "CFTR" not in text
    assert "not read" not in text and "no reads" not in text.lower()


def test_no_partial_coverage_means_no_alert():
    from app.reports.generator import partial_coverage_alert

    assert (
        partial_coverage_alert(
            [
                {
                    "gene": "TPMT",
                    "diplotype": "*1/*1",
                    "uncalled_haplotypes": [],
                    "n_positions": 45,
                    "missing_positions": [],
                }
            ]
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


def test_the_position_statuses_are_read_from_the_published_file(tmp_path):
    from app.reports.generator import POSITION_STATUS_FILE, read_position_statuses

    path = tmp_path / POSITION_STATUS_FILE
    path.parent.mkdir()
    path.write_text(
        "chrom\tposition\trsid\tgene\tstatus\treference_reads\tother_reads\n"
        "chr19\t15878886\trs3952537\tCYP4F2\tuncertain\t4\t0\n"
        "chrX\t154532439\trs1\tG6PD\tno_reads\t\t\n"
        "chr1\t97078987\trs114096998\tDPYD\treference\t31\t0\n",
        encoding="utf-8",
    )
    assert read_position_statuses(str(tmp_path)) == {
        "chr19:15878886": "uncertain",
        "chrX:154532439": "no_reads",
        "chr1:97078987": "reference",
    }
    # A run without one (any input but an alignment, or a run before the file).
    assert read_position_statuses(str(tmp_path / "elsewhere")) == {}


def test_the_parser_counts_the_positions_pharmcat_had_no_genotype_for():
    """PharmCAT's report lists every position of a gene, a null call where it had
    no genotype: NA12878's CYP4F2, from a real run, is 20 positions, two of them
    (rs3952537 for *19, rs4020346 for *17) missing."""
    from app.pharmcat.pharmcat_parser import position_coverage

    def variant(pos, rsid, call, alleles):
        return {
            "chromosome": "chr19",
            "position": pos,
            "dbSnpId": rsid,
            "call": call,
            "alleles": alleles,
        }

    gene = {
        "variants": [variant(15873000 + i, f"rs{i}", "C/C", ["*2"]) for i in range(18)]
        + [
            variant(15878886, "rs3952537", None, ["*19"]),
            variant(15878920, "rs4020346", None, ["*17"]),
        ]
    }
    assert position_coverage(gene) == {
        "n_positions": 20,
        "missing_positions": [
            {"position": "chr19:15878886", "rsid": "rs3952537", "alleles": ["*19"]},
            {"position": "chr19:15878920", "rsid": "rs4020346", "alleles": ["*17"]},
        ],
    }
    # An outside call (CYP2D6, HLA) has no positions of PharmCAT's own.
    assert position_coverage({"variants": []}) == {
        "n_positions": 0,
        "missing_positions": [],
    }
    assert position_coverage(None)["missing_positions"] == []


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


def test_the_missing_positions_reach_the_report_genes():
    from app.services.pharmcat_data_service import PharmCATDataService

    missing = [{"position": "chr13:1", "rsid": "rs1", "alleles": ["*4"]}]
    service = PharmCATDataService.__new__(PharmCATDataService)
    genes = service._transform_genes_for_reports(
        [
            {
                "gene_symbol": "NUDT15",
                "call_source": "MATCHER",
                "uncalled_haplotypes": ["*4"],
                "n_positions": 20,
                "missing_positions": missing,
            }
        ],
        [{"gene_symbol": "NUDT15", "diplotype_label": "*1/*1"}],
    )
    assert genes[0]["n_positions"] == 20
    assert genes[0]["missing_positions"] == missing


def test_an_unannotated_genes_missing_positions_reach_the_report_too():
    """F2 and F5 come from PharmCAT's unannotatedGeneCalls, straight from its JSON.
    Shaped as NA12878's F5 is in a real run's JSON, with the position missing."""
    from app.services.pharmcat_data_service import PharmCATDataService

    f5 = {
        "geneSymbol": "F5",
        "callSource": "MATCHER",
        "sourceDiplotypes": [],
        "uncalledHaplotypes": ["rs6025 Factor V Leiden (T)"],
        "variants": [
            {
                "gene": "F5",
                "chromosome": "chr1",
                "position": 169549811,
                "dbSnpId": "rs6025",
                "call": None,
                "alleles": ["rs6025 Factor V Leiden (T)"],
            }
        ],
    }
    service = PharmCATDataService.__new__(PharmCATDataService)
    (gene,) = service._transform_unannotated_calls_for_reports([f5], set())
    assert gene["gene"] == "F5"
    assert gene["n_positions"] == 1
    assert gene["missing_positions"] == [
        {
            "position": "chr1:169549811",
            "rsid": "rs6025",
            "alleles": ["rs6025 Factor V Leiden (T)"],
        }
    ]


def test_the_parser_reads_them_back_from_the_stored_gene_json():
    src = (REPO / "app" / "pharmcat" / "pharmcat_parser.py").read_text(encoding="utf-8")
    body = src[src.index("def get_gene_summary") :]
    body = body[: body.index("\n    def ", 1)]
    assert '(r.gene_full_data or {}).get("uncalledHaplotypes")' in body
    assert "**position_coverage(r.gene_full_data)" in body


def test_the_generator_appends_the_alert_to_the_rendered_warnings():
    src = (REPO / "app" / "reports" / "generator.py").read_text(encoding="utf-8")
    assert "coverage_alert = partial_coverage_alert(" in src
    assert '(meta.get("file_analysis") or {}).get("file_type")' in src
    assert "workflow_warnings.append(coverage_alert)" in src
    assert "read_position_statuses(output_dir)" in src


def test_the_position_statuses_are_published_beside_pharmcats_vcf():
    """The wrapper hands the file's path back; main.nf copies it where the report
    reads it (generator.POSITION_STATUS_FILE). Optional: it explains PharmCAT's VCF,
    which is used without it."""
    from app.reports.generator import POSITION_STATUS_FILE

    body = _process("PyPGxBam2Vcf")
    line = next(l for l in body.splitlines() if "pharmcat_position_status.tsv" in l)
    assert f'path "{POSITION_STATUS_FILE}", optional: true' in line
    assert "pharmcat_status_path" in body
    assert f'cp "$STATUS_PATH" {POSITION_STATUS_FILE}' in body
    assert '"pharmcat_status_path": res.get("pharmcat_status")' in WRAPPER.read_text(
        encoding="utf-8"
    )


# --------------------------------------------------------------------------
# Positions PharmCAT did not use as found
# --------------------------------------------------------------------------
# PharmCAT 3.4.0's own texts, as review captured them from pharmcat_pipeline runs and
# as stored in this stack's PharmCAT JSON.
_UNDOCUMENTED = (
    "The genetic variation at this position does not match what is in the allele "
    "definition (expected G, found T in VCF).  Undocumented variations will be "
    "replaced with reference."
)
_DISCARDED = (
    "Discarded genotype at this position because REF in VCF (C) does not match "
    "expected reference (CGAT)"
)


def _gene_json():
    return {
        "variants": [
            {
                "chromosome": "chr1",
                "position": 97078993,
                "dbSnpId": "rs148799944",
                "call": "C/T",
                "hasUndocumentedVariations": True,
                "warnings": [_UNDOCUMENTED],
            },
            {
                "chromosome": "chr19",
                "position": 38440747,
                "dbSnpId": "rs193922745",
                "call": None,
                "hasUndocumentedVariations": False,
                "warnings": [_DISCARDED],
            },
            {
                "chromosome": "chr1",
                "position": 97079005,
                "dbSnpId": None,
                "call": None,
                "hasUndocumentedVariations": False,
                "warnings": ["Ignoring: no call (./.)"],
            },
            {
                "chromosome": "chr1",
                "position": 97079071,
                "dbSnpId": "rs1801268",
                "call": "C/C",
                "hasUndocumentedVariations": False,
                "warnings": [],
            },
        ]
    }


def test_the_parser_names_what_pharmcat_did_not_use():
    from app.pharmcat.pharmcat_parser import unread_variants

    assert unread_variants(_gene_json()) == [
        {
            "position": "chr1:97078993",
            "rsid": "rs148799944",
            "call": "C/T",
            "reason": "undocumented",
            "detail": None,
        },
        {
            "position": "chr19:38440747",
            "rsid": "rs193922745",
            "call": None,
            "reason": "discarded",
            "detail": "REF C where PharmCAT expects CGAT",
        },
    ]
    assert unread_variants(None) == []


def test_pharmcats_other_discard_wording_is_read_too():
    """Review: "Discarding genotype ... GT field indicates heterozygous (0/1) but AD
    field indicates homozygous (0,15)" -- PharmCAT's wording, as captured from a
    pharmcat_pipeline run -- starts differently and was missed."""
    from app.pharmcat.pharmcat_parser import unread_variants

    gene = {
        "variants": [
            {
                "chromosome": "chr1",
                "position": 97078993,
                "dbSnpId": "rs148799944",
                "call": None,
                "hasUndocumentedVariations": False,
                "warnings": [
                    "Discarding genotype at this position because GT field indicates "
                    "heterozygous (0/1) but AD field indicates homozygous (0,15)"
                ],
            }
        ]
    }
    assert unread_variants(gene)[0]["reason"] == "discarded"
    assert unread_variants(gene)[0]["detail"] == "genotype and read counts disagree"


def test_the_report_says_what_the_call_did_not_use():
    from app.reports.generator import unread_variants_alert

    text = unread_variants_alert(
        [
            {
                "gene": "DPYD",
                "diplotype": "Reference/Reference",
                "unread_variants": [
                    {
                        "position": "chr1:97078993",
                        "rsid": "rs148799944",
                        "call": "C/T",
                        "reason": "undocumented",
                    }
                ],
            },
            {
                "gene": "RYR1",
                "diplotype": "Reference/Reference",
                "unread_variants": [
                    {
                        "position": "chr19:38440747",
                        "detail": "REF C where PharmCAT expects CGAT",
                        "rsid": None,
                        "call": None,
                        "reason": "discarded",
                    }
                ],
            },
            # Not called at all: already under "examined without a call".
            {
                "gene": "CFTR",
                "diplotype": "Unknown/Unknown",
                "unread_variants": [
                    {
                        "position": "chr7:1",
                        "rsid": "rs1",
                        "call": "A/G",
                        "reason": "undocumented",
                    }
                ],
            },
        ]
    )
    assert (
        "Allele PharmCAT does not define, matched as reference: DPYD rs148799944 (C/T)"
    ) in text
    assert (
        "Record PharmCAT could not read, taken as missing: "
        "RYR1 chr19:38440747 (REF C where PharmCAT expects CGAT)"
    ) in text
    assert "CFTR" not in text


# PharmCAT 3.4.0's preprocessor line, from a live plain-VCF run (job 1709a064): the
# record was dropped and the run's assume-reference setting then made the position
# `TG|TG`.
_PREPROCESSOR_LOG = (
    "Preprocessing...\n"
    '  * WARNING: "chr1:97450065 REF=TGG ALT=T" does not match PharmCAT expectation of '
    'REF at "chr1:97450065 REF=TG ALT=T"\n'
    '  * WARNING: "chr1:97082365 REF=TA ALT=T" does not match PharmCAT expectation of '
    'REF at "chr1:97082365 REF=T ALT=C"\n'
)


def test_the_preprocessors_dropped_records_are_read_from_its_log():
    from app.reports.generator import preprocessor_ref_mismatches

    assert preprocessor_ref_mismatches(_PREPROCESSOR_LOG) == [
        ("chr1", 97450065, "TGG", "TG"),
        ("chr1", 97082365, "TA", "T"),
    ]
    assert preprocessor_ref_mismatches("") == []


def test_a_dropped_record_is_named_with_what_pharmcat_used_instead(tmp_path):
    """Its fate is PharmCAT's own final call there: a call means the assume-reference
    setting filled it in (reference), none means it read as missing."""
    import json as _json

    from app.reports.generator import add_preprocessor_drops, unread_variants_alert

    (tmp_path / "run_pharmcat_pipeline.log").write_text(
        _PREPROCESSOR_LOG, encoding="utf-8"
    )
    (tmp_path / "job_pgx_pharmcat.json").write_text(
        _json.dumps(
            {
                "genes": {
                    "DPYD": {
                        "variants": [
                            {
                                "chromosome": "chr1",
                                "position": 97450065,
                                "dbSnpId": "rs72549303",
                                "call": "TG|TG",
                            },
                            {
                                "chromosome": "chr1",
                                "position": 97082365,
                                "dbSnpId": "rs141044036",
                                "call": None,
                            },
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    genes = [{"gene": "DPYD", "diplotype": "Reference/Reference"}]

    add_preprocessor_drops(genes, str(tmp_path))

    assert [(u["rsid"], u["reason"]) for u in genes[0]["unread_variants"]] == [
        ("rs72549303", "assumed_reference"),
        ("rs141044036", "discarded"),
    ]
    text = unread_variants_alert(genes)
    assert (
        "Record PharmCAT could not read, taken as reference by the assume-reference "
        "setting: DPYD rs72549303 (REF TGG where PharmCAT expects TG)"
    ) in text
    assert "DPYD rs141044036 (REF TA where PharmCAT expects T)" in text


def test_no_artifacts_means_nothing_added(tmp_path):
    from app.reports.generator import add_preprocessor_drops

    genes = [{"gene": "DPYD", "diplotype": "*1/*1"}]
    add_preprocessor_drops(genes, str(tmp_path))
    assert "unread_variants" not in genes[0]


def test_no_unread_positions_means_no_alert():
    from app.reports.generator import unread_variants_alert

    assert unread_variants_alert([{"gene": "DPYD", "diplotype": "*1/*1"}]) is None


def test_the_unread_positions_reach_the_rendered_warnings():
    service = (REPO / "app" / "services" / "pharmcat_data_service.py").read_text(
        encoding="utf-8"
    )
    assert '"unread_variants": list(gene.get("unread_variants") or [])' in service
    parser = (REPO / "app" / "pharmcat" / "pharmcat_parser.py").read_text(
        encoding="utf-8"
    )
    body = parser[parser.index("def get_gene_summary") :]
    assert '"unread_variants": unread_variants(r.gene_full_data)' in body
    generator = (REPO / "app" / "reports" / "generator.py").read_text(encoding="utf-8")
    assert "workflow_warnings.append(unread_alert)" in generator
    assert 'add_preprocessor_drops((data or {}).get("genes"), output_dir)' in generator


def test_a_plain_vcf_is_not_told_its_data_did_not_cover_a_position():
    """Review: on a variants-only VCF a position is not listed, not uncovered."""
    from app.reports.generator import partial_coverage_alert

    genes = [
        {
            "gene": "CYP2C9",
            "diplotype": "*1/*1",
            "uncalled_haplotypes": ["*8"],
            "n_positions": 88,
            "missing_positions": [{"position": "chr10:1", "alleles": ["*8"]}],
        }
    ]
    vcf = partial_coverage_alert(genes, "vcf")
    assert (
        "The VCF has no genotype at" in vcf
        and "1 of 88 positions not in the VCF" in vcf
    )
    assert "not read" not in vcf
    bam = partial_coverage_alert(genes, "bam")
    assert "The alignment gave no genotype at" in bam
    assert "1 of 88 positions not read" in bam
    gvcf = partial_coverage_alert(genes, "gvcf")
    assert "1 of 88 positions without a genotype" in gvcf


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
