"""The reads' own view of a call's phase, and the note where it contradicts the call.

PyPGx phases with Beagle, from a reference panel. Two heterozygous variants close
enough for one fragment to cover both are checkable without a phaser, and on the 61
GeT-RM genomes the reads decided 413 of 719 such pairs and agreed with the call in 411
(scripts/getrm/arrangement.py). The two they contradicted were CYP2D6 ``*2/*21`` in
HG00589, where 7 fragments to 0 put the ``*2`` cores on the ``*21`` chromosome, and
CYP2F1 in NA19026.

The counting lives in docker/pypgx/read_phase.py, which has the reads; the comparison
with what each call asserts is in app/reports/read_phase.py, which has the call. Both
are tested here, the counting against synthetic fragments whose arrangement is known
and the comparison against HG00589's own numbers.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from app.reports import read_phase as report_phase

SERVICE = Path(__file__).resolve().parents[1] / "docker" / "pypgx" / "read_phase.py"
CHR22 = 50_818_468  # GRCh38's length, so a real coordinate can be used


def _service():
    """docker/pypgx/read_phase.py, loaded by path as the image lays it out."""
    pytest.importorskip("pysam")
    spec = importlib.util.spec_from_file_location("service_read_phase", SERVICE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# HG00589's CYP2D6 call: PyPGx printed *2/*21, Beagle putting the two *2 cores
# opposite the *21 insertion. The reads put them together, which makes that
# chromosome *21 and the other reference -- GeT-RM's *1/*21.
HG00589_DETAILS = {
    "haplotype1": ["*2"],
    "haplotype2": ["*21"],
    "supporting_variants": [
        {"allele": "*21", "variants": [{"variant": "22-42128211-C-CG"}]},
        {
            "allele": "*2",
            "variants": [
                {"variant": "22-42126611-C-G"},
                {"variant": "22-42127941-G-A"},
            ],
        },
    ],
}
HG00589_RESULTS = {
    "results": {
        "CYP2D6": {
            "success": True,
            # The call the report prints: PharmCAT takes CYP2D6 from PyPGx as an
            # outside call, so without it there is no CYP2D6 row to annotate.
            "diplotype": "*2/*21",
            "details": HG00589_DETAILS,
        }
    }
}
HG00589_EVIDENCE = {
    "status": "done",
    "pairs": [
        {
            "gene": "CYP2D6",
            "a": "22-42127941-G-A",
            "b": "22-42128211-C-CG",
            "distance": 270,
            "cis": 7,
            "trans": 0,
            "shows": "cis",
        }
    ],
}


def _row(gene="CYP2D6", tool="PYPGX", diplotype="*2/*21"):
    """A report gene row, as the generator builds one.

    Provenance is what the run recorded, never the gene name
    (app/reports/provenance.py): PharmCAT takes CYP2D6 from PyPGx as an outside call,
    and its own matcher's calls say "MATCHER".
    """
    if tool == "PHARMCAT":
        return {"gene": gene, "diplotype": diplotype, "call_source": "MATCHER"}
    return {
        "gene": gene,
        "diplotype": diplotype,
        "call_source": "OUTSIDE",
        "tool_source": tool,
    }


# --- what the call asserts -------------------------------------------------


def test_the_call_asserts_trans_for_variants_on_different_haplotypes():
    assert report_phase.asserted(HG00589_RESULTS, "CYP2D6") == {
        frozenset(("22-42127941-G-A", "22-42128211-C-CG")): "trans",
        frozenset(("22-42126611-C-G", "22-42128211-C-CG")): "trans",
        frozenset(("22-42126611-C-G", "22-42127941-G-A")): "cis",
    }


def test_a_variant_on_both_haplotypes_asserts_nothing():
    """Homozygous: no pair of positions can contradict "on both chromosomes"."""
    results = {
        "results": {
            "CYP2D6": {
                "success": True,
                "details": {
                    "haplotype1": ["*2"],
                    "haplotype2": ["*2"],
                    "supporting_variants": [
                        {
                            "allele": "*2",
                            "variants": [
                                {"variant": "22-42126611-C-G"},
                                {"variant": "22-42127941-G-A"},
                            ],
                        }
                    ],
                },
            }
        }
    }
    assert report_phase.asserted(results, "CYP2D6") == {}


def test_a_gene_pypgx_did_not_call_asserts_nothing():
    assert report_phase.asserted({"results": {}}, "CYP2D6") == {}
    assert report_phase.asserted(None, "CYP2D6") == {}


# --- the comparison --------------------------------------------------------


def test_the_reads_contradict_the_printed_arrangement():
    found = report_phase.contradicted(HG00589_EVIDENCE, HG00589_RESULTS)
    assert set(found) == {"CYP2D6"}
    assert found["CYP2D6"]["says"] == "trans"
    assert found["CYP2D6"]["shows"] == "cis"
    assert found["CYP2D6"]["fragments"] == 7


def test_reads_that_agree_say_nothing():
    evidence = json.loads(json.dumps(HG00589_EVIDENCE))
    evidence["pairs"][0].update(cis=0, trans=7, shows="trans")
    assert report_phase.contradicted(evidence, HG00589_RESULTS) == {}


def test_reads_that_do_not_decide_say_nothing():
    evidence = json.loads(json.dumps(HG00589_EVIDENCE))
    evidence["pairs"][0].update(cis=2, trans=1, shows=None)
    assert report_phase.contradicted(evidence, HG00589_RESULTS) == {}


def test_a_check_that_did_not_run_says_nothing():
    for status in ("not_run", "failed"):
        assert report_phase.contradicted({"status": status}, HG00589_RESULTS) == {}
    assert report_phase.contradicted({}, HG00589_RESULTS) == {}


def test_the_strongest_pair_is_the_one_quoted():
    """A gene can have several contradicted pairs; the note quotes one."""
    evidence = json.loads(json.dumps(HG00589_EVIDENCE))
    evidence["pairs"].append(
        {
            "gene": "CYP2D6",
            "a": "22-42126611-C-G",
            "b": "22-42128211-C-CG",
            "distance": 1607,
            "cis": 11,
            "trans": 0,
            "shows": "cis",
        }
    )
    found = report_phase.contradicted(evidence, HG00589_RESULTS)
    assert found["CYP2D6"]["fragments"] == 11


# --- the notes -------------------------------------------------------------


def test_only_a_call_of_pypgx_own_is_marked():
    """A gene the report prints PharmCAT's call for has no PyPGx arrangement on the
    page, so a note about one would question a call the reader cannot see."""
    rows = [_row(tool="PYPGX"), _row(tool="PHARMCAT")]
    found = report_phase.annotate_genes(rows, HG00589_EVIDENCE, HG00589_RESULTS)
    assert set(found) == {"CYP2D6"}
    assert "read_phase_conflict" in rows[0]
    assert "read_phase_conflict" not in rows[1]


def test_the_note_says_which_way_the_reads_read_and_keeps_the_call():
    rows = [_row()]
    report_phase.annotate_genes(rows, HG00589_EVIDENCE, HG00589_RESULTS)
    note = rows[0]["read_phase_conflict"]
    assert "same chromosome" in note["call_note"]
    assert "7 fragments" in note["note"]
    # The call itself is untouched: naming the right allele needs the definitions.
    assert rows[0]["diplotype"] == "*2/*21"


def test_the_alert_names_the_gene_and_says_the_alleles_are_not_in_question():
    found = report_phase.annotate_genes([_row()], HG00589_EVIDENCE, HG00589_RESULTS)
    alert = report_phase.alert(found)
    assert "CYP2D6" in alert and "7 fragments" in alert
    assert "only how they are paired" in alert
    assert report_phase.alert({}) is None


def test_a_run_without_the_file_costs_the_note_not_the_report(tmp_path):
    assert report_phase.read_result(str(tmp_path)) == {}
    (tmp_path / "read_phase.json").write_text("not json", encoding="utf-8")
    assert report_phase.read_result(str(tmp_path)) == {}


# --- the counting ----------------------------------------------------------


def _fragment(pysam, name, start, sequence, cigar, mate_start=None):
    read = pysam.AlignedSegment()
    read.query_name = name
    read.query_sequence = sequence
    read.flag = 99 if mate_start is None else 147
    read.reference_id = 0
    read.reference_start = start
    read.mapping_quality = 60
    read.cigarstring = cigar
    read.query_qualities = pysam.qualitystring_to_array("I" * len(sequence))
    return read


def _alignment(tmp_path, reads):
    pysam = pytest.importorskip("pysam")
    header = {
        "HD": {"VN": "1.6", "SO": "coordinate"},
        "SQ": [{"SN": "chr22", "LN": CHR22}],
    }
    path = str(tmp_path / "aln.bam")
    with pysam.AlignmentFile(path, "wb", header=header) as out:
        for read in sorted(reads, key=lambda r: r.reference_start):
            out.write(read)
    pysam.index(path)
    return path


def test_fragments_carrying_both_variants_read_cis(tmp_path):
    """Two substitutions 10 bp apart: three fragments with both, three with neither."""
    service = _service()
    pysam = pytest.importorskip("pysam")
    at = 42_127_941
    # 50 bp of reference A, with the two alternate bases written in at 0 and 10.
    variant_seq = "G" + "A" * 9 + "T" + "A" * 39
    reference_seq = "A" * 50
    reads = [
        _fragment(pysam, f"both{i}", at - 1, variant_seq, "50M") for i in range(3)
    ] + [
        _fragment(pysam, f"neither{i}", at - 1, reference_seq, "50M") for i in range(3)
    ]
    path = _alignment(tmp_path, reads)
    a = service.Variant("chr22", at, "A", "G")
    b = service.Variant("chr22", at + 10, "A", "T")
    row = service.count(path, [("CYP2D6", a, b)])[0]
    # Reference-reference is cis evidence too: both variants are then together on the
    # chromosome the fragment is not from.
    assert (row["cis"], row["trans"], row["shows"]) == (6, 0, "cis")


def test_one_variant_each_reads_trans(tmp_path):
    service = _service()
    pysam = pytest.importorskip("pysam")
    at = 42_127_941
    left = "G" + "A" * 49
    right = "A" * 10 + "T" + "A" * 39
    reads = [_fragment(pysam, f"left{i}", at - 1, left, "50M") for i in range(3)]
    reads += [_fragment(pysam, f"right{i}", at - 1, right, "50M") for i in range(2)]
    path = _alignment(tmp_path, reads)
    a = service.Variant("chr22", at, "A", "G")
    b = service.Variant("chr22", at + 10, "A", "T")
    row = service.count(path, [("CYP2D6", a, b)])[0]
    assert (row["cis"], row["trans"], row["shows"]) == (0, 5, "trans")


def test_an_insertion_is_read_from_the_read_sequence(tmp_path):
    """HG00589's deciding variant is an insertion: a substitutions-only pass misses
    it, so the inserted bases are compared."""
    service = _service()
    pysam = pytest.importorskip("pysam")
    at = 42_127_941
    # The insertion sits 10 bp in: 11M 1I 39M, with a G inserted after the anchor.
    with_insert = "G" + "A" * 9 + "A" + "G" + "A" * 39
    reads = [
        _fragment(pysam, f"ins{i}", at - 1, with_insert, "11M1I39M") for i in range(3)
    ]
    path = _alignment(tmp_path, reads)
    a = service.Variant("chr22", at, "A", "G")
    b = service.Variant("chr22", at + 10, "A", "AG")
    row = service.count(path, [("CYP2D6", a, b)])[0]
    assert (row["cis"], row["trans"], row["shows"]) == (3, 0, "cis")


def test_an_indel_of_another_sequence_is_not_evidence(tmp_path):
    """UGT1A1 *28 and *36 insert and delete the same TA repeat. A read carrying one
    is not evidence for the other, so the inserted sequence must match."""
    service = _service()
    pysam = pytest.importorskip("pysam")
    at = 42_127_941
    with_insert = "G" + "A" * 9 + "A" + "G" + "A" * 39
    reads = [
        _fragment(pysam, f"ins{i}", at - 1, with_insert, "11M1I39M") for i in range(3)
    ]
    path = _alignment(tmp_path, reads)
    a = service.Variant("chr22", at, "A", "G")
    # The same position, a two-base insertion: these reads carry one base, not two.
    b = service.Variant("chr22", at + 10, "A", "ATA")
    row = service.count(path, [("CYP2D6", a, b)])[0]
    assert (row["cis"], row["trans"], row["shows"]) == (0, 0, None)


def test_two_variants_at_one_position_are_not_paired():
    """Competing alleles of one site: no fragment can carry both."""
    service = _service()
    a = service.Variant("chr22", 42_127_941, "A", "AT")
    b = service.Variant("chr22", 42_127_941, "AT", "A")
    assert service.pairs({"UGT1A1": [a, b]}) == []


def test_variants_further_apart_than_a_fragment_are_not_paired():
    service = _service()
    a = service.Variant("chr22", 42_127_941, "A", "G")
    b = service.Variant("chr22", 42_127_941 + service.REACH + 1, "A", "T")
    assert service.pairs({"CYP2D6": [a, b]}) == []
    near = service.Variant("chr22", 42_127_941 + service.REACH, "A", "T")
    assert len(service.pairs({"CYP2D6": [a, near]})) == 1


def test_a_poorly_called_base_is_not_evidence(tmp_path):
    """The anchor base must be called confidently whatever kind of variant it is: an
    indel read off a quality-6 anchor is no more evidence than a substitution is."""
    service = _service()
    pysam = pytest.importorskip("pysam")
    at = 42_127_941
    read = _fragment(pysam, "poor", at - 1, "G" + "A" * 49, "50M")
    qualities = pysam.qualitystring_to_array("I" * 50)
    qualities[0] = 6
    read.query_qualities = qualities
    path = _alignment(tmp_path, [read])
    variant = service.Variant("chr22", at, "A", "G")
    with pysam.AlignmentFile(path) as alignment:
        assert service.fragments(alignment, variant) == {}


def test_too_few_fragments_is_not_an_answer():
    service = _service()
    assert service.verdict(2, 0) is None
    assert service.verdict(3, 0) == "cis"
    assert service.verdict(0, 3) == "trans"


def test_mixed_fragments_are_not_an_answer():
    service = _service()
    assert service.verdict(5, 3) is None  # 37% minority
    assert service.verdict(9, 1) == "cis"  # 10%


def test_a_check_with_nothing_to_ask_is_still_done(tmp_path):
    """No two heterozygous defining variants within reach: nothing asked, not a
    failure."""
    service = _service()
    vcf = tmp_path / "empty.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=chr22,length=50818468>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n",
        encoding="utf-8",
    )
    alignment = _alignment(tmp_path, [])
    result = service.check(alignment, vcf, {"CYP2D6": [("22", 42_127_940, 42_127_941)]})
    assert result["status"] == "done"
    assert result["pairs"] == []


def test_an_unreadable_alignment_does_not_fail_the_run(tmp_path):
    service = _service()
    result = service.check(tmp_path / "missing.bam", tmp_path / "missing.vcf", {})
    assert result["status"] == "not_run"
    assert "reason" in result


# --- the delivered report --------------------------------------------------
#
# The note has to reach the page, not just the dictionary. These render the real
# report from PharmCAT's own example (test_data/, public) with a read_phase.json
# beside it, as a run leaves one, and read the note out of the HTML and the PDF.

DATA = Path(__file__).resolve().parents[1] / "test_data"
EXAMPLE_JSON = DATA / "pharmcat.example.v340.report.json"


def _with_outside_cyp2d6(data):
    """PharmCAT's example, given the outside CYP2D6 call a real alignment run gives
    it. The example itself has callSource NONE for CYP2D6 -- PharmCAT's matcher does
    not call that gene -- so without this there is no CYP2D6 row on the page at all.
    """
    gene = data["genes"]["CYP2D6"]
    gene["callSource"] = "OUTSIDE"
    allele = dict(gene["sourceDiplotypes"][0]["allele1"])
    pair = dict(gene["sourceDiplotypes"][0])
    pair["allele1"] = {**allele, "name": "*2"}
    pair["allele2"] = {**allele, "name": "*21"}
    pair["phenotypes"] = ["Intermediate Metabolizer"]
    pair["activityScore"] = "1.0"
    # The report prints the label, and the PyPGx merge marks the row as PyPGx's only
    # when the outside call's diplotype matches the one PyPGx reported.
    pair["label"] = "*2/*21"
    gene["sourceDiplotypes"] = [pair]
    gene["recommendationDiplotypes"] = [pair]
    return data


def _rendered(monkeypatch, tmp_path, evidence, results=None):
    """The report's HTML, generated as a run generates it."""
    pytest.importorskip("weasyprint")
    import app.reports.generator as generator_module

    (tmp_path / "read_phase.json").write_text(json.dumps(evidence), encoding="utf-8")
    (tmp_path / "pypgx_result.json").write_text(
        json.dumps(results if results is not None else HG00589_RESULTS),
        encoding="utf-8",
    )
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(
                generator_module.REPORT_CONFIG, key, key == "write_html"
            )
    data = _with_outside_cyp2d6(json.loads(EXAMPLE_JSON.read_text(encoding="utf-8")))
    result = generator_module.generate_report(
        {"data": data}, str(tmp_path), {"id": "p1"}, job_id=None
    )
    return (tmp_path / result["html_path"].split("/")[-1]).read_text(encoding="utf-8")


