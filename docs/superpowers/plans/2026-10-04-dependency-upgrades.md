# ZaroPGx Dependency Upgrades Plan (2026-10)

Oct 4, 2026 · @Iliya Yaroshevskiy

One branch, `chore/deps-2026-10`, brings every dependency with a safe newer release up to date, closes the urllib3 and WeasyPrint advisories and leaves every pipeline call unchanged. It runs on the Fedora boot. Nothing is released.

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking. Iliya always picks subagent-driven.

**Architecture:** each task bumps one dependency family, verifies it in isolation and commits. Task 9 rebuilds every image, runs the full suite and the compose e2e, and A/B-diffs PharmCAT calls against a baseline captured from `main` in Task 0.

**Tech stack:** uv 0.12 (uv.lock), pytest, Docker Compose + buildx bake, GitHub Actions, WeasyPrint/Pango, Nextflow, HAPI FHIR JPA starter, haplogrep3.

**Where this runs:** the Fedora clone. The Windows machine's Claude memory and `dev-notes/BACKLOG.md` are not there, so every rule that matters is copied into this plan. The plan is committed on `main` as `docs/superpowers/plans/2026-10-04-dependency-upgrades.md` (a one-off, at Iliya's request, so it reaches Fedora; Docker and Sphinx both exclude that directory). The same text is in the Claude Doc at https://claude.ai/code/artifact/d86040f0-aa04-4cc5-bb74-4be35f91d9ba.

## Audit findings

Audited 2026-10-04 against `main` @ 5bf194f (v0.3.2). Thirteen items are behind; one advisory (ecdsa) has no fix and does not apply.

| Item | Now | Target | Why |
| --- | --- | --- | --- |
| urllib3 (transitive) | 2.7.0 | 2.8.0 | 3 CVEs: CVE-2026-97687/97688/97689 (proxy TLS ignored, deflate-stream infinite loop, unbounded chunk line). Ships in the 0.3.2 app image. |
| Python in-cap packages (about 45) | various | latest in cap | fastapi 0.142.2, numpy 2.5.3, pandas 3.0.6, werkzeug 3.1.9, pydantic-settings 2.15, python-dotenv 1.2.4 and more |
| pyarrow | 24.0.0 (cap `<25`) | 25.x | cap lift |
| isort (dev) | 8.0.1 (cap `<9`) | 9.0.2 | cap lift. CI's Lint job installs black/isort/flake8 unpinned, so CI already drifts from the lock |
| black / mypy / flake8 (dev) | 26.5.1 / 2.2.0 / 7.3.0 | 26.10.0 / 2.4.0 / 7.4.1 | in cap |
| SQLAlchemy | 2.0.51 (cap `<2.1`) | 2.1.3 | Exposure checked: no `filter_by()` in app, one `sessionmaker` with `autoflush=False`, no async engine |
| WeasyPrint | 66.0 (cap `<67`) | 70.0 | 3 CVEs (CVE-2025-68616, CVE-2026-49452, CVE-2026-55073). None applies today (no custom `url_fetcher`, no presentational hints, only our own templates). 67–70 assert in `weasyprint/layout/page.py` on every real report; cause found and a CSS fix verified on 2026-10-04 (Decision 4); the crash is at `page.py:717` |
| ecdsa (via python-jose) | 0.19.2 | — | CVE-2024-23342, no upstream fix. Not exploitable here: JWTs are HS256. Accepted |
| Nextflow | 25.10.7 | 25.10.8 | patch on the last legacy-parser line; 26.x stays blocked |
| Docker CLI in nextflow image | 24.0.7 (API 1.43) | 29.8.2 | 24.x is EOL |
| Nextflow runner pins | fastapi 0.141.1, python-dotenv 1.2.3, sqlalchemy 2.1.1 | 0.142.2, 1.2.4, 2.1.3 |  |
| HAPI FHIR | v8.10.0-2 | v8.12.0-2 | Released 2026-10-02. Migrates the shared `fhir` schema in `zaropgx_db` on boot: one-way |
| haplogrep3 (mtdna image) | 3.2.2 + phylotree-fu-rcrs@1.2 | 3.3.2 + phylotree-fu-rcrs@1.3 | Upstream `mtdna-server-2` hasn't released since v2.1.16 (2024-12). 3.3.x adds Mitotree support and a graphviz fix; the classifier is unchanged. Tree 1.3 differs from 1.2 only in its `version` and citation fields |
| GitHub Actions | checkout v5, setup-uv v7, setup-node v4/v5, upload-artifact v4, git-auto-commit v5 | v7, v10, v7, v7, v7 | `setup-node@v4` already warns "Node.js 20 is deprecated" |
| mermaid-cli (render-diagrams) | 11.16.0 | 12.0.0 | major |

Already current, no action: PharmCAT 3.4.0, htslib/bcftools/samtools 1.24, GATK 4.7.0.0 (HTSJDK 5.0.0, Picard 3.5.0, bundled bwa-mem), PyPGx + bundle 0.27.0, fuc 0.38.0, OptiType 1.5.0, mutserve 2.0.3, nf-core/hlatyping 2.2.0, Kroki 0.32.1, Postgres 18.6, Java 21.0.12 / 17.0.20, `docs/requirements.txt` (no advisories), and the zarohla and pypgx image pins (current or deliberately held).

## Decisions (settled 2026-10-04)

Settled with Iliya on 2026-10-04. Item 1 still needs his answer at session start.

1. **Bootstrap branch (`origin/fix/v0.3.2-bootstrap`, 595a35f): merge only if Iliya confirms it is finished.** The code looks complete (no TODOs; tests and docs included; Tests and E2E green). But its contract tests only read the scripts, and nothing in the repo shows the bootstrap was run end to end. Its CI goes green either way (Task 0 step 3 applies black to its two test files). If he confirms, merge it first; otherwise the deps branch starts from current `main`.
2. **Tree: stay on `phylotree-fu-rcrs`, bumped to 1.3. No Mitotree.** Mitotree has about 10× the resolution, but three things stand against it here. The haplogroup feeds the MT-RNR1 Tier C reference call through haplogrep's `Not_Found_Polys` (`app/mtdna/mt_rnr1.py`, `vcf_evidence`), so a deeper tree changes that evidence and needs its own validation. Mitotree is a May 2026 preprint, while PhyloTree 17 Forensic Update is peer-reviewed (Dür et al. 2021). And Mitotree's tree data is CC BY-NC-ND 4.0 (non-commercial, no derivatives). Revisit as its own validation task once the paper is published.
3. **CI runs on `ubuntu-24.04`, the LTS zimerguz runs.** The E2E job builds and runs the stack on the runner's Docker engine, so matching production's host OS tests what production runs. The images are Debian-based, so the app itself is unaffected. Move CI to 26.04 when zimerguz moves. Done in Task 3 step 7.
4. **WeasyPrint 70: cause found, fix verified; upgrade.** It isn't slow: 67+ crash in about 2 s with `assert not page_is_empty` (`layout/page.py:717`). Our print CSS forbids breaking `.drugs-grid` (`page-break-inside: avoid`), but real reports' drug grids are taller than a page. 66 ignored the impossible request; 67+ can break grids and asserts instead. Making the grid breakable, and putting the avoid on the real grid items (`.drug-item-link`), renders both crashing reports cleanly (checked by eye). Task 5 applies it. Stay on 66 only if Task 5's full-report look finds a regression we can't fix.

