"""Reads PyPGx never sees, on GRCh38's ALT contigs, are aligned again before the calls.

An alignment made against a GRCh38 that has the ALT contigs puts some of the CYP2D6
and UGT2B17/UGT2B15 loci's reads on them, and PyPGx reads main contigs only. On
NA12878's reads realigned with plain bwa mem to GRCh38 with ALTs, PyPGx would have
read CYP2D6 from 272 reads instead of 1,479, and at 48% MAPQ 0 the report's "mostly
MAPQ 0" flag did not fire. The PyPGx sidecar now sends those loci's reads to gatk-api
(/realign-reads: BwaSpark against the main contigs, MarkDuplicatesSpark) and calls
from a patched alignment: the uploaded reads over every region it reads, with the
loci's reads replaced by their new alignments. The report says what was done, and
when it could not be done flags the genes whatever their MAPQ 0 share.
"""

from __future__ import annotations

import ast
import csv
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from app.reports.alt_locus import (
    alt_locus_alert,
    alt_locus_diagram_line,
    alt_locus_flags,
    alt_realignment_diagram_line,
    annotate_alt_locus,
    realigned_alt_loci,
)
from tests.test_gatk_api_no_mock_bam import client, gatk_api  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "docker" / "pypgx" / "pypgx_wrapper.py"

NAMES = [
    "ALT_LOCI_GRCH38",
    "ALT_REALIGN_MIN_READS",
    "ALT_REALIGN_PAD",
    "ALT_REALIGN_READ_GROUP",
    "ALT_LOCUS_GENES_GRCH38",
    "alt_loci_to_realign",
    "_locus_regions",
    "_fetch_mates",
    "collect_locus_reads",
    "write_unaligned_reads",
    "RealignmentUnavailable",
    "write_patched_alignment",
    "patched_alignment_regions",
    "WGS_PROBE_WINDOWS_GRCH38",
    "PHARMCAT_PILEUP_PAD",
    "realign_alt_loci",
    "alt_locus_mapq0",
]


def _load(names):
    tree = ast.parse(WRAPPER.read_text(encoding="utf-8"))
    wanted, found = [], set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
            wanted.append(node)
            found.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in names:
                    wanted.append(node)
                    found.add(target.id)
    assert not set(names) - found, f"missing: {sorted(set(names) - found)}"
    ns = {
        "os": os,
        "time": time,
        "shutil": shutil,
        "Path": Path,
        "Any": Any,
        "Dict": Dict,
        "List": List,
        "Optional": Optional,
        "Tuple": Tuple,
        "logger": logging.getLogger("test_realign_alt_loci"),
    }
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(WRAPPER), "exec"), ns)
    return ns


NS = _load(NAMES)
LOCI = {locus["locus"]: locus for locus in NS["ALT_LOCI_GRCH38"]}


# --------------------------------------------------------------------------
# The loci
# --------------------------------------------------------------------------


def test_the_loci_are_ncbi_s_placements_over_pypgx_genes():
    """GCA_000001405.15 alt_scaffold_placement.txt (1-based, inclusive): KB663609.1
    42077656-42139533, KI270928.1 42077656-42253758, GL383582.2 42077656-42252562 on
    chromosome 22 (region CYP2D6); GL000257.2 68306359-69010457 on chromosome 4
    (region UGT2B17). Each span is the union of its ALTs' placements, 0-based."""
    assert LOCI["CYP2D6"]["span"] == ("chr22", 42077655, 42253758)
    assert set(LOCI["CYP2D6"]["alts"]) == {
        "chr22_KB663609v1_alt",
        "chr22_KI270928v1_alt",
        "chr22_GL383582v2_alt",
    }
    assert LOCI["UGT2B17-UGT2B15"]["span"] == ("chr4", 68306358, 69010457)
    assert LOCI["UGT2B17-UGT2B15"]["alts"] == ("chr4_GL000257v2_alt",)
    with open(ROOT / "lexicon" / "gene-table.csv", newline="", encoding="utf-8") as fh:
        table = {row["Gene"]: row for row in csv.DictReader(fh)}
    for locus in LOCI.values():
        chrom, start, end = locus["span"]
        for gene in locus["genes"]:
            g_chrom, g_range = table[gene]["GRCh38Region"].split(":")
            g_start, g_end = (int(x) for x in g_range.split("-"))
            assert "chr" + g_chrom == chrom
            assert start <= g_start and g_end <= end, gene


# --------------------------------------------------------------------------
# A synthetic alignment
# --------------------------------------------------------------------------

