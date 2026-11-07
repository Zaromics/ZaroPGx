"""How common the variants behind a call are, as the report says it (app/reports/rarity.py).

The numbers in the fake bundle below are the shape of the real one: gnomAD v4.1's
joint release, keyed chrom/pos/ref/alt with ``chr`` prefixes, read against
``regions.bed``'s coding-exon intervals. The distinction the tests are really about is
between a variant gnomAD has not seen and a variant the bundle's regions do not
reach: 36% of the variants behind PyPGx's calls over the 61 GeT-RM genomes are the
second kind, and calling those "not in gnomAD" would assert something the data cannot
support.
"""

import gzip

import pytest

from app.reports import rarity

GNOMAD_HEADER = (
    "chrom\tpos\tref\talt\tfilter\tAF_joint\tAF_grpmax_joint\tgrpmax_joint"
    "\tfafmax_faf95_max_joint\tnhomalt_joint\tnhomalt_joint_XY\n"
)


@pytest.fixture
def bundle(tmp_path):
    """A bundle holding one common variant, one rare one, and one with no frequency."""
    with gzip.open(tmp_path / "gnomad.tsv.gz", "wt") as f:
        f.write(GNOMAD_HEADER)
        # CYP2C19 *2, common everywhere.
        f.write("chr10\t94781859\tG\tA\tPASS\t0.15\t0.31\teas\t0.30\t900\t0\n")
        # Rare, and an indel, to show the key survives multi-base alleles.
        f.write("chr22\t42128241\tCT\tC\tPASS\t2.0e-05\t9.1e-05\tafr\t1.0e-05\t0\t0\n")
        # In gnomAD, no group frequency given.
        f.write("chr19\t40991369\tC\tT\tPASS\t.\t.\t\t.\t0\t0\n")
    # Two exons; chr7's position below falls outside both.
    (tmp_path / "regions.bed").write_text(
        "chr10\t94781800\t94781900\n"
        "chr22\t42128200\t42128300\n"
        "chr19\t40991300\t40991400\n"
        "chr10\t94762800\t94762900\n"
    )
    (tmp_path / "manifest.json").write_text(
        '{"sources": {"gnomad": {"version": "gnomAD v4.1 joint"}}}'
    )
    return tmp_path


def test_a_pypgx_variant_name_becomes_a_bundle_key():
    # PyPGx writes no chr prefix; the bundle has one.
    assert rarity.variant_key("19-40991369-C-T") == ("chr19", 40991369, "C", "T")
    assert rarity.variant_key("chr19-40991369-C-T") == ("chr19", 40991369, "C", "T")


def test_lower_case_bases_still_match():
    """A VCF called against a soft-masked reference carries indels in lower case.

    That is the upstream bug in dev-notes/upstream/pypgx-lowercase-alleles.md; here it
    would silently cost every indel in a masked stretch its frequency.
    """
    assert rarity.variant_key("15-74747271-at-a") == ("chr15", 74747271, "AT", "A")


@pytest.mark.parametrize(
    "text", ["", "nonsense", "19-40991369-C", "19-x-C-T", "19-40991369-C-T-G", None, 7]
)
def test_a_name_that_is_not_a_variant_is_refused(text):
    assert rarity.variant_key(text) is None


def test_a_common_variant_reads_as_a_percentage_with_its_group(bundle):
    found = rarity.lookup(["10-94781859-G-A"], bundle)
    assert found["10-94781859-G-A"] == "31% (eas)"


def test_a_rare_variant_is_not_rounded_away(bundle):
    """Rare reads as rare, not as 0%.

    Below 0.01% the wording stops giving digits and says "under 0.01%", inherited from
    the unlisted-variants table: at that end the extra figures are noise, and the
    column exists so a reader can tell this apart from the 31% above. The real data
    reaches 0.0022% at its rarest, so the floor is reached in practice.
    """
    found = rarity.lookup(["22-42128241-CT-C"], bundle)
    assert found["22-42128241-CT-C"] == "under 0.01% (afr)"
    assert "0%" != found["22-42128241-CT-C"]
    # Just above the floor the digits are kept.
    assert rarity.frequency_text({"af_grpmax": 0.00011, "grpmax": "afr"}) == (
        "0.011% (afr)"
    )


