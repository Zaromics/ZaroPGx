"""The positions behind the calls PharmCAT makes itself (app/reports/pharmcat_variants.py).

The shape here is the shape of PharmCAT's own report JSON: per gene, every position
its matcher reads, with the genotype it read there. The thing these tests are really
about is that almost all of those positions are homozygous reference and say nothing
-- 1,049 of the 1,066 on the NA12878 demo run -- so what reaches the page is the
seventeen that differ, and a count for the rest.
"""

import gzip
import json
from pathlib import Path

import pytest

from app.reports import pharmcat_variants, rarity

GNOMAD_HEADER = (
    "chrom\tpos\tref\talt\tfilter\tAF_joint\tAF_grpmax_joint\tgrpmax_joint"
    "\tfafmax_faf95_max_joint\tnhomalt_joint\tnhomalt_joint_XY\n"
)


def _variant(position, call, reference="C", rsid=None, alleles=(), **extra):
    return {
        "gene": "CYP2C19",
        "chromosome": "chr10",
        "position": position,
        "dbSnpId": rsid,
        "call": call,
        "alleles": list(alleles),
        "phased": False,
        "referenceAllele": reference,
        "hasUndocumentedVariations": False,
        "warnings": [],
        **extra,
    }


def _report():
    """Two matcher-called genes and one outside call, as a real run gives them."""
    return {
        "genes": {
            "CYP2C19": {
                "callSource": "MATCHER",
                "variants": [
                    _variant(94761900, "C/C", rsid="rs12248560", alleles=["*1", "*17"]),
                    _variant(
                        94781859,
                        "G/A",
                        reference="G",
                        rsid="rs4244285",
                        alleles=["*1", "*2"],
                    ),
                    _variant(94775367, "A/A", reference="A", rsid="rs12769205"),
                ],
            },
            "VKORC1": {
                "callSource": "MATCHER",
                "variants": [
                    {
                        "gene": "VKORC1",
                        "chromosome": "chr16",
                        "position": 31096368,
                        "dbSnpId": "rs9923231",
                        "call": "T/T",
                        "alleles": ["rs9923231 variant (T)"],
                        "phased": False,
                        "referenceAllele": "C",
                        "warnings": [],
                    },
                    {
                        "gene": "VKORC1",
                        "chromosome": "chr16",
                        "position": 31096400,
                        "dbSnpId": "rs1",
                        "call": None,
                        "referenceAllele": "C",
                    },
                ],
            },
            # PyPGx's call, carried as an outside one: no positions of its own here.
            "CYP2D6": {"callSource": "OUTSIDE", "variants": []},
        }
    }


@pytest.fixture
def bundle(tmp_path):
    with gzip.open(tmp_path / "gnomad.tsv.gz", "wt") as f:
        f.write(GNOMAD_HEADER)
        f.write("chr10\t94781859\tG\tA\tPASS\t0.15\t0.31\teas\t0.30\t900\t0\n")
        f.write("chr16\t31096368\tC\tT\tPASS\t0.40\t0.47\tnfe\t0.39\t3000\t0\n")
        # CYP2C19 *17's -806C>T, in the promoter: the real numbers, and a position
        # only a bundle cut over the called positions reaches.
        f.write("chr10\t94761900\tC\tT\tPASS\t0.18\t0.23\tmid\t0.17\t1200\t0\n")
    (tmp_path / "regions.bed").write_text("chr10\t94781800\t94781900\n")
    (tmp_path / "gnomad_regions.bed").write_text(
        "chr10\t94781800\t94781900\n"
        "chr10\t94761890\t94761910\n"
        "chr16\t31096360\t31096375\n"
    )
    (tmp_path / "manifest.json").write_text(
        '{"sources": {"gnomad": {"version": "gnomAD v4.1 joint"}}}'
    )
    return tmp_path


# --- reading a genotype ----------------------------------------------------


@pytest.mark.parametrize(
    "call,reference,alts",
    [
        ("C/T", "C", ["T"]),
        ("T/C", "C", ["T"]),
        ("C|T", "C", ["T"]),  # phased, same answer
        ("T/T", "C", ["T"]),
        ("C/C", "C", []),  # the common case: nothing to say
        ("./.", "C", []),
        ("", "C", []),
        (None, "C", []),
        ("CAT/CATAT", "CAT", ["CATAT"]),  # an insertion, as PharmCAT writes it
        ("A/G", "C", ["A", "G"]),  # neither base is the reference
    ],
)
def test_the_bases_that_are_not_the_reference_are_the_evidence(call, reference, alts):
    assert pharmcat_variants._alts(call, reference) == alts


@pytest.mark.parametrize(
    "call,reference,zygosity",
    [
        ("C/T", "C", "heterozygous"),
        ("T/T", "C", "homozygous"),
        ("C/C", "C", ""),  # reference: the table does not reach it
        ("C|T", "C", "heterozygous"),
        ("./.", "C", ""),
        (None, "C", ""),
    ],
)
def test_zygosity_is_worded_as_the_other_variant_table_words_it(
    call, reference, zygosity
):
    assert pharmcat_variants._zygosity(call, reference) == zygosity