pysam = pytest.importorskip("pysam")

CONTIGS = [
    ("chr4", 190214555),
    ("chr22", 50818468),
    ("chr22_KI270928v1_alt", 176103),
    ("chr22_GL383582v2_alt", 162811),
    ("chr4_GL000257v2_alt", 586476),
]
SEQ = "ACGTTGCAAC" * 5  # 50 bases, not a palindrome
QUAL = "".join(chr(33 + 20 + (i % 20)) for i in range(50))
SPAN_AT = 42127000  # inside the CYP2D6 span
OUTSIDE = 43000000  # chr22, outside every locus


def _read(header, name, contig, pos, flag, mate=None, rg=None, mapq=0):
    read = pysam.AlignedSegment(header)
    read.query_name = name
    read.flag = flag
    read.reference_name = contig
    read.reference_start = pos
    read.mapping_quality = mapq
    read.cigarstring = "50M"
    read.query_sequence = SEQ
    read.query_qualities = pysam.qualitystring_to_array(QUAL)
    if mate is not None:
        read.next_reference_name, read.next_reference_start = mate
    if rg:
        read.set_tag("RG", rg)
    return read


def _alignment(tmp_path, records, read_groups=None, name="aln.bam"):
    header = {
        "HD": {"VN": "1.6", "SO": "coordinate"},
        "SQ": [{"SN": n, "LN": ln} for n, ln in CONTIGS],
    }
    if read_groups:
        header["RG"] = read_groups
    path = str(tmp_path / name)
    unsorted = path + ".tmp.bam"
    with pysam.AlignmentFile(unsorted, "wb", header=header) as out:
        for args in records:
            out.write(_read(out.header, *args))
    pysam.sort("-o", path, unsorted)
    os.remove(unsorted)
    pysam.index(path)
    return path


P1, P2 = 0x1 | 0x40, 0x1 | 0x80  # paired, read 1 / read 2
REV = 0x10


def _locus_reads(n_alt=25, rg="L1"):
    """Pairs on the CYP2D6 ALT and primary span, one mate far away, an orphan, a
    single-end read, a secondary and a supplementary record, and a read elsewhere."""
    alt = "chr22_KI270928v1_alt"
    records = []
    for i in range(n_alt):  # both mates on the ALT
        records += [
            (f"alt{i}", alt, 1000 + i, P1, (alt, 1200 + i), rg),
            (f"alt{i}", alt, 1200 + i, P2 | REV, (alt, 1000 + i), rg),
        ]
    records += [
        ("span", "chr22", SPAN_AT, P1, ("chr22", SPAN_AT + 300), rg),
        ("span", "chr22", SPAN_AT + 300, P2 | REV, ("chr22", SPAN_AT), rg),
        ("far", "chr22", SPAN_AT + 10, P1, ("chr22", OUTSIDE), rg),
        ("far", "chr22", OUTSIDE, P2 | REV, ("chr22", SPAN_AT + 10), rg),
        ("orphan", "chr22", SPAN_AT + 20, P1, ("chr22", OUTSIDE + 5000), rg),
        ("single", "chr22", SPAN_AT + 30, 0, None, rg),
        ("span", "chr22", SPAN_AT + 500, P1 | 0x100, ("chr22", SPAN_AT + 300), rg),
        ("span", "chr22", SPAN_AT + 600, P1 | 0x800, ("chr22", SPAN_AT + 300), rg),
        ("elsewhere", "chr22", OUTSIDE + 100, 0, None, rg, 60),
    ]
    return records


def test_a_locus_with_reads_on_its_alt_contigs_is_realigned(tmp_path):
    aln = _alignment(tmp_path, _locus_reads(n_alt=25))
    loci = NS["alt_loci_to_realign"](aln, "GRCh38")
    assert [locus["locus"] for locus in loci] == ["CYP2D6"]
    assert loci[0]["alt_reads"] == 50


def test_too_few_alt_reads_or_another_build_means_no_realignment(tmp_path):
    aln = _alignment(tmp_path, _locus_reads(n_alt=9))  # 18 reads on the ALT
    assert NS["alt_loci_to_realign"](aln, "GRCh38") == []
    assert NS["alt_loci_to_realign"](aln, "GRCh37") == []


