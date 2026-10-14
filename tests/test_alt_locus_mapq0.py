"""CYP2D6 or UGT2B15 whose reads mostly have mapping quality 0 is flagged: the
alignment was not ALT-aware.

Found 2026-10-13 on a whole genome aligned with plain ``bwa mem`` to NCBI's full GRCh38,
which has ALT contigs and no ``.alt`` file beside the index. Two ALT contigs cover the
whole of CYP2D6, one UGT2B15, and the seven MHC haplotypes HLA-A, -B and -C. A read
from those genes matches two places equally, gets mapping quality 0 and lands on
either. PyPGx calls CYP2D6 and UGT2B15 from reads at MAPQ >= 1 and their copy number
from the primary copy's depth. NA12878's reads, realigned without ALT-awareness: 73%
and 82% at MAPQ 0; ALT-aware, at most 16%.

HLA-A, -B and -C are measured and never flagged: zarohla gives OptiType every MHC and
ALT-contig read whatever its MAPQ, and its MAPQ >= 1 probe only gates typing.

The PyPGx sidecar counts the reads when it makes the VCF (it has the alignment), the
counts travel on the pypgx_bam2vcf step's output_data, and the report flags a gene
above half: an alert, a note beside the call, a note to the Executive Summary, and a
line on the workflow diagram. Calls are kept.
"""

from __future__ import annotations

import ast
import csv
import re
from pathlib import Path
from typing import Dict, Optional

import pytest

from app.reports.alt_locus import (
    FLAGGED_GENES,
    MAPQ0_SHARE_FLAGGED,
    MIN_READS,
    alt_locus_alert,
    alt_locus_diagram_line,
    alt_locus_flags,
    annotate_alt_locus,
)

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "docker" / "pypgx" / "pypgx_wrapper.py"


def _load(names):
    tree = ast.parse(WRAPPER.read_text(encoding="utf-8"))
    wanted, found = [], set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            wanted.append(node)
            found.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in names:
                    wanted.append(node)
                    found.add(t.id)
    assert not set(names) - found, f"missing: {sorted(set(names) - found)}"
    ns = {"Optional": Optional, "Dict": Dict}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(WRAPPER), "exec"), ns)
    return ns


NS = _load(["ALT_LOCUS_GENES_GRCH38", "alt_locus_mapq0"])
GENES = {
    gene: (chrom, start, end)
    for gene, chrom, start, end in NS["ALT_LOCUS_GENES_GRCH38"]
}


# --------------------------------------------------------------------------
# The sidecar's count, on a real BAM
# --------------------------------------------------------------------------

LENGTHS = {"chr22": 50818468, "chr4": 190214555, "chr6": 170805979}


def _bam(tmp_path, reads, prefixed=True, contigs=("chr22", "chr6")):
    pysam = pytest.importorskip("pysam")
    names = [c if prefixed else c[len("chr") :] for c in contigs]
    header = {
        "HD": {"VN": "1.6", "SO": "coordinate"},
        "SQ": [{"SN": n, "LN": LENGTHS[c]} for n, c in zip(names, contigs)],
    }
    path = str(tmp_path / "aln.bam")
    with pysam.AlignmentFile(path, "wb", header=header) as out:
        for i, (contig, start, mapq, flag) in enumerate(
            sorted(reads, key=lambda r: (contigs.index(r[0]), r[1]))
        ):
            read = pysam.AlignedSegment()
            read.query_name = f"r{i}"
            read.query_sequence = "A" * 50
            read.flag = flag
            read.reference_id = contigs.index(contig)
            read.reference_start = start
            read.mapping_quality = mapq
            read.cigarstring = "50M"
            read.query_qualities = pysam.qualitystring_to_array("I" * 50)
            out.write(read)
    pysam.index(path)
    return path


def _cyp2d6_reads():
    _, start, _ = GENES["CYP2D6"]
    at = start + 100
    return (
        [("chr22", at, 0, 0)] * 30  # MAPQ 0: counted, and counted as MAPQ 0
        + [("chr22", at, 60, 0)] * 10  # placed
        + [
            ("chr22", at, 0, 1024),  # duplicate
            ("chr22", at, 0, 256),  # secondary
            ("chr22", at, 0, 2048),  # supplementary
            ("chr22", start - 5000, 0, 0),  # outside the gene body
        ]
        + [("chr6", GENES["HLA-A"][1] + 10, 60, 0)] * 5
    )


