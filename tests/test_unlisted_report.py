"""Variants no allele definition lists, as the report says them (app/reports/unlisted.py).

The variants below are the ones planted in NA12878's demo VCF to exercise the check
(2026-10-23): a CYP2C19 splice donor, a DPYD stop gain ClinVar calls likely
pathogenic for DPD deficiency, and a CYP2C9 missense AlphaMissense scores 0.96.
Every tabled variant marks its gene's call, in its tier's words (the user's choice,
2026-10-24: "this report should be maximally accurate and complete, not abridged").
"""

import html
import json
import re
from unittest.mock import MagicMock

import pytest

from app.reports import unlisted
from app.reports.generator import build_report_credits, credit_run_for_job


def _variant(gene, tier, label, **kw):
    v = {
        "chrom": "chr10",
        "pos": 94762874,
        "ref": "G",
        "alt": "A",
        "gene": gene,
        "genes": [gene],
        "transcript": "ENST0",
        "refseq": "NM_0",
        "consequence": "splice donor",
        "hgvs_c": None,
        "hgvs_p": None,
        "zygosity": "heterozygous",
        "quality": [],
        "defined_position": False,
        "gnomad": None,
        "alphamissense": None,
        "clinvar": None,
        "low_confidence": None,
        "tier": tier,
        "label": label,
    }
    v.update(kw)
    return v


def _result(variants, flagged):
    return {
        "status": "done",
        "genes_checked": ["CYP2C19", "CYP2C9", "DPYD", "CYP2D6", "G6PD"],
        "genes_without_transcript": ["GSTT1"],
        "variants_listed": 44,
        "variants": variants,
        "flagged_genes": flagged,
        "annotation": {"sources": {"gnomad": {"version": "gnomAD v4.1 joint"}}},
    }


SPLICE = _variant("CYP2C19", "A", "Predicted loss of function", hgvs_c="c.168+1G>A")
STOP = _variant(
    "DPYD",
    "A",
    "Predicted loss of function",
    chrom="chr1",
    pos=97082449,
    hgvs_c="c.2788C>T",
    hgvs_p="p.Gln930Ter",
    consequence="stop gained",
    clinvar={
        "id": "3241364",
        "significance": "Likely pathogenic",
        "review": "x",
        "stars": 1,
        "conditions": ["Dihydropyrimidine dehydrogenase deficiency"],
    },
)
MISSENSE = _variant(
    "CYP2C9",
    "B",
    "Predicted damaging (AlphaMissense 0.96)",
    pos=94938805,
    hgvs_c="c.123T>G",
    hgvs_p="p.Asn41Lys",
    consequence="missense",
    alphamissense={"score": 0.9613, "class": "likely pathogenic"},
)
CYP2D6 = _variant(
    "CYP2D6", "A", "Predicted loss of function", chrom="chr22", pos=42127000
)
COMMON = _variant(
    "CYP2C9", "common", "Common", gnomad={"af": 0.2, "af_grpmax": 0.3, "grpmax": "afr"}
)


def _ctx(male=None):
    pypgx = (
        None
        if male is None
        else {
            "results": {
                "G6PD": {"details": {"copy_number": "Male" if male else "Female"}}
            }
        }
    )
    result = _result(
        [SPLICE, STOP, MISSENSE, CYP2D6, COMMON],
        {"CYP2C19": [0], "DPYD": [1], "CYP2C9": [2], "CYP2D6": [3]},
    )
    return unlisted.context(result, pypgx)


def test_a_run_before_the_check_and_one_that_failed_say_so():
    assert unlisted.context(None)["status"] == "not_run"
    failed = unlisted.context({"status": "failed", "reason": "The check failed: x"})
    assert failed == {
        "status": "failed",
        "reason": "The check failed: x",
        "flagged": {},
    }


def test_every_tabled_variant_marks_its_gene_cyp2d6_too():
    ctx = _ctx()
    assert set(ctx["flagged"]) == {"CYP2C19", "DPYD", "CYP2C9", "CYP2D6"}
    assert [r["genes"] for r in ctx["rows"]] == ["CYP2C19", "DPYD", "CYP2C9", "CYP2D6"]
    # CYP2D6's row says the variant may be its neighbour's.
    assert ctx["rows"][3]["notes"] == ["may be CYP2D7's"]
    assert ctx["others"] == {"none": 0, "common": 1, "low_quality": 0}
    assert ctx["dpyd_caveat"]