def test_the_note_and_the_alert_reach_the_page(monkeypatch, tmp_path):
    # The page is escaped, so the note is matched on a stretch without an apostrophe.
    page = _rendered(monkeypatch, tmp_path, HG00589_EVIDENCE)
    assert "variants on the same chromosome, which this call does not" in page
    assert "The reads contradict how a call is phased" in page
    assert "only how they are paired" in page
    # The call is printed as it was called.
    assert "*2/*21" in page


def test_a_run_whose_reads_agree_says_nothing_on_the_page(monkeypatch, tmp_path):
    evidence = json.loads(json.dumps(HG00589_EVIDENCE))
    evidence["pairs"][0].update(cis=0, trans=7, shows="trans")
    page = _rendered(monkeypatch, tmp_path, evidence)
    assert "The reads contradict how a call is phased" not in page
    # The class is in the stylesheet on every report; the note is not on this one.
    assert '<div class="call-note read-phase-note">' not in page


def test_the_note_survives_into_the_pdf(monkeypatch, tmp_path):
    """The PDF is the delivered artifact: a note that only renders in HTML is not
    delivered. The Executive Summary is laid out to one page
    (app/reports/pdf_fit.py), so an added note must not push it over."""
    import app.reports.generator as generator_module

    page = _rendered(monkeypatch, tmp_path, HG00589_EVIDENCE)
    document, _ = generator_module.render_pdf_document(page)
    text = "\n".join(
        "".join(
            child.text
            for line in box.children
            for child in line.children
            if hasattr(child, "text")
        )
        for pdf_page in document.pages
        for box in pdf_page._page_box.descendants()
        if getattr(box, "children", None) and getattr(box, "element_tag", "") == "p"
    )
    assert "reads" in text and "chromosome" in text
    summary = [
        i
        for i, pdf_page in enumerate(document.pages)
        if any(
            getattr(box, "element_tag", "") == "div"
            and getattr(box, "element", None) is not None
            and box.element.get("id") == "exec-summary"
            for box in pdf_page._page_box.descendants()
        )
    ]
    assert len(summary) == 1, f"the Executive Summary ran to {len(summary)} pages"


