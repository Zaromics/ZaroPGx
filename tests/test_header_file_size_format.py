"""The header view must state a file size the reader can act on.

The size row divided by 1024 twice and appended " MB", unconditionally. Uploads
here span six orders of magnitude -- ``test_data/grch37_pgx_snps.vcf`` is 1150
bytes, a WGS BAM is ~100 GB -- so everything below about 5 MB rendered as
"0.00 MB". Reported against the shipped sample: the header inspector had read the
file correctly and the UI threw the number away in formatting.

Two behaviours are easy to get wrong and are pinned here:

* ``0`` is a real answer, not a missing one. The old code branched on
  truthiness, so a genuinely empty file claimed "Unknown" -- which reads as "we
  could not tell" when in fact we could.
* precision has to follow the unit. Rounding to a fixed 2dp makes 100 MB and
  100.00 MB equally noisy while still collapsing small values; roughly three
  significant figures keeps 1.12 KB and 11.2 KB apart.

The function is executed in Node against the real template text rather than
reimplemented here, the same approach as tests/test_ui_workflow_flag_reads.py --
a copy of the logic in the test would pass while the page stayed broken. Skips
when Node is absent; CI installs Node 22 so it cannot skip there.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

INDEX_HTML = Path(__file__).resolve().parents[1] / "app" / "templates" / "index.html"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not installed"
)


def _formatter_source() -> str:
    html = INDEX_HTML.read_text(encoding="utf-8")
    match = re.search(
        r"window\.formatFileSize = function\(bytes\) \{.*?\n            \};", html, re.S
    )
    assert match is not None, "window.formatFileSize is gone from index.html"
    return match.group(0)


def _format(values: list) -> list[str]:
    """Run the page's own formatter over `values` in Node."""
    script = (
        "const window = {};\n"
        + _formatter_source()
        + "\nconst input = JSON.parse(process.argv[1]);"
        + "\nconsole.log(JSON.stringify(input.map((v) => window.formatFileSize(v))));"
    )
    proc = subprocess.run(
        ["node", "-e", script, json.dumps(values)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_the_shipped_sample_is_not_reported_as_zero():
    """1150 bytes -- the exact file that surfaced this."""
    size = (
        (Path(__file__).resolve().parents[1] / "test_data" / "grch37_pgx_snps.vcf")
        .stat()
        .st_size
    )
    (rendered,) = _format([size])

    assert "0.00" not in rendered, rendered
    assert rendered.endswith(" KB"), rendered


def test_the_unit_tracks_the_magnitude():
    values = [512, 1024, 56983, 1048576, 10485760, 1073741824, 1099511627776]
    rendered = _format(values)

    assert [r.split()[1] for r in rendered] == [
        "B",
        "KB",
        "KB",
        "MB",
        "MB",
        "GB",
        "TB",
    ], rendered


def test_bytes_are_exact_and_never_given_false_decimals():
    assert _format([0, 1, 999, 1023]) == ["0 B", "1 B", "999 B", "1023 B"]


def test_zero_is_a_size_not_an_unknown():
    """The old truthiness check reported an empty file as "Unknown"."""
    (rendered,) = _format([0])
    assert rendered == "0 B"


def test_precision_follows_the_unit():
    """Roughly three significant figures, so neighbouring sizes stay distinct."""
    assert _format([1150, 11500, 115000]) == ["1.12 KB", "11.2 KB", "112 KB"]


@pytest.mark.parametrize("junk", [None, "", "abc", -5, {}])
def test_unusable_input_says_unknown_rather_than_rendering_nonsense(junk):
    """The shapes `file_size` can actually arrive as: absent, blank, or garbage.

    Deliberately not `true` or `[]`. Both coerce to a number in JS (1 and 0), so
    they render as sizes rather than "Unknown" -- but neither can come out of the
    header inspector, and hardening against them would add a branch that only
    ever exists to satisfy a test.
    """
    (rendered,) = _format([junk])
    assert rendered == "Unknown", rendered


def test_nan_says_unknown_too():
    """Evaluated directly: NaN has no JSON representation to pass through."""
    script = (
        "const window = {};\n"
        + _formatter_source()
        + "\nconsole.log(window.formatFileSize(NaN));"
    )
    proc = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "Unknown"


def test_the_header_row_actually_calls_the_formatter():
    """A helper nothing uses would leave the page exactly as broken."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    row = re.search(
        r"File Size:</strong></div>\s*<div class=\"col-sm-8\">(.*?)</div>", html, re.S
    )
    assert row is not None, "the File Size row is gone"
    assert "formatFileSize" in row.group(1), row.group(1)
    assert "1024 / 1024" not in row.group(1), "the fixed MB divisor is back"