@pytest.mark.parametrize(
    "tier, extra, effect, short",
    [
        ("A", {}, "predicted to cause loss of function", "predicted loss of function"),
        (
            "A-low",
            {},
            "possibly causing loss of function (a low-confidence prediction)",
            "possible loss of function",
        ),
        (
            "B",
            {"alphamissense": {"score": 0.9613}},
            "predicted to damage the protein (AlphaMissense 0.96)",
            "predicted damaging",
        ),
        (
            "B-low",
            {"alphamissense": {"score": 0.31}},
            "possibly damaging the protein (AlphaMissense 0.31, under its likely-pathogenic cut-off of 0.564)",
            "possibly damaging",
        ),
        # Over the cut-off but in a region where the call is less sure.
        (
            "B-low",
            {
                "alphamissense": {"score": 0.74},
                "low_confidence": "segmental duplication",
            },
            "possibly damaging the protein (AlphaMissense 0.74, in a segmental duplication region)",
            "possibly damaging",
        ),
        (
            "A-low",
            {"low_confidence": "segmental duplication"},
            "possibly causing loss of function (a low-confidence prediction: in a segmental duplication region)",
            "possible loss of function",
        ),
        (
            "C",
            {"consequence": "inframe deletion"},
            "of uncertain effect (inframe deletion)",
            "effect uncertain",
        ),
        (
            "A",
            {"clinvar_basis": True, "label": "Loss of function, by ClinVar"},
            "classified by ClinVar as pathogenic, a loss of function",
            "pathogenic in ClinVar",
        ),
        (
            "A",
            {
                "clinvar_basis": True,
                "label": "Pathogenic for malignant hyperthermia susceptibility, by ClinVar",
            },
            "classified by ClinVar as pathogenic for malignant hyperthermia susceptibility",
            "pathogenic in ClinVar",
        ),
    ],
)
def test_each_tier_marks_in_its_own_words(tier, extra, effect, short):
    v = _variant("CYP2C19", tier, extra.pop("label", "x"), hgvs_c="c.1A>G", **extra)
    flag = unlisted.gene_flag("CYP2C19", [v])
    assert flag["note"].startswith(f"c.1A>G, {effect}: no allele definition lists it")
    assert (
        flag["call_note"]
        == f"Unlisted variant: {short} (see Variants Outside the Allele Definitions)"
    )


def test_a_gene_with_a_near_identical_neighbour_says_the_variant_may_be_its():
    flag = unlisted.gene_flag("CYP2D6", [CYP2D6])
    assert flag["call_note"] == (
        "Unlisted variant: predicted loss of function, may be another gene's "
        "(see Variants Outside the Allele Definitions)"
    )
    assert (
        "Reads of CYP2D7, a near-identical gene beside CYP2D6, can align to it"
        in flag["note"]
    )
    assert "may be CYP2D7's" in flag["fhir_note"]
    assert "CYP2D7" in unlisted.alert(_ctx())


def test_a_disease_gene_in_the_table_is_said_to_be_one():
    cftr = _variant(
        "CFTR", "C", "Effect uncertain", chrom="chr7", consequence="inframe deletion"
    )
    ryr1 = _variant("RYR1", "B", "Predicted damaging", chrom="chr19")
    one = unlisted.context(_result([cftr], {"CFTR": [0]}))
    assert one["disease_note"] == (
        "CFTR is also a disease gene: a variant in it can bear on cystic fibrosis as well "
        "as on a drug's effect."
    )
    two = unlisted.context(_result([cftr, ryr1], {"CFTR": [0], "RYR1": [1]}))
    assert two["disease_note"].startswith("CFTR and RYR1 are also disease genes")
    assert unlisted.context(_result([STOP], {"DPYD": [0]}))["disease_note"] is None