def test_the_locus_reads_come_with_their_mates(tmp_path):
    aln = _alignment(tmp_path, _locus_reads())
    with pysam.AlignmentFile(aln) as bam:
        reads, orphans = NS["collect_locus_reads"](bam, [LOCI["CYP2D6"]])
        got = {
            name: [(r.reference_name, r.reference_start, r.flag) for r in records]
            for name, records in reads.items()
        }
    assert orphans == 1 and "orphan" not in got
    # The far mate fetched by its position; primaries only.
    assert got["far"] == [
        ("chr22", SPAN_AT + 10, P1),
        ("chr22", OUTSIDE, P2 | REV),
    ]
    assert got["span"] == [
        ("chr22", SPAN_AT, P1),
        ("chr22", SPAN_AT + 300, P2 | REV),
    ]
    assert got["single"] == [("chr22", SPAN_AT + 30, 0)]
    assert "elsewhere" not in got
    assert sum(1 for name in got if name.startswith("alt")) == 25


def test_mates_far_away_are_fetched_a_window_at_a_time(tmp_path):
    """Two mates in one window, one on another contig; a secondary record of a mate
    at its position is not the mate."""
    records = [
        ("a", "chr22", SPAN_AT, P1, ("chr22", OUTSIDE), "L1"),
        ("a", "chr22", OUTSIDE, P2 | REV, ("chr22", SPAN_AT), "L1"),
        ("a", "chr22", OUTSIDE, P2 | 0x100, ("chr22", SPAN_AT), "L1"),
        ("b", "chr22", SPAN_AT + 5, P2, ("chr22", OUTSIDE + 400), "L1"),
        ("b", "chr22", OUTSIDE + 400, P1 | REV, ("chr22", SPAN_AT + 5), "L1"),
        ("c", "chr22", SPAN_AT + 9, P1, ("chr4", 1000), "L1"),
        ("c", "chr4", 1000, P2 | REV, ("chr22", SPAN_AT + 9), "L1"),
    ]
    aln = _alignment(tmp_path, records)
    with pysam.AlignmentFile(aln) as bam:
        reads, orphans = NS["collect_locus_reads"](bam, [LOCI["CYP2D6"]])
        got = {
            name: [(r.reference_name, r.reference_start, r.flag) for r in pair]
            for name, pair in reads.items()
        }
    assert orphans == 0
    assert got == {
        "a": [("chr22", SPAN_AT, P1), ("chr22", OUTSIDE, P2 | REV)],
        "b": [("chr22", OUTSIDE + 400, P1 | REV), ("chr22", SPAN_AT + 5, P2)],
        "c": [("chr22", SPAN_AT + 9, P1), ("chr4", 1000, P2 | REV)],
    }


def test_the_reads_go_unaligned_in_their_own_orientation(tmp_path):
    aln = _alignment(tmp_path, _locus_reads(), read_groups=[{"ID": "L1", "SM": "S"}])
    with pysam.AlignmentFile(aln) as bam:
        reads, _ = NS["collect_locus_reads"](bam, [LOCI["CYP2D6"]])
    out = NS["write_unaligned_reads"](
        reads, [{"ID": "L1", "SM": "S"}], str(tmp_path / "u")
    )
    assert set(out) == {"paired", "single"}
    with pysam.AlignmentFile(out["paired"], check_sq=False) as paired:
        assert paired.header.to_dict()["RG"] == [{"ID": "L1", "SM": "S"}]
        records = list(paired.fetch(until_eof=True))
    names = [r.query_name for r in records]
    assert names == sorted(names) and len(names) == 2 * 27  # 25 alt + span + far
    for first, second in zip(records[::2], records[1::2]):
        assert first.query_name == second.query_name
        assert first.flag == 0x1 | 0x4 | 0x8 | 0x40
        assert second.flag == 0x1 | 0x4 | 0x8 | 0x80
        assert first.query_sequence == SEQ
        # Read 2 was on the reverse strand: as sequenced, reverse-complemented back.
        assert second.query_sequence == str(
            SEQ[::-1].translate(str.maketrans("ACGT", "TGCA"))
        )
        assert pysam.qualities_to_qualitystring(second.query_qualities) == QUAL[::-1]
        assert first.get_tag("RG") == "L1" and second.get_tag("RG") == "L1"
        assert first.reference_id == -1
    with pysam.AlignmentFile(out["single"], check_sq=False) as single:
        (only,) = list(single.fetch(until_eof=True))
    assert only.query_name == "single" and only.flag == 0x4


