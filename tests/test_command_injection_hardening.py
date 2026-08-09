"""Hardening for request-controlled values that become paths and command arguments.

The recurring pattern, not a fixed number of instances: a value taken straight
off an HTTP request — a multipart filename, a form field, a query parameter —
is used to build a filesystem path or a command argument without ever being
constrained. Each place it happens is a separate hole, and the shapes it takes
are not interchangeable, so a fix aimed at one shape leaves the others open:

  * as *shell syntax* — `job_dir / file.filename` fed `shell=True` invocations
    of pypgx/bgzip/tabix in docker/pypgx/pypgx_wrapper.py. The pypgx service
    has no authentication and is reachable from every container on the compose
    network, so this was live inside the trust boundary.

  * as a *path* — a gene name from /genotype's comma-separated branch builds
    `Path(output_dir) / f"{gene}-pipeline"`, so `../../../../TMP/X` writes
    outside the job directory. Running commands as argv does nothing about
    this; no shell is involved.

  * as an *option* — the same gene name is a positional pypgx argument, so a
    leading `-` is read by pypgx's own parser as a flag. Again no shell.

  * as a *glob* — `upload_{filename}` in /data/uploads becomes `--input` to
    Nextflow, and main.nf opens with `Channel.fromPath(params.input)`, which
    globs. Shell metacharacters do *not* survive that hop (Nextflow escapes
    `path`-typed inputs before interpolating them into a task script) but
    `*`, `?`, `[…]` and `{…}` do, and fan the run out across every other file
    in /data/uploads.

  * as a *path*, again, in a third service — `job_dir / file.filename` in
    docker/zarohla/app.py, for all three of its upload fields. That sidecar
    runs no `shell=True`, so this one was never command injection; it was the
    write primitive and the glob shape above, in a service the Nextflow HLA
    processes reach with a filename derived from the patient's own upload.

So the tests below pin the sanitisers at each source, the argv-list sinks, the
gene-name shape rule, and the glob containment. Each is written so that
reverting the corresponding production change fails it. When auditing this
area, look for the pattern above rather than for these five instances.

Why AST/exec rather than `import`:
docker/pypgx/pypgx_wrapper.py and docker/zarohla/app.py are container entry
points. Importing either on the host dies at `import psutil`, then at
`sys.path.append('/job-client')`, then at a RotatingFileHandler pointed into
/data — none of which exist here. This is the same constraint
tests/test_log_rotation_252.py documents. Rather than settle for a text grep,
the individual top-level functions are extracted from the parsed source and
executed against stub modules, so the assertions are made against the functions
actually running in the image.
"""

import ast
import re
import subprocess
from pathlib import Path
from typing import Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPGX_WRAPPER = REPO_ROOT / "docker" / "pypgx" / "pypgx_wrapper.py"
ZAROHLA_APP = REPO_ROOT / "docker" / "zarohla" / "app.py"

# The canonical payload: a filename that, interpolated into a shell string,
# ends the pypgx command and starts a second one. '/' is not used because it is
# illegal in a POSIX filename — an attacker would reach for a relative target or
# build the separator in the shell, and the injection is identical either way.
PAYLOAD_NAME = "x;touch pwned.bam"
PAYLOAD_MARKER = ";touch pwned"


# ---------------------------------------------------------------------------
# Loading individual functions out of the container entry point
# ---------------------------------------------------------------------------