# --- what the reads' arrangement would be called ---------------------------
#
# The pypgx lane names it with PyPGx's own ranking; these check the report says it
# only when the reads rule the printed call out, and says nothing when they do not.

NAMED_EVIDENCE = {
    **HG00589_EVIDENCE,
    "names": {
        "CYP2D6": {
            "allows": ["*1/*21", "*21/*39"],
            "settles": ["*21"],
            # Both readings, and the printed *2/*21, are Intermediate Metabolisers.
            "phenotypes": ["Intermediate Metabolizer"],
        }
    },
}


def test_the_note_says_what_the_reads_arrangement_would_be_called():
    rows = [_row()]
    report_phase.annotate_genes(rows, NAMED_EVIDENCE, HG00589_RESULTS)
    note = rows[0]["read_phase_conflict"]
    assert "*1/*21 or *21/*39" in note["call_note"]
    assert "*1/*21 or *21/*39" in note["note"]
    # and the call itself is still the one that was called
    assert rows[0]["diplotype"] == "*2/*21"


def test_nothing_is_named_when_the_printed_call_is_still_allowed():
    """A single contradicted pair does not rule the call out if the arrangements the
    reads allow still include it."""
    evidence = json.loads(json.dumps(NAMED_EVIDENCE))
    evidence["names"]["CYP2D6"]["allows"] = ["*2/*21", "*21/*34"]
    rows = [_row()]
    report_phase.annotate_genes(rows, evidence, HG00589_RESULTS)
    assert rows[0]["read_phase_conflict"]["allows"] == []
    assert "would be" not in rows[0]["read_phase_conflict"]["call_note"]