def test_a_row_names_the_change_once_and_puts_the_score_under_the_effect():
    row = next(r for r in _ctx()["rows"] if r["genes"] == "CYP2C9")
    assert (row["variant"], row["protein"]) == ("c.123T>G", "p.Asn41Lys")
    assert row["effect"] == "Predicted damaging"
    assert row["consequence"] == "missense, AlphaMissense 0.96"
    dpyd = next(r for r in _ctx()["rows"] if r["genes"] == "DPYD")
    assert dpyd["clinvar"] == {
        "text": "ClinVar: Likely pathogenic, 1 star",
        "conditions": "Dihydropyrimidine dehydrogenase deficiency",
    }


def test_a_het_chrx_call_in_a_male_is_counted_as_unreliable_not_flagged():
    g6pd = _variant(
        "G6PD", "A", "Predicted loss of function", chrom="chrX", pos=154532083
    )
    result = _result([g6pd], {"G6PD": [0]})
    male = unlisted.context(
        result, {"results": {"G6PD": {"details": {"copy_number": "Male"}}}}
    )
    assert male["flagged"] == {} and male["rows"] == []
    assert male["others"]["low_quality"] == 1
    female = unlisted.context(
        result, {"results": {"G6PD": {"details": {"copy_number": "Female"}}}}
    )
    assert set(female["flagged"]) == {"G6PD"}


def test_the_notes_say_what_is_predicted_and_that_the_phenotype_assumes_nothing():
    one = unlisted.gene_flag("DPYD", [STOP])
    assert one["call_note"] == (
        "Unlisted variant: predicted loss of function (see Variants Outside the Allele Definitions)"
    )
    assert one["note"] == (
        "c.2788C>T (p.Gln930Ter), predicted to cause loss of function: no allele "
        "definition lists it, and the phenotype here assumes it does nothing. See "
        "Variants Outside the Allele Definitions."
    )
    c = _variant(
        "CYP2C9",
        "C",
        "Effect uncertain",
        consequence="inframe deletion",
        hgvs_c="c.9del",
    )
    two = unlisted.gene_flag("CYP2C9", [c, MISSENSE])
    assert (
        "c.123T>G (p.Asn41Lys), predicted to damage the protein (AlphaMissense 0.96)"
        in two["note"]
    )
    assert "c.9del, of uncertain effect (inframe deletion)" in two["note"]
    assert "assumes they do nothing" in two["note"]
    # The strongest of them leads the short note.
    assert two["call_note"].startswith(
        "2 unlisted variants: strongest predicted damaging"
    )
    assert "not validated for clinical use" in one["fhir_note"]


def test_the_alert_names_each_gene_and_never_says_pathogenic_of_its_own():
    text = unlisted.alert(_ctx())
    for gene in ("CYP2C19", "DPYD", "CYP2C9", "CYP2D6"):
        assert f"<strong>{gene}</strong>" in text
    assert "pathogenic" not in text.lower()
    assert unlisted.alert(unlisted.context(_result([COMMON], {}))) is None


def test_flagged_rows_carry_the_notes():
    rows = [
        {"gene": "DPYD", "diplotype": "*1/*1"},
        {"gene": "TPMT", "diplotype": "*1/*1"},
    ]
    unlisted.annotate_genes(rows, _ctx())
    assert rows[0]["unlisted_flag"]["call_note"].startswith("Unlisted variant")
    assert "unlisted_flag" not in rows[1]


def test_read_result_tells_a_missing_file_from_a_broken_one(tmp_path):
    assert unlisted.read_result(str(tmp_path)) is None
    (tmp_path / "unlisted_variants.json").write_text("{not json")
    assert unlisted.read_result(str(tmp_path))["status"] == "failed"
    (tmp_path / "unlisted_variants.json").write_text(json.dumps({"status": "done"}))
    assert unlisted.read_result(str(tmp_path)) == {"status": "done"}


def _render(rows, ctx):
    from app.reports.generator import env

    tsv = [
        {
            "gene": g,
            "rec_lookup_diplotype": "*1/*1",
            "rec_lookup_phenotype": "Normal Metabolizer",
        }
        for g in ("CYP2C19", "DPYD")
    ]
    return env.get_template("report_template.html").render(
        execsum_from_tsv=tsv,
        diplotypes=rows,
        recommendations=[],
        patient_id="p",
        report_id="r",
        report_date="2026-10-23",
        organization="ZaroPGx",
        disclaimer="",
        unlisted=ctx,
    )


