# ADR 0001 — What runs at runtime: the image, or the checkout

**Status:** Proposed — awaiting the maintainer's decision
**Date:** 2026-11-14
**Context version:** v0.4.3
**Deciders:** repository maintainer

## Context

The stack runs eight ZaroPGx images. Two of them do not run their own code.

`app` mounts the repository over its application directory (`./:/app`, `compose.yml`), and
`nextflow` mounts it over its working directory (`./:/workspace`). The other six —
`pharmcat`, `pypgx`, `gatk-api`, `zarohla`, `mtdna`, `genome-downloader` — run what was
baked into them and mount only `data/`, `reference/` and `config/`.

This is not a gap in the images. `Dockerfile` ends with `COPY . /app/`, and the
dependencies are installed into the image's system environment at `/usr/local` rather than
into `/app/.venv`, so the bind mount shadows the image's copy of the application while
leaving everything it needs to run in place. Under `compose.yml` as published, the
application code inside `zaromicsresearch/zaropgx-app:<tag>` never executes. The same is
true of `pipelines/pgx/main.nf` inside the nextflow image.

Four consequences, all of them present today.

**"What is running?" has two answers.** `ZAROPGX_TAG` fixes the images, and an upgrade
verifies all eight against their registry digests. Application behaviour comes from the git
checkout, which may be at any commit, may be dirty, and may be part-way through an edit.
Nothing checks that the two agree, and nothing reports the disagreement if they do not.

**A release verifies an artifact that does not run.** v0.4.3 published and digest-verified
eight images. The report defect found the same afternoon lives in
`app/reports/generator.py` — code that ships inside `zaropgx-app:0.4.3` and is shadowed on
every install by the checkout.

**The published images are not what this project runs.** Someone who deploys the images
gets the application baked into them. This install runs its working tree. The two agree
when the checkout is at the tag, and differ silently when it is not.

**Both delivery paths carry the same source anyway.** Every bake target's context is the
repository root, and `mtdna` copies four files out of `app/` into its image. The stale-tree
trap recorded in the release procedure is the baked half of the same question: what is
delivered is only ever as good as the tree it was taken from.

Against all of that: the mount is why an application fix reaches the running stack by
moving the checkout and restarting one container. For a single maintainer who runs the
stack they develop, that is not a small thing.

## Decision

Proposed: **the image is the runtime source of truth for every service.** The repository
mount becomes an explicit development mode rather than the default, in two steps, so
nothing changes under a release that is already out.

## Options considered

### Option A — keep the split, document it

| Dimension | Assessment |
|---|---|
| Complexity | None; this is the status quo |
| Release integrity | Two artifacts, one verified, the other authoritative |
| Development loop | Fastest available |
| Operability | "What is running" needs two questions and a `git status` |

**Pros:** nothing to build, nothing to relearn; the fastest edit-to-running loop there is.
**Cons:** every consequence above stays, and the release's digest discipline keeps
guaranteeing something other than what serves the report.

### Option B — the image is authoritative; the mount becomes a development override

| Dimension | Assessment |
|---|---|
| Complexity | Low: delete two mounts, add one committed `compose.dev.yml` |
| Release integrity | The verified artifact is the one that runs |
| Development loop | One rebuild per change — **16 s measured**, see below |
| Operability | One question: which tag is up |

**Pros:** a release means what it says; rollback is a tag change rather than a tag change
plus a checkout; an install elsewhere behaves like this one; the e2e suite exercises the
artifact that ships.
**Cons:** iterating on application code needs `-f compose.dev.yml` or a rebuild; the
maintainer's habit of editing the live checkout stops working, which is a real change to
how this project is worked on.

### Option C — mount everywhere, for all eight services

| Dimension | Assessment |
|---|---|
| Complexity | Low to apply, high to live with |
| Release integrity | No artifact is authoritative; images become dependency bundles |
| Development loop | Fastest for all services |
| Operability | Images stop being self-contained; distribution needs the repository |

**Pros:** one rule instead of two; no rebuild for any service.
**Cons:** reproducibility goes: an image would no longer determine behaviour, and the
digests the upgrade checks would stop meaning anything. Rejected.

### Option D — keep the split, but make the disagreement loud

| Dimension | Assessment |
|---|---|
| Complexity | Low: one startup check, one health field |
| Release integrity | Unchanged, but a mismatch is no longer silent |
| Development loop | Unchanged |
| Operability | Both identities become visible |

**Pros:** the cheapest thing that removes the silence; useful whichever option wins.
**Cons:** it reports the problem rather than removing it.

## Trade-off analysis

The whole case for Option A is the development loop, so it is worth measuring rather than
assuming. On this host (rootless Docker, warm layer cache), editing `app/main.py` and
rebuilding the application image takes **16 seconds** — every layer above `COPY . /app/` is
cached, so the cost is that one layer. Today's loop is an edit and a container restart, and
a restart is not free either: the app's health start period runs again. The difference is
tens of seconds per change, not minutes.

Against that: a release today publishes, pushes and digest-verifies an application image
whose code is then shadowed on every install. The asymmetry is the argument. Option C is
the mirror of Option A and loses reproducibility outright; Option D is not an alternative
to B so much as the first half of it, and it is worth doing either way.

## Consequences

**Easier.** A release becomes a single fact: these digests, this behaviour. Rollback is
`ZAROPGX_TAG` alone. An image pulled on another machine behaves the way it does here. The
DAG compile test and the e2e suite exercise what ships rather than what is checked out.

**Harder.** Application changes need a rebuild before they run, which is the point and also
the friction. The `-f compose.dev.yml` habit has to be learned, and anyone who forgets it
will wonder why their edit did nothing — which is the same confusion the current model
creates in the other direction, where an edit takes effect without a build.

**To revisit.** Whether `nextflow` should follow: the pipeline is data more than code, and
`-preview` compiles whichever copy it is pointed at. Under this decision the test should
compile the image's copy, not the checkout's.

## Action items

1. [ ] Report both identities: the image tag the container was built as, and the git SHA of
   the code it is running, in `/health` and in the startup log. Warn when they differ.
2. [ ] Add a committed `compose.dev.yml` carrying `./:/app` and `./:/workspace`, documented
   in `development-setup.md`.
3. [ ] Remove both mounts from `compose.yml`, in a release of its own, with the change in
   `UPGRADING.md` — an install that upgrades without noticing would otherwise start running
   different code with no visible cause.
4. [ ] Decide whether the mtdna read-back check (release step 4) generalises: after a bake,
   assert that a file the release changed is present in each image that carries it.
