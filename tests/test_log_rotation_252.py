"""BACKLOG item 252: bound the growth of every sidecar log on the shared volume.

Each ZaroPGx sidecar writes a progress log onto the ``./data`` bind mount that
is shared with the app container and with the user's own filesystem. An
unrotated handler there grows for the life of the container and eventually
takes the host disk with it.

**One rule, five modules.** The item was closed three different ways by three
different changes -- 5 MiB x 3 at DEBUG in gatk-api, 10 MB x 5 at INFO in
nextflow/pharmcat/pypgx, nothing at all in zarohla -- and two different failure
behaviours: gatk-api and nextflow guarded the handler and degraded to console,
while pharmcat and pypgx built theirs inline in ``basicConfig`` and so *died at
import* when ``/data`` was absent. This module now pins the converged shape:

  * exactly one ``RotatingFileHandler`` construction per module,
  * ``maxBytes``/``backupCount`` taken from module-level ``LOG_MAX_BYTES`` /
    ``LOG_BACKUP_COUNT`` constants that resolve to the same values everywhere,
  * built inside a ``try`` that catches ``OSError`` and returns ``None``
    instead of re-raising, so a missing volume degrades to console logging,
  * a declared progress-log path pointing at the shared volume,
  * no plain ``logging.FileHandler`` anywhere.

Why the guard and not a fatal error: the handler is not the job. If ``/data``
really is unmounted, the first read or write of pipeline data fails with an
error that names the real operation, whereas raising inside logging setup
reports the wrong cause -- and does it *before* ``logger`` exists, so under
``restart: unless-stopped`` the container crash-loops saying nothing. The
import-time raise is also precisely why pharmcat.py and pypgx_wrapper.py can
only be tested by parsing their source (see below); gatk_api.py, which
degrades, is imported for real by tests/test_gatk_api_no_mock_bam.py and gets
runtime coverage as a result.

Why AST instead of a direct import or a text grep:
All five modules are Docker container entry points and none is importable
under the host interpreter as-is -- ``import psutil`` (pharmcat.py,
pypgx_wrapper.py, zarohla/app.py) raises ModuleNotFoundError on the dev venv,
and they ``sys.path.append('/job-client')`` for a helper that only exists
inside the image. So this test parses each module's source and asserts on the
actual handler-construction Call node rather than skipping, or doing a
plain-text substring grep that could not distinguish a real bounded value from
a stray comment mentioning "RotatingFileHandler".

Complementary runtime coverage, deliberately not duplicated here:
  * tests/test_gatk_api_no_mock_bam.py imports gatk_api.py with stubbed
    psutil/job_client and asserts the *live* handler bounds, that rotation
    actually caps the file on disk, and that an unopenable destination yields
    None rather than raising.
"""

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# The one set of bounds every sidecar uses. 10 MiB x 5 backups caps each
# destination at 60 MiB. Deliberately generous rather than frugal: these logs
# sit on a volume already holding tens of gigabytes of BAM/CRAM, a whole-genome
# run is long and gatk-api logs it at DEBUG, and the failures they exist to
# explain are read hours after the fact. The item is about *unbounded* growth.
EXPECTED_MAX_BYTES = 10 * 1024 * 1024
EXPECTED_BACKUP_COUNT = 5

MODULES = {
    "gatk_api": {
        "path": Path("docker/gatk-api/gatk_api.py"),
        # gatk-api opens two destinations through the one helper.
        "log_files": ["gatk_progress.log", "/var/log/gatk_api.log"],
    },
    "nextflow_runner": {
        "path": Path("docker/nextflow/runner.py"),
        "log_files": ["/data/nextflow_progress.log"],
    },
    "pharmcat": {
        "path": Path("docker/pharmcat/pharmcat.py"),
        "log_files": ["/data/pharmcat_progress.log"],
    },
    "pypgx_wrapper": {
        "path": Path("docker/pypgx/pypgx_wrapper.py"),
        "log_files": ["/data/pypgx_progress.log"],
    },
    "zarohla": {
        "path": Path("docker/zarohla/app.py"),
        "log_files": ["zarohla_progress.log"],
    },
}

