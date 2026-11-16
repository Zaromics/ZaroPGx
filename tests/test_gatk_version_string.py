"""GATK's version field holds a version, not its banner with a hole in it.

`gatk --version` prints "The Genome Analysis Toolkit (GATK) v4.7.0.0". The manifest
writer removed the substring "GATK" from that sentence, which left

    The Genome Analysis Toolkit () v4.7.0.0

empty parentheses and all. That string is what `data/versions/gatk.json` held on the
live install, what the report's Platform and Citations table printed as GATK's version,
and what its bwa row printed after the words "in GATK":

    in GATK The Genome Analysis Toolkit () v4.7.0.0

Measured on the running 0.4.3 stack, whose GATK container reports the banner correctly.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

MODULE = (
    pathlib.Path(__file__).resolve().parents[1] / "docker" / "gatk-api" / "gatk_api.py"
)


def _version_from_banner(line):
    """The helper, lifted out of the module without importing it.

    gatk_api imports FastAPI, pysam and a job client; this test is about one function,
    and the file is read the same way tests/test_verify_image_contents.py reads its
    script.
    """
    import re

    source = MODULE.read_text(encoding="utf-8")
    start = source.index("    def _version_from_banner(line: str) -> str:")
    end = source.index("\n    with open(os.path.join(versions_dir", start)
    body = "\n".join(
        l[4:] if l.startswith("    ") else l for l in source[start:end].splitlines()
    )
    namespace = {"re": re}
    exec(compile(body, str(MODULE), "exec"), namespace)  # noqa: S102
    return namespace["_version_from_banner"](line)


@pytest.mark.parametrize(
    "banner,expected",
    [
        ("The Genome Analysis Toolkit (GATK) v4.7.0.0", "4.7.0.0"),
        ("The Genome Analysis Toolkit (GATK) v4.6.1.0", "4.6.1.0"),
        ("GATK v4.5.0.0", "4.5.0.0"),
        ("4.7.0.0", "4.7.0.0"),
    ],
)
def test_it_takes_the_version_out_of_the_banner(banner, expected):
    assert _version_from_banner(banner) == expected


def test_the_old_behaviour_is_what_this_replaces():
    """The bug, stated as a test so it cannot come back: removing the substring is not
    the same as taking the version."""
    banner = "The Genome Analysis Toolkit (GATK) v4.7.0.0"

    assert (
        banner.replace("GATK", "").strip() == "The Genome Analysis Toolkit () v4.7.0.0"
    )
    assert _version_from_banner(banner) != banner.replace("GATK", "").strip()


@pytest.mark.parametrize("banner", ["", "   ", "something with no numbers"])
def test_an_unrecognised_banner_keeps_what_it_has(banner):
    """Better evidence of what ran than "unknown", and never an empty field."""
    result = _version_from_banner(banner)

    assert result == (banner.strip() or "unknown")