def test_a_run_without_names_still_gets_the_note():
    """Evidence from before naming existed, or a gene whose naming is not trusted."""
    rows = [_row()]
    report_phase.annotate_genes(rows, HG00589_EVIDENCE, HG00589_RESULTS)
    assert rows[0]["read_phase_conflict"]["allows"] == []
    assert "same chromosome" in rows[0]["read_phase_conflict"]["call_note"]


def test_naming_reproduces_what_pypgx_itself_would_print():
    """The service names a haplotype with PyPGx's own ranking, so a chromosome
    carrying both *2's cores and *21's core is *21, not *2. Measured over the 61
    GeT-RM genomes: 2,434 of 2,497 printed haplotype pairs came back identical."""
    service = _service()
    pytest.importorskip("pypgx")
    both = {"22-42126611-C-G", "22-42127941-G-A", "22-42128211-C-CG"}
    assert service.name_haplotype("CYP2D6", both) == "*21"
    assert (
        service.name_haplotype("CYP2D6", {"22-42126611-C-G", "22-42127941-G-A"}) == "*2"
    )
    assert service.name_haplotype("CYP2D6", set()) == "*1"


def test_a_gene_whose_naming_does_not_reproduce_pypgx_is_never_named():
    service = _service()
    v = [
        service.Variant("chr12", 21178615, "T", "C"),
        service.Variant("chr12", 21178700, "A", "G"),
    ]
    rows = [
        {
            "gene": "SLCO1B1",
            "a": v[0].name(),
            "b": v[1].name(),
            "distance": 85,
            "cis": 9,
            "trans": 0,
            "shows": "cis",
        }
    ]
    assert "SLCO1B1" in service.NAMING_UNFAITHFUL
    assert service.reads_name("SLCO1B1", v, rows, "GRCh38") == []