def test_counts_primary_non_duplicate_reads_over_the_gene_body(tmp_path):
    counts = NS["alt_locus_mapq0"](_bam(tmp_path, _cyp2d6_reads()), "GRCh38")
    assert counts["CYP2D6"] == {"reads": 40, "mapq0": 30}
    assert counts["HLA-A"] == {"reads": 5, "mapq0": 0}
    assert counts["HLA-B"] == {"reads": 0, "mapq0": 0}
    # chr4 is not in this alignment: UGT2B15 is not measured, not "0 reads".
    assert "UGT2B15" not in counts


def test_contigs_without_the_chr_prefix_are_read(tmp_path):
    counts = NS["alt_locus_mapq0"](
        _bam(tmp_path, _cyp2d6_reads(), prefixed=False), "GRCh38"
    )
    assert counts["CYP2D6"] == {"reads": 40, "mapq0": 30}


def test_only_grch38_is_measured(tmp_path):
    assert NS["alt_locus_mapq0"]("unused.bam", "GRCh37") is None


def test_gene_bodies_are_pypgx_exon_spans_and_zarohla_hla_loci():
    with open(ROOT / "lexicon" / "gene-table.csv", newline="", encoding="utf-8") as fh:
        table = {row["Gene"]: row for row in csv.DictReader(fh)}
    for gene in ("CYP2D6", "UGT2B15"):
        starts = [int(x) for x in table[gene]["GRCh38ExonStarts"].strip(",").split(",")]
        ends = [int(x) for x in table[gene]["GRCh38ExonEnds"].strip(",").split(",")]
        chrom = "chr" + table[gene]["GRCh38Region"].split(":")[0]
        assert GENES[gene] == (chrom, min(starts), max(ends))
    # zarohla's probe pads each HLA gene by 1 kb (0-based start - 1000, end + 1000).
    zarohla = (ROOT / "docker" / "zarohla" / "app.py").read_text(encoding="utf-8")
    for gene in ("HLA-A", "HLA-B", "HLA-C"):
        _, start, end = GENES[gene]
        assert f"({start + 1 - 1000}, {end + 1000}),  # {gene}" in zarohla


def test_create_input_vcf_measures_and_records_the_counts():
    src = WRAPPER.read_text(encoding="utf-8")
    body = src[src.index("def run_pypgx_create_input_vcf") :]
    body = body[: body.index("\ndef ", 1)]
    assert "alt_locus_mapq0(str(alignment_path), str(assembly))" in body
    assert '"alt_locus_mapq0": alt_locus' in body
    route = src[src.index("async def create_input_vcf(") :]
    route = route[: route.index("\ndef ", 1)]
    # On the step row, which is where the report reads it.
    complete = route[route.index("complete_step(") :]
    complete = complete[: complete.index(")\n")]
    assert '"alt_locus_mapq0": res.get("alt_locus_mapq0")' in complete


# --------------------------------------------------------------------------
# Which genes the report flags
# --------------------------------------------------------------------------


def _output(**genes):
    return {"pharmcat_positions": {}, "alt_locus_mapq0": genes}


def test_more_than_half_at_mapq0_is_flagged():
    flags = alt_locus_flags(
        _output(
            CYP2D6={"reads": 546, "mapq0": 400},  # NA12878, not ALT-aware: 73%
            UGT2B15={"reads": 5553, "mapq0": 115},  # ALT-aware: 2%
        )
    )
    assert flags == {"CYP2D6": {"reads": 546, "mapq0": 400, "percent": 73}}
    flags = alt_locus_flags(_output(UGT2B15={"reads": 100, "mapq0": 50}))
    assert flags == {}, "exactly half is not most"
    assert MAPQ0_SHARE_FLAGGED == 0.5
    assert FLAGGED_GENES == ("CYP2D6", "UGT2B15")


def test_hla_is_never_flagged():
    """OptiType gets every read in the MHC and on its ALT contigs, whatever its
    mapping quality, and maps them itself: MAPQ 0 there does not weaken the call."""
    output = _output(
        **{
            "HLA-A": {"reads": 367, "mapq0": 291},
            "HLA-B": {"reads": 567, "mapq0": 465},
            "HLA-C": {"reads": 187, "mapq0": 185},
        }
    )
    assert alt_locus_flags(output) == {}
    assert alt_locus_alert(output) is None
    rows = [{"gene": "HLA-B", "diplotype": "*08:01/*56:01"}]
    annotate_alt_locus(rows, alt_locus_flags(output))
    assert "alt_locus" not in rows[0]


def test_too_few_reads_say_nothing():
    flags = alt_locus_flags(
        _output(CYP2D6={"reads": MIN_READS - 1, "mapq0": MIN_READS - 1})
    )
    assert flags == {}
    flags = alt_locus_flags(_output(CYP2D6={"reads": MIN_READS, "mapq0": MIN_READS}))
    assert list(flags) == ["CYP2D6"]