def test_reads_without_a_read_group_get_one_for_markduplicates(tmp_path):
    aln = _alignment(tmp_path, [r[:5] + (None,) for r in _locus_reads()])
    with pysam.AlignmentFile(aln) as bam:
        reads, _ = NS["collect_locus_reads"](bam, [LOCI["CYP2D6"]])
    out = NS["write_unaligned_reads"](reads, [], str(tmp_path / "u"))
    with pysam.AlignmentFile(out["paired"], check_sq=False) as paired:
        group = NS["ALT_REALIGN_READ_GROUP"]
        assert paired.header.to_dict()["RG"] == [{"ID": group, "SM": group}]
        assert {r.get_tag("RG") for r in paired.fetch(until_eof=True)} == {group}


def _realigned_bam(tmp_path, names, rg, contigs=None, unknown=False):
    """What gatk-api sends back: its own header (another contig order), the reads
    placed on chr22 inside CYP2D6's span at MAPQ 60."""
    contigs = contigs or [("chr22", 50818468), ("chr4", 190214555)]
    if unknown:
        contigs = contigs + [("chrUn_decoy", 1000)]
    header = {"HD": {"VN": "1.6"}, "SQ": [{"SN": n, "LN": ln} for n, ln in contigs]}
    if rg:
        header["RG"] = [{"ID": rg, "SM": "S"}]
    path = str(tmp_path / "realigned.bam")
    with pysam.AlignmentFile(path, "wb", header=header) as out:
        for i, name in enumerate(sorted(names)):
            out.write(
                _read(out.header, name, "chr22", SPAN_AT + 2 * i, 0, rg=rg, mapq=60)
            )
        if unknown:
            out.write(_read(out.header, "decoy", "chrUn_decoy", 10, 0, rg=rg))
    return path


def test_the_patched_alignment_swaps_the_locus_reads_for_their_new_alignments(
    tmp_path,
):
    groups = [{"ID": "L1", "SM": "S"}]
    aln = _alignment(tmp_path, _locus_reads(), read_groups=groups)
    replaced = {"span", "far", "single"} | {f"alt{i}" for i in range(25)}
    realigned = _realigned_bam(tmp_path, ["span", "far"], "L1", unknown=True)
    regions = {
        # Two intervals the "elsewhere" read overlaps both of: written once.
        "chr22": [[SPAN_AT - 100, SPAN_AT + 1000], [OUTSIDE + 120, OUTSIDE + 200]],
    }
    regions["chr22"].insert(1, [OUTSIDE - 10, OUTSIDE + 110])
    out = str(tmp_path / "patched.bam")
    counts = NS["write_patched_alignment"](
        aln, [realigned], replaced, regions, out, drop_read_group=False
    )
    assert counts == {"kept": 2, "realigned": 2, "dropped": 1}
    assert os.path.exists(out + ".bai")
    with pysam.AlignmentFile(out) as patched:
        header = patched.header.to_dict()
        assert [sq["SN"] for sq in header["SQ"]] == [n for n, _ in CONTIGS]
        assert header["RG"] == groups
        assert "zaropgx-realign" in [pg["ID"] for pg in header["PG"]]
        reads = [
            (r.query_name, r.reference_name, r.reference_start, r.mapping_quality)
            for r in patched.fetch()
        ]
    # Kept: the orphan (not replaced) and the read elsewhere; not the replaced
    # pairs, nor the far mate at OUTSIDE (replaced by name).
    assert ("orphan", "chr22", SPAN_AT + 20, 0) in reads
    assert [r for r in reads if r[0] == "elsewhere"] == [
        ("elsewhere", "chr22", OUTSIDE + 100, 60)
    ]
    assert sorted(r[0] for r in reads if r[3] == 60 and r[0] != "elsewhere") == [
        "far",
        "span",
    ]
    assert reads == sorted(reads, key=lambda r: (r[1] != "chr4", r[2])), "sorted"


def test_a_read_group_made_up_for_markduplicates_is_removed_again(tmp_path):
    aln = _alignment(tmp_path, [r[:5] + (None,) for r in _locus_reads()])
    group = NS["ALT_REALIGN_READ_GROUP"]
    realigned = _realigned_bam(tmp_path, ["span"], group)
    out = str(tmp_path / "patched.bam")
    NS["write_patched_alignment"](
        aln, [realigned], {"span"}, {}, out, drop_read_group=True
    )
    with pysam.AlignmentFile(out) as patched:
        assert "RG" not in patched.header.to_dict()
        (read,) = list(patched.fetch())
    assert not read.has_tag("RG")