def test_the_arrangements_a_variant_out_of_reach_leaves_open():
    """Two groups, one of them a single variant no pair links: both ways round."""
    service = _service()
    a = service.Variant("chr22", 42126611, "C", "G")
    b = service.Variant("chr22", 42127941, "G", "A")
    c = service.Variant("chr22", 42128211, "C", "CG")
    rows = [{"a": b.name(), "b": c.name(), "shows": "cis"}]
    splits = service.arrangements([a, b, c], rows)
    assert len(splits) == 2
    for left, right in splits:
        assert {b.name(), c.name()} <= left or {b.name(), c.name()} <= right


# --- whether the phasing error would change anything downstream ------------


def _row_with_phenotype(phenotype="Intermediate Metabolizer"):
    row = _row()
    row["phenotype"] = phenotype
    return row


def test_a_wrongly_paired_call_whose_phenotype_is_unchanged_says_so():
    """The point of the note is what a reader must do about it, and here: nothing."""
    rows = [_row_with_phenotype()]
    report_phase.annotate_genes(rows, NAMED_EVIDENCE, HG00589_RESULTS)
    note = rows[0]["read_phase_conflict"]
    assert "the phenotype above is the same either way" in note["call_note"]
    assert "the phenotype above is the same either way" in note["note"]


def test_a_phasing_error_that_would_change_the_phenotype_says_that_instead():
    evidence = json.loads(json.dumps(NAMED_EVIDENCE))
    evidence["names"]["CYP2D6"]["phenotypes"] = ["Poor Metabolizer"]
    rows = [_row_with_phenotype()]
    report_phase.annotate_genes(rows, evidence, HG00589_RESULTS)
    note = rows[0]["read_phase_conflict"]
    assert "would make the phenotype Poor Metabolizer" in note["call_note"]


def test_nothing_is_said_about_a_phenotype_pypgx_has_no_rule_for():
    evidence = json.loads(json.dumps(NAMED_EVIDENCE))
    evidence["names"]["CYP2D6"]["phenotypes"] = []
    rows = [_row_with_phenotype()]
    report_phase.annotate_genes(rows, evidence, HG00589_RESULTS)
    assert rows[0]["read_phase_conflict"].get("phenotype_note") is None
    assert "phenotype" not in rows[0]["read_phase_conflict"]["call_note"]


# --- what the reads are not evidence of ------------------------------------
#
# Each of these is a way of counting evidence that is not there. They are kept
# together because they share a shape: the read says nothing, and the question is
# whether the code admits it.


def test_a_soft_clip_is_not_an_insertion(tmp_path):
    """An aligner clipping a read at an indel breakpoint leaves bases aligned to no
    reference position -- exactly what an insertion leaves. Reading the clip as the
    inserted sequence invents fragments at the one place they decide the most."""
    service = _service()
    pysam = pytest.importorskip("pysam")
    at = 42_127_941
    # 11M1S: the read stops at the anchor and carries a clipped G after it.
    clipped = _fragment(pysam, "clip", at - 1, "G" + "A" * 9 + "A" + "G", "11M1S")
    insertion = service.Variant("chr22", at + 10, "A", "AG")
    assert service.allele_in_read(clipped, insertion) is None