## Global constraints

Every task's requirements include these.

- `requires-python = ">=3.12,<3.13"` is unchanged. Do not move base images off Python 3.12.
- **Nextflow stays on 25.10.x.** 26.x's strict CLI parser leaves the runner's string params (`--skip_hla false`) truthy, so every run would silently skip HLA and PyPGx.
- **PyPGx image keeps `pandas==2.2.3` and `setuptools==80.9.0`** (2.3.x untested with PyPGx; `pkg_resources` was removed in setuptools 81).
- **PharmCAT image keeps Java 17** (temurin-17). PharmCAT's jar targets 17.
- **mtDNA tree family stays `phylotree-fu-rcrs`.** No Mitotree.
- **No release actions without Iliya's explicit go:** no version bump, tag, `gh release`, Docker Hub push or zimerguz deploy.
- **Git hygiene:** read `git status` and `git diff --cached --stat` before every commit, and stage files **by name**, never `git add -A` / `git add .`. Working notes go in `dev-notes/` (gitignored), never in a tracked file.
- **Commit trailer:** end every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **uv:** a bare `uv run <tool>` re-syncs the env without the dev extra and silently removes pytest. Always run `uv sync --all-extras --dev` first, then `uv run --no-sync ...`. Use `uv tool run` for black/isort.
- **CI's Tests job has no bcftools/tabix/bgzip.** Reproduce it with `PATH=.venv/bin:/usr/bin:/bin uv run --no-sync pytest -q -m "not e2e"` if a CI-only failure appears.
- **After every push:** `gh run list --limit 3`, then `gh run watch <id> --exit-status`. Pushed work isn't done until CI is green. For a red run: `gh run view <id> --log-failed | grep -E "FAILED|passed|failed|would reformat"`.
- **Never run compose from a worktree.** Run it only from the repo root, on the intended branch. Subagent worktrees branch from `origin/main`, so every dispatched agent must first check that its base is this plan's branch head (`git log -1 --format=%h`).
- **Docker on Fedora:** the `pgx-native` context in the repo's `CLAUDE.md` is specific to the Windows/WSL machine. On Fedora, use the local engine, but first confirm with `docker ps` that no other `pgx_*` stack is bound to the same ports.
- **Front end and PDF are not covered by tests.** A green suite proves nothing about the rendered report. Open the PDF and the interactive HTML before calling Task 5 or Task 9 done.

## Review focus

Five failure modes no existing test exercises, most likely first. Each is pinned in the task named.

1. **A real report with 100+ drug recommendations must render through WeasyPrint, not ReportLab and not the `.txt` fallback.** The drugs grid stays in two columns, no tile splits across pages, and there are no blank pages. Pinned by `tests/test_weasyprint_real_render.py` (Task 5, 150-drug case) plus the manual PDF look (Task 9).
2. **`--skip_hla false` must still run HLA and PyPGx under Nextflow 25.10.8.** Pinned by the existing `tests/test_nextflow_skip_flags_406.py` (Task 6) and the e2e `tests/e2e/test_bam_hla_pipeline.py` (Task 9).
3. **HAPI 8.12 must migrate a populated `fhir` schema**, the live one rather than a fresh DB, and keep existing resources readable. Pinned by Task 7's pre-upgrade dump, read-back of a pre-existing resource ID, and rollback command.
4. **Haplogroup calls must not change** with haplogrep3 3.3.2 + tree 1.3. Pinned by Task 8's A/B classification of haplogrep3's own `example-wgs.vcf` on the old vs new image. The citation must name the haplogrep3 version the image installs (Task 8 test).
5. **The CI lint result must equal the local lint result.** Pinned by Task 3: CI's lint tools are pinned to the `uv.lock` versions and checked by `tests/test_ci_lint_pins.py`.

## Task 0: Pre-flight on Fedora (no code)

Creates `dev-notes/ab-capture.sh` and `dev-notes/ab/baseline/*.tsv`, both gitignored. No tracked files change.

- [ ] **Step 1: Repo state**

```bash
git fetch --all --prune
git status -sb
git log -1 --format='%h %s' origin/main   # expect 5bf194f or later
git log --oneline main..origin/fix/v0.3.2-bootstrap
```

- [ ] **Step 2: Toolchain**

```bash
uv --version            # need >= 0.12 (uv.lock is in 0.12's format); `uv self update` if older
docker version --format '{{.Client.Version}} / {{.Server.Version}} api={{.Server.APIVersion}}'
docker compose version
docker buildx version
gh auth status
rpm -q pango jq || sudo dnf install -y pango jq
docker ps --format '{{.Names}} {{.Image}}' | sort   # note any existing pgx_* stack
```

- [ ] **Step 3: Bootstrap branch (Decision 1).** Make its CI green either way. Merge it only if Iliya confirms it is finished and was run end to end.

```bash
git switch fix/v0.3.2-bootstrap || git switch -c fix/v0.3.2-bootstrap origin/fix/v0.3.2-bootstrap
git status -sb          # Fedora may hold unpushed work here: commit or push it first
uv sync --all-extras --dev
uv tool run black tests/test_bootstrap_contract.py tests/test_compose_contract.py
git diff --stat
git add tests/test_bootstrap_contract.py tests/test_compose_contract.py
git commit -m "style(tests): black the bootstrap contract tests

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push
gh run list --limit 1   # then: gh run watch <id> --exit-status  (must be green)

# Only if Iliya says the bootstrap work is finished:
git switch main && git merge --ff-only fix/v0.3.2-bootstrap && git push
gh run list --limit 1   # watch main's run to green too
```

- [ ] **Step 4: Branch**

```bash
git switch main && git pull --ff-only
git switch -c chore/deps-2026-10
```

- [ ] **Step 5: Baseline unit suite**

```bash
uv sync --all-extras --dev
uv run --no-sync pytest -q -m "not e2e" 2>&1 | tail -3   # record the pass/skip counts in dev-notes/deps-2026-10.log
```

- [ ] **Step 6: A/B capture helper.** Write `dev-notes/ab-capture.sh`:

```bash
#!/usr/bin/env bash
# Copy the PharmCAT calls TSV of every job newer than MARKER into dev-notes/ab/LABEL/,
# named after the uploaded input in the same job directory (falls back to the job id).
set -euo pipefail
label=$1 marker=$2
out="dev-notes/ab/$label"
mkdir -p "$out"
find data/reports -name '*_pgx_pharmcat.tsv' -newer "$marker" | while read -r tsv; do
  dir=$(dirname "$tsv")
  input=$(ls "$dir" | grep -m1 '^upload_' | sed -E 's/^upload_[0-9a-f]+_//; s/_[0-9a-f-]{36}//' || true)
  cp "$tsv" "$out/${input:-$(basename "$dir")}.tsv"
done
ls -l "$out"
```

- [ ] **Step 7: Baseline e2e on `main` code** (still on the new branch, which equals main). The e2e builds images from the working tree, starts its own `zaropgx_e2e` project, and runs the BAM, VCF, CRAM/SAM conversion, HLA and GRCh37-liftover lanes.

```bash
mkdir -p dev-notes/ab && touch dev-notes/ab/.baseline-marker
./scripts/e2e.sh 2>&1 | tee dev-notes/e2e-baseline.log | tail -5
bash dev-notes/ab-capture.sh baseline dev-notes/ab/.baseline-marker
./scripts/e2e-down.sh
```

Locally (`CI` unset), `e2e-up.sh` runs `docker compose build` for every service and tags the result `zaromicsresearch/zaropgx-*:0.3.2`. After any e2e run, the local `:0.3.2` images are therefore working-tree builds; `docker compose pull` restores the Hub ones.

Expected: e2e passes, and `dev-notes/ab/baseline/` holds one TSV per pipeline job. If a job directory had no `upload_*` file, its TSV is named by job id. Note in `dev-notes/deps-2026-10.log` which input each one was, using the job ids in `dev-notes/e2e-baseline.log`.

## Task 1: Python lock refresh within caps, plus the pyarrow cap lift

**Files:** modify `pyproject.toml` (the `pyarrow` line) and `uv.lock`. Produces the refreshed `uv.lock` that Tasks 2–5 build on.

- [ ] **Step 1: Lift the pyarrow cap.** In `pyproject.toml` replace

```toml
  "pyarrow>=21.0.0,<25.0.0", # Backs pandas' PyArrow-based string dtype.
```

with

```toml
  "pyarrow>=21.0.0,<26.0.0", # Backs pandas' PyArrow-based string dtype.
```

- [ ] **Step 2: Upgrade the lock**

```bash
uv lock --upgrade
uv tree --outdated --depth 1 2>&1 | grep latest
```

Expected: only `weasyprint`, `sqlalchemy` and `isort` still show `(latest: …)`. Those are capped and handled in Tasks 2, 4 and 5.

- [ ] **Step 3: Confirm the security fix landed**

```bash
grep -A1 '^name = "urllib3"' uv.lock    # version = "2.8.0" or later
```

- [ ] **Step 4: Run the suite**

```bash
uv sync --all-extras --dev
uv run --no-sync pytest -q -m "not e2e" 2>&1 | tail -3
```

Expected: same pass count as the Task 0 baseline, 0 failed.

- [ ] **Step 5: Audit**

```bash
uv export --frozen --all-extras --no-hashes --no-emit-project -o /tmp/req.txt
uvx pip-audit --no-deps --disable-pip -r /tmp/req.txt
```

Expected: only `weasyprint` (fixed in Task 5) and `ecdsa` (accepted). urllib3 is gone.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): refresh the lock within caps; urllib3 2.8.0, pyarrow 25

urllib3 2.8.0 fixes CVE-2026-97687/97688/97689. pyarrow's cap moves to <26.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

## Task 2: Lint toolchain, isort 9 and black 26.10 applied

**Files:** modify `pyproject.toml` (the `isort` line in the `dev` extra), `uv.lock`, and any `app/**.py` or `tests/**.py` that black/isort reformat. Consumes Task 1's lock. Produces the exact black/isort/flake8 versions that Task 3 pins in CI; read them from `uv.lock` after this task.

- [ ] **Step 1: Lift the isort cap.** In `pyproject.toml` replace

```toml
  "isort>=6.1.0,<9.0.0",
```

with

```toml
  "isort>=9.0.2,<10.0.0",
```

- [ ] **Step 2: Lock and sync**

```bash
uv lock --upgrade-package isort
uv sync --all-extras --dev
for p in black isort flake8; do printf '%s ' $p; grep -A1 "^name = \"$p\"" uv.lock | sed -n 2p; done
```

Record the three versions (expected: black 26.10.0, isort 9.0.2, flake8 7.4.1).

- [ ] **Step 3: Apply the formatters at the locked versions**

```bash
uv tool run --from black==26.10.0 black app tests
uv tool run --from isort==9.0.2 isort --profile black app tests
git diff --stat
```

- [ ] **Step 4: Check the blocking flake8 gate and the suite**

```bash
uv tool run --from flake8==7.4.1 flake8 --select=E9,F63,F7,F82 app tests
uv run --no-sync pytest -q -m "not e2e" 2>&1 | tail -3
```

Expected: flake8 prints nothing (exit 0), and the suite has the same counts as before. If the new flake8 adds an E9/F63/F7/F82 finding, fix that code. Don't widen the ignore list.

- [ ] **Step 5: Commit, in two commits so the reformat is reviewable on its own**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): isort 9

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git status --short | grep -E '^ M (app|tests)/'    # the files black/isort touched
git add <each file listed above, by name>
git commit -m "style: black 26.10 and isort 9 formatting

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

## Task 3: CI lint pins and GitHub Actions majors

**Files:** modify `.github/workflows/ci.yml` and `.github/workflows/render-diagrams.yml`; create `tests/test_ci_lint_pins.py`. The render bot may commit `app/visualizations/workflow.svg` and `.png` after the push. Consumes the black/isort/flake8 versions from Task 2.

- [ ] **Step 1: Write the failing test** `tests/test_ci_lint_pins.py`:

```python
"""CI's Lint job must run the same black/isort/flake8 the lock pins.

It used to `uv tool install black` unpinned, so CI linted with whatever PyPI
served that day while developers ran the locked version: the branch
fix/v0.3.2-bootstrap went red on formatting nobody could reproduce locally.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _locked(name: str) -> str:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    return next(p["version"] for p in lock["package"] if p["name"] == name)


def test_ci_lint_tools_are_pinned_to_the_locked_versions():
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    for tool in ("black", "isort", "flake8"):
        pins = re.findall(rf"uv tool install {tool}==([\w.]+)", ci)
        assert pins, f"{tool} is installed unpinned in ci.yml"
        assert pins == [_locked(tool)], (tool, pins, _locked(tool))
```

- [ ] **Step 2: Run it to verify it fails**

```bash
uv run --no-sync pytest -q tests/test_ci_lint_pins.py
```

Expected: FAIL with `black is installed unpinned in ci.yml`.