@pytest.mark.parametrize(
    "output",
    [
        None,
        {},
        {"alt_locus_mapq0": None},
        {"alt_locus_mapq0": ["CYP2D6"]},
        {"alt_locus_mapq0": {"CYP2D6": "lots"}},
        {"alt_locus_mapq0": {"CYP2D6": {"reads": "x"}}},
    ],
)
def test_nothing_measured_or_unreadable_flags_nothing(output):
    assert alt_locus_flags(output) == {}


def test_the_alert_says_what_why_consequence_and_remedy():
    alert = alt_locus_alert(
        _output(
            CYP2D6={"reads": 546, "mapq0": 400},
            UGT2B15={"reads": 2748, "mapq0": 2264},
        )
    )
    text = re.sub(r"<[^>]+>", "", alert)
    assert "CYP2D6 (73% of 546 reads); UGT2B15 (82% of 2,748 reads)" in text
    assert "mapping quality 0" in text
    assert "ALT-aware" in text
    assert "mapping quality 1 or more" in text
    assert "may be wrong" in text
    assert "copy number can read as a deletion" in text
    assert "no-alt analysis set" in text
    assert ".alt" in text
    assert "HLA" not in text
    assert alt_locus_alert({}) is None


def test_the_alert_says_why_mostly_mapq0_hla_is_not_flagged():
    text = re.sub(
        r"<[^>]+>",
        "",
        alt_locus_alert(
            _output(
                CYP2D6={"reads": 546, "mapq0": 400},
                **{
                    "HLA-A": {"reads": 367, "mapq0": 291},
                    "HLA-B": {"reads": 2861, "mapq0": 1},
                    "HLA-C": {"reads": 187, "mapq0": 185},
                },
            )
        ),
    )
    assert "CYP2D6 (73% of 546 reads)." in text
    assert "HLA-A and HLA-C also had mostly mapping quality 0" in text
    assert "HLA-B" not in text
    assert "whatever its mapping quality" in text
    assert "HLA typing can miss" not in text


def test_flagged_rows_carry_the_note():
    flags = alt_locus_flags(_output(CYP2D6={"reads": 546, "mapq0": 400}))
    rows = [
        {"gene": "CYP2D6", "diplotype": "*5/*5"},
        {"gene": "CYP2C19", "diplotype": "*1/*2"},
    ]
    annotate_alt_locus(rows, flags)
    assert rows[0]["alt_locus"]["percent"] == 73
    assert rows[0]["alt_locus"]["note"] == (
        "73% of the reads over this gene had mapping quality 0 (the alignment was "
        "not ALT-aware), so its call may be wrong. See Alerts and Warnings."
    )
    assert rows[0]["alt_locus"]["call_note"] == (
        "73% of reads had mapping quality 0: this call may be wrong (see Alerts)"
    )
    assert "alt_locus" not in rows[1]


def test_the_workflow_diagram_names_the_genes():
    from app.reports.generator import workflow_run_facts

    records = [
        {
            "name": "pypgx_bam2vcf",
            "status": "completed",
            "seconds": 60,
            "output": _output(
                CYP2D6={"reads": 546, "mapq0": 400},
                UGT2B15={"reads": 2748, "mapq0": 2264},
            ),
        }
    ]
    facts = workflow_run_facts(records, [], [], None, False, 0, [])
    assert facts.details["pypgx_bam2vcf"] == [
        alt_locus_diagram_line(alt_locus_flags(records[0]["output"]))
    ]
    assert "CYP2D6, UGT2B15" in facts.details["pypgx_bam2vcf"][0]


def test_report_generation_reads_the_step_row_and_annotates_after_the_gene_list():
    src = (ROOT / "app" / "reports" / "generator.py").read_text(encoding="utf-8")
    body = src[src.index("def generate_report(") :]
    flags_at = body.index("alt_locus = alt_locus_flags(")
    assert body.index("workflow_records = load_job_step_records(") < flags_at
    assert flags_at < body.index('"workflow_warnings": workflow_warnings')
    # After the canonical list: PyPGx-only genes (UGT2B15) exist only from there on.
    assert body.index('data["genes"] = canonical_diplotypes') < body.index(
        'annotate_alt_locus(data.get("genes", []), alt_locus)'
    )


# --------------------------------------------------------------------------
# What the reports show
# --------------------------------------------------------------------------