def _realigned_flags_bam(tmp_path, flags):
    header = {"HD": {"VN": "1.6"}, "SQ": [{"SN": n, "LN": ln} for n, ln in CONTIGS]}
    path = str(tmp_path / "realigned_flags.bam")
    with pysam.AlignmentFile(path, "wb", header=header) as out:
        for name, flag in flags:
            out.write(_read(out.header, name, "chr22", SPAN_AT, flag, mapq=60))
    return path


@pytest.mark.parametrize("keep", [True, False])
def test_duplicate_flags_follow_the_upload(tmp_path, keep):
    """MarkDuplicatesSpark marks the loci; an upload that never marked duplicates
    keeps them in its control gene's depth, so the loci must keep theirs too."""
    aln = _alignment(tmp_path, [("x", "chr22", OUTSIDE, 0, None, None, 60)])
    realigned = _realigned_flags_bam(tmp_path, [("d", 0x400), ("q", 0)])
    out = str(tmp_path / "patched.bam")
    NS["write_patched_alignment"](
        aln, [realigned], {"d", "q"}, {}, out, False, keep_duplicates=keep
    )
    with pysam.AlignmentFile(out) as patched:
        flags = {r.query_name: r.is_duplicate for r in patched.fetch()}
    assert flags == {"d": keep, "q": False}


def test_the_qc_fail_flag_comes_back(tmp_path):
    """BwaSpark builds each record from name, bases, qualities and read group."""
    aln = _alignment(tmp_path, [("x", "chr22", OUTSIDE, 0, None, None, 60)])
    realigned = _realigned_flags_bam(tmp_path, [("q", P1), ("q", P2), ("ok", 0)])
    out = str(tmp_path / "patched.bam")
    NS["write_patched_alignment"](
        aln,
        [realigned],
        {"q", "ok"},
        {},
        out,
        False,
        qc_failed=frozenset({("q", False)}),
    )
    with pysam.AlignmentFile(out) as patched:
        got = sorted((r.query_name, r.is_read1, r.is_qcfail) for r in patched.fetch())
    assert got == [("ok", False, False), ("q", False, True), ("q", True, False)]


class _Frame:
    """Just enough of a DataFrame for patched_alignment_regions."""

    def __init__(self, rows):
        self.rows = rows

    def __getitem__(self, columns):
        return self

    def itertuples(self, index=False):
        return iter(self.rows)


def test_the_regions_cover_what_the_step_reads(tmp_path, monkeypatch):
    import sys
    import types

    rows = [("chr22", 42116499, 42155810), ("chr12", 47838537, 47907994)]
    bed = types.SimpleNamespace(gr=types.SimpleNamespace(df=_Frame(rows)))
    utils = types.SimpleNamespace(create_regions_bed=lambda **kw: bed)
    api = types.ModuleType("pypgx.api")
    api.utils = utils
    monkeypatch.setitem(sys.modules, "pypgx", types.ModuleType("pypgx"))
    monkeypatch.setitem(sys.modules, "pypgx.api", api)
    vcf = tmp_path / "positions.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n##contig=<ID=chr22,length=50818468>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        "chr22\t43000100\trs1\tAC\tA\t.\t.\t.\n"
    )
    monkeypatch.setitem(NS, "PHARMCAT_POSITIONS_VCF", vcf)
    monkeypatch.setitem(NS, "WGS_PROBE_WINDOWS_GRCH38", (("chr4", 1000, 2000),))
    aln = _alignment(tmp_path, [("x", "chr22", OUTSIDE, 0, None, None, 60)])
    regions = NS["patched_alignment_regions"](aln, "GRCh38")
    pad = NS["PHARMCAT_PILEUP_PAD"]
    # chr12 is not in this alignment's header: left out, not fetched.
    assert set(regions) == {"chr22", "chr4"}
    # PyPGx's 1-based start is fetched from the base before it; the CYP2D6 gene
    # region falls inside its locus span, which merges it.
    assert [42077655, 42253758] in regions["chr22"]
    assert [43000099 - pad, 43000099 + 2 + pad] in regions["chr22"]
    assert regions["chr4"][0] == [1000, 2000]
    assert [68306358, 69010457] in regions["chr4"]


def test_orphans_alone_are_not_reported_as_realigned(tmp_path):
    """Every ALT read without its mate (a sliced BAM): nothing to align, so the
    genes must be flagged, not reported as re-aligned."""
    alt = "chr22_KI270928v1_alt"
    records = [
        (f"o{i}", alt, 1000 + i, P1, ("chr22", OUTSIDE + 10000 + i), "L1")
        for i in range(25)
    ]
    aln = _alignment(tmp_path, records)
    calls_from, info = NS["realign_alt_loci"](aln, "GRCh38", str(tmp_path / "w"))
    assert calls_from == aln
    assert info["status"] == "failed"
    assert "mate" in info["reason"]