def _clean(fragment):
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip())


def test_the_pdf_marks_the_summary_row_notes_it_and_tables_the_variants():
    ctx = _ctx()
    rows = [
        {
            "gene": "CYP2C19",
            "diplotype": "*1/*2",
            "phenotype": "Intermediate Metabolizer",
        },
        {"gene": "DPYD", "diplotype": "*1/*1", "phenotype": "Normal Metabolizer"},
    ]
    unlisted.annotate_genes(rows, ctx)
    page = _render(rows, ctx)
    summary = page[
        page.index('id="exec-summary"') : page.index('id="exec-summary-notes"')
    ]
    row = summary[summary.index('id="summary-DPYD"') :]
    assert "A variant no allele definition lists" in row[: row.index("</tr>")]
    notes = _clean(
        page[
            page.index('id="exec-summary-notes"') : page.index(
                "<h2>Genetic Results</h2>"
            )
        ]
    )
    assert (
        "⚠ DPYD : c.2788C>T (p.Gln930Ter), predicted to cause loss of function: no allele definition lists it"
        in notes
    )
    results = page[page.index("<h2>Genetic Results</h2>") :]
    row = results[results.index("<td>DPYD</td>") :]
    assert "⚠ Unlisted variant: predicted loss of function" in _clean(
        row[: row.index("</tr>")]
    )
    section = _clean(
        page[page.index("<h2>Variants Outside the Allele Definitions</h2>") :]
    )
    assert "of 5 pharmacogenes" in section
    assert (
        "Of the 49 variants found there, 44 are part of an allele definition and 5 are not."
        in section
    )
    assert "Each one marks its gene's call" in section
    assert "c.168+1G>A" in section and "p.Asn41Lys" in section
    assert "For DPYD, CPIC and DPWG note" in section
    assert "Not tabled: 1 common" in section


def test_a_report_without_the_check_says_it_did_not_run():
    page = _render([{"gene": "DPYD", "diplotype": "*1/*1"}], unlisted.context(None))
    section = _clean(
        page[page.index("<h2>Variants Outside the Allele Definitions</h2>") :]
    )
    assert section.startswith(
        "Variants Outside the Allele Definitions This run did not check for variants that "
        "no allele definition lists. It ran before ZaroPGx had the check."
    )
    assert "unlisted-note" not in page.split("</style>")[-1]


def test_the_fhir_genotype_carries_the_note():
    from app.services.fhir_export_service import FHIRExportService

    row = {"gene": "DPYD", "diplotype": "*1/*1", "phenotype": "Normal Metabolizer"}
    unlisted.annotate_genes([row], _ctx())
    obs = FHIRExportService(MagicMock())._create_genotype_observation(
        gene_data=row, patient_ref="Patient/p", run_id="r"
    )
    assert obs["note"] == [{"text": row["unlisted_flag"]["fhir_note"]}]
    plain = FHIRExportService(MagicMock())._create_genotype_observation(
        gene_data={"gene": "CYP2C9", "diplotype": "*1/*1"},
        patient_ref="Patient/p",
        run_id="r",
    )
    assert "note" not in plain


def test_its_sources_are_credited_only_when_it_ran():
    steps = [{"name": "pypgx_analysis", "status": "completed"}]
    ran = json.dumps(
        build_report_credits(credit_run_for_job(steps, "vcf", unlisted_checked=True)),
        default=str,
    )
    for name in (
        "AlphaMissense",
        "gnomAD",
        "ClinVar",
        "Ensembl and MANE",
        "GIAB genomic stratifications",
        "BCFtools/csq: haplotype-aware variant consequences",
    ):
        assert name in ran, name
    did_not = json.dumps(
        build_report_credits(credit_run_for_job(steps, "vcf")), default=str
    )
    for name in ("AlphaMissense", "gnomAD", "ClinVar", "BCFtools/csq"):
        assert name not in did_not, name