- [ ] **Step 3: Pin the lint tools.** In `.github/workflows/ci.yml` replace

```yaml
      - name: Install lint tools
        run: uv tool install black && uv tool install isort && uv tool install flake8
```

with the versions recorded in Task 2 step 2:

```yaml
      # Pinned to uv.lock's versions (tests/test_ci_lint_pins.py enforces it), so CI
      # lints with exactly what `uv sync --all-extras --dev` gives a developer.
      - name: Install lint tools
        run: uv tool install black==26.10.0 && uv tool install isort==9.0.2 && uv tool install flake8==7.4.1
```

- [ ] **Step 4: Run it to verify it passes**

```bash
uv run --no-sync pytest -q tests/test_ci_lint_pins.py
```

- [ ] **Step 5: Bump the Actions in `ci.yml`**

| Find | Replace (every occurrence) |
| --- | --- |
| `actions/checkout@v5` | `actions/checkout@v7` |
| `astral-sh/setup-uv@v7` | `astral-sh/setup-uv@v10` |
| `actions/setup-node@v4` | `actions/setup-node@v7` |
| `actions/upload-artifact@v4` | `actions/upload-artifact@v7` |

`docker/setup-buildx-action@v4` is already the latest major. Keep `enable-cache: true` as it is: setup-uv v10 only changed the `auto` default.

- [ ] **Step 6: Bump `render-diagrams.yml`**

| Find | Replace |
| --- | --- |
| `actions/checkout@v5` | `actions/checkout@v7` |
| `actions/setup-node@v5` | `actions/setup-node@v7` |
| `stefanzweifel/git-auto-commit-action@v5` | `stefanzweifel/git-auto-commit-action@v7` |
| `@mermaid-js/mermaid-cli@11.16.0` | `@mermaid-js/mermaid-cli@12.0.0` |

git-auto-commit v6 removed `create_branch`, `skip_checkout` and `skip_fetch`; this workflow uses none of them.

- [ ] **Step 7: Pin the runner to production's LTS (Decision 3).** In both workflow files, change every `runs-on: ubuntu-latest` to `runs-on: ubuntu-24.04`. Above the first `runs-on` in each file, add the comment `# Same Ubuntu LTS as production (zimerguz). Move CI when zimerguz moves.`

```bash
sed -i 's/runs-on: ubuntu-latest/runs-on: ubuntu-24.04/' .github/workflows/ci.yml .github/workflows/render-diagrams.yml
grep -n 'runs-on' .github/workflows/*.yml     # every job reads ubuntu-24.04
```

- [ ] **Step 8: Commit and push.** This is the branch's first push, and it validates Tasks 1–3 in CI.

```bash
git add .github/workflows/ci.yml .github/workflows/render-diagrams.yml tests/test_ci_lint_pins.py
git commit -m "ci: pin lint tools to the lock; bump Actions to current majors

checkout v7, setup-uv v10, setup-node v7, upload-artifact v7,
git-auto-commit v7, mermaid-cli 12. Runners on ubuntu-24.04, production's LTS.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin chore/deps-2026-10
gh run list --limit 3
```

Pushing the workflow file triggers **Render Mermaid diagrams**. That job may auto-commit re-rendered `app/visualizations/workflow.{svg,png}` to this branch. Watch both runs:

```bash
gh run watch <ci-run-id> --exit-status
gh run watch <render-run-id> --exit-status
git pull --ff-only     # pick up the bot's commit, if any
```

Expected: CI green, and no "Node.js 20 is deprecated" warning in the annotations:

```bash
for j in $(gh run view <ci-run-id> --json jobs --jq '.jobs[].databaseId'); do gh api repos/Zaromics/ZaroPGx/check-runs/$j/annotations --jq '.[].message'; done | sort -u
```

If the bot committed new diagrams, open `app/visualizations/workflow.svg` and check that it still reads correctly (mermaid 12 can change layout).

## Task 4: SQLAlchemy 2.1

**Files:** modify `pyproject.toml` (the `sqlalchemy` line and its comment) and `uv.lock`; create `tests/test_sqlalchemy_21_assumptions.py`. Consumes the lock from Tasks 1–2.

- [ ] **Step 1: Write the test** that pins the two facts that make 2.1 safe here. `tests/test_sqlalchemy_21_assumptions.py`:

```python
"""SQLAlchemy 2.1 changed two behaviours; ZaroPGx is insulated from both by
facts these tests pin.

1. 2.1 autoflushes on *every* execute (Core text() included) when autoflush is
   on. SessionLocal turns it off, and several call sites rely on that
   (app/services/job_service.py comments at the populate_existing reads).
2. 2.1's filter_by() searches every FROM entity and raises AmbiguousColumnError
   on a shared column name. The app does not call filter_by() at all.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_sessionlocal_keeps_autoflush_off():
    from app.api.db import SessionLocal

    assert SessionLocal.kw["autoflush"] is False


def test_app_does_not_use_filter_by():
    hits = [
        f"{p.relative_to(ROOT)}:{n}"
        for p in (ROOT / "app").rglob("*.py")
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"\.filter_by\(", line)
    ]
    assert not hits, f"filter_by() is ambiguity-prone under SQLAlchemy 2.1: {hits}"
```

- [ ] **Step 2: Run it on 2.0.** It should pass, which documents the baseline.

```bash
uv run --no-sync pytest -q tests/test_sqlalchemy_21_assumptions.py
```

- [ ] **Step 3: Confirm there is no other exposure**

```bash
git grep -nE '\bSession\(|sessionmaker\(' -- app          # only app/api/db.py
git grep -nE 'create_async_engine|AsyncSession' -- app     # none (2.1 no longer installs greenlet by default)
```

- [ ] **Step 4: Bump.** In `pyproject.toml` replace

```toml
  # 2.1 autoflushes on every execute and widens filter_by; test it on its own first.
  "sqlalchemy>=2.0.43,<2.1.0",
```

with

```toml
  # 2.1: unconditional autoflush (SessionLocal has it off) and filter_by across all
  # FROM entities (unused); tests/test_sqlalchemy_21_assumptions.py pins both.
  "sqlalchemy>=2.1.3,<2.2.0",
```

```bash
uv lock --upgrade-package sqlalchemy
uv sync --all-extras --dev
uv run --no-sync python -c "import sqlalchemy; print(sqlalchemy.__version__)"   # 2.1.3
uv run --no-sync pytest -q -m "not e2e" 2>&1 | tail -3
```

Expected: 2.1.3 and the same counts as before. Real-Postgres coverage comes from the e2e in Task 9.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock tests/test_sqlalchemy_21_assumptions.py
git commit -m "chore(deps): SQLAlchemy 2.1

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

## Task 5: WeasyPrint 70 (cause found and fix verified 2026-10-04)