def test_a_read_that_stops_at_the_anchor_says_nothing_about_an_insertion(tmp_path):
    """Ending where the insertion would start is not evidence that it is absent."""
    service = _service()
    pysam = pytest.importorskip("pysam")
    at = 42_127_941
    stops = _fragment(pysam, "stop", at - 1, "G" + "A" * 9 + "A", "11M")
    insertion = service.Variant("chr22", at + 10, "A", "AG")
    assert service.allele_in_read(stops, insertion) is None


def test_a_half_call_is_not_a_heterozygote(tmp_path):
    """``1/.`` names one chromosome and leaves the other unknown. Counting it as
    heterozygous asserts a reference allele nobody called."""
    service = _service()
    pytest.importorskip("pysam")
    vcf = tmp_path / "half.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=chr22,length=50818468>\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
        "chr22\t42127941\t.\tG\tA\t.\tPASS\t.\tGT\t1/.\n"
        "chr22\t42128211\t.\tC\tCG\t.\tPASS\t.\tGT\t0/1\n",
        encoding="utf-8",
    )
    positions = {
        "CYP2D6": [("22", 42_127_940, 42_127_941), ("22", 42_128_210, 42_128_211)]
    }
    assert service.heterozygous(vcf, positions) == {}


def test_an_alignment_with_no_index_is_not_silence(tmp_path):
    """Every fetch would raise and every pair would come back 0/0, which reads like
    "the reads reach nothing" -- a different claim from "they were never read"."""
    service = _service()
    pysam = pytest.importorskip("pysam")
    header = {
        "HD": {"VN": "1.6", "SO": "coordinate"},
        "SQ": [{"SN": "chr22", "LN": CHR22}],
    }
    path = tmp_path / "noindex.bam"
    with pysam.AlignmentFile(str(path), "wb", header=header) as out:
        out.write(_fragment(pysam, "r", 42_127_900, "A" * 50, "50M"))
    vcf = tmp_path / "two.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=chr22,length=50818468>\n"
        '##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">\n'
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE\n"
        "chr22\t42127941\t.\tG\tA\t.\tPASS\t.\tGT\t0/1\n"
        "chr22\t42128211\t.\tC\tT\t.\tPASS\t.\tGT\t0/1\n",
        encoding="utf-8",
    )
    positions = {
        "CYP2D6": [("22", 42_127_940, 42_127_941), ("22", 42_128_210, 42_128_211)]
    }
    result = service.check(path, vcf, positions)
    assert result["status"] == "not_run"
    assert "index" in result["reason"] or "position" in result["reason"]


def test_a_core_variant_on_both_chromosomes_still_names_its_allele():
    """A homozygous core variant is in neither partition, so no allele needing it
    could fit either side and the haplotype would fall back to *1. CYP2D6 with
    42126611 homozygous reads *21/*39, not *1/*21."""
    service = _service()
    pytest.importorskip("pypgx")
    on_both = service.Variant("chr22", 42126611, "C", "G")
    core = service.Variant("chr22", 42127941, "G", "A")
    insertion = service.Variant("chr22", 42128211, "C", "CG")
    rows = [
        {
            "gene": "CYP2D6",
            "a": core.name(),
            "b": insertion.name(),
            "distance": 270,
            "cis": 7,
            "trans": 0,
            "shows": "cis",
        }
    ]
    variants = [core, insertion]
    assert service.reads_name("CYP2D6", variants, rows, "GRCh38") == ["*1/*21"]
    assert service.reads_name("CYP2D6", variants, rows, "GRCh38", [on_both]) == [
        "*21/*39"
    ]


def test_more_readings_than_can_be_read_say_what_they_agree_on():
    """Six groups the reads leave free is 32 arrangements. Listing them is not a
    statement, and it would not fit the Executive Summary's page."""
    many = [f"*{i}/*21" for i in range(1, 9)]
    found = {"shows": "cis", "allows": many, "settles": ["*21"], "fragments": 7}
    phrase = report_phase.arrangement_phrase(found)
    assert "*21" in phrase
    for name in many:
        assert name not in phrase
    assert "every arrangement they allow carries" in phrase
    few = {"shows": "cis", "allows": ["*1/*21", "*21/*39"], "settles": ["*21"]}
    assert report_phase.arrangement_phrase(few) == (
        "their arrangement would be *1/*21 or *21/*39"
    )


# --- what the reads could check, and what they could not -------------------
#
# The contradiction note is rare by construction, so without these counts a report
# says nothing at all about phase whenever nothing was contradicted -- neither that
# the check ran nor how much of it the reads could reach.


def _agreeing_evidence():
    evidence = json.loads(json.dumps(HG00589_EVIDENCE))
    evidence["pairs"][0].update(cis=0, trans=7, shows="trans")
    return evidence


def test_a_call_the_reads_agree_with_is_counted_as_checked():
    counts = report_phase.coverage([_row()], _agreeing_evidence(), HG00589_RESULTS)
    assert counts == {"asserting": 1, "agree": 1, "conflict": 0, "unchecked": 0}


