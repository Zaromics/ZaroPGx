"""One filename-sanitising rule on both sides of the app/sidecar boundary.

The split this pins closed:

  * ``docker/gatk-api/gatk_api.py`` rebuilt the stored name out of parts it
    controls -- an allowlisted ``[A-Za-z0-9_-]`` fragment of the original,
    capped at 40 characters, a uuid, and an extension taken from a literal
    tuple -- with an ``or 'upload'`` fallback so the result is never empty.

  * ``app/main.py:/api/variant-call`` used werkzeug's bare ``secure_filename``.
    That returns ``""`` for a wholly pathological name such as ``"..."``, and
    the next line was ``os.path.join(temp_dir, "")``, which is the *directory*
    -- opened for writing two lines later. It also imposes no length cap and
    allowlists no extension. The app-facing route was the weaker of the two.

  * ``docker/gatk-api/gatk_api.py`` itself still saved the CRAM and SAM uploads
    through bare ``os.path.basename``, which that same file's ``safe_upload_name``
    docstring calls insufficient (``;``, ``|``, ``$(...)``, backticks, ``*`` and
    ``?`` are all legal in a POSIX filename), and derived ``output_bam`` -- on
    the *shared* volume -- from the result.

Can it be a shared helper? Mechanically yes, and this test says so rather than
pretending otherwise: every sidecar Dockerfile already does
``COPY app/utils/job_client.py /job-client/``, so a pure-Python module under
``app/utils/`` can be copied into the gatk-api image the same way. It was not
done here because it costs a Dockerfile edit and an image rebuild per sidecar,
and because the sidecar has no werkzeug (see Dockerfile.gatk-api), which is why
it hand-rolled the filter to begin with. So the two remain a *documented*
duplicate, and the duplication is held honest by the differential test below:
both implementations run over the same corpus and must return identical names.

Related but deliberately out of scope: ``app/api/utils/file_processor.py``'s
``safe_upload_basename`` (upload-router path, guarantees non-emptiness only)
and ``docker/pypgx/pypgx_wrapper.py``'s ``safe_upload_name`` (different and
stricter signature -- it discards the original stem entirely and takes a
default suffix). Neither is a copy of this pair.
"""

from __future__ import annotations

import ast
import re
import uuid
from pathlib import Path

import pytest

from app.main import SAFE_UPLOAD_EXTENSIONS as APP_EXTENSIONS
from app.main import safe_upload_name as app_safe_upload_name

REPO_ROOT = Path(__file__).resolve().parents[1]
GATK_API_SOURCE = REPO_ROOT / "docker" / "gatk-api" / "gatk_api.py"

# Names that have to survive intact enough to still be recognisable, plus the
# adversarial shapes each layer of the sanitiser exists for.
CORPUS = [
    # Ordinary uploads.
    "sample.vcf",
    "sample.vcf.gz",
    "SAMPLE.VCF.GZ",
    "reads.bam",
    "reads.cram",
    "reads.sam",
    "reads.bcf",
    "reads.fastq",
    "reads.fq",
    "patient_001-run2.vcf",
    # The empty-result case that broke the app route.
    "...",
    "..",
    ".",
    "",
    None,
    "/",
    "////",
    # Shell syntax.
    "x;touch pwned.bam",
    "$(touch pwned).vcf",
    "`touch pwned`.bam",
    "a|b&c>d<e.vcf",
    "a b\tc.bam",
    "'quoted'.vcf",
    '"quoted".vcf',
    "a\nb.vcf",
    # Glob metacharacters -- Nextflow's Channel.fromPath globs its argument.
    "*.vcf",
    "?.vcf",
    "[a-z].vcf",
    "{a,b}.vcf",
    # Traversal, both separators.
    "../../etc/passwd.vcf",
    "..\\..\\windows\\evil.bam",
    "/etc/passwd",
    # Extension games.
    "payload.sh",
    "noextension",
    "archive.vcf.gz.exe",
    "double..vcf",
    ".vcf",
    ".bashrc",
    # Length.
    "a" * 300 + ".vcf",
    "éèê" * 50 + ".bam",
    # Unicode / control bytes.
    "null.vcf",
    "naïve.vcf",
    "‮reversed.vcf",
]

JOB_IDS = ["job-uuid", "abc123", "0" * 32]


@pytest.fixture(scope="module")
def sidecar_safe_upload_name():
    """Exec gatk-api's sanitiser out of its source.

    docker/gatk-api/gatk_api.py is a container entry point: importing it whole
    needs stubs for psutil and the /job-client helper (tests/test_gatk_api_no_mock_bam.py
    does exactly that). Only the sanitiser is wanted here, so the two top-level
    definitions it depends on are lifted out and executed against the real `os`
    and `re`. The assertions are therefore made against the function that runs in
    the image, not a transcription of it.
    """
    tree = ast.parse(
        GATK_API_SOURCE.read_text(encoding="utf-8"), filename=str(GATK_API_SOURCE)
    )
    wanted = {"safe_upload_name", "SAFE_UPLOAD_EXTENSIONS"}
    picked, found = [], set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            picked.append(node)
            found.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in wanted:
                    picked.append(node)
                    found.add(target.id)
    missing = wanted - found
    assert not missing, f"gatk_api.py no longer defines {sorted(missing)}"

    import os as _os

    namespace = {"os": _os, "re": re}
    exec(
        compile(ast.Module(body=picked, type_ignores=[]), str(GATK_API_SOURCE), "exec"),
        namespace,
    )
    return namespace