**Files:** create `tests/test_weasyprint_real_render.py`; modify `app/reports/templates/report_template.html` (the `@media print` `.drugs-grid` block, around line 731), `pyproject.toml` (the `weasyprint` line), `uv.lock`, and `.github/workflows/ci.yml` (Tests job installs Pango so the new test runs in CI).

**Interfaces:** `app.reports.generator.generate_pdf_from_html(html_content: str, output_path: str) -> None` re-raises WeasyPrint's exception after writing a `.txt` fallback. `app.reports.generator.env` is the Jinja environment. `app.reports.generator._HAS_WEASYPRINT: bool`.

**Root cause (verified 2026-10-04 on the WSL machine).** The print CSS sets `page-break-inside: avoid` on `.drugs-grid`, but real reports list 100+ drugs, which is taller than a page. 66 ignored the impossible request. 67+ can break grids across pages and instead hit `assert not page_is_empty` (`layout/page.py:717`) in about 2 s. Of the three newest real reports, the two with long drug lists crashed on 70 as-is (`996906c5…`, `b0e5dcd6…`), and the small one rendered. Making the grid breakable fixed both. The tile `break-inside: avoid` sits on `.drug-item`, which is not the grid item (the `<a class="drug-item-link">` around it is), so without step 4's second rule one tile split across a page break.

- [ ] **Step 1: Write the test.** Every existing WeasyPrint test stubs the engine, so none of them can see this crash. `tests/test_weasyprint_real_render.py`:

```python
"""The real report template must render to PDF under the pinned WeasyPrint.

Every other WeasyPrint test stubs the engine (tests/test_report_autoescape.py's
weasyprint_lane), so none could see WeasyPrint 67-70's AssertionError in
weasyprint/layout/page.py on real reports. This drives the real engine and skips
only where WeasyPrint's native libraries (Pango) are absent.
"""
from __future__ import annotations

import pytest

import app.reports.generator as generator

pytestmark = pytest.mark.skipif(
    not generator._HAS_WEASYPRINT, reason="WeasyPrint native libraries unavailable"
)

_CLASSES = ["evidence-3", "evidence-2", "evidence-1", "evidence-unspecified", "evidence-0"]


def _recommendations(n: int) -> list[dict]:
    return [
        {
            "drug": f"drug{i:03d}",
            "gene": "CYP2D6",
            "guideline_source": "CPIC",
            "classification": "Strong",
            "recommendation": "Initiate therapy with the recommended starting dose.",
            "evidence_rank": i % 5,
            "evidence_class": _CLASSES[i % 5],
        }
        for i in range(n)
    ]


@pytest.mark.parametrize("n_drugs", [10, 150])
def test_report_template_renders_to_a_real_pdf(tmp_path, n_drugs):
    html = generator.env.get_template("report_template.html").render(
        diplotypes=[],
        recommendations=_recommendations(n_drugs),
        gene_drug_recommendations=[],
        organized_recommendations=[],
        patient_id="test-patient",
        report_id="test-report",
        report_date="2026-10-04",
        organization="ZaroPGx",
        disclaimer="",
    )
    out = tmp_path / "report.pdf"
    # Re-raises WeasyPrint's own exception (after dumping report.txt), so a
    # layout assertion fails this test with WeasyPrint's traceback.
    generator.generate_pdf_from_html(html, str(out))

    data = out.read_bytes()
    assert data.startswith(b"%PDF") and data.rstrip().endswith(b"%%EOF")
    assert not (tmp_path / "report.txt").exists()
```

There is no page-count assertion: WeasyPrint writes compressed object streams, so `/Type /Page` isn't greppable. Layout gets checked by eye in step 6.

- [ ] **Step 2: Run it on 66.0.** Expected: 2 passed. If it skips, Pango isn't visible; fix that (`rpm -q pango`) before going on.

```bash
uv run --no-sync pytest -q tests/test_weasyprint_real_render.py -rs
```

- [ ] **Step 3: Bump and reproduce.** In `pyproject.toml` replace

```toml
  "weasyprint>=66.0,<67.0", # Capped at minor version due to nonstandard versioning.
```

with

```toml
  "weasyprint>=70.0,<71.0", # Capped at major: each major has changed page layout.
```

```bash
uv lock --upgrade-package weasyprint
uv sync --all-extras --dev
uv run --no-sync pytest -q tests/test_weasyprint_real_render.py
```

Expected: FAIL with `AssertionError` raised from `weasyprint/layout/page.py`. If it passes, the synthetic data doesn't hit the trigger; reproduce on a real saved report instead. Any finished job leaves `data/reports/<job>/<run>/<run>_pgx_report.html`, and Task 0's e2e runs left several.

```bash
f=$(find data/reports -name '*_pgx_report.html' | head -1)
uv run --no-sync python -c "
import sys; from app.reports.generator import generate_pdf_from_html
generate_pdf_from_html(open(sys.argv[1], encoding='utf-8').read(), '/tmp/real.pdf')" "$f"
```

Then cut that HTML down, deleting whole `<div class="section">` blocks, until the smallest crashing input remains. Make the test's rendered context reproduce it, so the test fails for the real reason.

- [ ] **Step 4: Apply the verified fix.** In `report_template.html`'s `@media print` block, replace

```css
            /* PDF-specific styling for drugs grid */
            .drugs-grid {
                grid-template-columns: repeat(2, 1fr) !important;
                gap: 6px 10px !important;
                page-break-inside: avoid !important;
            }
```

with

```css
            /* PDF-specific styling for drugs grid. The grid itself must stay
               breakable: real reports list 100+ drugs, taller than a page, and
               WeasyPrint >= 67 asserts in layout/page.py when told not to break
               it. The grid items are the links, so they carry the avoid. */
            .drugs-grid {
                grid-template-columns: repeat(2, 1fr) !important;
                gap: 6px 10px !important;
            }
            .drugs-grid > .drug-item-link {
                break-inside: avoid;
                page-break-inside: avoid;
            }
```

```bash
uv run --no-sync pytest -q tests/test_weasyprint_real_render.py
```

- [ ] **Step 5: Only if the test still fails.** Use superpowers:systematic-debugging on the traceback. If the trigger is somewhere else, cut a real report's HTML down as in step 3 until the smallest crashing input remains, and fix that rule the same way.

**Fallback (Decision 4):** if 70 can't render our report without a layout regression we can't fix, stay on 66.0. Run `git checkout pyproject.toml uv.lock app/reports/templates/report_template.html`, then `uv sync --all-extras --dev`. Keep the test, which still guards 66.0, and commit only it. Write the traceback and the minimal reproducing HTML to `dev-notes/weasyprint-70.md`, then skip to step 8.

- [ ] **Step 6: Look at real PDFs**, since the suite can't judge layout. For three real saved reports, including the one with the most drugs:

```bash
for f in $(find data/reports -name '*_pgx_report.html' | head -3); do
  uv run --no-sync python -c "
import sys; from app.reports.generator import generate_pdf_from_html
generate_pdf_from_html(open(sys.argv[1], encoding='utf-8').read(), sys.argv[2])" "$f" "/tmp/$(basename "$f" .html).pdf"
done
xdg-open /tmp/*_pgx_report.pdf
```

Compare each against the 66.0 PDF saved next to its HTML (`*_pgx_report.pdf`). The drugs grid must be two columns, with no tile split across a page and no blank pages. The gene table, legends and citations must be unchanged.

- [ ] **Step 7: Run the new test in CI.** In `.github/workflows/ci.yml`, in the `test` job, add after the "Set up Node" step:

```yaml
      # WeasyPrint's native libraries, so tests/test_weasyprint_real_render.py
      # renders with the real engine instead of skipping.
      - name: Install Pango
        run: sudo apt-get update && sudo apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0
```

Then run the whole suite locally, where Pango is present just as it now will be in CI:

```bash
uv run --no-sync pytest -q -m "not e2e" 2>&1 | tail -3
```

Expected: same counts as before, plus the 2 new tests.

- [ ] **Step 8: Audit and commit**

```bash
uv export --frozen --all-extras --no-hashes --no-emit-project -o /tmp/req.txt
uvx pip-audit --no-deps --disable-pip -r /tmp/req.txt     # only ecdsa remains (on the success path)
git add tests/test_weasyprint_real_render.py pyproject.toml uv.lock app/reports/templates/report_template.html .github/workflows/ci.yml
git commit -m "chore(deps): WeasyPrint 70; stop asking it not to break the drugs grid

67-70 asserted in layout/page.py on every real report. Fixes
CVE-2025-68616, CVE-2026-49452, CVE-2026-55073. First test that drives
the real engine; CI now installs Pango so it runs there.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

On the fallback path, stage only `tests/test_weasyprint_real_render.py` and `.github/workflows/ci.yml`, with a message that says WeasyPrint stays on 66.0 and why.

## Task 6: Nextflow image: Nextflow 25.10.8, Docker CLI 29.8.2, runner pins

**Files:** modify `docker/nextflow/Dockerfile.nextflow` (lines 7, 15, 45–47).

- [ ] **Step 1: Edit.** In `docker/nextflow/Dockerfile.nextflow`, change `ARG NXF_VERSION=25.10.7` to `ARG NXF_VERSION=25.10.8`, and in the Docker CLI download change `docker-24.0.7.tgz` to `docker-29.8.2.tgz`. Then replace

```dockerfile
# Pinned at the versions installed and running when this was pinned (2026-09-27).
RUN uv pip install fastapi==0.141.1 uvicorn==0.54.0 httpx==0.28.1 python-dotenv==1.2.3 \
    psutil==7.2.2 sqlalchemy==2.1.1 psycopg==3.3.6 requests==2.34.2
```

with

```dockerfile
# Pinned; last refreshed 2026-10-04.
RUN uv pip install fastapi==0.142.2 uvicorn==0.54.0 httpx==0.28.1 python-dotenv==1.2.4 \
    psutil==7.2.2 sqlalchemy==2.1.3 psycopg==3.3.6 requests==2.34.2
```

- [ ] **Step 2: Build and check it**

```bash
ZAROPGX_TAG=deps-test docker buildx bake --load nextflow
docker run --rm --entrypoint nextflow zaromicsresearch/zaropgx-nextflow:deps-test -version | grep -i version   # 25.10.8
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock --entrypoint docker \
  zaromicsresearch/zaropgx-nextflow:deps-test version --format 'client={{.Client.Version}} server={{.Server.Version}}'
```

Expected: `client=29.8.2 server=<Fedora's engine>`, with no "client version … is too old / too new" error. Record the daemon's API version. Before any deploy (not part of this plan), run the same `docker version` check against zimerguz's and the WSL machine's daemons.

- [ ] **Step 3: Runner flag tests**

```bash
uv run --no-sync pytest -q tests/test_nextflow_skip_flags_406.py tests/test_nextflow_cancelled_branch.py tests/test_nextflow_wait_deadline_359.py
```

Expected: pass. End-to-end HLA under 25.10.8 is covered by `tests/e2e/test_bam_hla_pipeline.py` in Task 9.

- [ ] **Step 4: Commit**

```bash
git add docker/nextflow/Dockerfile.nextflow
git commit -m "chore(deps): Nextflow 25.10.8, Docker CLI 29.8.2, runner pins

25.10.x is still the newest legacy-parser line; 26.x stays blocked.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

## Task 7: HAPI FHIR 8.12.0-2 (one-way schema migration, so back up first)

**Files:** modify `compose.yml` (lines 166–171: the comment and the image default), `.env.example:86`, `.env.local:67`, `.env.production:66` (`HAPI_FHIR_TAG=`) and `docs/advanced-configuration.md:80`. Not tracked, update by hand: the Fedora clone's own `.env`, if it sets `HAPI_FHIR_TAG`. If Task 0 merged the bootstrap branch, line numbers may have shifted; find them with `git grep -n 'v8.10.0-2'`.

- [ ] **Step 1: Edit**

```bash
git grep -l 'v8.10.0-2'    # expect exactly the five tracked files listed above; stop if there are others
sed -i 's/v8\.10\.0-2/v8.12.0-2/g' compose.yml .env.example .env.local .env.production docs/advanced-configuration.md
git grep -n 'v8\.1[02]\.0'      # every hit now reads v8.12.0-2
grep -n HAPI_FHIR_TAG .env 2>/dev/null   # update the untracked .env too if present
```

In `compose.yml`, the comment above `fhir-server:` says to update `data/versions/hapi.json`. No such file exists (`git grep -n hapi.json` finds nothing; `app/core/version_manager.py` detects HAPI's version live). Replace that one line:

```yaml
  # Bump ${HAPI_FHIR_TAG} deliberately; update data/versions/hapi.json to match.
```

with

```yaml
  # Bump ${HAPI_FHIR_TAG} deliberately: its schema migration runs on first boot.
```

- [ ] **Step 2: Back up the `fhir` schema** of the stack you're about to upgrade. On Fedora that's the local stack; skip if it has no `fhir` schema yet.

```bash
set -a; . ./.env; set +a
docker compose exec -T db pg_dump -U "${DB_USER:-zaropgx_user}" -d "${DB_NAME:-zaropgx_db}" -n fhir -Fc > dev-notes/fhir-pre-8.12.dump
ls -l dev-notes/fhir-pre-8.12.dump
curl -s 'http://127.0.0.1:8090/fhir/Patient?_count=1' | jq -r '.entry[0].resource.id // "none"'   # note a pre-existing id
```

- [ ] **Step 3: Upgrade and watch the migration**

```bash
docker compose pull fhir-server
docker compose up -d fhir-server
docker compose logs -f fhir-server 2>&1 | grep -m1 -E 'Started Application|APPLICATION FAILED|ERROR'
docker compose logs fhir-server 2>&1 | grep -iE 'migrat' | tail -5
```

Expected: `Started Application`, and migration lines with no failure.

- [ ] **Step 4: Verify**

```bash
curl -s http://127.0.0.1:8090/fhir/metadata | jq -r '.software.version, .fhirVersion'   # 8.12.0 / 4.0.1
id=$(curl -s -X POST http://127.0.0.1:8090/fhir/Patient -H 'Content-Type: application/fhir+json' \
      -d '{"resourceType":"Patient","name":[{"family":"DepsTest"}]}' | jq -r .id)