# --------------------------------------------------------------------------
# The whole of it, gatk-api faked
# --------------------------------------------------------------------------


def _fake_gatk(tmp_path, sent):
    def request_realignment(unaligned, paired, out_path):
        sent.append((paired, unaligned))
        with pysam.AlignmentFile(unaligned, check_sq=False) as ubam:
            names = sorted({r.query_name for r in ubam.fetch(until_eof=True)})
            group = ubam.header.to_dict()["RG"][0]["ID"]
        shutil.copy(_realigned_bam(tmp_path, names, group), out_path)

    return request_realignment


def test_realign_alt_loci_returns_the_patched_alignment(tmp_path, monkeypatch):
    aln = _alignment(tmp_path, _locus_reads(), read_groups=[{"ID": "L1", "SM": "S"}])
    sent = []
    monkeypatch.setitem(NS, "request_realignment", _fake_gatk(tmp_path, sent))
    monkeypatch.setitem(
        NS, "patched_alignment_regions", lambda path, assembly: {"chr22": []}
    )
    calls_from, info = NS["realign_alt_loci"](aln, "GRCh38", str(tmp_path / "work"))
    assert calls_from == str(tmp_path / "work" / "patched.bam")
    assert info["status"] == "realigned"
    assert info["loci"] == [{"locus": "CYP2D6", "genes": ["CYP2D6"], "alt_reads": 50}]
    assert info["reads"] == {"pairs": 27, "single": 1, "orphans": 1}
    assert [paired for paired, _ in sent] == [True, False]
    with pysam.AlignmentFile(calls_from) as patched:
        assert {r.mapping_quality for r in patched.fetch()} == {60}


def test_without_gatk_the_upload_is_used_and_the_reason_kept(tmp_path, monkeypatch):
    aln = _alignment(tmp_path, _locus_reads())

    def unavailable(unaligned, paired, out_path):
        raise NS["RealignmentUnavailable"]("the bwa-mem index image is not built")

    monkeypatch.setitem(NS, "request_realignment", unavailable)
    calls_from, info = NS["realign_alt_loci"](aln, "GRCh38", str(tmp_path / "work"))
    assert calls_from == aln
    assert info["status"] == "unavailable"
    assert info["reason"] == "the bwa-mem index image is not built"

    def broken(unaligned, paired, out_path):
        raise RuntimeError("gatk-api /realign-reads answered 500: BwaSpark failed")

    monkeypatch.setitem(NS, "request_realignment", broken)
    calls_from, info = NS["realign_alt_loci"](aln, "GRCh38", str(tmp_path / "work2"))
    assert (calls_from, info["status"]) == (aln, "failed")
    assert "BwaSpark failed" in info["reason"]


def test_an_alignment_with_nothing_on_the_alt_contigs_is_left_alone(tmp_path):
    aln = _alignment(tmp_path, _locus_reads(n_alt=0))
    assert NS["realign_alt_loci"](aln, "GRCh38", str(tmp_path / "w")) == (aln, None)


def test_every_reader_in_the_step_reads_the_patched_alignment():
    src = WRAPPER.read_text(encoding="utf-8")
    body = src[src.index("def run_pypgx_create_input_vcf") :]
    body = body[: body.index("\ndef ", 1)]
    after = body[body.index("realign_alt_loci(") :]
    for reader in (
        "str(output_vcf_gz), str(fasta), calls_from,",
        "uncovered_genes_in_alignment(calls_from,",
        "alignment_sample_name(calls_from)",
        "alt_locus_mapq0(calls_from,",
        "prepare_sv_inputs(\n            calls_from,",
        "genotype_pharmcat_positions(\n                calls_from,",
    ):
        assert reader in after, reader
    assert '"alt_realignment": alt_realignment' in body
    assert "shutil.rmtree(realign_dir" in body, "the patched alignment goes after"
    route = src[src.index("async def create_input_vcf(") :]
    complete = route[route.index("complete_step(") :]
    complete = complete[: complete.index(")\n")]
    assert '"alt_realignment": res.get("alt_realignment")' in complete


