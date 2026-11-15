"""The version the API reports and the version the report cites are one number.

They are declared independently. ``app/main.py``'s ``APP_VERSION`` is what the API
serves -- ``/api-status``, the OpenAPI document, and ``build_identity``, whose startup
warning compares it against the image's own tag. The report's provenance comes from
somewhere else: ``generator.get_zaropgx_version()`` reads ``pyproject.toml``'s
``version`` unless ``ZAROPGX_VERSION`` overrides it, and ``credits.py`` prints that
beside the tools that produced the result.

So a release that bumps one and not the other leaves the report citing one version of
this software and the API stating another, with nothing to say which produced the file
in front of you. Measured before this test existed: setting ``APP_VERSION`` to a number
the package does not have passes **412** of the version, credits, identity, bootstrap
and citation tests without a murmur.

The other links are already pinned, which is why only this one needed writing:
``test_bootstrap_contract.py`` ties both bootstrap scripts and the ``.env`` profiles to
one release constant, ``test_compose_contract.py`` ties those profiles to compose's
default tag, and ``test_credits_docs.py`` ties ``CITATION.cff`` to ``pyproject``.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: ``APP_VERSION = "0.4.3"`` in app/main.py, read as text so that importing the app --
#: which wants a database -- is not the price of checking a string.
APP_VERSION = re.compile(r'^APP_VERSION\s*=\s*"([^"]+)"', re.M)


def _packaged() -> str:
    with (REPO / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def _served() -> str:
    text = (REPO / "app" / "main.py").read_text(encoding="utf-8")
    match = APP_VERSION.search(text)
    assert match, "app/main.py no longer declares APP_VERSION as a plain string"
    return match.group(1)


def test_the_api_serves_the_version_the_package_has():
    served, packaged = _served(), _packaged()
    assert served == packaged, (
        f"the API reports {served} and the package is {packaged}, so the report "
        "cites one version of this software and the API states another"
    )


def test_the_report_takes_its_provenance_from_the_package():
    """Pin where the report's version comes from: the test above compares to it."""
    path = REPO / "app" / "reports" / "generator.py"
    generator = path.read_text(encoding="utf-8")
    assert "_read_version_from_pyproject" in generator, (
        "get_zaropgx_version no longer reads pyproject.toml, so the comparison "
        "above no longer covers what the report prints"
    )
    assert (
        'os.getenv("ZAROPGX_VERSION")' in generator
    ), "the documented override is gone; a run can no longer state its own version"


def test_both_are_a_plain_version_a_tag_can_carry():
    for name, value in (("app/main.py", _served()), ("pyproject.toml", _packaged())):
        assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-.][0-9A-Za-z.]+)?", value), (
            f"{name} declares {value!r}, which a tag of the form v<version> cannot "
            "carry cleanly"
        )
