"""Which image this container is, and which code it is actually running.

Those are two different questions here, and the stack answers them from two different
places. `compose.yml` mounts the repository over `/app`, so the application code inside
`zaropgx-app:<tag>` -- which `Dockerfile` does copy in, and which a release publishes and
verifies by digest -- is shadowed on every install by whatever the checkout holds. The
image fixes the dependencies; the checkout decides the behavior.

That is a deliberate arrangement with a real cost (docs/developer/decisions/
0001-runtime-code-delivery.md), and the cheapest half of the answer is to stop it being
silent: write the image's identity at build time somewhere the mount cannot hide, read the
checkout's commit at runtime, and say both. A mismatch is not an error -- it is the normal
state of a machine someone is developing on -- so it is reported, not refused.

**Written outside `/app` on purpose.** A build stamp inside the application directory is
shadowed by the very mount it exists to detect, and would report the checkout's stamp as
the image's.

The commit is read out of `.git` directly rather than by running git, which the image does
not carry: `HEAD` names a ref, the ref is a file under `.git/refs` or a line in
`.git/packed-refs`, and a detached HEAD is the SHA itself. A worktree's `.git` is a file
pointing at the real directory, which is how every branch in `~/zaropgx-wt` looks.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

#: Where Dockerfile writes the image's own identity. Outside /app, see the module note.
BUILD_STAMP = Path(os.getenv("ZAROPGX_BUILD_STAMP", "/opt/zaropgx/build.json"))

#: The checkout the application is running from.
CODE_ROOT = Path(os.getenv("ZAROPGX_CODE_ROOT", "/app"))

UNKNOWN = "unknown"


def _read_stamp(path: Path) -> Dict[str, str]:
    """The image's build stamp, or {} where there is none.

    None is normal: a developer running uvicorn outside a container has no image, and
    neither has an image built before this stamp existed.
    """
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("build stamp at %s unreadable: %s", path, exc)
        return {}
    if not isinstance(loaded, dict):
        logger.warning("build stamp at %s is not an object", path)
        return {}
    return {str(k): str(v) for k, v in loaded.items() if v not in (None, "")}


def _git_dir(root: Path) -> Optional[Path]:
    """`<root>/.git`, following the one-line indirection a worktree uses."""
    dot_git = root / ".git"
    try:
        if dot_git.is_dir():
            return dot_git
        text = dot_git.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    pointed = Path(text.split(":", 1)[1].strip())
    return pointed if pointed.is_absolute() else (root / pointed)


def _common_dir(git_dir: Path) -> Path:
    """Where the refs live.

    A linked worktree has its own HEAD but shares the repository's refs, and says so in
    a `commondir` file beside that HEAD. Reading refs from the worktree's own directory
    finds nothing -- which is exactly the case this module exists for, since a developer
    checking a branch out is who sees a mismatch.
    """
    try:
        pointed = Path((git_dir / "commondir").read_text(encoding="utf-8").strip())
    except OSError:
        return git_dir
    return pointed if pointed.is_absolute() else (git_dir / pointed).resolve()


def _resolve(ref: str, *where: Path) -> Optional[str]:
    """A ref's commit, as a loose file or a line in packed-refs, in any of `where`."""
    for base in where:
        try:
            return (base / ref).read_text(encoding="utf-8").strip() or None
        except OSError:
            pass
    for base in where:
        try:
            packed = (base / "packed-refs").read_text(encoding="utf-8")
        except OSError:
            continue
        for line in packed.splitlines():
            if line.startswith(("#", "^")):
                continue
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[1].strip() == ref:
                return parts[0]
    return None


def commit_of(root: Path = CODE_ROOT) -> Optional[str]:
    """The commit the code at `root` is on, or None where it is not a checkout.

    Not a checkout is normal too: an image running its own copy has no `.git`, which is
    the whole point of the arrangement the ADR proposes.
    """
    git_dir = _git_dir(root)
    if git_dir is None:
        return None
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not head.startswith("ref:"):
        return head or None  # detached: HEAD is the commit
    ref = head.split(":", 1)[1].strip()
    return _resolve(ref, git_dir, _common_dir(git_dir))


def _known(value: object) -> Optional[str]:
    """A commit, or None for anything that is not one.

    The sentinel has two spellings and both mean the same thing: absent, and the
    literal word "unknown". `ARG ZAROPGX_COMMIT=unknown` in the app Dockerfile is the
    second one, so an image built without that argument carries `"commit": "unknown"`
    in its stamp -- a string, and therefore truthy. Comparing it against a real commit
    yields "these differ", and the page then tells every reader it is serving code its
    image does not have. Which is the one claim this whole mechanism exists to avoid
    making falsely.
    """
    text = str(value or "").strip()
    return None if not text or text.lower() == UNKNOWN else text


def identity(
    stamp_path: Path = BUILD_STAMP, root: Path = CODE_ROOT, version: str = UNKNOWN
) -> Dict[str, object]:
    """What this process is: the image it was built as, the code it is running, and
    whether those agree.

    `agrees` is True when both commits are known and equal, False when both are known
    and differ, and None when one of them is not known -- which is not a disagreement
    and must not be reported as one.
    """
    stamp = _read_stamp(stamp_path)
    running = _known(commit_of(root))
    built = _known(stamp.get("commit"))
    agrees: Optional[bool] = None
    if built and running:
        agrees = built.startswith(running) or running.startswith(built)
    return {
        "version": version,
        "image": {
            "tag": stamp.get("tag", UNKNOWN),
            "commit": built or UNKNOWN,
            "built": stamp.get("built", UNKNOWN),
        },
        "code": {
            "commit": running or UNKNOWN,
            "from": "checkout" if running else "image",
        },
        "agrees": agrees,
    }


def disagreement(found: Dict[str, object]) -> Optional[str]:
    """One line for the log when the running code is not the image's, else None."""
    if found.get("agrees") is not False:
        return None
    image, code = found["image"], found["code"]  # type: ignore[index]
    return (
        f"running the checkout, not the image: image {image['tag']} was built from "  # type: ignore[index]
        f"{str(image['commit'])[:12]}, the mounted code is {str(code['commit'])[:12]}. "  # type: ignore[index]
        "compose.yml mounts ./ over /app, so the code decides behavior and the tag "
        "describes the dependencies only."
    )