# Sidecars that run more than one process, and therefore cannot share one
# RotatingFileHandler destination. Kept as data so that adding such a service is
# a deliberate edit rather than an oversight.
#
# Empty on purpose. zarohla was the one entry, on `gunicorn --workers 2`, and it
# carried the pid in its log filename to survive that. It now starts one worker:
# its `running_processes` registry is a module-level dict, so a POST /cancel
# accepted by the worker that did not start the job found nothing to kill and
# still answered success -- roughly half of all cancels. `--preload` is not a fix
# (it forks after import and each worker then mutates its own copy), and a
# genuinely shared registry is a lot of machinery for a service whose /call-hla
# is `async def` awaiting a subprocess, so one worker already serves many
# concurrent typings. See docker/zarohla/Dockerfile for the full argument.
MULTIPROCESS_MODULES: dict = {}

# Every sidecar's Dockerfile, checked against MULTIPROCESS_MODULES in both
# directions by test_the_multiprocess_list_still_matches_the_dockerfiles.
SIDECAR_DOCKERFILES = {
    "gatk_api": Path("docker/gatk-api/Dockerfile.gatk-api"),
    "nextflow_runner": Path("docker/nextflow/Dockerfile.nextflow"),
    "pharmcat": Path("docker/pharmcat/Dockerfile"),
    "pypgx_wrapper": Path("docker/pypgx/Dockerfile.pypgx"),
    "zarohla": Path("docker/zarohla/Dockerfile"),
}

# Every spelling gunicorn accepts, so the rule does not depend on how the CMD is
# written: `"--workers", "2"` (exec form), `--workers 2`, `--workers=2`, `-w 2`.
# An earlier cut matched only the first two, which would have read `--workers=2`
# as no workers at all -- the silent-pass direction. `--worker-class` must not
# match, hence the trailing `s` and the boundaries around the short form.
WORKERS_RE = re.compile(
    r'(?:--workers|(?<![\w-])-w(?![\w-]))["\'\s]*[=,]?["\'\s]*(\d+)'
)


def _instructions(dockerfile_text: str) -> str:
    """`dockerfile_text` with comment lines removed.

    Every rule below reads this rather than the raw file. The comments matter:
    the CMD in docker/zarohla/Dockerfile is preceded by a long explanation of
    why it is one worker and not two, and why `--preload` would not have helped.
    A rule that read prose would report the service as multi-process, and would
    flag a `--preload` that exists only in a sentence saying not to use one.
    """
    return "\n".join(
        line
        for line in dockerfile_text.splitlines()
        if not line.lstrip().startswith("#")
    )


def _worker_counts(dockerfile_text: str):
    """Every `--workers N` in the actual instructions, comments excluded."""
    return [int(n) for n in WORKERS_RE.findall(_instructions(dockerfile_text))]


def _handler_name(call: ast.Call) -> str:
    """Resolve the (possibly dotted) callee name of a Call node.

    Matches both `RotatingFileHandler(...)` (imported via
    `from logging.handlers import RotatingFileHandler`) and
    `logging.handlers.RotatingFileHandler(...)` (attribute-chain style).
    """
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _find_calls_by_name(tree: ast.AST, name: str):
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _handler_name(node) == name
    ]


def _kwarg_value(call: ast.Call, kwarg_name: str):
    for kw in call.keywords:
        if kw.arg == kwarg_name:
            return kw.value
    return None


