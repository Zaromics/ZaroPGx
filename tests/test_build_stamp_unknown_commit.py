"""An image that did not record its commit must not be reported as disagreeing.

The sentinel has two spellings -- absent, and the literal word ``unknown`` -- and only
one of them was handled. ``ARG ZAROPGX_COMMIT=unknown`` in the app Dockerfile is the
second: an image built without that build argument carries ``"commit": "unknown"`` in
its stamp, which is a string and therefore truthy, so the comparison ran and returned
"these differ". Every install then showed a version carrying a commit, which is the
page's way of saying *this container is serving code its image was not built from* --
the one claim the mechanism exists to avoid making falsely.

Found in production, on the 0.4.4 install, minutes after the release: the page said
``ZaroPGx 0.4.4+0c72d0e`` on a container whose code was exactly the tagged tree.

**These tests build a real stamp file and call ``identity()``**, rather than handing
``disagreement()`` a dictionary assembled by hand. The hand-made dictionaries passed
throughout, because they never contained the one value the Dockerfile actually writes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.utils import build_identity

REAL = "0c72d0e0da086fce56015a04e3b52addd7c30a2d"


def _stamp(tmp_path: Path, commit: object) -> Path:
    path = tmp_path / "build.json"
    body = {"tag": "0.4.4", "built": "2026-11-16T05:38:58Z"}
    if commit is not None:
        body["commit"] = commit
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


@pytest.fixture
def running_commit(monkeypatch):
    monkeypatch.setattr(build_identity, "commit_of", lambda _root: REAL)
    return REAL


@pytest.mark.parametrize("unrecorded", ["unknown", "UNKNOWN", " unknown ", "", None])
def test_an_unrecorded_commit_is_not_a_disagreement(
    tmp_path, running_commit, unrecorded
):
    """What the Dockerfile writes when no commit is passed to the build."""
    found = build_identity.identity(
        stamp_path=_stamp(tmp_path, unrecorded), version="0.4.4"
    )
    assert found["agrees"] is None, (
        f"{unrecorded!r} means the image did not record a commit, which is not a "
        f"disagreement: {found}"
    )
    assert build_identity.disagreement(found) is None


def test_a_recorded_commit_that_differs_is_still_a_disagreement(
    tmp_path, running_commit
):
    found = build_identity.identity(
        stamp_path=_stamp(tmp_path, "b" * 40), version="0.4.4"
    )
    assert found["agrees"] is False, "a real, different commit must still be caught"
    assert "running the checkout" in (build_identity.disagreement(found) or "")


def test_a_recorded_commit_that_matches_agrees(tmp_path, running_commit):
    found = build_identity.identity(stamp_path=_stamp(tmp_path, REAL), version="0.4.4")
    assert found["agrees"] is True
    assert build_identity.disagreement(found) is None


def test_a_short_commit_still_matches_its_full_form(tmp_path, running_commit):
    """The stamp may carry an abbreviated commit; a prefix match is a match."""
    found = build_identity.identity(stamp_path=_stamp(tmp_path, REAL[:12]), version="x")
    assert found["agrees"] is True


def test_the_image_commit_is_still_reported_as_unknown(tmp_path, running_commit):
    """Treating it as unknown must not make it disappear from the identity."""
    found = build_identity.identity(stamp_path=_stamp(tmp_path, "unknown"), version="x")
    assert found["image"]["commit"] == build_identity.UNKNOWN  # type: ignore[index]
    assert found["code"]["commit"] == REAL  # type: ignore[index]


def test_the_dockerfile_default_is_the_sentinel_this_guards(tmp_path):
    """If the ARG default changes, this test's premise changes with it."""
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(
        encoding="utf-8"
    )
    assert (
        f"ARG ZAROPGX_COMMIT={build_identity.UNKNOWN}" in dockerfile
    ), "the guard is written against the Dockerfile's default; they must agree"


def test_the_release_procedure_records_the_commit():
    """The other half of the defect, and the half a code fix cannot reach.

    ``build_identity`` can stop reporting an unrecorded commit as a disagreement, but
    only the build can make the commit recorded in the first place. ``docker-bake.hcl``
    has always said to pass it; the release procedure did not, so the release that
    followed the procedure shipped without it.
    """
    procedure = (
        Path(__file__).resolve().parents[1] / "docs/developer/contributing.md"
    ).read_text(encoding="utf-8")
    bake = [ln for ln in procedure.splitlines() if "buildx bake" in ln]
    assert bake, "the release procedure no longer shows a bake command"
    assert any(
        "ZAROPGX_COMMIT" in ln or "ZAROPGX_COMMIT" in procedure.splitlines()[i - 1]
        for i, ln in enumerate(procedure.splitlines())
        if "buildx bake" in ln
    ), "the release bake must pass ZAROPGX_COMMIT, or images ship stamped 'unknown'"