# ---------------------------------------------------------------------------
# The differential: the documented duplicate must actually match
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", CORPUS)
@pytest.mark.parametrize("job_id", JOB_IDS)
def test_app_and_sidecar_return_identical_names(sidecar_safe_upload_name, name, job_id):
    """The whole justification for duplicating the helper is that it matches.

    An undocumented divergence is what this suite exists to prevent; a
    documented duplicate is only acceptable while it is still a duplicate.
    """
    assert app_safe_upload_name(name, job_id) == sidecar_safe_upload_name[
        "safe_upload_name"
    ](name, job_id), f"app and gatk-api disagree on {name!r}"


def test_the_two_extension_allowlists_are_the_same(sidecar_safe_upload_name):
    assert tuple(APP_EXTENSIONS) == tuple(
        sidecar_safe_upload_name["SAFE_UPLOAD_EXTENSIONS"]
    ), "the app and sidecar extension allowlists have drifted apart"


# ---------------------------------------------------------------------------
# The guarantees themselves, asserted on the app-facing implementation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", CORPUS)
def test_output_is_always_inert(name):
    """Every byte is [A-Za-z0-9_-], our uuid, or a literal extension."""
    result = app_safe_upload_name(name, "job1")
    assert re.fullmatch(r"[A-Za-z0-9_.-]+", result), result
    assert "/" not in result and "\\" not in result
    assert ".." not in result
    for hostile in (
        ";",
        "|",
        "&",
        "$",
        "`",
        "*",
        "?",
        "[",
        "]",
        "{",
        "}",
        "<",
        ">",
        " ",
        "\t",
        "\n",
        "'",
        '"',
    ):
        assert hostile not in result, f"{hostile!r} survived in {result!r}"


@pytest.mark.parametrize("name", CORPUS)
def test_output_is_never_empty_and_is_never_the_directory(tmp_path, name):
    """The exact defect: secure_filename('...') == '' and join(dir, '') == dir.

    Written as the filesystem consequence rather than as `!= ""`, because the
    consequence is what actually happened -- the route then opened the temp
    directory for writing.
    """
    result = app_safe_upload_name(name, "job1")
    assert result, f"empty stored name for {name!r}"

    import os

    joined = Path(os.path.join(str(tmp_path), result))
    assert joined != tmp_path
    assert joined.parent == tmp_path
    joined.write_bytes(b"ok")  # would raise IsADirectoryError/PermissionError before
    assert joined.is_file()


@pytest.mark.parametrize(
    "name,expected",
    [
        ("sample.vcf", ".vcf"),
        ("sample.VCF", ".vcf"),
        ("sample.vcf.gz", ".vcf.gz"),  # longest suffix wins
        ("sample.VCF.GZ", ".vcf.gz"),
        ("reads.cram", ".cram"),
        ("reads.sam", ".sam"),
        ("reads.fq", ".fq"),
    ],
)
def test_whitelisted_extensions_are_preserved(name, expected):
    """Downstream code branches on the extension, so it has to survive."""
    assert app_safe_upload_name(name, "j").endswith(expected)


@pytest.mark.parametrize("name", ["payload.sh", "noextension", "evil.exe", "x.tar.gz"])
def test_extensions_outside_the_allowlist_are_dropped(name):
    result = app_safe_upload_name(name, "j")
    assert not any(result.endswith(ext) for ext in (".sh", ".exe", ".tar.gz"))


def test_the_fragment_is_length_capped():
    """A 300-character name must not become a 300-character path component."""
    result = app_safe_upload_name("a" * 300 + ".vcf", "j")
    assert result == "a" * 40 + "_j.vcf"


def test_distinct_job_ids_give_distinct_names():
    """Concurrent uploads of the same filename must not collide."""
    names = {app_safe_upload_name("sample.vcf", uuid.uuid4().hex) for _ in range(50)}
    assert len(names) == 50


# ---------------------------------------------------------------------------
# Call-site fences: the sanitiser is only useful where it is actually called
# ---------------------------------------------------------------------------


def _save_site_calls(source: str, marker: str):
    tree = ast.parse(source)
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == marker
    ]


def test_gatk_api_conversion_routes_no_longer_use_bare_basename():
    """The CRAM and SAM save sites went through os.path.basename until now.

    output_bam is derived from this name and lands on the volume shared with the
    app, so a metacharacter here did not stay inside the sidecar's work dir.
    """
    source = GATK_API_SOURCE.read_text(encoding="utf-8")
    offenders = [
        (i, line.strip())
        for i, line in enumerate(source.splitlines(), 1)
        if "os.path.basename(file.filename" in line
    ]
    assert not offenders, (
        f"gatk_api.py still saves an upload under a bare basename at {offenders}; "
        "it must go through safe_upload_name()"
    )
    # Four save sites now: /variant-call, /cram-to-bam, /sam-to-bam, plus the
    # definition's own recursive use of basename() on an already-split name.
    assert (
        len(_save_site_calls(source, "safe_upload_name")) >= 3
    ), "expected safe_upload_name() at every gatk-api upload save site"


def test_app_variant_call_route_does_not_use_bare_secure_filename():
    source = (REPO_ROOT / "app" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    bare = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "secure_filename"
    ]
    assert not bare, (
        "app/main.py still calls werkzeug secure_filename() directly; it returns "
        '"" for "..." and the caller then opens the directory'
    )
    assert _save_site_calls(
        source, "safe_upload_name"
    ), "app/main.py no longer routes the upload filename through safe_upload_name()"
