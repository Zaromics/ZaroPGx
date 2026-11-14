"""The report's paragraph about a consumer array that was read onto PharmCAT's positions.

A chip's report is SHORT, and a short report is indistinguishable from a healthy one
unless the denominator is on the page. So this paragraph carries the one number that
frames everything else, plus the two things the reader cannot infer from the results:
that the match was by rs identifier rather than by their file's coordinates, and what
could not be carried.

Shaped after tests/test_gvcf_report_provenance.py, and the counts below are the ones the
conversion actually reported on the measured chip file.
"""

import re

import pytest

from app.utils.array_provenance import array_provenance_paragraph

# What /array-to-vcf returned for the measured 8-row chip export, read against the real
# pharmcat_positions.vcf: 1,018 of PharmCAT's 1,226 positions carry an rs identifier.
MEASURED = {
    "vendor_shape": "23andme",
    "n_rows_read": 8,
    "n_genotypes_usable": 8,
    "n_no_calls": 0,
    "n_indel_coded": 0,
    "n_malformed": 0,
    "n_off_panel": 0,
    "n_unmappable": 0,
    "unmappable_rsids": [],
    "n_pharmcat_positions_rsid": 1018,
    "n_positions_carried": 8,
}

TEMPLATES = ("report_template.html", "interactive_report.html")


def _text(markup):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", markup))


def _render(template_name, **overrides):
    from app.reports.generator import env

    context = {
        "diplotypes": [],
        "recommendations": [],
        "gene_drug_recommendations": [],
        "organized_recommendations": [],
        "patient_id": "test-patient",
        "report_id": "test-report",
        "report_date": "2026-11-12",
        "organization": "ZaroPGx",
        "disclaimer": "",
        "genome_build": "GRCh38.p14",
        "named_allele_matcher_version": "2.0.0",
        "pharmcat_data_version": "2025-11-05-00-25",
    }
    context.update(overrides)
    return env.get_template(template_name).render(**context)


# --------------------------------------------------------------------------
# The paragraph
# --------------------------------------------------------------------------


@pytest.mark.parametrize("empty", [None, {}])
def test_no_paragraph_when_no_array_was_converted(empty):
    """Every other input: no array_to_vcf step row, so nothing to say."""
    assert array_provenance_paragraph(empty) is None


def test_the_paragraph_carries_the_coverage_and_its_denominator():
    """The number that frames the whole report. Without the denominator a reader cannot
    tell a short report from a complete one."""
    text = _text(array_provenance_paragraph(MEASURED))

    assert "8 of the 1,018 positions" in text
    assert "rs identifier" in text


def test_the_paragraph_says_the_match_is_not_by_coordinate():
    """The uncommon choice, and the reason no liftover appears anywhere in the report.
    A reader who knows a chip's coordinates are build 37 will look for one."""
    text = _text(array_provenance_paragraph(MEASURED))

    assert "no liftover is involved" in text
    assert "build 37" in text
    # And the concrete loss it avoids, named: this is the measurement that settled the
    # design, not a general caution.
    assert "rs6025" in text
    assert "Factor V Leiden" in text


def test_the_paragraph_says_pypgx_did_not_run_and_why():
    text = _text(array_provenance_paragraph(MEASURED))

    assert "PyPGx was not run" in text
    assert "an array has no reads" in text


def test_the_vendor_is_named_as_the_reader_names_it():
    assert "23andMe export" in array_provenance_paragraph(MEASURED)
    assert "AncestryDNA export" in array_provenance_paragraph(
        dict(MEASURED, vendor_shape="ancestrydna")
    )


def test_an_unknown_shape_does_not_invent_a_vendor():
    """The conversion decides the shape from the file; an unrecognized value must not
    become a confident vendor name in a clinical report."""
    text = array_provenance_paragraph(dict(MEASURED, vendor_shape="acme-chip"))

    assert "genotyping array export" in text
    assert "acme" not in text.lower()


# --------------------------------------------------------------------------
# What could not be carried, counted apart because the kinds differ
# --------------------------------------------------------------------------


def test_nothing_dropped_says_nothing_about_drops():
    """A standing "0 dropped" line is noise on the common case."""
    text = _text(array_provenance_paragraph(MEASURED))

    assert "Not carried" not in text


def test_a_no_call_is_reported_as_a_failed_read_not_a_missing_position():
    text = _text(array_provenance_paragraph(dict(MEASURED, n_no_calls=1200)))

    assert "1,200 position(s) the chip reported as a no-call" in text
    assert "tried to read and could not" in text


def test_the_indel_coding_is_explained_rather_than_counted_silently():
    """`I`/`D` names no sequence, so those rows are unrepresentable rather than merely
    absent -- and the UGT1A1*28 TA repeat is one of them, which a reader looking for
    that gene needs to know."""
    text = _text(array_provenance_paragraph(dict(MEASURED, n_indel_coded=42)))

    assert "42 row(s) written with the insertion/deletion coding" in text
    assert "UGT1A1*28" in text


def test_unmappable_bases_are_named_because_they_indicate_a_systematic_error():
    """A base matching neither allele is a strand or annotation mismatch, not a
    genotype. Named, because a handful of them is a different problem from thousands."""
    text = _text(
        array_provenance_paragraph(
            dict(
                MEASURED,
                n_unmappable=3,
                unmappable_rsids=["rs1065852", "rs16947", "rs3892097"],
            )
        )
    )

    assert "3 position(s) whose reported bases match neither allele" in text
    assert "rs1065852" in text
    assert "strand or annotation mismatch" in text


def test_the_named_unmappable_list_is_bounded():
    """Thousands of strand mismatches must not become a thousand-rsID paragraph."""
    many = [f"rs{n}" for n in range(1, 40)]
    text = _text(
        array_provenance_paragraph(
            dict(MEASURED, n_unmappable=len(many), unmappable_rsids=many)
        )
    )

    assert "39 position(s)" in text
    assert text.count("rs") < 15, "the list is capped, the count is not"


def test_unusable_counts_drop_the_numbers_but_keep_the_method():
    """How the file was matched is what the reader cannot infer, and it does not depend
    on the counts."""
    text = _text(array_provenance_paragraph({"vendor_shape": "23andme"}))

    assert "positions" not in text.split("PharmCAT's own GRCh38 positions")[-1][:40]
    assert "no liftover is involved" in text
    assert "PyPGx was not run" in text


@pytest.mark.parametrize(
    "bad", [{"n_positions_carried": "lots"}, {"n_positions_carried": -1}]
)
def test_a_count_that_does_not_parse_is_not_rendered_as_zero(bad):
    """ "0 positions carried" and "we do not know" are different claims."""
    text = _text(array_provenance_paragraph(dict(MEASURED, **bad)))

    assert "0 of the" not in text


# --------------------------------------------------------------------------
# The rendered page
# --------------------------------------------------------------------------


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_the_paragraph_reaches_both_report_lanes(template_name):
    text = _text(
        _render(template_name, array_provenance=array_provenance_paragraph(MEASURED))
    )

    assert "Genotyping array:" in text
    assert "8 of the 1,018 positions" in text


@pytest.mark.parametrize("template_name", TEMPLATES)
def test_an_ordinary_run_says_nothing_about_an_array(template_name):
    """Negative control: no invented paragraph when no array was converted."""
    text = _text(_render(template_name, array_provenance=None))

    assert "Genotyping array:" not in text
    assert "rs identifier" not in text