def _eval_int_literal(node, constants=None):
    """Best-effort evaluation of an int-valued expression.

    Handles plain int constants, simple constant arithmetic such as
    `10 * 1024 * 1024` (a self-documenting way to spell a byte size), and --
    when `constants` is supplied -- references to module-level names bound to
    such an expression. Resolving names is what lets one rule cover a module
    that inlines the number and a module that hoists it into a shared constant,
    without falling back to a general-purpose eval(). Returns None if the node
    is not a recognizable int-valued expression.
    """
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    ):
        return node.value
    if isinstance(node, ast.Name) and constants and node.id in constants:
        return _eval_int_literal(constants[node.id], None)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        operand = _eval_int_literal(node.operand, constants)
        return None if operand is None else -operand
    if isinstance(node, ast.BinOp) and isinstance(
        node.op, (ast.Mult, ast.Add, ast.Sub)
    ):
        left = _eval_int_literal(node.left, constants)
        right = _eval_int_literal(node.right, constants)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Add):
            return left + right
        return left - right
    return None


def _module_constants(tree: ast.Module) -> dict:
    """Map module-level `NAME = <expr>` bindings to their value nodes."""
    constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value
    return constants


def _enclosing_try(tree: ast.AST, call: ast.Call):
    """Return the innermost ast.Try whose *body* contains `call`, or None."""
    best = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        if any(call is inner for stmt in node.body for inner in ast.walk(stmt)):
            best = node
    return best


def _catches_oserror(handler: ast.ExceptHandler) -> bool:
    names = []
    if isinstance(handler.type, ast.Name):
        names = [handler.type.id]
    elif isinstance(handler.type, ast.Tuple):
        names = [e.id for e in handler.type.elts if isinstance(e, ast.Name)]
    elif handler.type is None:
        return True  # bare except catches it too, though nothing uses one
    return any(n in {"OSError", "IOError", "Exception"} for n in names)


@pytest.fixture(scope="module", params=sorted(MODULES))
def module(request):
    info = MODULES[request.param]
    path = REPO_ROOT / info["path"]
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    return {
        "name": request.param,
        "rel": info["path"],
        "log_files": info["log_files"],
        "source": source,
        "tree": tree,
        "constants": _module_constants(tree),
    }


def test_no_plain_file_handler(module):
    """The unbounded plain FileHandler must be gone from the logging wiring."""
    plain = _find_calls_by_name(module["tree"], "FileHandler")
    assert not plain, (
        f"{module['rel']} still constructs a plain logging.FileHandler; "
        "expected logging.handlers.RotatingFileHandler instead"
    )


def test_exactly_one_rotating_handler_is_constructed(module):
    """One construction site, reused for every destination the module opens.

    gatk-api opens two files; both go through the same helper, so "one call
    node" is the rule rather than "one destination".
    """
    calls = _find_calls_by_name(module["tree"], "RotatingFileHandler")
    assert calls, (
        f"{module['rel']} does not construct a RotatingFileHandler for its "
        "progress log"
    )
    assert len(calls) == 1, (
        f"{module['rel']} constructs {len(calls)} RotatingFileHandlers; "
        "expected exactly 1, built by _bounded_file_handler()"
    )


def test_bounds_come_from_the_shared_constants(module):
    """Every sidecar caps its log at the same size, via the same constant names.

    Asserted on the resolved value, not just ">0": divergent-but-bounded values
    are what this convergence removed, and a test that only checks for
    boundedness would let them straight back in.
    """
    call = _find_calls_by_name(module["tree"], "RotatingFileHandler")[0]
    constants = module["constants"]

    max_bytes_node = _kwarg_value(call, "maxBytes")
    assert isinstance(max_bytes_node, ast.Name) and max_bytes_node.id == (
        "LOG_MAX_BYTES"
    ), f"{module['rel']}: maxBytes must be the module's LOG_MAX_BYTES constant"

    backup_node = _kwarg_value(call, "backupCount")
    assert isinstance(backup_node, ast.Name) and backup_node.id == (
        "LOG_BACKUP_COUNT"
    ), f"{module['rel']}: backupCount must be the module's LOG_BACKUP_COUNT constant"

    max_bytes = _eval_int_literal(max_bytes_node, constants)
    assert max_bytes == EXPECTED_MAX_BYTES, (
        f"{module['rel']}: LOG_MAX_BYTES is {max_bytes}, expected "
        f"{EXPECTED_MAX_BYTES} (10 MiB) like every other sidecar"
    )

    backup_count = _eval_int_literal(backup_node, constants)
    assert backup_count == EXPECTED_BACKUP_COUNT, (
        f"{module['rel']}: LOG_BACKUP_COUNT is {backup_count}, expected "
        f"{EXPECTED_BACKUP_COUNT} like every other sidecar"
    )