def test_the_patched_alignment_covers_every_region_the_step_reads():
    """A reader added to /create-input-vcf must add its regions, or a re-aligned run
    reads nothing there. Pin the list so the two change together."""
    src = WRAPPER.read_text(encoding="utf-8")
    body = src[src.index("def patched_alignment_regions") :]
    body = body[: body.index("\ndef ", 1)]
    for source in (
        "create_regions_bed(",
        "WGS_PROBE_WINDOWS_GRCH38",
        "ALT_LOCUS_GENES_GRCH38",
        "ALT_LOCI_GRCH38",
        "PHARMCAT_POSITIONS_VCF",
        "PHARMCAT_PILEUP_PAD",
    ):
        assert source in body, source


# --------------------------------------------------------------------------
# What the report says
# --------------------------------------------------------------------------


def _realigned_output():
    return {
        "alt_locus_mapq0": {
            "CYP2D6": {"reads": 1512, "mapq0": 90},
            "UGT2B15": {"reads": 5400, "mapq0": 100},
        },
        "alt_realignment": {
            "status": "realigned",
            "loci": [
                {"locus": "CYP2D6", "genes": ["CYP2D6"], "alt_reads": 32700},
                {
                    "locus": "UGT2B17-UGT2B15",
                    "genes": ["UGT2B17", "UGT2B15"],
                    "alt_reads": 67754,
                },
            ],
            "reads": {"pairs": 95594, "single": 0, "orphans": 0},
            "mapq0_before": {
                "CYP2D6": {"reads": 526, "mapq0": 254},
                "UGT2B15": {"reads": 2428, "mapq0": 1646},
            },
        },
    }


def test_realigned_loci_are_reported_not_flagged():
    output = _realigned_output()
    assert alt_locus_flags(output) == {}
    alert = alt_locus_alert(output)
    assert alert.startswith(
        "<p><strong>Re-aligned before genotyping:</strong> CYP2D6, UGT2B17 and "
    )
    assert "32,700 for CYP2D6, 67,754 for UGT2B17 and UGT2B15" in alert
    assert "95,594 read pairs" in alert
    assert "and marked duplicates." in alert
    output["alt_realignment"]["duplicates_marked"] = False
    assert "leaving duplicates unmarked, as the upload left them." in (
        alt_locus_alert(output)
    )
    output["alt_realignment"]["duplicates_marked"] = True
    assert "CYP2D6 526 before (48% at mapping quality 0), 1,512 after (6%)" in alert
    assert "UGT2B15 2,428 before (68% at mapping quality 0)" in alert
    assert "⚠️" not in alert
    assert alt_realignment_diagram_line(output) == (
        "re-aligned without ALT contigs: CYP2D6, UGT2B17, UGT2B15"
    )
    assert realigned_alt_loci(output) is True


def test_loci_left_on_their_alt_contigs_are_flagged_whatever_their_mapq0():
    """NA12878 without ALT-awareness: CYP2D6 at 48% MAPQ 0 slipped under the 50% flag
    while PyPGx had 272 of its 1,479 usable reads."""
    output = _realigned_output()
    output["alt_realignment"].update(
        status="unavailable", reason="the bwa-mem index image is not built"
    )
    output["alt_locus_mapq0"] = {
        "CYP2D6": {"reads": 526, "mapq0": 254},
        "UGT2B15": {"reads": 2428, "mapq0": 1646},
    }
    flags = alt_locus_flags(output)
    assert flags["CYP2D6"] == {
        "reads": 526,
        "mapq0": 254,
        "percent": 48,
        "alt_reads": 32700,
    }
    assert flags["UGT2B15"]["percent"] == 68 and flags["UGT2B15"]["alt_reads"] == 67754
    assert "UGT2B17" not in flags, "its reads do not go to GL000257, which lacks it"
    alert = alt_locus_alert(output)
    assert "Reads on ALT contigs or with mapping quality 0: CYP2D6 (32,700 reads" in (
        alert
    )
    assert "UGT2B15 (68% of 2,428 reads at mapping quality 0; 67,754 reads" in alert
    assert "could not (the bwa-mem index image is not built)" in alert
    assert "Re-aligned before genotyping" not in alert
    assert alt_locus_diagram_line(flags).startswith("reads on ALT contigs, not re-al")
    genes = [{"gene": "CYP2D6", "diplotype": "*1/*2"}]
    annotate_alt_locus(genes, flags)
    assert genes[0]["alt_locus"]["note"].startswith(
        "32,700 reads from this gene's locus were on GRCh38's ALT contigs"
    )
    assert realigned_alt_loci(output) is False