def _load_from_source(path: Path, names, namespace: dict) -> dict:
    """Exec the named top-level functions/assignments of `path` in `namespace`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    wanted = []
    found = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in names:
                wanted.append(node)
                found.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in names:
                    wanted.append(node)
                    found.add(target.id)
    missing = set(names) - found
    assert not missing, f"{path.name} no longer defines: {sorted(missing)}"
    module = ast.fix_missing_locations(ast.Module(body=wanted, type_ignores=[]))
    exec(compile(module, str(path), "exec"), namespace)  # noqa: S102
    return namespace


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeProcess:
    def __init__(self, returncode=0):
        self.pid = 4242
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""

    def communicate(self, timeout=None):
        return "", ""

    def kill(self):  # pragma: no cover - not reached in these tests
        pass


class _SubprocessRecorder:
    """Stands in for the `subprocess` module and records every invocation."""

    PIPE = subprocess.PIPE
    STDOUT = subprocess.STDOUT
    CalledProcessError = subprocess.CalledProcessError
    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self, returncode=0):
        self.calls = []
        self._returncode = returncode

    def run(self, args, **kwargs):
        self.calls.append((args, kwargs))
        return _FakeCompleted(returncode=self._returncode)

    def Popen(self, args, **kwargs):  # noqa: N802 - mirrors subprocess.Popen
        self.calls.append((args, kwargs))
        return _FakeProcess(returncode=self._returncode)


class _NullLogger:
    def __getattr__(self, _name):
        return lambda *a, **k: None


def _pypgx_namespace(recorder, **extra):
    """A namespace with everything the extracted pypgx functions close over."""
    import os
    import re
    import shlex
    import uuid

    from fastapi import HTTPException

    ns = {
        "subprocess": recorder,
        "logger": _NullLogger(),
        "os": os,
        "re": re,
        "shlex": shlex,
        "uuid": uuid,
        "Path": Path,
        "Optional": Optional,
        "Dict": dict,
        "Any": object,
        "List": list,
        "HTTPException": HTTPException,
    }
    ns.update(extra)
    return ns


def _assert_argv_is_safe(args, kwargs, payload_fragment):
    """The payload must ride as one opaque argv element, with no shell."""
    assert isinstance(
        args, list
    ), f"command was built as a shell string, not an argv list: {args!r}"
    assert kwargs.get("shell") is not True, "command still runs with shell=True"
    carriers = [a for a in args if payload_fragment in str(a)]
    assert len(carriers) == 1, (
        f"expected the payload in exactly one argv element, got {carriers!r} "
        f"out of {args!r}"
    )


# ---------------------------------------------------------------------------
# Source-side sanitiser: safe_upload_name
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pypgx_sanitiser():
    ns = _load_from_source(
        PYPGX_WRAPPER,
        {"safe_upload_name", "ALLOWED_UPLOAD_SUFFIXES"},
        _pypgx_namespace(_SubprocessRecorder()),
    )
    return ns["safe_upload_name"]


@pytest.mark.parametrize(
    "hostile",
    [
        PAYLOAD_NAME,
        "x;touch pwned.vcf",
        "$(touch pwned).vcf",
        "`touch pwned`.bam",
        "a|b&c>d<e.vcf",
        "a b\tc.bam",
        "*.vcf",
        "?.vcf",
        "[a-z].vcf",
        "../../etc/passwd.vcf",
        "..\\..\\windows\\evil.bam",
        "'quoted'.vcf",
        '"quoted".vcf',
    ],
)
def test_safe_upload_name_emits_no_metacharacters(pypgx_sanitiser, hostile):
    """Only a hex stem plus a whitelisted suffix may reach the filesystem."""
    result = pypgx_sanitiser(hostile, ".bam")
    assert set(result) <= set("abcdefghijklmnopqrstuvwxyz0123456789."), result
    assert "/" not in result and "\\" not in result
    assert ".." not in result
    # And nothing of the attacker's own text is carried through.
    assert "touch" not in result
    assert "pwned" not in result


def test_safe_upload_name_keeps_a_whitelisted_extension(pypgx_sanitiser):
    """Legitimate uploads keep the extension the rest of the code branches on."""
    assert pypgx_sanitiser("sample.vcf", ".bam").endswith(".vcf")
    assert pypgx_sanitiser("sample.VCF", ".bam").endswith(".vcf")
    # Longest suffix wins, so a bgzipped VCF is not mistaken for a plain one.
    assert pypgx_sanitiser("sample.vcf.gz", ".bam").endswith(".vcf.gz")
    assert pypgx_sanitiser("reads.cram", ".bam").endswith(".cram")


def test_safe_upload_name_falls_back_for_unknown_extensions(pypgx_sanitiser):
    assert pypgx_sanitiser("noextension", ".bam").endswith(".bam")
    assert pypgx_sanitiser(None, ".vcf").endswith(".vcf")
    assert pypgx_sanitiser("", ".vcf").endswith(".vcf")
    # An extension is never inherited from outside the whitelist.
    assert pypgx_sanitiser("payload.sh", ".vcf").endswith(".vcf")


def test_safe_upload_name_is_unique_per_call(pypgx_sanitiser):
    names = {pypgx_sanitiser("sample.vcf", ".vcf") for _ in range(50)}
    assert len(names) == 50, "generated names collide; concurrent jobs would clash"


# ---------------------------------------------------------------------------
# The third instance: docker/zarohla/app.py, and its documented duplicate
# ---------------------------------------------------------------------------
#
# zarohla carries its own copy of ALLOWED_UPLOAD_SUFFIXES / safe_upload_name
# because it ships in a separate image and imports the module before sys.path
# reaches the shared /job-client directory (the same argument the bounded
# logging block in every sidecar makes). A documented duplicate is acceptable;
# an undocumented divergence is not, so both are executed out of their sources
# below and required to agree.

# 32 lowercase hex characters, then one of the allowlisted suffixes.
_STORED_NAME_RE = re.compile(r"^[0-9a-f]{32}(\.[a-z.]+)?$")

# Ordinary names, the shapes the sanitiser exists for, and the degenerate ones.
SUFFIX_CORPUS = [
    "reads.fastq",
    "reads.fq",
    "reads.fastq.gz",
    "READS.FASTQ.GZ",
    "reads.fq.gz",
    "sample.vcf",
    "sample.vcf.gz",
    "sample.vcf.bgz",
    "reads.bam",
    "reads.cram",
    "reads.sam",
    "reads.bcf",
    "payload.sh",
    "noextension",
    "archive.tar.gz",
    "",
    None,
    "...",
    "/",
    PAYLOAD_NAME,
    "*.fastq.gz",
    "?.bam",
    "[a-z].vcf",
    "{a,b}.fq.gz",
    "../../etc/passwd.bam",
    "..\\..\\windows\\evil.bam",
    "$(touch pwned).fastq",
    "`touch pwned`.bam",
    "a|b&c>d<e.vcf",
    "a b\tc.bam",
    "a\nb.fastq",
]


@pytest.fixture(scope="module")
def zarohla_sanitiser():
    ns = _load_from_source(
        ZAROHLA_APP,
        {"safe_upload_name", "ALLOWED_UPLOAD_SUFFIXES"},
        _pypgx_namespace(_SubprocessRecorder()),
    )
    return ns


def test_zarohla_and_pypgx_allowlists_are_identical(zarohla_sanitiser):
    ns = _load_from_source(
        PYPGX_WRAPPER,
        {"ALLOWED_UPLOAD_SUFFIXES"},
        _pypgx_namespace(_SubprocessRecorder()),
    )
    assert tuple(zarohla_sanitiser["ALLOWED_UPLOAD_SUFFIXES"]) == tuple(
        ns["ALLOWED_UPLOAD_SUFFIXES"]
    ), "the zarohla and pypgx upload-suffix allowlists have drifted apart"


@pytest.mark.parametrize("suffix", [".fastq.gz", ".fq.gz"])
def test_gzipped_fastq_suffixes_are_allowlisted(zarohla_sanitiser, suffix):
    """OptiType is a FASTQ consumer; a mangled `.fastq.gz` is a confusing failure.

    Written against the two-part suffixes specifically because a single-part
    matcher silently reduces `reads.fastq.gz` to no recognised extension at all
    (`.gz` is not on the list) and drops it.
    """
    assert suffix in zarohla_sanitiser["ALLOWED_UPLOAD_SUFFIXES"]
    assert zarohla_sanitiser["safe_upload_name"](f"reads{suffix}", ".fastq").endswith(
        suffix
    )


@pytest.mark.parametrize("name", SUFFIX_CORPUS)
def test_zarohla_and_pypgx_choose_the_same_suffix(zarohla_sanitiser, name):
    """The whole justification for duplicating the helper is that it matches.

    The uuid stem differs by construction on every call, so the differential is
    on the part that is a decision: which suffix the name is allowed to keep.
    """
    pypgx_ns = _load_from_source(
        PYPGX_WRAPPER,
        {"safe_upload_name", "ALLOWED_UPLOAD_SUFFIXES"},
        _pypgx_namespace(_SubprocessRecorder()),
    )
    zarohla = zarohla_sanitiser["safe_upload_name"](name, ".fastq")
    pypgx = pypgx_ns["safe_upload_name"](name, ".fastq")
    assert zarohla[32:] == pypgx[32:], f"zarohla and pypgx disagree on {name!r}"


@pytest.mark.parametrize("name", SUFFIX_CORPUS)
def test_zarohla_stored_name_is_always_inert(zarohla_sanitiser, name):
    """Every byte is our own hex uuid or a literal suffix from the tuple."""
    result = zarohla_sanitiser["safe_upload_name"](name, ".fastq")
    assert _STORED_NAME_RE.match(result), result
    assert "/" not in result and "\\" not in result
    assert ".." not in result
    for meta in "*?[]{}$`|&;<>() \t\n'\"":
        assert meta not in result, f"{meta!r} survived {name!r} -> {result!r}"


def test_zarohla_upload_names_do_not_collide(zarohla_sanitiser):
    """file1 and file2 share one job directory, so equal names used to clobber."""
    names = {
        zarohla_sanitiser["safe_upload_name"]("reads.fastq", ".fastq")
        for _ in range(50)
    }
    assert len(names) == 50


def test_zarohla_never_builds_a_path_from_the_client_filename():
    """Reverting any of the three save sites to `job_dir / fileN.filename` fails here."""
    offenders = _raw_filename_path_joins(
        ast.parse(ZAROHLA_APP.read_text(encoding="utf-8"), filename=str(ZAROHLA_APP))
    )
    assert not offenders, (
        f"{ZAROHLA_APP.name} joins a path with the raw client filename at "
        f"line(s) {offenders}; it must go through safe_upload_name()"
    )


def test_zarohla_upload_endpoint_calls_the_sanitiser():
    """All three multipart fields -- file, file1, file2 -- must be routed through it."""
    calls = [
        node
        for node in ast.walk(
            ast.parse(
                ZAROHLA_APP.read_text(encoding="utf-8"), filename=str(ZAROHLA_APP)
            )
        )
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "safe_upload_name"
    ]
    assert (
        len(calls) >= 3
    ), "expected safe_upload_name() at all three /call-hla save sites"


# ---------------------------------------------------------------------------
# Sink side: every command is an argv list, so a payload cannot become syntax
# ---------------------------------------------------------------------------


def test_bgzip_and_tabix_helpers_use_argv(tmp_path):
    recorder = _SubprocessRecorder()
    ns = _load_from_source(
        PYPGX_WRAPPER,
        {"bgzip_to", "bgzip_in_place", "tabix_index"},
        _pypgx_namespace(recorder),
    )

    hostile_src = tmp_path / PAYLOAD_NAME
    hostile_src.write_bytes(b"")
    dest = tmp_path / "out.vcf.gz"

    ns["bgzip_to"](str(hostile_src), str(dest))
    ns["bgzip_in_place"](str(hostile_src))
    ns["tabix_index"](str(hostile_src))
    ns["tabix_index"](str(hostile_src), force=True)

    assert len(recorder.calls) == 4
    for args, kwargs in recorder.calls:
        _assert_argv_is_safe(args, kwargs, PAYLOAD_MARKER)

    # bgzip's `> dest` redirection was the only real reason for a shell; it must
    # now be an explicit stdout handle rather than shell syntax.
    bgzip_to_args, bgzip_to_kwargs = recorder.calls[0]
    assert bgzip_to_args[:2] == ["bgzip", "-c"]
    assert ">" not in bgzip_to_args
    assert bgzip_to_kwargs.get("stdout") is not None

    tabix_forced = recorder.calls[3][0]
    assert "-f" in tabix_forced


def test_create_input_vcf_passes_hostile_path_as_one_argv_element(tmp_path):
    recorder = _SubprocessRecorder()
    ns = _load_from_source(
        PYPGX_WRAPPER,
        {"run_pypgx_create_input_vcf"},
        _pypgx_namespace(recorder),
    )

    hostile_bam = tmp_path / PAYLOAD_NAME
    hostile_bam.write_bytes(b"")
    out_vcf = tmp_path / "out.vcf.gz"
    out_vcf.write_bytes(b"")
    (tmp_path / "out.vcf.gz.tbi").write_bytes(b"")

    result = ns["run_pypgx_create_input_vcf"](str(hostile_bam), str(out_vcf), "GRCh38")

    assert result["success"] is True
    assert recorder.calls, "no command was executed"
    args, kwargs = recorder.calls[0]
    _assert_argv_is_safe(args, kwargs, PAYLOAD_MARKER)
    assert args[:2] == ["pypgx", "create-input-vcf"]
    # The payload sits behind --bam as a single opaque argument.
    assert args[args.index("--bam") + 1] == str(hostile_bam)


def test_create_input_vcf_fallback_form_is_also_argv(tmp_path):
    """The retry path is a second sink and must be closed too."""
    recorder = _SubprocessRecorder(returncode=1)
    ns = _load_from_source(
        PYPGX_WRAPPER,
        {"run_pypgx_create_input_vcf"},
        _pypgx_namespace(recorder),
    )

    hostile_bam = tmp_path / PAYLOAD_NAME
    hostile_bam.write_bytes(b"")

    ns["run_pypgx_create_input_vcf"](
        str(hostile_bam), str(tmp_path / "out.vcf.gz"), "GRCh38"
    )

    assert len(recorder.calls) == 2, "fallback invocation did not run"
    for args, kwargs in recorder.calls:
        _assert_argv_is_safe(args, kwargs, PAYLOAD_MARKER)


def test_run_ngs_pipeline_is_argv_for_both_path_and_gene(tmp_path):
    """`gene` is unvalidated on the comma-separated branch of /genotype."""
    recorder = _SubprocessRecorder()
    ns = _load_from_source(
        PYPGX_WRAPPER,
        {"run_pypgx"},
        _pypgx_namespace(
            recorder,
            register_process=lambda *a, **k: None,
            unregister_process=lambda *a, **k: None,
            parse_pypgx_results=lambda *a, **k: ("*1/*1", {}),
        ),
    )

    hostile_vcf = str(tmp_path / "x;touch pwned.vcf.gz")
    hostile_gene = "CYP2D6;touch pwned_gene"

    ns["run_pypgx"](hostile_vcf, str(tmp_path), hostile_gene, "GRCh38", job_id="j1")

    assert recorder.calls, "no command was executed"
    args, kwargs = recorder.calls[0]
    assert isinstance(args, list)
    assert kwargs.get("shell") is not True
    assert args[:2] == ["pypgx", "run-ngs-pipeline"]
    # Both attacker-controlled values ride as single argv elements.
    assert hostile_gene in args
    assert args[args.index("--variants") + 1] == hostile_vcf


# ---------------------------------------------------------------------------
# Whole-module guards: these fail if any individual fix is reverted
# ---------------------------------------------------------------------------


def _pypgx_tree():
    return ast.parse(
        PYPGX_WRAPPER.read_text(encoding="utf-8"), filename=str(PYPGX_WRAPPER)
    )


def test_pypgx_wrapper_never_uses_shell_true():
    """Reverting any sink to a shell string fails here."""
    offenders = []
    for node in ast.walk(_pypgx_tree()):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if (
                kw.arg == "shell"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
            ):
                offenders.append(getattr(node, "lineno", "?"))
    assert not offenders, (
        f"{PYPGX_WRAPPER.name} passes shell=True at line(s) {offenders}; "
        "an attacker-controlled filename or gene name reaches a shell there"
    )


def _raw_filename_path_joins(tree: ast.AST):
    """Line numbers of every `<anything> / <upload>.filename` path join.

    Matched on any Name receiver, not just `file`: zarohla has three upload
    fields (`file`, `file1`, `file2`), and a guard written against one of them
    would have passed while the other two stayed open.
    """
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Div):
            continue
        right = node.right
        if (
            isinstance(right, ast.Attribute)
            and right.attr == "filename"
            and isinstance(right.value, ast.Name)
        ):
            offenders.append(getattr(node, "lineno", "?"))
    return offenders


def test_pypgx_wrapper_never_builds_a_path_from_the_client_filename():
    """Reverting either source site to `job_dir / file.filename` fails here."""
    offenders = _raw_filename_path_joins(_pypgx_tree())
    assert not offenders, (
        f"{PYPGX_WRAPPER.name} joins a path with the raw client filename at "
        f"line(s) {offenders}; it must go through safe_upload_name()"
    )


def test_pypgx_upload_endpoints_call_the_sanitiser():
    """Both save sites must route the client name through safe_upload_name."""
    source = PYPGX_WRAPPER.read_text(encoding="utf-8")
    calls = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "safe_upload_name"
    ]
    assert len(calls) >= 2, (
        "expected safe_upload_name() at both upload save sites "
        "(/create-input-vcf and /genotype)"
    )


# ---------------------------------------------------------------------------
# Gene names: path escape and argument injection, neither of which needs a shell
# ---------------------------------------------------------------------------

GENES_JSON = REPO_ROOT / "config" / "genes.json"


@pytest.fixture(scope="module")
def gene_validator():
    ns = _load_from_source(
        PYPGX_WRAPPER,
        {"validate_gene_names", "GENE_NAME_RE"},
        _pypgx_namespace(_SubprocessRecorder()),
    )
    return ns["validate_gene_names"]


@pytest.mark.parametrize(
    "hostile",
    [
        # Path escape: run_pypgx builds Path(output_dir) / f"{gene}-pipeline".
        "../../../../TMP/X",
        "../../etc",
        "..",
        "a/../../b",
        "CYP2D6/../../../TMP",
        "sub/dir",
        "back\\slash",
        # Argument injection: gene is a *positional* pypgx argument.
        "-o",
        "--output",
        "-CYP2D6",
        # Shell shapes, already dead at the sink but rejected here too.
        "CYP2D6;touch pwned",
        "CYP2D6 $(touch pwned)",
        "CYP2D6\ntouch pwned",
        "CYP2D6\x00",
        "",
    ],
)
def test_validate_gene_names_rejects_paths_and_options(gene_validator, hostile):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        gene_validator([hostile])
    assert excinfo.value.status_code == 400


def test_validate_gene_names_reports_every_offender(gene_validator):
    """A valid gene alongside a hostile one must not launder the request."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        gene_validator(["CYP2D6", "../../../../TMP/X", "-o"])
    detail = str(excinfo.value.detail)
    assert "../../../../TMP/X" in detail and "-o" in detail