def test_a_position_defining_more_alleles_than_the_cell_holds_is_counted():
    """NAT2's rs1801280 defines 29 star alleles; naming them all is a column, not a
    cell."""
    few = ["*5", "*16"]
    assert pharmcat_variants._alleles(few) == "*5, *16"
    many = [f"*{n}" for n in range(1, 11)]
    assert pharmcat_variants._alleles(many) == "*1, *2, *3, *4 and 6 more"
    assert pharmcat_variants._alleles([]) == ""


def test_the_key_is_the_one_the_bundle_uses():
    variant = _variant(94781859, "G/A", reference="G")
    assert pharmcat_variants.variant_key(variant, "A") == "chr10-94781859-G-A"
    assert rarity.variant_key(pharmcat_variants.variant_key(variant, "A")) == (
        "chr10",
        94781859,
        "G",
        "A",
    )


# --- what reaches the page -------------------------------------------------


def test_only_the_positions_that_differ_are_listed(bundle):
    found = pharmcat_variants.collect(_report(), bundle)
    assert found["status"] == "done"
    assert [g["gene"] for g in found["genes"]] == ["CYP2C19", "VKORC1"]
    assert found["genotyped"] == 5
    assert found["differing"] == 2
    assert found["unread"] == 1
    rows = {g["gene"]: g["rows"] for g in found["genes"]}
    assert [r["position"] for r in rows["CYP2C19"]] == ["chr10:94781859"]
    assert rows["CYP2C19"][0]["zygosity"] == "heterozygous"
    assert rows["VKORC1"][0]["zygosity"] == "homozygous"


def test_a_gene_whose_call_came_from_another_tool_is_left_to_it(bundle):
    """CYP2D6 here is PyPGx's call, carried as an outside one. Its evidence is in the
    PyPGx panel, with the read fractions this table has nothing to say about."""
    found = pharmcat_variants.collect(_report(), bundle)
    assert "CYP2D6" not in [g["gene"] for g in found["genes"]]


def test_each_base_found_carries_its_frequency(bundle):
    found = pharmcat_variants.collect(_report(), bundle)
    rows = {g["gene"]: g["rows"] for g in found["genes"]}
    assert rows["CYP2C19"][0]["frequency"] == "31% (eas)"
    assert rows["VKORC1"][0]["frequency"] == "47% (nfe)"
    assert found["priced"] is True
    assert found["release"] == "gnomAD v4.1 joint"


def test_a_position_the_annotation_does_not_reach_says_so(bundle):
    """The wording rarity.py uses, for the same reason: absence of a row where the
    data does not reach is not absence from gnomAD."""
    report = _report()
    report["genes"]["CYP2C19"]["variants"].append(
        _variant(99999999, "C/T", rsid="rs999")
    )
    found = pharmcat_variants.collect(report, bundle)
    rows = found["genes"][0]["rows"]
    assert rows[-1]["frequency"] == rarity.NO_DATA
    assert found["no_data"] == rarity.NO_DATA


def test_without_a_bundle_there_is_no_frequency_column(tmp_path):
    """As in the PyPGx panel: a column of the same message down every row is worse
    than no column."""
    found = pharmcat_variants.collect(_report(), tmp_path)
    assert found["status"] == "done" and found["differing"] == 2
    assert found["priced"] is False
    assert all(
        row["frequency"] == "" for gene in found["genes"] for row in gene["rows"]
    )


def test_a_report_with_no_positions_at_all_yields_nothing():
    assert pharmcat_variants.collect(None)["status"] == "none"
    assert pharmcat_variants.collect({})["status"] == "none"
    assert pharmcat_variants.collect({"genes": {}})["status"] == "none"
    outside = {"genes": {"CYP2D6": {"callSource": "OUTSIDE", "variants": []}}}
    assert pharmcat_variants.collect(outside)["status"] == "none"


def test_a_warning_on_a_position_is_kept(bundle):
    report = _report()
    report["genes"]["CYP2C19"]["variants"][1]["warnings"] = ["Unexpected allele"]
    found = pharmcat_variants.collect(report, bundle)
    assert found["genes"][0]["rows"][0]["warnings"] == ["Unexpected allele"]


def test_the_report_json_is_not_changed(bundle):
    """The generator hands this module PharmCAT's own parsed report, which other
    passes read after it (attach_pharmcat_messages, add_preprocessor_drops)."""
    report = _report()
    before = json.dumps(report, sort_keys=True)
    pharmcat_variants.collect(report, bundle)
    assert json.dumps(report, sort_keys=True) == before


# --- the public example ----------------------------------------------------


EXAMPLE = Path(__file__).resolve().parents[1] / "test_data"


def test_pharmcats_own_example_is_reference_at_every_position():
    """It is an all-reference sample, which is the case the section must still say
    something sensible about: twelve positions read, none of them differing."""
    data = json.loads(
        (EXAMPLE / "pharmcat.example.v340.report.json").read_text(encoding="utf-8")
    )
    found = pharmcat_variants.collect(data)
    assert found["status"] == "done"
    assert found["genotyped"] == 12
    assert found["differing"] == 0
    assert found["genes"] == []