def test_a_variant_in_gnomad_without_a_frequency_says_so(bundle):
    found = rarity.lookup(["19-40991369-C-T"], bundle)
    assert found["19-40991369-C-T"] == "In gnomAD, frequency not given"


def test_inside_the_regions_but_absent_means_gnomad_has_not_seen_it(bundle):
    found = rarity.lookup(["10-94762874-G-A"], bundle)
    assert found["10-94762874-G-A"] == "Not in gnomAD"


def test_outside_the_regions_claims_nothing_about_gnomad(bundle):
    """The honest answer for a promoter or intronic core variant."""
    found = rarity.lookup(["7-99672916-T-C"], bundle)
    assert found["7-99672916-T-C"] == rarity.NO_DATA
    assert "gnomAD" not in found["7-99672916-T-C"]


def test_no_bundle_yields_nothing_so_the_column_can_be_left_out(tmp_path):
    assert rarity.lookup(["10-94781859-G-A"], tmp_path) == {}


def test_without_regions_the_weaker_answer_is_given(bundle):
    """A bundle whose regions cannot be read must not upgrade silence to absence."""
    (bundle / "regions.bed").unlink()
    found = rarity.lookup(["10-94762874-G-A"], bundle)
    assert found["10-94762874-G-A"] == rarity.NO_DATA


def test_the_frequency_tables_own_regions_are_the_ones_read(bundle):
    """From bundle version 3 the table is cut wider than the coding exons.

    The promoter position below is outside regions.bed, which is the unlisted
    check's scope, and inside gnomad_regions.bed, which is the table's own: with the
    wider file there, absence is gnomAD's and not the bundle's, and the report says
    so. CYP3A5 *3's 6986A>G is the real case -- an intronic core variant of an allele
    carried by most of the world.
    """
    (bundle / "gnomad_regions.bed").write_text(
        (bundle / "regions.bed").read_text() + "chr7\t99672910\t99672920\n"
    )
    assert (
        rarity.lookup(["7-99672916-T-C"], bundle)["7-99672916-T-C"] == "Not in gnomAD"
    )


def test_an_older_bundle_is_read_through_the_only_file_it_has(bundle):
    """Version 2 and earlier wrote regions.bed alone, and cut the table over it, so
    that file describes the table there. A bundle in place is not rebuilt until the
    downloader sees a new version, so both shapes are live at once."""
    assert not (bundle / "gnomad_regions.bed").exists()
    found = rarity.lookup(["10-94762874-G-A", "7-99672916-T-C"], bundle)
    assert found["10-94762874-G-A"] == "Not in gnomAD"
    assert found["7-99672916-T-C"] == rarity.NO_DATA


def _genes():
    return [
        {
            "gene": "CYP2C19",
            "pypgx": {
                "supporting_variants": [
                    {
                        "allele": "*2",
                        "variants": [
                            {"variant": "10-94781859-G-A", "allele_fraction": 0.51}
                        ],
                    }
                ]
            },
        },
        {
            "gene": "CYP3A5",
            "pypgx": {
                "supporting_variants": [
                    {
                        "allele": "*3",
                        "variants": [
                            {"variant": "7-99672916-T-C", "allele_fraction": 1.0}
                        ],
                    }
                ]
            },
        },
        {"gene": "TPMT"},  # no PyPGx record at all
    ]


def test_each_variant_behind_a_call_gets_a_frequency(bundle):
    genes = _genes()
    summary = rarity.annotate_genes(genes, bundle)
    assert summary["status"] == "done"
    assert summary["total"] == 2
    assert summary["shown"] == 1  # the CYP3A5 one is outside the regions
    assert summary["release"] == "gnomAD v4.1 joint"
    first = genes[0]["pypgx"]["supporting_variants"][0]["variants"][0]
    second = genes[1]["pypgx"]["supporting_variants"][0]["variants"][0]
    assert first["frequency"] == "31% (eas)"
    assert second["frequency"] == rarity.NO_DATA
    # The allele fraction is still there beside it.
    assert first["allele_fraction"] == 0.51


