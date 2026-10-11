"""Every report link a completed job advertises must resolve.

The e2e tests used to stop at "the report endpoint lists some paths". That is how
the 2026-08-29 bug, where every completed job deleted its own reports, passed the
whole suite: the paths were listed and nothing behind them existed. This is step (1)
of the front-end harness plan in docs/to-do.md.

The report payload mixes served links ("/reports/...") with values that are not
links (job_directory is a path inside the container, pharmcat_run_id an id), so only
the "/reports/" values are fetched.
"""

from __future__ import annotations

from typing import Mapping


def report_links(reports: Mapping) -> list[str]:
    """The served report links in a /reports/job/{id} payload's `reports` dict."""
    return sorted(
        v for v in reports.values() if isinstance(v, str) and v.startswith("/reports/")
    )


def assert_report_links_resolve(client, reports: Mapping) -> None:
    """GET every report link; each must answer 200 with a non-empty body."""
    links = report_links(reports)
    assert links, f"completed job lists no /reports/ links: {sorted(reports)}"
    broken = []
    for link in links:
        resp = client.get(link)
        if resp.status_code != 200 or not resp.content:
            broken.append(f"{resp.status_code} ({len(resp.content)} bytes) {link}")
    assert not broken, "report links that do not resolve:\n  " + "\n  ".join(broken)