def test_handler_construction_degrades_instead_of_raising(module):
    """A missing shared volume must not kill the container inside logging setup.

    This is the half pharmcat.py and pypgx_wrapper.py were missing: an inline
    handler in basicConfig() raises before `logger` exists, so the container
    dies without ever saying why.
    """
    tree = module["tree"]
    call = _find_calls_by_name(tree, "RotatingFileHandler")[0]

    guard = _enclosing_try(tree, call)
    assert guard is not None, (
        f"{module['rel']}: RotatingFileHandler is constructed unguarded; a missing "
        "/data would raise at import time"
    )
    assert any(_catches_oserror(h) for h in guard.handlers), (
        f"{module['rel']}: the guard around RotatingFileHandler does not catch "
        "OSError"
    )
    for handler in guard.handlers:
        if not _catches_oserror(handler):
            continue
        raises = [n for n in ast.walk(handler) if isinstance(n, ast.Raise)]
        assert not raises, (
            f"{module['rel']}: the OSError guard re-raises, so the module still "
            "dies at import when the volume is absent"
        )


def test_helper_and_warning_have_the_agreed_names(module):
    """The five copies must stay recognisably the same block.

    They are duplicated rather than imported (five separate images; the logging
    block runs before sys.path is extended with the shared /job-client dir), so
    the only thing keeping them from drifting is this assertion.
    """
    functions = {
        node.name
        for node in module["tree"].body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for expected in ("_bounded_file_handler", "_warn_about_unopened_logs"):
        assert expected in functions, (
            f"{module['rel']} is missing {expected}(); the bounded-logging block "
            "must be the same shape in every sidecar"
        )
    assert "_log_file_errors" in module["constants"], (
        f"{module['rel']} does not collect unopened destinations in " "_log_file_errors"
    )


def test_the_declared_log_path_is_on_the_shared_volume(module):
    """The bound is only useful if it is applied to the file that actually grows."""
    for expected in module["log_files"]:
        assert expected in module["source"], (
            f"{module['rel']} no longer declares its progress log at {expected!r}; "
            "the rotation bound may be pointed somewhere else"
        )


def test_only_multiprocess_sidecars_use_a_per_worker_filename(module):
    """A shared RotatingFileHandler across processes silently deletes logs.

    When worker A rolls over it renames the file out from under worker B, which
    goes on appending to the renamed inode. B's lines migrate down the .1/.2/...
    chain and are unlinked once they fall past backupCount -- silently -- while
    the advertised ceiling stops holding in the meantime. So a service that runs
    more than one process must give each one its own destination, and a service
    that does not must NOT (a per-pid name there would only scatter the log).
    """
    uses_pid = "getpid()" in module["source"]
    expected = module["name"] in MULTIPROCESS_MODULES
    assert uses_pid == expected, (
        f"{module['rel']}: per-pid log filename is {'missing' if expected else 'present'}; "
        f"this service is {'multi' if expected else 'single'}-process"
    )


@pytest.mark.parametrize("name", sorted(SIDECAR_DOCKERFILES))
def test_the_multiprocess_list_still_matches_the_dockerfiles(name):
    """The rule above is only right while the process model is what it says.

    Checked in both directions and over every sidecar, not just the declared
    multi-process ones: adding workers anywhere must force a decision here
    rather than quietly invalidating the reasoning. When zarohla was the one
    entry this was parametrised over MULTIPROCESS_MODULES, which would have
    become a no-op the moment that dict emptied.
    """
    rel = SIDECAR_DOCKERFILES[name]
    dockerfile = (REPO_ROOT / rel).read_text(encoding="utf-8")
    workers = _worker_counts(dockerfile)
    declared = name in MULTIPROCESS_MODULES

    assert any(n > 1 for n in workers) == declared, (
        f"{rel} starts {workers or 1} worker(s) but is "
        f"{'not ' if not declared else ''}listed in MULTIPROCESS_MODULES. A "
        "service that runs several processes needs a per-pid log filename; one "
        "that does not must not have one (it would only scatter the log)."
    )
    # Unconditional, for every sidecar, not just the declared multi-process ones.
    # --preload forks after import: with several workers it makes them share one
    # handler, which is the same unsafe sharing the per-pid filename exists to
    # avoid, and it does nothing for mutable state mutated after the fork (see
    # zarohla's `running_processes`). No sidecar here has a use for it. Guarding
    # this behind `if declared` would have silently switched the check off the
    # moment MULTIPROCESS_MODULES emptied.
    assert "--preload" not in _instructions(dockerfile)


def test_zarohla_runs_one_worker_so_its_cancel_registry_is_visible():
    """Not a logging rule -- the reason the logging rule changed.

    `running_processes` in docker/zarohla/app.py is a module-level dict and
    /cancel can only kill a pid it finds there. Under `gunicorn --workers 2` the
    OS picked which worker accepted the cancel, so about half of them found an
    empty registry and answered success while OptiType kept running.
    """
    dockerfile = (REPO_ROOT / SIDECAR_DOCKERFILES["zarohla"]).read_text(
        encoding="utf-8"
    )
    assert _worker_counts(dockerfile) == [1], (
        "docker/zarohla/Dockerfile no longer starts exactly one worker; "
        "running_processes must become genuinely shared state first (a module "
        "global is not that, and neither is --preload)"
    )

    # And the premise: the registry really is a module-level dict, i.e. genuinely
    # per-process state rather than something already shared. Asserted on the AST
    # so this does not pass on a mention in a comment.
    app_path = REPO_ROOT / MODULES["zarohla"]["path"]
    tree = ast.parse(app_path.read_text(encoding="utf-8"), filename=str(app_path))
    module_level = [
        node
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (
            [node.target] if isinstance(node, ast.AnnAssign) else node.targets
        )
        if isinstance(target, ast.Name) and target.id == "running_processes"
    ]
    assert module_level, (
        "docker/zarohla/app.py no longer binds `running_processes` at module "
        "level; if it became shared state, this test's reasoning is stale"
    )


def test_single_process_sidecars_are_still_single_process():
    """Guard the other half: a new `--workers N` must not slip in unnoticed."""
    others = {
        "gatk_api": "docker/gatk-api/Dockerfile.gatk-api",
        "nextflow_runner": "docker/nextflow/Dockerfile.nextflow",
        "pharmcat": "docker/pharmcat/Dockerfile",
        "pypgx_wrapper": "docker/pypgx/Dockerfile.pypgx",
    }
    for name, rel in others.items():
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "--workers" not in text, (
            f"{rel} now starts multiple workers, but {name} still writes one "
            "shared RotatingFileHandler destination -- see "
            "test_only_multiprocess_sidecars_use_a_per_worker_filename"
        )


def test_every_sidecar_agrees(request):
    """Cross-module guard: no module may be dropped from the rule quietly."""
    seen = {}
    for name, info in MODULES.items():
        tree = ast.parse(
            (REPO_ROOT / info["path"]).read_text(encoding="utf-8"),
            filename=str(info["path"]),
        )
        constants = _module_constants(tree)
        seen[name] = (
            _eval_int_literal(constants.get("LOG_MAX_BYTES"), constants),
            _eval_int_literal(constants.get("LOG_BACKUP_COUNT"), constants),
        )
    assert len(set(seen.values())) == 1, f"sidecar log bounds have diverged: {seen}"
    assert set(seen.values()) == {(EXPECTED_MAX_BYTES, EXPECTED_BACKUP_COUNT)}