def test_a_record_from_before_the_realignment_reads_as_it_did():
    output = {"alt_locus_mapq0": {"CYP2D6": {"reads": 266, "mapq0": 155}}}
    assert alt_locus_flags(output) == {
        "CYP2D6": {"reads": 266, "mapq0": 155, "percent": 58}
    }
    assert "⚠️ Reads with mapping quality 0: CYP2D6 (58% of 266 reads)" in (
        alt_locus_alert(output)
    )
    assert alt_realignment_diagram_line(output) is None


def test_the_credits_name_bwa_and_gatk_for_a_realigned_run():
    from app.reports.credits import CREDITS
    from app.reports.generator import credit_run_for_job

    records = [
        {"name": "pypgx_bam2vcf", "status": "completed", "output": _realigned_output()},
        {"name": "pypgx_analysis", "status": "completed", "output": {}},
    ]
    run = credit_run_for_job(records, "bam")
    by_key = {credit.key: credit for credit in CREDITS}
    assert by_key["bwa"].when(run) and by_key["gatk"].when(run)
    assert by_key["bwa"].used_for_in(run) == (
        "Aligns to GRCh38, with GATK's BwaSpark, the CYP2D6 and UGT2B loci's reads"
    )
    assert by_key["gatk"].used_for_in(run) == (
        "Re-alignment of the CYP2D6 and UGT2B loci"
    )
    plain = credit_run_for_job(records[1:], "bam")
    assert not by_key["bwa"].when(plain)


# --------------------------------------------------------------------------
# gatk-api /realign-reads
# --------------------------------------------------------------------------


@pytest.fixture()
def gatk(gatk_api, monkeypatch, tmp_path):  # noqa: F811
    fasta = tmp_path / "ref.fasta"
    fasta.write_text(">chr1\nACGT\n", encoding="utf-8")
    image = tmp_path / "ref.fasta.img"
    image.write_bytes(b"index image")
    monkeypatch.setattr(gatk_api, "ALIGN_REFERENCE_FASTA", str(fasta))
    monkeypatch.setattr(gatk_api, "ALIGN_INDEX_IMAGE", str(image))
    ran = []

    def run_align_step(job_label, argv, label):
        ran.append(argv)
        out = argv[argv.index("-O") + 1]
        Path(out).write_bytes(b"\x1f\x8b" + label.encode())

    monkeypatch.setattr(gatk_api, "_run_align_step", run_align_step)
    monkeypatch.setattr(gatk_api, "_warm_index_image", lambda *a: 0)
    return gatk_api, ran


UBAM = b"\x1f\x8b unaligned reads"


def test_realign_reads_aligns_then_marks_duplicates(gatk, client):  # noqa: F811
    module, ran = gatk
    resp = client.post(
        "/realign-reads",
        files={"file": ("reads.bam", UBAM, "application/octet-stream")},
        data={"paired": "true"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.content == b"\x1f\x8bMarkDuplicatesSpark"
    assert [argv[1] for argv in ran] == ["BwaSpark", "MarkDuplicatesSpark"]
    bwa = ran[0]
    assert bwa[bwa.index("--single-end-alignment") + 1] == "false"
    assert bwa[bwa.index("--bwa-mem-index-image") + 1] == module.ALIGN_INDEX_IMAGE
    assert bwa[bwa.index("-I") + 1].endswith(".bam"), "GATK reads by extension"
    work = Path(bwa[bwa.index("-I") + 1]).parent
    assert not work.exists(), "the work dir goes once the BAM is sent"


def test_realign_reads_single_end(gatk, client):  # noqa: F811
    _, ran = gatk
    client.post(
        "/realign-reads",
        files={"file": ("reads.bam", UBAM, "application/octet-stream")},
        data={"paired": "false"},
    )
    assert ran[0][ran[0].index("--single-end-alignment") + 1] == "true"


def test_realign_reads_refusals(gatk, client, monkeypatch):  # noqa: F811
    module, ran = gatk
    resp = client.post(
        "/realign-reads",
        files={"file": ("reads.bam", b"plain text", "application/octet-stream")},
    )
    assert resp.status_code == 400
    monkeypatch.setattr(module, "REALIGN_MAX_UPLOAD_BYTES", 4)
    resp = client.post(
        "/realign-reads",
        files={"file": ("reads.bam", UBAM, "application/octet-stream")},
    )
    assert resp.status_code == 413
    monkeypatch.setattr(module, "ALIGN_INDEX_IMAGE", "/nonexistent.img")
    resp = client.post(
        "/realign-reads",
        files={"file": ("reads.bam", UBAM, "application/octet-stream")},
    )
    assert resp.status_code == 503
    assert "build-align-index.sh" in resp.json()["detail"]
    assert ran == []
