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
