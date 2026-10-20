"""native_report_paths: links to the reports the pipeline publishes itself.

generate_report adds them to the reports it builds; a job with the custom report
turned off gets them alone. Only a file that exists gets a link, and an mtDNA file
only when it holds a result.
"""

import gzip
import json
import zlib

import pytest

import app.reports.generator as generator
from app.reports.generator import (
    NATIVE_REPORT_URL_KEYS,
    mtdna_report_paths,
    native_report_paths,
)
from tests.mtdna_outputs import (
    NA12878_VCF,
    write_na12878_run,
    write_no_chrm_vcf_run,
    write_vcf_gz,
)

PREFIX = "/reports/patient/job"


@pytest.fixture
def all_pharmcat_reports(monkeypatch):
    for flag in ("html", "json", "tsv"):
        monkeypatch.setitem(
            generator.REPORT_CONFIG, f"show_pharmcat_{flag}_report", True
        )


def test_links_each_published_file_and_nothing_else(tmp_path, all_pharmcat_reports):
    for name in ("job_pgx_pharmcat.html", "job_pgx_pharmcat.tsv"):
        (tmp_path / name).write_text("x")
    write_vcf_gz(tmp_path / "mtdna_variants.vcf.gz", NA12878_VCF)

    assert native_report_paths(str(tmp_path), "job", PREFIX) == {
        "pharmcat_html_path": f"{PREFIX}/job_pgx_pharmcat.html",
        "pharmcat_tsv_path": f"{PREFIX}/job_pgx_pharmcat.tsv",
        "mtdna_vcf_path": f"{PREFIX}/mtdna_variants.vcf.gz",
    }


def test_pharmcat_raw_outputs_are_copied_under_the_served_names(
    tmp_path, all_pharmcat_reports
):
    (tmp_path / "job.report.json").write_text("{}")
    (tmp_path / "job.report.tsv").write_text("t")

    paths = native_report_paths(str(tmp_path), "job", PREFIX)

    assert (tmp_path / "job_pgx_pharmcat.json").read_text() == "{}"
    assert (tmp_path / "job_pgx_pharmcat.tsv").read_text() == "t"
    assert set(paths) == {"pharmcat_json_path", "pharmcat_tsv_path"}


def test_a_pharmcat_report_turned_off_is_not_linked(tmp_path, monkeypatch):
    monkeypatch.setitem(generator.REPORT_CONFIG, "show_pharmcat_html_report", False)
    (tmp_path / "job_pgx_pharmcat.html").write_text("x")

    assert "pharmcat_html_path" not in native_report_paths(str(tmp_path), "job", PREFIX)


def test_every_key_has_the_url_key_the_page_reads():
    assert set(NATIVE_REPORT_URL_KEYS.values()) == {
        "pharmcat_html_report_url",
        "pharmcat_json_report_url",
        "pharmcat_tsv_report_url",
        "mtdna_report_url",
        "mtdna_haplogroups_url",
        "mtdna_vcf_url",
    }


MTDNA_KEYS = {"mtdna_report_path", "mtdna_haplogroups_path", "mtdna_vcf_path"}


def test_a_file_without_chrm_data_gets_no_mtdna_links(tmp_path):
    """PharmCAT's example VCF has no chrM record. Its run left a no-call
    mtdna_result.json and a chrM VCF with a header only, and the page offered both
    as "View Haplogroup Report" and "Download chrM VCF"."""
    write_no_chrm_vcf_run(tmp_path)

    assert mtdna_report_paths(str(tmp_path), PREFIX) == {}
    assert not MTDNA_KEYS & set(native_report_paths(str(tmp_path), "job", PREFIX))


def test_a_run_with_chrm_data_gets_all_three_links(tmp_path):
    write_na12878_run(tmp_path)

    assert native_report_paths(str(tmp_path), "job", PREFIX) == {
        "mtdna_report_path": f"{PREFIX}/mtdna_report.html",
        "mtdna_haplogroups_path": f"{PREFIX}/mtdna_result.json",
        "mtdna_vcf_path": f"{PREFIX}/mtdna_variants.vcf.gz",
    }


@pytest.mark.parametrize(
    "result",
    [
        # An alignment whose header names no mitochondrion (the sidecar's
        # no_mito_contig answer), and one with no reads on it (0x): no VCF either.
        {
            "haplogroup": None,
            "mt_rnr1": None,
            "mt_rnr1_no_call_reason": "no_mito_contig",
            "variants": [],
            "chrm_vcf": None,
        },
        {
            "haplogroup": None,
            "mt_rnr1": None,
            "mt_rnr1_no_call_reason": "coverage_below_floor",
            "mean_coverage": 0.0,
            "variants": [],
            "chrm_vcf": None,
        },
    ],
)
def test_an_alignment_without_mitochondrial_reads_gets_no_mtdna_links(tmp_path, result):
    (tmp_path / "mtdna_result.json").write_text(json.dumps(result))

    assert mtdna_report_paths(str(tmp_path), PREFIX) == {}


def test_a_haplogroup_alone_is_a_result(tmp_path):
    """MT-RNR1 can stay a no-call (absent_to_ref not set) where the haplogroup is
    called: the result is still worth opening."""
    (tmp_path / "mtdna_result.json").write_text(
        json.dumps(
            {
                "haplogroup": "H13a1a1a",
                "mt_rnr1": None,
                "mt_rnr1_no_call_reason": "absent_to_ref_not_set",
                "variants": [],
            }
        )
    )

    assert mtdna_report_paths(str(tmp_path), PREFIX) == {
        "mtdna_haplogroups_path": f"{PREFIX}/mtdna_result.json"
    }


def test_empty_or_unreadable_mtdna_files_are_not_linked(tmp_path):
    (tmp_path / "mtdna_report.html").write_text("")
    (tmp_path / "mtdna_result.json").write_text("not json")
    (tmp_path / "mtdna_variants.vcf.gz").write_text("not gzip")

    assert mtdna_report_paths(str(tmp_path), PREFIX) == {}


def test_a_corrupt_mtdna_vcf_is_not_linked_and_fails_nothing(tmp_path):
    """Corrupt deflate data raises zlib.error, which is not an OSError: it used to
    propagate and fail the job's final stage instead of leaving the link out."""
    data = bytearray(
        gzip.compress(b"##fileformat=VCFv4.2\n" + b"chrM\t1\t.\tG\tA\n" * 500)
    )
    data[20:40] = b"\xff" * 20
    (tmp_path / "mtdna_variants.vcf.gz").write_bytes(bytes(data))
    with pytest.raises(zlib.error):
        with gzip.open(tmp_path / "mtdna_variants.vcf.gz", "rt") as fh:
            fh.read()

    assert mtdna_report_paths(str(tmp_path), PREFIX) == {}