# --- the two shapes PharmCAT has emitted -----------------------------------
#
# 3.x writes genes -> symbol; 2.x wrote genes -> {CPIC|DPWG|FDA} -> symbol, and
# app/pharmcat/report_json.py exists because both are live. Reading the symbols
# straight off `genes` found the guideline sources instead and no variants under them,
# so the table vanished without a word -- on a report the repository itself carries.


def _nested(report):
    """The same report in 2.x's shape: every gene under each guideline source, with
    its reference base in the field that release wrote it to."""
    genes = {}
    for source in ("CPIC", "DPWG"):
        copied = {}
        for symbol, gene in report["genes"].items():
            payload = json.loads(json.dumps(gene))
            payload["geneSymbol"] = symbol
            for variant in payload.get("variants") or []:
                variant["wildtypeAllele"] = variant.pop("referenceAllele")
            copied[symbol] = payload
        genes[source] = copied
    return {"genes": genes}


def test_the_older_nested_shape_is_read_too(bundle):
    """Not just read: counted once. A gene in that shape appears under every source
    that annotates it, so walking it naively would double every panel."""
    flat = pharmcat_variants.collect(_report(), bundle)
    nested = pharmcat_variants.collect(_nested(_report()), bundle)
    assert nested["status"] == flat["status"] == "done"
    assert nested["genotyped"] == flat["genotyped"]
    assert nested["differing"] == flat["differing"]
    assert [gene["gene"] for gene in nested["genes"]] == [
        gene["gene"] for gene in flat["genes"]
    ]


def test_the_reference_base_is_read_from_either_field(bundle):
    """3.x's referenceAllele, 2.x's wildtypeAllele. Without the second, nothing in an
    older report differs from a reference it cannot see, and the table comes back empty
    rather than absent -- the quieter half of the same bug."""
    nested = pharmcat_variants.collect(_nested(_report()), bundle)
    rows = {row["call"]: row for gene in nested["genes"] for row in gene["rows"]}
    assert rows, "no position differed from a reference the table could not see"
    heterozygous = rows["G/A"]
    assert heterozygous["reference"] == "G"
    assert heterozygous["zygosity"] == "heterozygous"
    # And the key the frequency lookup is made with still names the reference base.
    assert heterozygous["keys"] == ["chr10-94781859-G-A"]


def test_the_repositorys_own_2x_report_still_yields_its_panel():
    """test_data/pharmcat.example.report.json is a 2.15 report, and the only in-repo
    sample of that shape: it came back `status: none` before."""
    data = json.loads(
        (EXAMPLE / "pharmcat.example.report.json").read_text(encoding="utf-8")
    )
    found = pharmcat_variants.collect(data)
    assert found["status"] == "done"
    assert found["genotyped"] > 500
    # It is reference at every position, like the 3.4.0 example beside it.
    assert found["differing"] == 0


# --- the delivered page ----------------------------------------------------
#
# The context is passed to the interactive lane as its own argument rather than
# through the dicts that feed the templates, which is where a key has been lost
# before (report_branding_context, and the frequency column itself). The plumbing is
# still worth a test: an import renamed or an argument dropped would cost the whole
# table, and nothing else would fail.


def _with_one_variant_call(data):
    """PharmCAT's example is reference at every position. Give one of them the
    heterozygous call a real sample has: CYP2C19's rs12248560, which is *17."""
    variant = data["genes"]["CYP2C19"]["variants"][0]
    assert variant["dbSnpId"] == "rs12248560" and variant["call"] == "C/C"
    variant["call"] = "C/T"
    return data


def test_the_table_reaches_the_interactive_report(monkeypatch, tmp_path, bundle):
    pytest.importorskip("weasyprint")
    import app.reports.generator as generator_module

    monkeypatch.setattr(rarity, "DEFAULT_BUNDLE", bundle)
    wanted = {"write_html", "write_interactive_html"}
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(generator_module.REPORT_CONFIG, key, key in wanted)
    data = _with_one_variant_call(
        json.loads(
            (EXAMPLE / "pharmcat.example.v340.report.json").read_text(encoding="utf-8")
        )
    )
    result = generator_module.generate_report(
        {"data": data}, str(tmp_path), {"id": "p1"}, job_id=None
    )
    name = result["html_path"].split("/")[-1].replace(".html", "_interactive.html")
    page = (tmp_path / name).read_text(encoding="utf-8")
    assert "PharmCAT evidence" in page
    assert "chr10:94761900" in page and "rs12248560" in page
    assert "heterozygous" in page
    assert "<th>Population frequency</th>" in page
    assert "23% (mid)" in page
    # The printed report carries no such table: it is evidence, and the PDF carries
    # the calls and what they mean. Where to find it is in the Methodology paragraph
    # rarity.note builds, which tests/test_rarity.py pins.
    printed = (tmp_path / result["html_path"].split("/")[-1]).read_text(
        encoding="utf-8"
    )
    assert "PharmCAT evidence</h4>" not in printed
    assert "chr10:94761900" not in printed