def test_the_pipelines_own_variant_dicts_are_not_mutated(bundle):
    """``generator.pypgx_record`` passes PyPGx's lists through by reference.

    read_phase.py reads the same parsed results afterwards, so the annotation is made
    on copies rather than on the pipeline's own objects.
    """
    source = {"variant": "10-94781859-G-A", "allele_fraction": 0.51}
    genes = [
        {
            "gene": "CYP2C19",
            "pypgx": {"supporting_variants": [{"allele": "*2", "variants": [source]}]},
        }
    ]
    rarity.annotate_genes(genes, bundle)
    assert "frequency" not in source
    assert genes[0]["pypgx"]["supporting_variants"][0]["variants"][0]["frequency"]


def test_a_report_with_no_pypgx_evidence_asks_nothing_of_the_bundle(bundle):
    assert rarity.annotate_genes([{"gene": "TPMT"}], bundle)["status"] == "none"


def test_a_missing_bundle_is_reported_not_raised(tmp_path):
    summary = rarity.annotate_genes(_genes(), tmp_path)
    assert summary["status"] == "no_bundle"
    assert summary["shown"] == 0


def test_rows_that_are_not_what_they_should_be_do_not_raise(bundle):
    genes = [
        "not a mapping",
        {"gene": "A", "pypgx": "not a mapping"},
        {"gene": "B", "pypgx": {"supporting_variants": ["not a mapping"]}},
        {"gene": "C", "pypgx": {"supporting_variants": [{"allele": "*1"}]}},
        {
            "gene": "D",
            "pypgx": {
                "supporting_variants": [
                    {
                        "allele": "*2",
                        "variants": ["not a mapping", {"no_variant_key": 1}],
                    }
                ]
            },
        },
    ]
    assert rarity.annotate_genes(genes, bundle)["status"] == "none"


def test_the_unlisted_table_words_a_frequency_the_same_way():
    """One wording for both tables: app/reports/unlisted.py delegates here."""
    from app.reports import unlisted

    v = {"gnomad": {"af_grpmax": 0.31, "grpmax": "eas"}}
    assert unlisted._frequency(v) == rarity.frequency_text(v["gnomad"]) == "31% (eas)"
    assert unlisted._frequency({"gnomad": None}) == "Not in gnomAD"


def test_a_frequency_too_small_to_print_is_not_called_zero():
    assert rarity.frequency_text({"af_grpmax": 1e-7}) == "under 0.01%"
    assert rarity.frequency_text({"af_grpmax": 0}) == "In gnomAD, not seen in any group"


# --- the Methodology paragraph, which is what the PDF gets -------------------
# The PDF carries no evidence table, so the per-variant numbers cannot reach it. What
# it gets instead is the part a reader cannot work out alone: which calls rest on a
# variant almost nobody has. Over the 61 GeT-RM genomes that is a median of one allele
# per report and four at most, which is why naming them stays short enough to print.


def test_the_note_names_the_calls_that_rest_on_a_rare_variant(bundle):
    genes = [
        {
            "gene": "CYP2D6",
            "pypgx": {
                "supporting_variants": [
                    {
                        "allele": "*21",
                        "variants": [
                            {"variant": "22-42128241-CT-C", "allele_fraction": 0.47}
                        ],
                    }
                ]
            },
        },
        {
            "gene": "CYP2C19",
            "pypgx": {
                "supporting_variants": [
                    {
                        "allele": "*2",
                        "variants": [
                            {"variant": "10-94781859-G-A", "allele_fraction": 0.5}
                        ],
                    }
                ]
            },
        },
    ]
    summary = rarity.annotate_genes(genes, bundle)
    assert [r["allele"] for r in summary["rare"]] == ["*21"]
    text = rarity.note(summary)
    assert "CYP2D6 *21" in text
    assert "1 person in 100" in text
    # The common one is not named as rare.
    assert "CYP2C19" not in text
    # A reader is told what rarity does and does not mean.
    assert "not that it is less" in text


