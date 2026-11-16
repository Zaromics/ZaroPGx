"""Do the published images actually contain the code this release changed?

    python scripts/verify_image_contents.py --tag 0.4.4 --since v0.4.3 --rev v0.4.4
    python scripts/verify_image_contents.py --tag 0.4.4 --files app/utils/liftover_chain.py

Seven of the eight images are built from the repository root, and five of them copy files
out of `app/` into themselves. So an image is only as current as the tree it was built in
-- and a build run in a checkout still on the previous release publishes the previous
release's code inside a correctly tagged image. Nothing else in the release catches that:
the tag is right, the push succeeds, and the registry check finds all eight manifests.

Not hypothetical. v0.4.3 changed `app/utils/liftover_chain.py`, which `mtdna` carries, and
`mtdna` is the one image built by hand rather than by `docker buildx bake`, from whatever
directory the person happens to be standing in. It was built from the tag and checked by
hand afterwards; this is that check, for every image and every file the release touched.

It reads each service's Dockerfile to learn which paths it copies and where they land,
hashes those files at the revision being released, hashes them inside the image, and
compares. Three things it has to get right, each of which would otherwise produce a
confident wrong answer:

- **The build context is per service.** `genome-downloader` builds from
  `docker/genome-downloader`, so its `COPY . /app/` carries that directory and nothing
  else. Reading every context as the repository root reports every file in it as missing.
- **`.dockerignore` decides what is in the context at all.** `.env.*` never reaches an
  image, and a file the context excludes is reported as excluded, not as missing.
- **The revision, not the working tree.** A release is checked from a checkout that may
  have moved on; `--rev v0.4.3` hashes the tag's blobs, which is what was built.

Exit status is 0 when every image carries what it should. Run it after the bake and the
mtdna build, before anything updates against the tag.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import posixpath
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parents[1]
REGISTRY_PREFIX = "zaromicsresearch/zaropgx-"


# --------------------------------------------------------------------------
# What is built, and from where
# --------------------------------------------------------------------------


class Service:
    """One buildable service, as compose.yml describes it."""

    def __init__(self, name: str, context: str, dockerfile: str):
        self.name = name
        self.context = context
        self.dockerfile = dockerfile

    @property
    def context_dir(self) -> Path:
        return (REPO / self.context).resolve()

    @property
    def dockerfile_path(self) -> Path:
        candidate = REPO / self.dockerfile
        return candidate if candidate.exists() else self.context_dir / self.dockerfile

    def image(self, tag: str) -> str:
        return f"{REGISTRY_PREFIX}{self.name}:{tag}"

    def inside(self, repo_path: str) -> Optional[str]:
        """`repo_path` as the build context sees it, or None if outside the context."""
        try:
            return (REPO / repo_path).resolve().relative_to(self.context_dir).as_posix()
        except ValueError:
            return None


def services(compose: Path = REPO / "compose.yml") -> List[Service]:
    """Every service compose.yml can build into a published image.

    compose.yml rather than docker-bake.hcl, because mtdna is deliberately not a bake
    target and is exactly the one this check exists for.
    """
    import yaml

    loaded = yaml.safe_load(compose.read_text(encoding="utf-8"))
    out = []
    for name, service in (loaded.get("services") or {}).items():
        build, image = service.get("build"), str(service.get("image") or "")
        if not isinstance(build, dict) or not image.startswith(REGISTRY_PREFIX):
            continue
        out.append(
            Service(
                name, build.get("context", "."), build.get("dockerfile", "Dockerfile")
            )
        )
    return out


# --------------------------------------------------------------------------
# What a Dockerfile copies, and where it lands
# --------------------------------------------------------------------------


class Unparsed(RuntimeError):
    """A Dockerfile instruction this checker cannot read, so it must not report."""


def copies(dockerfile: str) -> List[Tuple[List[str], str]]:
    """The (sources, destination) of every COPY that takes files from the build context.

    `--from=` copies come from another stage or image, not from this tree, so they say
    nothing about whether it reached the image. A destination is resolved against the
    WORKDIR in force, which is how `COPY x.py .` lands in /app rather than in /.
    """
    out: List[Tuple[List[str], str]] = []
    workdir = "/"
    pending = ""
    for raw in dockerfile.splitlines():
        line = pending + raw.rstrip()
        pending = ""
        if line.endswith("\\"):
            pending = line[:-1]
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        head, _, rest = stripped.partition(" ")
        word = head.upper()
        # A form this cannot read must stop the check, not be skipped: a verifier that
        # quietly ignores an instruction reports "all current" for files it never
        # looked at, which is worse than not running it. Neither form is used in this
        # repository today; both are legal Dockerfile.
        if word == "COPY" and rest.lstrip().startswith("["):
            raise Unparsed(
                f"JSON-form COPY is not read by this checker: {stripped[:70]}"
            )
        if word == "ADD" and not re.match(
            r"^(--\S+\s+)*(https?://|git@|github\.com)", rest.strip()
        ):
            raise Unparsed(
                f"ADD from the build context is not read by this checker: {stripped[:70]}"
            )
        if word == "WORKDIR":
            target = rest.strip()
            workdir = (
                target if target.startswith("/") else f"{workdir.rstrip('/')}/{target}"
            )
        elif word == "COPY":
            parts = shlex.split(rest)
            if any(p.startswith("--from=") for p in parts):
                continue
            parts = [p for p in parts if not p.startswith("--")]
            if len(parts) < 2:
                continue
            sources, destination = parts[:-1], parts[-1]
            # Docker reads the destination as a directory when it ends in a slash, when
            # it is the working directory itself, or whenever there is more than one
            # source. `COPY app.py .` under WORKDIR /app lands /app/app.py, not /app --
            # getting that wrong hashes a directory and calls every file missing.
            directory = (
                destination.endswith("/")
                or destination in (".", "./")
                or len(sources) > 1
            )
            if not destination.startswith("/"):
                joined = f"{workdir.rstrip('/')}/{destination}"
                destination = joined[:-2] if joined.endswith("/.") else joined
            if directory and not destination.endswith("/"):
                destination += "/"
            out.append((sources, destination))
    return out


def where(path: str, instructions: Sequence[Tuple[List[str], str]]) -> List[str]:
    """Every place a context-relative path ends up in the image, in COPY order.

    A later COPY can land the same file somewhere else and both copies are then in the
    image: `app/utils/job_client.py` is in /job-client in five of these images and in
    /app in one.
    """
    found: List[str] = []
    for sources, destination in instructions:
        for source in sources:
            clean = source.rstrip("/")
            if source in (".", "./"):
                found.append(f"{destination.rstrip('/')}/{path}")
            elif clean == path:
                if destination.endswith("/"):
                    found.append(f"{destination.rstrip('/')}/{Path(path).name}")
                else:
                    found.append(destination)
            elif path.startswith(clean + "/"):
                found.append(f"{destination.rstrip('/')}/{path[len(clean) + 1:]}")
    # `COPY x ./x` followed by `COPY . /app/` puts one file in one place by two routes;
    # "/app/./x" and "/app/x" are that place written two ways.
    seen: Dict[str, None] = {}
    for place in found:
        seen.setdefault(posixpath.normpath(place), None)
    return list(seen)


# --------------------------------------------------------------------------
# What the build context excludes
# --------------------------------------------------------------------------


def _as_regex_match(pattern: str, path: str) -> bool:
    """fnmatch, with `**` crossing directory boundaries and `*` not."""
    if "**" in pattern:
        head, _, tail = pattern.partition("**/")
        if head and not path.startswith(head):
            return False
        rest = path[len(head) :] if head else path
        return any(fnmatch.fnmatch(rest[i:], tail) for i in range(len(rest) + 1))
    return fnmatch.fnmatch(path, pattern)


def ignored(path: str, patterns: Sequence[str]) -> bool:
    """Whether .dockerignore excludes this context-relative path. Last match wins,
    which is how a `!pattern` line puts something back."""
    verdict = False
    for raw in patterns:
        pattern = raw.strip()
        if not pattern or pattern.startswith("#"):
            continue
        negated = pattern.startswith("!")
        pattern = pattern.lstrip("!").rstrip("/")
        if not pattern:
            continue
        if _as_regex_match(pattern, path) or any(
            _as_regex_match(pattern, part) or path.startswith(pattern.rstrip("*") + "/")
            for part in [path.split("/", 1)[0]]
        ):
            verdict = not negated
    return verdict


def ignore_patterns(context: Path) -> List[str]:
    try:
        return (context / ".dockerignore").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


# --------------------------------------------------------------------------
# Hashing, on both sides
# --------------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_at(path: str, rev: Optional[str]) -> Optional[bytes]:
    """The file's bytes at `rev` as a checkout would hold them, or from the working
    tree when rev is None.

    `cat-file --filters`, not `show`: .gitattributes checks PowerShell out with CRLF
    while the blob stores LF, so the image -- built from a checkout -- carries CRLF.
    Comparing against the raw blob reports bootstrap.ps1 and start-docker.ps1 as stale
    on every release, which is how a check teaches people to ignore it.
    """
    if rev is None:
        file = REPO / path
        return file.read_bytes() if file.is_file() else None
    done = subprocess.run(
        ["git", "cat-file", "--filters", f"{rev}:{path}"], capture_output=True, cwd=REPO
    )
    return done.stdout if done.returncode == 0 else None


def sha256_in_image(image: str, paths: Sequence[str]) -> Dict[str, Optional[str]]:
    """One container, every path: a run per file would cost a second each. A path the
    image does not have comes back None -- a finding about the image, not an error."""
    if not paths:
        return {}
    script = "; ".join(
        f"if [ -f {shlex.quote(p)} ]; then sha256sum {shlex.quote(p)}; else echo 'MISSING {p}'; fi"
        for p in paths
    )
    done = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "sh", image, "-c", script],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise RuntimeError(f"could not read {image}: {done.stderr.strip()[:200]}")
    found: Dict[str, Optional[str]] = {}
    for line in done.stdout.splitlines():
        if line.startswith("MISSING "):
            found[line.split(" ", 1)[1]] = None
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            found[parts[1].strip()] = parts[0]
    return found


def changed_files(since: str, until: str = "HEAD") -> List[str]:
    done = subprocess.run(
        ["git", "diff", "--name-only", f"{since}..{until}"],
        capture_output=True,
        text=True,
        cwd=REPO,
    )
    if done.returncode != 0:
        raise SystemExit(f"git diff failed: {done.stderr.strip()}")
    return [line for line in done.stdout.splitlines() if line]


# --------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------


def check(tag: str, files: Iterable[str], rev: Optional[str] = None) -> int:
    wrong = 0
    checked_any = False
    for service in services():
        instructions = copies(service.dockerfile_path.read_text(encoding="utf-8"))
        excludes = ignore_patterns(service.context_dir)
        wanted: Dict[str, str] = {}
        skipped_outside = skipped_ignored = 0
        for repo_path in files:
            relative = service.inside(repo_path)
            if relative is None:
                skipped_outside += 1
                continue
            if ignored(relative, excludes):
                skipped_ignored += 1
                continue
            for inside in where(relative, instructions):
                wanted[inside] = repo_path
        image = service.image(tag)
        if not wanted:
            note = []
            if skipped_ignored:
                note.append(f"{skipped_ignored} excluded by .dockerignore")
            if skipped_outside:
                note.append(f"{skipped_outside} outside its context")
            print(
                f"{image:60s} carries none of these files"
                + (f" ({', '.join(note)})" if note else "")
            )
            continue
        found = sha256_in_image(image, sorted(wanted))
        bad = []
        for inside, repo_path in sorted(wanted.items()):
            checked_any = True
            data = content_at(repo_path, rev)
            if data is None:
                continue
            got = found.get(inside)
            if got is None:
                bad.append(f"  MISSING  {inside}  ({repo_path})")
            elif got != sha256_bytes(data):
                bad.append(f"  STALE    {inside}  ({repo_path})")
        suffix = (
            f", {skipped_ignored} excluded by .dockerignore" if skipped_ignored else ""
        )
        if bad:
            wrong += len(bad)
            print(f"{image:60s} {len(wanted)} checked, {len(bad)} WRONG{suffix}")
            print("\n".join(bad))
        else:
            print(f"{image:60s} {len(wanted)} checked, all current{suffix}")
    if not checked_any:
        print("\nNo image carries any of the named files: nothing was verified.")
        return 1
    if wrong:
        print(f"\n{wrong} file(s) in the images are not the ones in this tree.")
        print("An image built in a checkout that is not at the tag does exactly this.")
        return 1
    print("\nEvery image carries the files this release changed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tag", required=True, help="the image tag to check, e.g. 0.4.3")
    ap.add_argument("--since", help="the previous release tag; files changed since it")
    ap.add_argument("--until", default="HEAD", help="the end of that range")
    ap.add_argument("--files", nargs="*", help="repository paths to check instead")
    ap.add_argument(
        "--rev",
        help="hash the files at this revision (the release tag) rather than in the "
        "working tree, which may have moved on since",
    )
    args = ap.parse_args()
    if not args.since and not args.files:
        ap.error("pass --since <previous tag> or --files <paths>")
    files = args.files or changed_files(args.since, args.until)
    where_from = args.rev or "the working tree"
    print(f"{len(files)} changed file(s), tag {args.tag}, hashed from {where_from}\n")
    return check(args.tag, files, args.rev)


if __name__ == "__main__":
    sys.exit(main())