def test_validate_gene_names_accepts_every_gene_in_the_shipped_catalogue(
    gene_validator,
):
    """The rule must not be stricter than the configuration it has to serve.

    Includes the hyphenated HLA-A/HLA-B/HLA-C and MT-RNR1, and the six names in
    `neuropsychopharmacogenes_panel_missing` that are deliberately absent from
    `sets.all` — which is exactly why SUPPORTED_GENES membership is not the
    check applied at the choke point.
    """
    import json

    config = json.loads(GENES_JSON.read_text(encoding="utf-8"))
    every = {g["name"] for g in config.get("genes", [])}
    for members in config.get("sets", {}).values():
        every.update(members)

    assert len(every) >= 90, "gene catalogue looks truncated; check the fixture"
    assert gene_validator(sorted(every)) == sorted(every)
    # The hyphenated and panel-missing names specifically.
    for name in ("HLA-A", "MT-RNR1", "ANK3", "CACNA1C", "SCN1A"):
        assert name in every
        gene_validator([name])


def test_genotype_validates_genes_at_a_choke_point():
    """Every branch must be covered, so the call sits outside the branch chain.

    Reverting the choke point fails here.
    """
    calls = [
        node
        for node in ast.walk(_pypgx_tree())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "validate_gene_names"
        and any(
            isinstance(a, ast.Name) and a.id == "requested_genes" for a in node.args
        )
    ]
    assert calls, (
        "validate_gene_names(requested_genes) is not called; a gene name can "
        "reach a path join and the pypgx CLI unchecked"
    )