def test_a_call_the_reads_contradict_is_counted_as_a_conflict():
    counts = report_phase.coverage([_row()], HG00589_EVIDENCE, HG00589_RESULTS)
    assert counts == {"asserting": 1, "agree": 0, "conflict": 1, "unchecked": 0}


def test_a_call_no_fragment_reaches_is_counted_as_unchecked():
    """The common case: the call asserts an arrangement and the reads cannot say."""
    counts = report_phase.coverage(
        [_row()], {"status": "done", "pairs": []}, HG00589_RESULTS
    )
    assert counts == {"asserting": 1, "agree": 0, "conflict": 0, "unchecked": 1}


def test_a_pair_the_reads_did_not_decide_leaves_the_call_unchecked():
    evidence = json.loads(json.dumps(HG00589_EVIDENCE))
    evidence["pairs"][0].update(cis=1, trans=1, shows=None)
    counts = report_phase.coverage([_row()], evidence, HG00589_RESULTS)
    assert counts["unchecked"] == 1 and counts["agree"] == 0


def test_a_pair_of_another_gene_does_not_check_this_call():
    """A decided pair only counts for the call that asserts that pair."""
    evidence = json.loads(json.dumps(HG00589_EVIDENCE))
    evidence["pairs"][0]["gene"] = "CYP2C19"
    counts = report_phase.coverage([_row()], evidence, HG00589_RESULTS)
    assert counts["unchecked"] == 1


def test_a_check_that_did_not_run_is_not_described():
    assert (
        report_phase.coverage([_row()], {"status": "not_run"}, HG00589_RESULTS) is None
    )


def test_only_calls_the_report_prints_from_pypgx_are_counted():
    counts = report_phase.coverage(
        [_row(tool="PHARMCAT")], HG00589_EVIDENCE, HG00589_RESULTS
    )
    assert counts is None


def test_the_note_gives_the_counts_and_names_what_was_not_reached():
    note = report_phase.coverage_note(
        {"asserting": 10, "agree": 3, "conflict": 1, "unchecked": 6}
    )
    assert "10 calls here assert" in note
    assert "both positions for 4 calls" in note
    assert "agree with the call in 3 and contradict it in 1" in note
    assert "the other 6 calls, no fragment covers two of the call's variants" in note
    assert "Beagle" in note


def test_the_note_does_not_report_a_contradiction_count_of_zero():
    """How a counter talks, not how a report reads."""
    note = report_phase.coverage_note(
        {"asserting": 4, "agree": 4, "conflict": 0, "unchecked": 0}
    )
    assert "contradict" not in note
    assert "cover both positions for all of them" in note
    assert "agree with the call in every one" in note
    # Nothing was out of reach, so the sentence about reach is not written either.
    assert "no fragment covers" not in note


def test_the_note_says_nothing_where_no_call_asserts_an_arrangement():
    assert report_phase.coverage_note(None) is None
    assert (
        report_phase.coverage_note(
            {"asserting": 0, "agree": 0, "conflict": 0, "unchecked": 0}
        )
        is None
    )


def test_a_report_with_no_contradiction_still_says_what_was_checked(
    monkeypatch, tmp_path
):
    """The gap this closes: agreement used to leave the page silent about phase."""
    page = _rendered(monkeypatch, tmp_path, _agreeing_evidence())
    assert "The reads contradict how a call is phased" not in page
    assert "phases against a reference panel and not against this sample" in page
    assert "1 call here asserts such an arrangement" in page
    assert "cover both positions for it, and agree with the call there" in page


def test_a_report_whose_reads_reach_nothing_says_so(monkeypatch, tmp_path):
    page = _rendered(monkeypatch, tmp_path, {"status": "done", "pairs": []})
    assert "no fragment covers two of the call's variants" in page
    # The count is not repeated when every one of them is out of reach.
    assert "The reads do not reach it" in page
    assert "For 1 call" not in page


def test_the_note_does_not_repeat_the_count_when_nothing_was_reached():
    note = report_phase.coverage_note(
        {"asserting": 7, "agree": 0, "conflict": 0, "unchecked": 7}
    )
    assert "7 calls here assert" in note
    assert "The reads reach none of them" in note
    assert "For 7 calls" not in note


def test_the_coverage_statement_survives_into_the_pdf(monkeypatch, tmp_path):
    """The common case delivered: nothing contradicted, and the reader is still told
    what the check could and could not reach. The paragraph sits in Methodology, not
    the Executive Summary, which app/reports/pdf_fit.py lays out to one page."""
    import app.reports.generator as generator_module

    page = _rendered(monkeypatch, tmp_path, _agreeing_evidence())
    document, _ = generator_module.render_pdf_document(page)
    text = "\n".join(
        "".join(
            child.text
            for line in box.children
            for child in line.children
            if hasattr(child, "text")
        )
        for pdf_page in document.pages
        for box in pdf_page._page_box.descendants()
        if getattr(box, "children", None) and getattr(box, "element_tag", "") == "p"
    )
    assert "reference panel" in text
    summary = [
        i
        for i, pdf_page in enumerate(document.pages)
        if any(
            getattr(box, "element_tag", "") == "div"
            and getattr(box, "element", None) is not None
            and box.element.get("id") == "exec-summary"
            for box in pdf_page._page_box.descendants()
        )
    ]
    assert len(summary) == 1, f"the Executive Summary ran to {len(summary)} pages"


