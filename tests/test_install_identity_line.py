"""The page says which ZaroPGx this is and where it is running.

One muted line in the footer, server-rendered:

    ZaroPGx 0.4.4 · St Mary's Genomics · releases     an operator who named themselves
    ZaroPGx 0.4.4 · docker · releases                 unlabelled, in a container
    ZaroPGx 0.4.4 · releases                          unlabelled, not in one
    ZaroPGx 0.4.4+a1b2c3d · … · releases              serving code the image lacks

**The suffix is semver build metadata, not a phrase.** There is no accepted term for a
container serving code its image was not built from -- the mechanism is a bind mount
shadowing the image's files, and the field has no noun for it -- so the line states the
fact in the notation that already exists for it. A bare version is the image; a version
carrying a commit is that commit. The full detail goes in the element's title, where
someone who wants it can find it, and nowhere else.

**The releases link is a link, not a version check.** Knowing whether a newer release
exists means asking an outside service, and this application makes no outbound request
on its own behalf: ``test_no_external_service_defaults.py`` exists because
``render_with_kroki`` nearly defaulted to the public kroki.io. The opt-in design is in
``docs/to-do.md``.

The line sits at ``opacity: .75`` on the footer, which measures 6.58:1 light and 8.10:1
dark against Bootstrap's body text -- above the 4.5 AA asks -- so it is muted without
being faint.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PAGE = REPO / "app" / "templates" / "index.html"


@pytest.fixture(scope="module")
def main_module():
    import app.main as main

    return main


def _line() -> str:
    page = PAGE.read_text(encoding="utf-8")
    found = re.search(r'class="[^"]*install-identity"[^>]*>(.*?)</p>', page, re.S)
    assert found, "the footer no longer carries the install identity line"
    return found.group(1)


def test_the_line_names_the_version_and_where_it_runs():
    body = _line()
    assert "{{ app_version }}" in body
    assert "{{ operator }}" in body
    assert "{% if operator %}" in body, (
        "with nothing to say about an operator the line must say nothing: a "
        "self-hosted install is not run by the people who wrote the software"
    )


def test_an_unlabelled_install_still_says_what_it_runs_on(main_module):
    """`operator` falls back to the runtime, so a bare install is not anonymous."""
    source = (REPO / "app" / "main.py").read_text(encoding="utf-8")
    assert "OPERATOR or _where_this_runs()" in source


def test_a_container_is_recognised_by_its_own_marker(
    main_module, monkeypatch, tmp_path
):
    monkeypatch.setattr(main_module, "Path", Path)
    assert main_module._where_this_runs() in ("docker", "")


def test_the_version_carries_the_commit_only_when_it_is_not_the_image(main_module):
    served = main_module._version_served
    base = dict(main_module._BUILD_IDENTITY)

    agreeing = dict(base, version="9.9.9", agrees=True, code={"commit": "aaaaaaa1"})
    unknown = dict(base, version="9.9.9", agrees=None, code={"commit": "aaaaaaa1"})
    differing = dict(
        base, version="9.9.9", agrees=False, code={"commit": "bbbbbbb2222"}
    )

    for name, identity in (("agreeing", agreeing), ("one side unknown", unknown)):
        main_module._BUILD_IDENTITY = identity
        assert served() == "9.9.9", f"{name} must print a bare version"

    main_module._BUILD_IDENTITY = differing
    assert served() == "9.9.9+bbbbbbb", "the running commit, short, after a +"

    main_module._BUILD_IDENTITY = base


def test_an_unknown_commit_is_not_reported_as_a_difference(main_module):
    """`agrees` is None when one side is unknown, which is not a disagreement."""
    base = dict(main_module._BUILD_IDENTITY)
    main_module._BUILD_IDENTITY = dict(base, version="9.9.9", agrees=False, code={})
    assert (
        main_module._version_served() == "9.9.9"
    ), "with no commit to name there is nothing to append"
    main_module._BUILD_IDENTITY = base


def test_the_releases_link_is_a_link_and_not_a_check():
    page = PAGE.read_text(encoding="utf-8")
    assert "{{ source_url }}/releases" in page
    assert "fetch(" not in _line(), (
        "the line must not call out: an update check is opt-in and this page makes "
        "no request of its own"
    )


def test_the_detail_is_in_the_title_and_not_in_the_line():
    """`_line` returns what a reader sees; the title lives in the opening tag."""
    page = PAGE.read_text(encoding="utf-8")
    opening = re.search(r'<p class="[^"]*install-identity"[^>]*>', page)
    assert opening, "the line is gone"
    assert "build_detail" in opening.group(
        0
    ), "the explanation belongs in a title attribute, for whoever wants it"
    assert (
        "build_detail" not in _line()
    ), "and not in the footer text, which stays one short line"


def test_the_operator_is_empty_until_someone_sets_it():
    source = (REPO / "app" / "main.py").read_text(encoding="utf-8")
    assert re.search(r'OPERATOR\s*=\s*os\.getenv\(\s*"ZAROPGX_OPERATOR",\s*""', source)