curl -s "http://127.0.0.1:8090/fhir/Patient/$id" | jq -r '.name[0].family'                 # DepsTest
curl -s "http://127.0.0.1:8090/fhir/Patient/<pre-existing id from step 2>" | jq -r .id      # still readable
curl -s -X DELETE "http://127.0.0.1:8090/fhir/Patient/$id" >/dev/null
```

**Rollback, if any check fails:** stop `fhir-server` and set `HAPI_FHIR_TAG=v8.10.0-2`. Then run `docker compose exec -T db pg_restore -U "$DB_USER" -d "$DB_NAME" --clean --if-exists -n fhir < dev-notes/fhir-pre-8.12.dump`, start it again, and leave HAPI on 8.10 in this branch.

- [ ] **Step 5: Commit**

```bash
git add compose.yml .env.example .env.local .env.production docs/advanced-configuration.md
git commit -m "chore(deps): HAPI FHIR v8.12.0-2

Migration verified on a populated fhir schema, backed up first.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

## Task 8: mtDNA image: haplogrep3 3.3.2 and phylotree-fu-rcrs@1.3

**Files:** create `tests/test_mtdna_haplogrep_pin.py`; modify `docker/mtdna-server-2/Dockerfile` (header comment line 3, plus a new install step), `docker/mtdna-server-2/app.py:136` (`PHYLOTREE`), `docker/mtdna-server-2/README.md` (lines 8, 29, 31) and `app/reports/generator.py:417` (the hardcoded `haplogrep3 3.2.2` in the mtDNA citation).

**Interfaces:** the sidecar reports `HAPLOGREP_VERSION` from the environment (`app.py:_tool_versions`). The new `ENV HAPLOGREP_VERSION` must override the base image's value.

- [ ] **Step 1: Write the failing test** `tests/test_mtdna_haplogrep_pin.py`:

```python
"""The mtDNA citation and the sidecar's tree must name what the image installs.

generator.py's mtDNA citation hardcodes the component versions (no component
manifest is exposed), so this ties that literal, and app.py's PHYLOTREE, to the
Dockerfile pins. The citation can't then silently claim a haplogrep3 the
image no longer ships.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "docker/mtdna-server-2/Dockerfile").read_text(encoding="utf-8")


def _arg(name: str) -> str:
    m = re.search(rf"^ARG {name}=(\S+)$", DOCKERFILE, re.M)
    assert m, f"ARG {name} missing from docker/mtdna-server-2/Dockerfile"
    return m.group(1)


def test_citation_names_the_installed_haplogrep3():
    from app.reports.generator import build_citations

    text = next(c["text"] for c in build_citations() if c["name"] == "mtDNA-server-2")
    assert f"haplogrep3 {_arg('HAPLOGREP_VERSION')}" in text, text


def test_sidecar_classifies_with_the_installed_tree():
    app_py = (ROOT / "docker/mtdna-server-2/app.py").read_text(encoding="utf-8")
    assert f'PHYLOTREE = "{_arg("PHYLOTREE")}"' in app_py
```

- [ ] **Step 2: Run it to verify it fails**

```bash
uv run --no-sync pytest -q tests/test_mtdna_haplogrep_pin.py
```

Expected: FAIL with `ARG HAPLOGREP_VERSION missing`.

- [ ] **Step 3: Look at the release tarball's layout** before writing the install step:

```bash
curl -fsSL https://github.com/genepi/haplogrep3/releases/download/v3.3.2/haplogrep3-3.3.2-linux.tar.gz | tar -tz
```

Expected entries include `haplogrep3` (launcher) and `haplogrep3.jar`. If the names differ, adjust the `cp` line in step 4.

- [ ] **Step 4: Dockerfile.** In `docker/mtdna-server-2/Dockerfile`, change header comment line 3 from

```dockerfile
# haplogrep3 3.2.2 with phylotree-fu-rcrs@1.2 pre-installed, haplocheck 1.3.3,
```

to

```dockerfile
# haplogrep3 (upgraded below), haplocheck 1.3.3,
```

and insert, after the `RUN apt-get update && apt-get install -y --no-install-recommends curl …` step and before `WORKDIR /app`:

```dockerfile
# haplogrep3 3.3.2 over the base image's 3.2.2: upstream mtdna-server-2 has not
# released since v2.1.16. 3.3.x adds Mitotree support and a graphviz fix; the
# classifier is unchanged. The tree stays phylotree-fu-rcrs: 1.3 differs from 1.2
# only in its version and citation fields. Only the jar and launcher are replaced,
# so the base image's haplogrep3.yaml and trees/ survive; install-tree registers 1.3.
ARG HAPLOGREP_VERSION=3.3.2
ARG PHYLOTREE=phylotree-fu-rcrs@1.3
RUN mkdir /tmp/hg \
    && curl -fsSL "https://github.com/genepi/haplogrep3/releases/download/v${HAPLOGREP_VERSION}/haplogrep3-${HAPLOGREP_VERSION}-linux.tar.gz" \
       | tar -xz -C /tmp/hg \
    && cp /tmp/hg/haplogrep3.jar /tmp/hg/haplogrep3 /opt/haplogrep/ \
    && rm -rf /tmp/hg \
    && cd /opt/haplogrep && ./haplogrep3 install-tree "${PHYLOTREE}"
ENV HAPLOGREP_VERSION=${HAPLOGREP_VERSION}
```

- [ ] **Step 5: app.py, citation, README**

In `docker/mtdna-server-2/app.py:136` change `PHYLOTREE = "phylotree-fu-rcrs@1.2"` to `PHYLOTREE = "phylotree-fu-rcrs@1.3"`.

In `app/reports/generator.py:417`, inside the f-string, change `haplogrep3 3.2.2` to `haplogrep3 3.3.2`. Change nothing else on that line.

In `docker/mtdna-server-2/README.md`: on line 8 change `phylotree-fu-rcrs@1.2` to `phylotree-fu-rcrs@1.3`. Change the table row `| haplogrep3 | 3.2.2 |` to `| haplogrep3 | 3.3.2 (installed over the base image's 3.2.2) |`, and `| phylotree | fu-rcrs@1.2 |` to `| phylotree | fu-rcrs@1.3 |`.