def test_the_note_says_plainly_when_nothing_is_rare(bundle):
    genes = [
        {
            "gene": "CYP2C19",
            "pypgx": {
                "supporting_variants": [
                    {"allele": "*2", "variants": [{"variant": "10-94781859-G-A"}]}
                ]
            },
        }
    ]
    summary = rarity.annotate_genes(genes, bundle)
    assert summary["rare"] == []
    text = rarity.note(summary)
    assert "none of the alleles PyPGx called rests on a rare variant" in text


def test_the_note_explains_the_variants_its_data_does_not_reach(bundle):
    summary = rarity.annotate_genes(_genes(), bundle)
    text = rarity.note(summary)
    # 2 variants, 1 reached: the reader is told why, not left with a silent gap.
    assert "reaches 1 of them" in text
    assert "coding exons" in text and "promoter or an intron" in text


def test_the_note_does_not_hedge_when_the_data_reaches_everything(bundle):
    genes = [
        {
            "gene": "CYP2C19",
            "pypgx": {
                "supporting_variants": [
                    {"allele": "*2", "variants": [{"variant": "10-94781859-G-A"}]}
                ]
            },
        }
    ]
    text = rarity.note(rarity.annotate_genes(genes, bundle))
    assert "reaches all" in text
    assert "outside the regions" not in text


def test_the_note_counts_one_allele_in_the_singular(bundle):
    genes = [
        {
            "gene": "CYP2D6",
            "pypgx": {
                "supporting_variants": [
                    {"allele": "*21", "variants": [{"variant": "22-42128241-CT-C"}]}
                ]
            },
        }
    ]
    text = rarity.note(rarity.annotate_genes(genes, bundle))
    assert "1 of the alleles PyPGx called rests on" in text
    # "the 1 variants that define" is how a counter talks, not how a report reads.
    assert "the one variant PyPGx reports behind an allele it called" in text


def test_the_note_reads_a_frequency_as_a_reader_would_say_it(bundle):
    """ "0.0091% (afr)" is a table cell; a sentence wants "in afr"."""
    assert (
        rarity._named([{"gene": "CYP2D6", "allele": "*21", "frequency": "0.41% (eas)"}])
        == "CYP2D6 *21 (0.41% in eas)"
    )
    assert (
        rarity._named(
            [
                {"gene": "A", "allele": "*1", "frequency": "1% (a)"},
                {"gene": "B", "allele": "*2", "frequency": "2% (b)"},
            ]
        )
        == "A *1 (1% in a) and B *2 (2% in b)"
    )
    assert rarity._named(
        [
            {"gene": "A", "allele": "*1", "frequency": "1% (a)"},
            {"gene": "B", "allele": "*2", "frequency": "2% (b)"},
            {"gene": "C", "allele": "*3", "frequency": "3% (c)"},
        ]
    ).endswith(", and C *3 (3% in c)")


@pytest.mark.parametrize(
    "summary",
    [None, {}, {"status": "no_bundle", "total": 3}, {"status": "done", "total": 0}],
)
def test_no_note_without_something_to_say(summary):
    assert rarity.note(summary) is None


def test_a_run_whose_data_reached_nothing_claims_nothing(bundle):
    """Every variant outside the regions: the note explains, and names no rarity."""
    genes = [
        {
            "gene": "CYP3A5",
            "pypgx": {
                "supporting_variants": [
                    {"allele": "*3", "variants": [{"variant": "7-99672916-T-C"}]}
                ]
            },
        }
    ]
    text = rarity.note(rarity.annotate_genes(genes, bundle))
    assert "reaches none of them" in text
    assert "coding exons" in text and "promoter or an intron" in text
    # Nothing was reached, so nothing is claimed about rarity either way.
    assert "rare" not in text


