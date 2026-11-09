"""The documentation is reprinted when it is out of date, not only when it is absent.

The old check was ``if not index.exists()``, which asks whether SOME build is there.
A build from an older release satisfies it forever, so any install that had ever been
started kept serving the pages it printed the first time.

That is what broke https://pgx.zaromics.com/docs/user/credits.html: the home page links
to the Credits page, the production install's build predated that page, and `/docs`
itself answered 200 from the stale copy, so nothing looked wrong from the outside.
"""

import time
from pathlib import Path

import pytest

from app import main


@pytest.fixture
def docs(tmp_path, monkeypatch):
    """A documentation source tree and an empty build directory."""
    src = tmp_path / "docs"
    (src / "user").mkdir(parents=True)
    (src / "index.rst").write_text("index", encoding="utf-8")
    (src / "user" / "credits.md").write_text("credits", encoding="utf-8")
    build = tmp_path / "docs" / "_build" / "html"
    monkeypatch.setattr(main, "DOCS_SOURCE_DIR", src)
    monkeypatch.setattr(main, "DOCS_BUILD_DIR", build)
    monkeypatch.setattr(main, "DOCS_STAMP", build / ".zaropgx-docs-build")
    return src, build


def _printed(build: Path, version: str, *, age: float = 0.0) -> None:
    """Pretend a build of `version` happened, optionally `age` seconds ago."""
    build.mkdir(parents=True, exist_ok=True)
    (build / "index.html").write_text("<html></html>", encoding="utf-8")
    (build / ".zaropgx-docs-build").write_text(version, encoding="utf-8")
    if age:
        when = time.time() - age
        import os

        os.utime(build / "index.html", (when, when))


def test_nothing_printed_yet_is_a_reason(docs):
    _, build = docs

    assert main._docs_build_reason() == "no documentation has been printed yet"


def test_a_build_from_an_older_release_is_reprinted(docs):
    """The production bug: a stale build used to satisfy the check forever."""
    _, build = docs
    _printed(build, "0.3.2")

    reason = main._docs_build_reason()

    assert reason is not None
    assert "0.3.2" in reason


def test_a_build_of_this_release_is_left_alone(docs):
    _, build = docs
    _printed(build, main.APP_VERSION)

    assert main._docs_build_reason() is None


def test_a_build_with_no_stamp_is_reprinted(docs):
    """Every install upgrading to this release has an unstamped build."""
    _, build = docs
    build.mkdir(parents=True, exist_ok=True)
    (build / "index.html").write_text("<html></html>", encoding="utf-8")

    reason = main._docs_build_reason()

    assert reason is not None
    assert "which release" in reason


def test_a_page_edited_since_the_build_is_reprinted(docs):
    """A doc change between releases, where the version alone says nothing."""
    src, build = docs
    _printed(build, main.APP_VERSION, age=60)
    (src / "user" / "credits.md").write_text("edited", encoding="utf-8")

    reason = main._docs_build_reason()

    assert reason == "a documentation source has changed since it was printed"


def test_the_build_directory_is_not_searched_for_changes(docs):
    """Sphinx's own output is newer than index.html by construction; counting it
    would reprint the documentation on every single start.

    The sources are aged behind the build here, so the only thing newer than
    index.html is Sphinx's own output."""
    import os

    src, build = docs
    old = time.time() - 300
    for path in src.rglob("*"):
        if path.is_file():
            os.utime(path, (old, old))
    _printed(build, main.APP_VERSION, age=60)
    (build / "user").mkdir(parents=True, exist_ok=True)
    (build / "user" / "credits.html").write_text("fresh output", encoding="utf-8")

    assert main._docs_build_reason() is None


def test_the_version_is_one_constant_the_release_bump_moves():
    """FastAPI's version and the docs stamp must not drift apart."""
    assert main.app.version == main.APP_VERSION
