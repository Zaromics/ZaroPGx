"""The release can check that its images carry the code the release changed.

Seven of the eight images build from the repository root and five copy files out of
`app/`, so an image is only as current as the tree it was built in. A build run in a
checkout still on the previous release publishes that code inside a correctly tagged
image, and the tag, the push and the registry check all pass regardless.

These pin the reading the checker depends on. Each one is a way to be confidently wrong:
read every context as the repository root and `genome-downloader` reports its whole
contents missing; ignore `.dockerignore` and `.env.*` reports missing on every release;
hash the blob instead of the checkout and the two PowerShell files report stale forever,
which is how a check teaches people to ignore it.

The Docker-side half needs images and is exercised by running the script; everything here
is the reading, which needs nothing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def verify():
    spec = importlib.util.spec_from_file_location(
        "verify_image_contents", REPO / "scripts" / "verify_image_contents.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# What a Dockerfile copies
# --------------------------------------------------------------------------


def test_a_relative_destination_lands_under_the_workdir(verify):
    """`COPY app.py .` under WORKDIR /app lands /app/app.py, not /app. Reading it as
    /app hashes a directory and calls every file in the image missing."""
    instructions = verify.copies("FROM x\nWORKDIR /app\nCOPY docker/zarohla/app.py .\n")

    assert verify.where("docker/zarohla/app.py", instructions) == ["/app/app.py"]


def test_a_stage_copy_says_nothing_about_this_tree(verify):
    """`--from=` takes files from another image; the tree never reached it."""
    text = (
        "FROM x\nCOPY --from=builder /opt/out/ /usr/local/\nCOPY app/utils/x.py /lib/\n"
    )

    assert [sources for sources, _ in verify.copies(text)] == [["app/utils/x.py"]]


def test_a_continued_line_is_one_instruction(verify):
    text = "FROM x\nWORKDIR /app\nCOPY docker/pypgx/a.py \\\n     docker/pypgx/b.py /app/\n"
    instructions = verify.copies(text)

    assert verify.where("docker/pypgx/b.py", instructions) == ["/app/b.py"]


def test_several_sources_mean_the_destination_is_a_directory(verify):
    """Docker requires it, so `COPY a b ./` is /app/a and /app/b even without a slash."""
    instructions = verify.copies(
        "FROM x\nWORKDIR /app\nCOPY pyproject.toml uv.lock ./\n"
    )

    assert verify.where("uv.lock", instructions) == ["/app/uv.lock"]


def test_a_whole_context_copy_carries_everything(verify):
    instructions = verify.copies("FROM x\nCOPY . /app/\n")

    assert verify.where("app/reports/generator.py", instructions) == [
        "/app/app/reports/generator.py"
    ]


def test_a_directory_copy_keeps_the_path_below_it(verify):
    instructions = verify.copies("FROM x\nCOPY app/mtdna/ /mtdna-lib/mtdna/\n")

    assert verify.where("app/mtdna/calls.py", instructions) == [
        "/mtdna-lib/mtdna/calls.py"
    ]


def test_one_place_reached_twice_is_reported_once(verify):
    """`COPY x ./x` then `COPY . /app/` is one file in one place by two routes;
    "/app/./x" and "/app/x" are that place written two ways."""
    text = "FROM x\nWORKDIR /app\nCOPY docs/requirements.txt ./docs/requirements.txt\nCOPY . /app/\n"

    assert verify.where("docs/requirements.txt", verify.copies(text)) == [
        "/app/docs/requirements.txt"
    ]


def test_a_file_the_image_does_not_copy_maps_nowhere(verify):
    """Not every image carries every file, and the ones that do not are not at fault."""
    instructions = verify.copies("FROM x\nCOPY app/utils/job_client.py /job-client/\n")

    assert verify.where("app/reports/generator.py", instructions) == []


# --------------------------------------------------------------------------
# What the build context excludes
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path, excluded",
    [
        (".env.production", True),
        (".env", True),
        ("app/main.py", False),
        ("data/reports/x.pdf", True),
        ("dev-notes/trial.md", True),
        ("docs/_build/html/index.html", True),
        ("docs/user/usage.md", False),
        ("app/logs/pipeline.log", True),
        ("reference/hg38.fa", True),
    ],
)
def test_the_dockerignore_decides_what_is_in_the_context(verify, path, excluded):
    """A file the context excludes never reaches any image, and reporting it as
    missing would make the check cry wolf on every release."""
    patterns = (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert verify.ignored(path, patterns) is excluded


def test_a_negation_puts_a_file_back(verify):
    patterns = ["docs/", "!docs/requirements.txt"]

    assert verify.ignored("docs/conf.py", patterns) is True
    assert verify.ignored("docs/requirements.txt", patterns) is False


# --------------------------------------------------------------------------
# What is built, and from where
# --------------------------------------------------------------------------


def test_every_published_service_is_checked(verify):
    """compose.yml rather than docker-bake.hcl: mtdna is deliberately not a bake
    target, and is the one image this check exists for."""
    names = {service.name for service in verify.services()}

    assert {
        "app",
        "mtdna",
        "pharmcat",
        "pypgx",
        "gatk-api",
        "nextflow",
        "zarohla",
    } <= names
    assert "db" not in names, "a pulled image carries none of this repository"


def test_a_service_builds_from_its_own_context(verify):
    """genome-downloader builds from docker/genome-downloader, so its `COPY . /app/`
    carries that directory and nothing else. Read as the repository root, every file in
    it reports missing and the release looks broken."""
    downloader = next(s for s in verify.services() if s.name == "genome-downloader")

    assert downloader.inside("docker/genome-downloader/downloader_api.py") == (
        "downloader_api.py"
    )
    assert downloader.inside("app/reports/generator.py") is None


def test_the_repository_root_services_see_the_whole_tree(verify):
    app = next(s for s in verify.services() if s.name == "app")

    assert app.inside("app/reports/generator.py") == "app/reports/generator.py"


# --------------------------------------------------------------------------
# Which bytes are compared
# --------------------------------------------------------------------------


def test_the_checked_out_form_is_what_the_image_holds(verify):
    """.gitattributes checks PowerShell out with CRLF while the blob stores LF, and the
    image is built from a checkout. Comparing against the blob reports bootstrap.ps1 and
    start-docker.ps1 stale on every release."""
    attributes = (REPO / ".gitattributes").read_text(encoding="utf-8")
    assert "eol=crlf" in attributes, "nothing is checked out with CRLF any more"

    source = (REPO / "scripts" / "verify_image_contents.py").read_text(encoding="utf-8")
    assert "cat-file" in source and "--filters" in source
