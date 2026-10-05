"""tests/e2e/report_links.py fetches what a job advertises, and only its links."""

from __future__ import annotations

import types

import pytest

from tests.e2e.report_links import assert_report_links_resolve, report_links

REPORTS = {
    "pdf_path": "/reports/p/j/j_pgx_report.pdf",
    "html_path": "/reports/p/j/j_pgx_report.html",
    "job_directory": "/data/reports/p/j",
    "pharmcat_run_id": "p",
    "is_provisional": False,
    "processed_data": {"genes": []},
}


def test_only_served_links_are_checked():
    assert report_links(REPORTS) == [
        "/reports/p/j/j_pgx_report.html",
        "/reports/p/j/j_pgx_report.pdf",
    ]


class _Client:
    def __init__(self, answers):
        self.answers, self.fetched = answers, []

    def get(self, link):
        self.fetched.append(link)
        status, body = self.answers.get(link, (200, b"ok"))
        return types.SimpleNamespace(status_code=status, content=body)


def test_resolving_links_pass():
    client = _Client({})
    assert_report_links_resolve(client, REPORTS)
    assert client.fetched == report_links(REPORTS)


@pytest.mark.parametrize("answer", [(404, b"missing"), (200, b"")])
def test_a_missing_or_empty_report_fails(answer):
    client = _Client({"/reports/p/j/j_pgx_report.pdf": answer})
    with pytest.raises(AssertionError, match="j_pgx_report.pdf"):
        assert_report_links_resolve(client, REPORTS)


def test_a_job_with_no_links_fails():
    with pytest.raises(AssertionError, match="no /reports/ links"):
        assert_report_links_resolve(_Client({}), {"job_directory": "/data/x"})
