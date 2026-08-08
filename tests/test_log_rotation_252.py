"""
BACKLOG item 252 (remaining half): bound growth of the PharmCAT and PyPGx
progress logs.

docker/pharmcat/pharmcat.py and docker/pypgx/pypgx_wrapper.py each wire up
logging.basicConfig() with a plain logging.FileHandler pointed at a log file
on the shared /data volume, so the file grows without limit for the life of
the container. The fix swaps in logging.handlers.RotatingFileHandler with a
bounded maxBytes and a non-zero backupCount.

Why AST instead of a direct import or a text grep:
Both modules are Docker container entry points. Importing either directly
under the host interpreter fails before it ever reaches the logging setup --
`import psutil` (docker/pharmcat/pharmcat.py:28, docker/pypgx/pypgx_wrapper.py:21)
raises ModuleNotFoundError on the host venv, which does not install the
container's runtime dependencies. (The modules would also fail afterwards:
they `sys.path.append('/job-client')` for a helper that only exists inside
the container image, and logging.FileHandler(...) is constructed at import
time against /data, which does not exist on this host.) A direct import is
therefore not possible, so this test parses the module source with `ast` and
asserts on the actual handler-construction Call node (function name, and the
maxBytes/backupCount keyword values) rather than skipping the test or doing a
plain-text substring grep, which could not distinguish a real bounded value
from a stray comment or a docstring mentioning "RotatingFileHandler".
"""

import ast
from pathlib import Path

import pytest

MODULES = {
    "pharmcat": {
        "path": Path("docker/pharmcat/pharmcat.py"),
        "log_file": "/data/pharmcat_progress.log",
    },
    "pypgx_wrapper": {
        "path": Path("docker/pypgx/pypgx_wrapper.py"),
        "log_file": "/data/pypgx_progress.log",
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


def _eval_int_literal(node):
    """Best-effort evaluation of an int-valued literal node.

    Handles plain int constants as well as simple constant arithmetic such
    as `10 * 1024 * 1024`, a common, self-documenting way to spell a byte
    size, without resorting to a general-purpose eval(). Returns None if the
    node is not a recognizable int-literal expression.
    """
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    ):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        operand = _eval_int_literal(node.operand)
        return None if operand is None else -operand
    if isinstance(node, ast.BinOp) and isinstance(
        node.op, (ast.Mult, ast.Add, ast.Sub)
    ):
        left = _eval_int_literal(node.left)
        right = _eval_int_literal(node.right)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Add):
            return left + right
        return left - right
    return None


@pytest.mark.parametrize("module_name", sorted(MODULES))
def test_progress_log_uses_bounded_rotating_file_handler(module_name):
    info = MODULES[module_name]
    source = info["path"].read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(info["path"]))

    # The unbounded plain FileHandler must be gone from the progress-log wiring.
    plain_handlers = _find_calls_by_name(tree, "FileHandler")
    assert not plain_handlers, (
        f"{info['path']} still constructs a plain logging.FileHandler; "
        "expected logging.handlers.RotatingFileHandler instead"
    )

    rotating_calls = _find_calls_by_name(tree, "RotatingFileHandler")
    assert rotating_calls, (
        f"{info['path']} does not construct a RotatingFileHandler for the "
        "progress log"
    )
    assert len(rotating_calls) == 1, (
        f"{info['path']} constructs {len(rotating_calls)} RotatingFileHandlers; "
        "expected exactly 1"
    )
    call = rotating_calls[0]

    # Same target file as before -- this change is only about bounding growth.
    if call.args:
        filename_node = call.args[0]
    else:
        filename_node = _kwarg_value(call, "filename")
    assert isinstance(
        filename_node, ast.Constant
    ), f"{info['path']}: RotatingFileHandler filename must be a literal"
    assert filename_node.value == info["log_file"]

    max_bytes_node = _kwarg_value(call, "maxBytes")
    backup_count_node = _kwarg_value(call, "backupCount")

    max_bytes = _eval_int_literal(max_bytes_node)
    assert max_bytes is not None, (
        f"{info['path']}: maxBytes must be an int literal (or simple constant "
        "arithmetic on int literals)"
    )
    assert (
        max_bytes > 0
    ), f"{info['path']}: maxBytes must be bounded (> 0), got {max_bytes}"

    backup_count = _eval_int_literal(backup_count_node)
    assert backup_count is not None, (
        f"{info['path']}: backupCount must be an int literal (or simple constant "
        "arithmetic on int literals)"
    )
    assert (
        backup_count > 0
    ), f"{info['path']}: backupCount must be > 0, got {backup_count}"