def _flagged_rows():
    rows = [
        {"gene": "CYP2D6", "diplotype": "*5/*5", "phenotype": "Poor Metabolizer"},
        {"gene": "UGT2B15", "diplotype": "*1/*2", "phenotype": "N/A"},
    ]
    annotate_alt_locus(
        rows,
        alt_locus_flags(
            _output(
                CYP2D6={"reads": 546, "mapq0": 400},
                UGT2B15={"reads": 2748, "mapq0": 2264},
            )
        ),
    )
    return rows


def _render_pdf_template(rows, execsum=True):
    from app.reports.generator import env

    tsv = {
        "gene": "CYP2D6",
        "diplotype_display": "*5/*5",
        "rec_lookup_diplotype": "*5/*5",
        "rec_lookup_phenotype": "Poor Metabolizer",
        "rec_lookup_activity_score": 0,
    }
    return env.get_template("report_template.html").render(
        execsum_from_tsv=[tsv] if execsum else [],
        diplotypes=rows,
        recommendations=[],
        patient_id="p",
        report_id="r",
        report_date="2026-10-14",
        organization="ZaroPGx",
        disclaimer="",
    )


def test_the_pdf_flags_the_call_the_summary_row_and_its_notes():
    rows = _flagged_rows() + [{"gene": "HLA-B", "diplotype": "*08:01/*56:01"}]
    annotate_alt_locus(
        rows, alt_locus_flags(_output(**{"HLA-B": {"reads": 567, "mapq0": 465}}))
    )
    page = _render_pdf_template(rows)
    hla_b = page[page.index('id="summary-HLA-B"') :]
    assert "⚠" not in hla_b[: hla_b.index("</tr>")]
    summary = page[
        page.index('id="exec-summary"') : page.index('id="exec-summary-notes"')
    ]
    row = summary[summary.index('id="summary-CYP2D6"') :]
    row = row[: row.index("</tr>")]
    assert "⚠" in row
    notes = page[page.index('id="exec-summary-notes"') :]
    notes = re.sub(r"\s+", " ", notes[: notes.index("Genetic Results")])
    assert (
        "<strong>⚠ CYP2D6</strong>: 73% of the reads over this gene had mapping "
        "quality 0" in notes
    )
    # UGT2B15 is outside PharmCAT's panel: no summary note, but its call is flagged.
    assert "UGT2B15" not in notes
    results = page[page.index("Genetic Results") :]
    cyp2d6 = results[results.index("<td>CYP2D6</td>") :]
    assert "⚠ 73% of reads had mapping quality 0" in cyp2d6[: cyp2d6.index("</tr>")]
    ugt2b15 = results[results.index("<td>UGT2B15</td>") :]
    assert "⚠ 82% of reads had mapping quality 0" in ugt2b15[: ugt2b15.index("</tr>")]


def test_an_unflagged_report_is_unchanged():
    rows = [{"gene": "CYP2D6", "diplotype": "*1/*2", "phenotype": "Normal Metabolizer"}]
    page = _render_pdf_template(rows)
    assert "mapping quality 0" not in page
    assert "alt-locus-note" not in page.split("</style>")[-1]


def test_the_interactive_report_flags_the_call():
    from app.reports.generator import env

    macros = env.get_template("_pypgx_evidence.html").module
    note = str(macros.call_notes(_flagged_rows()[0]))
    assert 'class="call-note alt-locus-note"' in note
    assert "73% of reads had mapping quality 0: this call may be wrong" in note
    interactive = (
        ROOT / "app" / "reports" / "templates" / "interactive_report.html"
    ).read_text(encoding="utf-8")
    assert interactive.count("pypgx.call_notes(d)") >= 2
    assert ".alt-locus-note" in interactive


def test_an_uncalled_gene_gets_no_call_note_only_the_alert():
    """CYP2D6 without a call: there is no call to question, so no note says "this
    call may be wrong"; the alert still names the gene and its reads."""
    from app.reports.generator import env

    output = _output(CYP2D6={"reads": 187, "mapq0": 185})
    rows = [{"gene": "CYP2D6", "diplotype": ""}]
    annotate_alt_locus(rows, alt_locus_flags(output))
    macros = env.get_template("_pypgx_evidence.html").module
    assert "alt-locus-note" not in str(macros.call_notes(rows[0]))
    page = _render_pdf_template(rows, execsum=False)
    summary = page[page.index('id="exec-summary"') :]
    row = summary[summary.index('id="summary-CYP2D6"') :]
    assert "⚠" not in row[: row.index("</tr>")]
    assert "this gene had mapping quality 0" not in page
    assert "CYP2D6 (99% of 187 reads)" in alt_locus_alert(output)