# --- the alternative, where the reads reach nothing -------------------------
#
# The commonest case by far: two defining variants further apart than a fragment is
# long. The reads constrain nothing, the printed call is the phaser's alone, and the
# other arrangement is a different diplotype 92% of the time in the two-variant case.

OPEN_EVIDENCE = {
    "status": "done",
    "pairs": [],
    "names": {
        "CYP2D6": {
            "open": ["*1/*21", "*2/*21"],
            "phenotypes": ["Normal Metabolizer", "Intermediate Metabolizer"],
        }
    },
}


def test_the_one_alternative_is_named_beside_the_printed_call():
    answer = report_phase.unreached_alternative(OPEN_EVIDENCE, "CYP2D6", "*2/*21")
    assert answer is not None
    alternative, phenotypes = answer
    assert alternative == "*1/*21"
    # The printed call's phenotype first, the alternative's second.
    assert phenotypes == ["Intermediate Metabolizer", "Normal Metabolizer"]


def test_nothing_is_named_when_the_printed_call_is_not_one_of_them():
    """An alternative to a call this module could not reproduce is the wrong thing."""
    assert report_phase.unreached_alternative(OPEN_EVIDENCE, "CYP2D6", "*4/*5") is None


def test_identical_phenotypes_are_not_contrasted():
    evidence = json.loads(json.dumps(OPEN_EVIDENCE))
    evidence["names"]["CYP2D6"]["phenotypes"] = ["Normal Metabolizer"] * 2
    _, phenotypes = report_phase.unreached_alternative(evidence, "CYP2D6", "*2/*21")
    assert phenotypes == []


def test_more_than_one_alternative_is_not_named():
    evidence = json.loads(json.dumps(OPEN_EVIDENCE))
    evidence["names"]["CYP2D6"]["open"] = ["*1/*21", "*2/*21", "*21/*39"]
    assert report_phase.unreached_alternative(evidence, "CYP2D6", "*2/*21") is None


def test_the_note_names_the_alternative_and_contrasts_the_phenotype():
    note = report_phase.open_note(
        "*1/*21", ["Intermediate Metabolizer", "Normal Metabolizer"]
    )
    assert "No fragment covers two of this call's variants" in note
    assert "would read *1/*21" in note
    assert "would be Normal Metabolizer rather than Intermediate Metabolizer" in note
    assert "Beagle" in note


def test_a_row_is_marked_with_the_alternative():
    row = _row(diplotype="*2/*21")
    found = report_phase.annotate_open([row], OPEN_EVIDENCE)
    assert set(found) == {"CYP2D6"}
    assert row["read_phase_open"]["alternative"] == "*1/*21"


def test_a_contradiction_wins_over_an_alternative():
    """Evidence beats the absence of it, and two notes would contradict each other."""
    row = _row(diplotype="*2/*21")
    row["read_phase_conflict"] = {"call_note": "already said"}
    assert report_phase.annotate_open([row], OPEN_EVIDENCE) == {}
    assert "read_phase_open" not in row


def test_a_call_not_of_pypgx_own_is_left_alone():
    row = _row(tool="PHARMCAT", diplotype="*2/*21")
    assert report_phase.annotate_open([row], OPEN_EVIDENCE) == {}


def test_the_alternative_reaches_the_page(monkeypatch, tmp_path):
    page = _rendered(monkeypatch, tmp_path, OPEN_EVIDENCE)
    assert "No fragment covers two of this call" in page
    assert "would read *1/*21" in page
    # The printed call is untouched, as ever.
    assert "*2/*21" in page


def test_the_contradiction_note_is_not_shown_beside_the_alternative(
    monkeypatch, tmp_path
):
    page = _rendered(monkeypatch, tmp_path, OPEN_EVIDENCE)
    assert "which this call does not" not in page


def test_the_alternative_does_not_cost_the_one_page_summary(monkeypatch, tmp_path):
    """The note sits beside the call, not in the Executive Summary, which
    app/reports/pdf_fit.py lays out to one page. Checked in the PDF, which is what is
    delivered."""
    import app.reports.generator as generator_module

    page = _rendered(monkeypatch, tmp_path, OPEN_EVIDENCE)
    document, _ = generator_module.render_pdf_document(page)
    text = "\n".join(
        "".join(
            child.text
            for line in box.children
            for child in line.children
            if hasattr(child, "text")
        )
        for pdf_page in document.pages
        for box in pdf_page._page_box.descendants()
        if getattr(box, "children", None)
        and getattr(box, "element_tag", "") in ("p", "div")
    )
    assert "would read *1/*21" in text
    summary = [
        i
        for i, pdf_page in enumerate(document.pages)
        if any(
            getattr(box, "element_tag", "") == "div"
            and getattr(box, "element", None) is not None
            and box.element.get("id") == "exec-summary"
            for box in pdf_page._page_box.descendants()
        )
    ]
    assert len(summary) == 1, f"the Executive Summary ran to {len(summary)} pages"