```bash
uv run --no-sync pytest -q tests/test_mtdna_haplogrep_pin.py tests/test_mtdna_citation_honesty.py
```

Expected: pass.

- [ ] **Step 6: Build, keeping the old image for the A/B**

```bash
docker pull zaromicsresearch/zaropgx-mtdna:0.3.2     # the released image, not a local e2e rebuild
ZAROPGX_TAG=deps-test docker compose build mtdna
docker run --rm --entrypoint sh zaromicsresearch/zaropgx-mtdna:deps-test -c \
  'echo $HAPLOGREP_VERSION; cd /opt/haplogrep && ./haplogrep3 2>&1 | head -1; ls trees/phylotree-fu-rcrs'
```

Expected: `3.3.2`, `Haplogrep 3 3.3.2`, and a `1.3` directory under the tree.

- [ ] **Step 7: A/B the haplogroup calls**

```bash
mkdir -p /tmp/hgab && chmod 777 /tmp/hgab    # the image runs as mtuser
curl -fsSL -o /tmp/hgab/example-wgs.vcf \
  https://raw.githubusercontent.com/genepi/haplogrep3/v3.3.2/data/examples/example-wgs.vcf
for pair in "0.3.2 phylotree-fu-rcrs@1.2" "deps-test phylotree-fu-rcrs@1.3"; do
  set -- $pair
  docker run --rm -v /tmp/hgab:/w -w /w --entrypoint java zaromicsresearch/zaropgx-mtdna:$1 \
    -jar /opt/haplogrep/haplogrep3.jar classify --tree "$2" --in /w/example-wgs.vcf --out "/w/hg-$1.txt" --extend-report
done
diff <(cut -f1-4 /tmp/hgab/hg-0.3.2.txt) <(cut -f1-4 /tmp/hgab/hg-deps-test.txt) && echo IDENTICAL
```

Expected: `IDENTICAL` (SampleID, Haplogroup, Rank, Quality). Any difference blocks this task: revert it and report.

- [ ] **Step 8: Commit**

```bash
git add tests/test_mtdna_haplogrep_pin.py docker/mtdna-server-2/Dockerfile docker/mtdna-server-2/app.py docker/mtdna-server-2/README.md app/reports/generator.py
git commit -m "chore(deps): haplogrep3 3.3.2, phylotree-fu-rcrs 1.3 in the mtDNA image

Upstream mtdna-server-2 is still on 3.2.2. Calls identical on haplogrep3's
example-wgs.vcf; no Mitotree switch.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

## Task 9: Whole-branch verification, review and hand-off

No new files; fixes go back to the task that owns them.

- [ ] **Step 1: Clean full suite, CI parity, lint, audit**

```bash
uv sync --all-extras --dev
uv run --no-sync pytest -q -m "not e2e" 2>&1 | tail -3
PATH=.venv/bin:/usr/bin:/bin uv run --no-sync pytest -q -m "not e2e" 2>&1 | tail -3   # CI's no-bcftools PATH
uv tool run --from black==26.10.0 black --check app tests
uv tool run --from isort==9.0.2 isort --check-only --profile black app tests
uv tool run --from flake8==7.4.1 flake8 --select=E9,F63,F7,F82 app tests
uv export --frozen --all-extras --no-hashes --no-emit-project -o /tmp/req.txt && uvx pip-audit --no-deps --disable-pip -r /tmp/req.txt
```

- [ ] **Step 2: e2e plus the PharmCAT A/B**

```bash
touch dev-notes/ab/.branch-marker
./scripts/e2e.sh 2>&1 | tee dev-notes/e2e-branch.log | tail -5
bash dev-notes/ab-capture.sh branch dev-notes/ab/.branch-marker
for f in dev-notes/ab/baseline/*.tsv; do
  b="dev-notes/ab/branch/$(basename "$f")"
  if diff -q <(sort "$f") <(sort "$b") >/dev/null; then echo "SAME  $(basename "$f")"; else echo "DIFF  $(basename "$f")"; fi
done
./scripts/e2e-down.sh
```

Expected: e2e green, and every pair `SAME`. Pair any job-id-named files by hand, as in Task 0. A `DIFF` blocks the hand-off: find which task caused it.

- [ ] **Step 3: Real stack, real report, looked at.** Build every image at `deps-test` and run the Fedora stack on it with mtDNA and HLA on:

```bash
ZAROPGX_TAG=deps-test docker buildx bake --load
ZAROPGX_TAG=deps-test docker compose build mtdna
ZAROPGX_TAG=deps-test docker compose up -d
```

In the browser, upload `test_data/pgx_wgs_hla_example.bam` with HLA and mtDNA enabled and wait for completion. Then open the PDF and the interactive report. Check the drugs grid and page breaks, that the mtDNA citation names haplogrep3 3.3.2, and that no "Workflow error" toast appears. Also do a hard refresh in a browser profile that visited before the upgrade, since stale cached JS has broken the UI after a past upgrade. When finished, put the stack back on the released images: `docker compose pull && docker compose up -d` (without `ZAROPGX_TAG`).

- [ ] **Step 4: Independent review.** Dispatch a fresh Opus reviewer on `git diff main...chore/deps-2026-10` with this plan as context. It must check that each change is limited to its task's scope, that the constraints hold (Nextflow 25.x, PyPGx pandas pin, Java 17, no Mitotree) and that no file outside the plan's lists changed. Fix confirmed findings in a new commit.
- [ ] **Step 5: Push and watch**

```bash
git push
gh run list --limit 3
gh run watch <id> --exit-status
```

- [ ] **Step 6: Hand off.** Use superpowers:finishing-a-development-branch; Iliya chooses merge vs PR. Report what was bumped, what was held and why (WeasyPrint if on the fallback path; ecdsa), and the A/B result. In the branch's last commit, `git rm docs/superpowers/plans/2026-10-04-dependency-upgrades.md`: CLAUDE.md keeps plans out of tracked files, and this one was committed only to reach Fedora.

## After this plan

Each of these needs Iliya's explicit go.

- **Release 0.3.3:** bump `version` in `pyproject.toml` and the `ZAROPGX_TAG` defaults (`compose.yml`, `docker-bake.hcl`, `scripts/e2e-up.sh`). Add an `UPGRADING.md` entry that warns about HAPI's schema migration, then tag. Build the images from `git archive <tag>` (not the working tree), push all 8 including mtdna, and run `gh release create`.
- **zimerguz deploy:** back up first, HAPI migration included.
- **Follow-up candidates, not in scope:** replace python-jose with PyJWT (removes ecdsa and a thinly maintained dependency). Decide whether the nextflow container still needs `/var/run/docker.sock`: `main.nf` has no `container` directives, so `-profile docker` may launch nothing. Mitotree. Python 3.13 base images.