def test_the_interactive_report_gets_the_same_section(tmp_path):
    """Its template is rendered from a context of its own: a key set only for the PDF
    never reaches it, and the section said the check had not run."""
    from app.reports.generator import create_interactive_html_report

    ctx = _ctx()
    rows = [{"gene": "DPYD", "diplotype": "*1/*1", "phenotype": "Normal Metabolizer"}]
    unlisted.annotate_genes(rows, ctx)
    out = tmp_path / "r_pgx_report_interactive.html"
    create_interactive_html_report("p", "r", rows, [], str(out), unlisted_context=ctx)
    page = _clean(out.read_text())
    assert (
        "Variants Outside the Allele Definitions PharmCAT and PyPGx call each gene"
        in page
    )
    assert "did not check" not in page
    # Called from elsewhere, it reads the result beside the report.
    (tmp_path / "unlisted_variants.json").write_text(
        json.dumps(_result([STOP], {"DPYD": [0]}))
    )
    create_interactive_html_report("p", "r", rows, [], str(out))
    assert "p.Gln930Ter" in _clean(out.read_text())


def test_the_export_tab_offers_the_result(tmp_path):
    from app.reports.generator import export_downloads

    assert export_downloads(str(tmp_path), "r") == []
    (tmp_path / "unlisted_variants.json").write_text("{}")
    assert export_downloads(str(tmp_path), "r") == [
        {
            "label": "Variants outside the allele definitions",
            "format": "JSON",
            "href": "unlisted_variants.json",
        }
    ]


def _fhir_bundle(variants, flagged, genes):
    from app.services.fhir_export_service import FHIRExportService

    rows = [
        {"gene": g, "diplotype": "*1/*1", "phenotype": "Normal Metabolizer"}
        for g in genes
    ]
    unlisted.annotate_genes(rows, unlisted.context(_result(variants, flagged)))
    return FHIRExportService(MagicMock())._build_fhir_bundle(
        {"genes": rows, "drugRecommendations": []}, "run-1"
    )


def _by_profile(bundle, name):
    return [
        e["resource"]
        for e in bundle["entry"]
        if e["resource"].get("meta", {}).get("profile", [""])[0].endswith("/" + name)
    ]


def _component(obs, code):
    found = [c for c in obs["component"] if c["code"]["coding"][0]["code"] == code]
    return found[0] if found else None


DPYD_FULL = dict(
    STOP, refseq="NM_000110.4", depth=30, alt_reads=14, clinvar_drug_response=True
)


def test_fhir_states_each_marking_variant_as_the_igs_variant():
    from app.services.fhir import terminology

    bundle = _fhir_bundle([DPYD_FULL], {"DPYD": [0]}, ["DPYD"])
    (variant,) = _by_profile(bundle, "variant")
    assert variant["code"]["coding"][0]["code"] == "69548-6"
    assert variant["valueCodeableConcept"]["coding"][0]["display"] == "Present"
    want = {
        "48018-6": ("valueCodeableConcept", "HGNC:3012"),
        "62374-4": ("valueCodeableConcept", "LA26806-2"),
        "48000-4": ("valueCodeableConcept", "LA21254-0"),
        "92822-6": ("valueCodeableConcept", "LA30102-0"),
        "48004-6": ("valueCodeableConcept", "NM_000110.4:c.2788C>T"),
        "51958-7": ("valueCodeableConcept", "NM_000110.4"),
        "53034-5": ("valueCodeableConcept", "LA6706-1"),
        "48002-0": ("valueCodeableConcept", "LA6683-2"),
        "81252-9": ("valueCodeableConcept", "3241364"),
    }
    for code, (kind, value) in want.items():
        assert _component(variant, code)[kind]["coding"][0]["code"] == value, code
    assert _component(variant, "81254-5")["valueRange"] == {"low": {"value": 97082449}}
    assert _component(variant, "69547-8")["valueString"] == "G"
    assert _component(variant, "69551-0")["valueString"] == "A"
    # No protein accession, so the p. name is text, not an HGVS code.
    assert _component(variant, "48005-3")["valueCodeableConcept"] == {
        "text": "p.Gln930Ter"
    }
    assert _component(variant, "82121-5")["valueQuantity"]["value"] == 14
    assert _component(variant, "81258-6")["valueQuantity"]["value"] == 0.467
    # Every LOINC code goes with its official display, and nothing is sent empty.
    for c in [variant["code"]] + [c["code"] for c in variant["component"]]:
        coding = c["coding"][0]
        assert coding["display"] == terminology.LOINC_DISPLAYS[coding["code"]]
    from tests.test_fhir_no_empty_values import empty_elements

    assert empty_elements(bundle) == []


