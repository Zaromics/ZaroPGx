"""The running app can say which image it is and which code it is serving.

Those are two answers here, not one. `compose.yml` mounts the repository over `/app`, so
the application code inside the published image is shadowed on every install: the tag
describes the dependencies, the checkout decides the behavior, and nothing said so.
These pin the reading, including the two shapes that quietly report nothing --- a linked
worktree, whose refs live in the repository rather than beside its HEAD, and an image
with no checkout at all.

A mismatch is not an error. It is what a machine someone is developing on looks like, and
the module must report it rather than refuse to start, which is why `agrees` has three
values and not two.
"""

from __future__ import annotations

import json

import pytest

from app.utils import build_identity

SHA = "b" * 40
OTHER = "c" * 40


@pytest.fixture
def checkout(tmp_path):
    """A plain repository: HEAD names a branch, the branch is a loose ref."""
    root = tmp_path / "checkout"
    (root / ".git" / "refs" / "heads").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (root / ".git" / "refs" / "heads" / "main").write_text(SHA + "\n", encoding="utf-8")
    return root


def test_a_plain_checkout_reports_its_commit(checkout):
    assert build_identity.commit_of(checkout) == SHA


def test_a_detached_head_is_the_commit_itself(checkout):
    """What a release build stands on: `git worktree add --detach <path> v0.4.3`."""
    (checkout / ".git" / "HEAD").write_text(OTHER + "\n", encoding="utf-8")

    assert build_identity.commit_of(checkout) == OTHER


def test_a_packed_ref_is_found_too(checkout):
    """git packs refs as a repository ages; a fresh clone has no loose heads at all."""
    (checkout / ".git" / "refs" / "heads" / "main").unlink()
    (checkout / ".git" / "packed-refs").write_text(
        f"# pack-refs with: peeled fully-peeled sorted\n{OTHER} refs/heads/main\n"
        f"{SHA} refs/tags/v0.4.3\n^{OTHER}\n",
        encoding="utf-8",
    )

    assert build_identity.commit_of(checkout) == OTHER


def test_a_linked_worktree_reads_the_repository_s_refs(tmp_path, checkout):
    """Every branch in ~/zaropgx-wt is one of these: `.git` is a file pointing at the
    repository, HEAD sits there, and the refs do not --- they stay in the repository.
    Reading only beside HEAD finds nothing, and a worktree is precisely where someone
    is running code that is not the image's."""
    linked = tmp_path / "worktree"
    linked.mkdir()
    git_dir = checkout / ".git" / "worktrees" / "feature"
    git_dir.mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "commondir").write_text("../..\n", encoding="utf-8")
    (linked / ".git").write_text(f"gitdir: {git_dir}\n", encoding="utf-8")

    assert build_identity.commit_of(linked) == SHA


def test_no_checkout_is_not_an_error(tmp_path):
    """An image running its own copy, without the mount, has no .git. That is not a
    fault, so it reads as "no commit here" and nothing is claimed."""
    assert build_identity.commit_of(tmp_path) is None


def test_the_image_and_the_code_are_reported_apart(tmp_path, checkout):
    stamp = tmp_path / "build.json"
    stamp.write_text(
        json.dumps({"tag": "0.4.3", "commit": OTHER, "built": "2026-11-14T19:00:00Z"}),
        encoding="utf-8",
    )

    found = build_identity.identity(stamp, checkout, version="0.4.3")

    assert found["image"] == {
        "tag": "0.4.3",
        "commit": OTHER,
        "built": "2026-11-14T19:00:00Z",
    }
    assert found["code"] == {"commit": SHA, "from": "checkout"}
    assert found["agrees"] is False
    assert "running the checkout, not the image" in build_identity.disagreement(found)
    assert OTHER[:12] in build_identity.disagreement(found)


def test_agreement_is_silent(tmp_path, checkout):
    stamp = tmp_path / "build.json"
    stamp.write_text(json.dumps({"tag": "0.4.3", "commit": SHA}), encoding="utf-8")

    found = build_identity.identity(stamp, checkout, version="0.4.3")

    assert found["agrees"] is True
    assert build_identity.disagreement(found) is None


def test_a_short_commit_in_the_stamp_still_matches(tmp_path, checkout):
    """`git rev-parse --short HEAD` is what a build script reaches for first."""
    stamp = tmp_path / "build.json"
    stamp.write_text(json.dumps({"tag": "0.4.3", "commit": SHA[:7]}), encoding="utf-8")

    assert build_identity.identity(stamp, checkout)["agrees"] is True


@pytest.mark.parametrize(
    "written",
    ["", "not json at all", json.dumps(["a", "list"]), json.dumps({"tag": None})],
)
def test_an_unusable_stamp_claims_nothing(tmp_path, checkout, written):
    """Half an answer is worse than none: an unreadable stamp must not turn into a
    mismatch warning about an image nobody can name."""
    stamp = tmp_path / "build.json"
    stamp.write_text(written, encoding="utf-8")

    found = build_identity.identity(stamp, checkout)

    assert found["image"]["tag"] == build_identity.UNKNOWN
    assert found["agrees"] is None
    assert build_identity.disagreement(found) is None


def test_one_unknown_side_is_not_a_disagreement(tmp_path):
    """No stamp and no checkout both happen, and neither is evidence of anything."""
    assert build_identity.identity(tmp_path / "none.json", tmp_path)["agrees"] is None


def test_the_stamp_is_written_outside_the_mounted_directory():
    """A stamp under /app is shadowed by the very mount it exists to detect, and would
    report the checkout's stamp as the image's."""
    assert not str(build_identity.BUILD_STAMP).startswith("/app")

    dockerfile = (
        build_identity.Path(__file__).resolve().parents[1] / "Dockerfile"
    ).read_text()
    assert "/opt/zaropgx/build.json" in dockerfile
    assert "ARG ZAROPGX_COMMIT" in dockerfile