def test_both_raw_text_gene_branches_check_supported_genes():
    """The comma-list branch bypassed the membership check the legacy one had."""
    source = PYPGX_WRAPPER.read_text(encoding="utf-8")
    checks = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Compare)
        and any(isinstance(op, ast.NotIn) for op in node.ops)
        and any(
            isinstance(c, ast.Name) and c.id == "SUPPORTED_GENES"
            for c in node.comparators
        )
    ]
    assert len(checks) >= 2, (
        "expected a SUPPORTED_GENES membership check on both branches that read "
        f"raw request text, found {len(checks)}"
    )


# ---------------------------------------------------------------------------
# app/api/utils/file_processor.py — glob containment on the Nextflow input
# ---------------------------------------------------------------------------


def test_file_processor_sanitiser_strips_glob_and_shell_metacharacters():
    from app.api.utils.file_processor import safe_upload_basename

    for hostile in (
        PAYLOAD_NAME,
        "*.vcf",
        "?.vcf",
        "[a-z].vcf",
        "{a,b}.vcf",
        "$(touch pwned).vcf",
        "`touch pwned`.vcf",
        "a|b&c.vcf",
    ):
        cleaned = safe_upload_basename(hostile)
        # secure_filename's own guarantee, restated as the property we rely on.
        assert not (
            set(cleaned)
            - set(
                "abcdefghijklmnopqrstuvwxyz"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                "0123456789._-"
            )
        ), f"{hostile!r} -> {cleaned!r}"
        for meta in "*?[]{}$`|&;<>() ":
            assert meta not in cleaned, f"{meta!r} survived {hostile!r} -> {cleaned!r}"