# --- through the report, as a run generates it -------------------------------
# These two guard the trap that cost this change an hour: generator.py builds one
# template_data, then **rebinds the name** with a second dict further down, and that
# second one is what renders the HTML and the PDF. A key set only on the first is
# silently dropped -- which is exactly what happened to the note below, and is what
# report_branding_context's docstring warns about. Rendering through
# generate_report is the only way to catch it.

import json
import pathlib

DATA = pathlib.Path(__file__).parent.parent / "test_data"
EXAMPLE_JSON = DATA / "pharmcat.example.v340.report.json"

PYPGX_RESULTS = {
    "results": {
        "CYP2D6": {
            "success": True,
            "diplotype": "*2/*21",
            "details": {
                "haplotype1": ["*2"],
                "haplotype2": ["*21"],
                "supporting_variants": [
                    {
                        "allele": "*21",
                        # Rare in the fixture bundle: 0.0091% in afr.
                        "variants": [
                            {"variant": "22-42128241-CT-C", "allele_fraction": 0.47}
                        ],
                    },
                    {
                        "allele": "*2",
                        # Common: 31% in eas.
                        "variants": [
                            {"variant": "10-94781859-G-A", "allele_fraction": 0.5}
                        ],
                    },
                ],
            },
        }
    }
}


def _rendered(monkeypatch, tmp_path, bundle, interactive=False):
    """The report as a run generates it, against the fixture bundle."""
    pytest.importorskip("weasyprint")
    import app.reports.generator as generator_module

    monkeypatch.setattr(rarity, "DEFAULT_BUNDLE", bundle)
    (tmp_path / "pypgx_result.json").write_text(
        json.dumps(PYPGX_RESULTS), encoding="utf-8"
    )
    wanted = {"write_html", "write_interactive_html"} if interactive else {"write_html"}
    for key in list(generator_module.REPORT_CONFIG):
        if key.startswith(("write_", "show_")):
            monkeypatch.setitem(generator_module.REPORT_CONFIG, key, key in wanted)
    data = json.loads(EXAMPLE_JSON.read_text(encoding="utf-8"))
    gene = data["genes"]["CYP2D6"]
    gene["callSource"] = "OUTSIDE"
    allele = dict(gene["sourceDiplotypes"][0]["allele1"])
    pair = dict(gene["sourceDiplotypes"][0])
    pair["allele1"] = {**allele, "name": "*2"}
    pair["allele2"] = {**allele, "name": "*21"}
    pair["phenotypes"] = ["Intermediate Metabolizer"]
    pair["activityScore"] = "1.0"
    pair["label"] = "*2/*21"
    gene["sourceDiplotypes"] = [pair]
    gene["recommendationDiplotypes"] = [pair]
    result = generator_module.generate_report(
        {"data": data}, str(tmp_path), {"id": "p1"}, job_id=None
    )
    name = result["html_path"].split("/")[-1]
    if interactive:
        name = name.replace(".html", "_interactive.html")
    return (tmp_path / name).read_text(encoding="utf-8")


def test_the_note_reaches_the_printed_report(monkeypatch, tmp_path, bundle):
    """The PDF lane renders from the rebound dict; the note must be listed there."""
    page = _rendered(monkeypatch, tmp_path, bundle)
    assert "variants PyPGx reports behind the alleles it called" in page
    # The rare one is named, the common one is not.
    assert "CYP2D6 *21" in page
    assert "fewer than" in page and "1 person in 100" in page


def test_the_frequency_column_reaches_the_interactive_report(
    monkeypatch, tmp_path, bundle
):
    page = _rendered(monkeypatch, tmp_path, bundle, interactive=True)
    assert "<th>Population frequency</th>" in page
    assert "31% (eas)" in page
    assert "under 0.01% (afr)" in page