def test_fhir_ties_the_variant_to_the_genotype_by_an_implication_of_uncertain_significance():
    c = _variant(
        "DPYD",
        "C",
        "Effect uncertain",
        consequence="inframe deletion",
        ref="GAAT",
        alt="G",
    )
    bundle = _fhir_bundle([c], {"DPYD": [0]}, ["DPYD"])
    (variant,) = _by_profile(bundle, "variant")
    (implication,) = _by_profile(bundle, "diagnostic-implication")
    (genotype,) = _by_profile(bundle, "genotype")
    assert _component(variant, "81254-5")["valueRange"] == {
        "low": {"value": 94762874},
        "high": {"value": 94762877},
    }
    assert implication["code"]["coding"][0] == {
        "system": "http://hl7.org/fhir/uv/genomics-reporting/CodeSystem/tbd-codes-cs",
        "code": "diagnostic-implication",
        "display": "Diagnostic Implication",
    }
    assert [r["reference"] for r in implication["derivedFrom"]] == [
        f"urn:uuid:{variant['id']}",
        f"urn:uuid:{genotype['id']}",
    ]
    significance = _component(implication, "53037-8")["valueCodeableConcept"]["coding"][
        0
    ]
    assert (significance["code"], significance["display"]) == (
        "LA26333-7",
        "Uncertain significance",
    )
    assert _component(implication, "81259-4") is None
    assert (
        "its significance is uncertain"
        in _component(implication, "conclusion-string")["valueString"]
    )
    # The genotype keeps its note, and the report lists both new results.
    assert genotype["note"][0]["text"].startswith(
        "In DPYD, chr10:94762874 GAAT>G, of uncertain effect"
    )
    report = bundle["entry"][0]["resource"]
    refs = {r["reference"] for r in report["result"]}
    assert {f"urn:uuid:{variant['id']}", f"urn:uuid:{implication['id']}"} <= refs


def test_fhir_gives_clinvars_classification_where_clinvar_has_one_for_the_drug_response():
    """Coded uncertain, a variant ClinVar classifies likely pathogenic for DPD
    deficiency would contradict ClinVar."""
    bundle = _fhir_bundle([DPYD_FULL], {"DPYD": [0]}, ["DPYD"])
    (implication,) = _by_profile(bundle, "diagnostic-implication")
    significance = _component(implication, "53037-8")["valueCodeableConcept"]["coding"][
        0
    ]
    assert (significance["code"], significance["display"]) == (
        "LA26332-9",
        "Likely pathogenic",
    )
    assert _component(implication, "81259-4")["valueCodeableConcept"] == {
        "text": "Dihydropyrimidine dehydrogenase deficiency"
    }
    assert (
        "ClinVar classifies it Likely pathogenic"
        in _component(implication, "conclusion-string")["valueString"]
    )


def test_fhir_states_a_variant_two_genes_share_once():
    shared = _variant(
        "UGT1A1",
        "B",
        "Predicted damaging",
        genes=["UGT1A1", "UGT1A4"],
        chrom="chr2",
        pos=233760000,
    )
    bundle = _fhir_bundle(
        [shared], {"UGT1A1": [0], "UGT1A4": [0]}, ["UGT1A1", "UGT1A4"]
    )
    assert len(_by_profile(bundle, "variant")) == 1
    implications = _by_profile(bundle, "diagnostic-implication")
    assert len(implications) == 2
    assert len({i["derivedFrom"][0]["reference"] for i in implications}) == 1
    assert len({i["derivedFrom"][1]["reference"] for i in implications}) == 2


def test_fhir_has_no_variant_where_nothing_marks_a_call():
    bundle = _fhir_bundle([COMMON], {}, ["CYP2C9"])
    assert (
        _by_profile(bundle, "variant") == []
        and _by_profile(bundle, "diagnostic-implication") == []
    )