def test_file_processor_sanitiser_never_returns_empty():
    """`..` and `...` reduce to nothing; a generated name has to take over."""
    from app.api.utils.file_processor import safe_upload_basename

    for degenerate in ("", None, "..", "...", "/", "///"):
        assert safe_upload_basename(degenerate), f"empty name for {degenerate!r}"


def test_file_processor_sanitiser_preserves_ordinary_names():
    from app.api.utils.file_processor import safe_upload_basename

    assert safe_upload_basename("sample.vcf") == "sample.vcf"
    assert safe_upload_basename("pharmcat.example.vcf") == "pharmcat.example.vcf"
    assert safe_upload_basename("sample.vcf.gz") == "sample.vcf.gz"


@pytest.mark.asyncio
async def test_process_files_writes_a_glob_free_path(tmp_path):
    """End to end: the path handed to Nextflow carries no glob metacharacter.

    `Channel.fromPath(params.input)` in pipelines/pgx/main.nf globs, so a `*` in
    this path makes the run ingest every other upload in the directory.
    """
    from app.api.utils.file_processor import FileProcessor

    class _FakeUpload:
        def __init__(self, filename, content=b"##fileformat=VCFv4.2\n"):
            self.filename = filename
            self._content = content

        async def read(self):
            return self._content

    processor = FileProcessor()
    processor.temp_dir = tmp_path

    # The file is written before any analysis happens, so the result of the
    # (inevitably failing) analysis of this stub content is irrelevant here.
    await processor.process_files([_FakeUpload("*.vcf")])

    written = list(tmp_path.iterdir())
    assert written, "nothing was written to the upload directory"
    for path in written:
        for meta in "*?[]{};$`|&<> ":
            assert meta not in path.name, f"{meta!r} survived into {path.name!r}"
        assert path.name.startswith("upload_")