def test_a_position_covered_by_an_earlier_interval_is_still_covered(tmp_path):
    """Overlapping and abutting intervals are merged, so one bisect is exact.

    Latent, not live: the bundle built 2026-10-25 has 654 intervals and none of them
    overlaps or abuts its neighbour, so every one of the 301 distinct positions behind
    PyPGx's calls is answered the same either way. It is guarded because the answer
    must not depend on that -- the builder's flank is a parameter, and against a raw
    sorted list the interval containing a position can sit any number of entries back,
    where a fixed look-back answers "not covered" for a position that is. That would
    turn "gnomAD has not seen it" into "no frequency data", or the reverse.
    """
    with gzip.open(tmp_path / "gnomad.tsv.gz", "wt") as f:
        f.write(GNOMAD_HEADER)
    # One long interval, then many short ones that start after it: a position inside
    # the first sits far behind the bisect's landing point.
    lines = ["chr1\t1000\t9000\n"]
    lines += [f"chr1\t{2000 + 10 * i}\t{2005 + 10 * i}\n" for i in range(40)]
    (tmp_path / "regions.bed").write_text("".join(lines))
    regions = rarity.Regions(tmp_path / "regions.bed")
    assert regions.covers("chr1", 8999)
    assert regions.covers("chr1", 2400)
    assert not regions.covers("chr1", 9001)
    assert not regions.covers("chr1", 999)
    # And through the public path: inside the regions, so absence means gnomAD's.
    assert rarity.lookup(["1-8999-G-A"], tmp_path)["1-8999-G-A"] == "Not in gnomAD"
    assert rarity.lookup(["1-9500-G-A"], tmp_path)["1-9500-G-A"] == rarity.NO_DATA


def test_the_note_blames_the_exons_only_where_they_are_to_blame(bundle):
    """CYP3A5 *3's variant is intronic and outside this fixture's exons.

    With a version 2 bundle that is the whole explanation and the paragraph gives it.
    With version 3 the table reaches every position either caller can name, so a
    variant it still misses is neither an exon nor a named position, and naming the
    exons would be a guess. Over the 61 GeT-RM genomes version 3 misses none.
    """
    text = rarity.note(rarity.annotate_genes(_genes(), bundle))
    assert "the rest sit outside the regions it covers" in text
    assert "coding exons and their splice sites" in text

    (bundle / "gnomad_regions.bed").write_text((bundle / "regions.bed").read_text())
    wider = rarity.note(rarity.annotate_genes(_genes(), bundle))
    assert "the rest sit outside the regions it covers" in wider
    assert "coding exons" not in wider
    assert "promoter" not in wider and "intron" not in wider


def test_a_run_that_reaches_nothing_says_so_either_way(bundle):
    """The same choice where no variant has a number at all."""
    genes = [_genes()[1]]
    assert "which cover the pharmacogenes' coding exons" in rarity.note(
        rarity.annotate_genes(genes, bundle)
    )
    (bundle / "gnomad_regions.bed").write_text((bundle / "regions.bed").read_text())
    text = rarity.note(rarity.annotate_genes([_genes()[1]], bundle))
    assert "whose regions do not reach them." in text
    assert "coding exons" not in text


def test_the_table_s_own_regions_are_what_wider_asks_about(bundle):
    assert rarity.wider(bundle) is False
    (bundle / rarity.GNOMAD_REGIONS_FILE).write_text("chr1\t0\t1\n")
    assert rarity.wider(bundle) is True
    assert rarity.annotate_genes(_genes(), bundle)["wider"] is True


def test_the_note_does_not_claim_the_genes_pharmcat_called(bundle):
    """The paragraph counts PyPGx's variants, and says so.

    The evidence table is PyPGx's, and the genes PharmCAT calls itself -- 19 of 23
    records on a real run -- name no variants anywhere in the report. Wording this as
    "the alleles called here" claimed a coverage the paragraph does not have.
    """
    text = rarity.note(rarity.annotate_genes(_genes(), bundle))
    assert "PyPGx reports behind the alleles it called" in text
    assert "The genes PharmCAT called itself name no variants" in text
    # Never the unscoped phrasing.
    assert "the alleles called here" not in text
