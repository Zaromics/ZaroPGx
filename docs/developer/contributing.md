---
title: Contributing
---

# Contributing

## Branches and pull requests

- One branch per concern, based on `main`, so each change can be reviewed and merged on its
  own. Name it `<type>/<short-description>`: `fix/`, `feat/`, `chore/`, `test/`, `ci/`,
  `doc/`. (Not `docs/`: the repository has a branch named `docs`, and Git cannot also have
  `docs/...`.)
- A change that depends on another goes on a branch built on that one, and its pull request
  says so; merge them in order.
- A pull request needs CI green: lint, tests and the end-to-end VCF lane
  ({doc}`testing`).

## Commits

The subject is `type(scope): what changed`, in the imperative, e.g.
`fix(report): label VKORC1, F2 and F5 reference calls as CYP4F2's`. Types in use: `fix`,
`feat`, `docs`, `chore`, `test`, `refactor`, `style`, `ci`, `perf`.

The body says why: what was wrong, how it showed, what the change does, and how you checked
it. A reviewer should not need the conversation that produced it.

## Code

- Formatting is black and isort (profile black); flake8 must find no syntax errors or
  undefined names. Run the commands in {doc}`development-setup` before pushing.
- A fix should come with a test that fails without it.
- Tests run against PostgreSQL ({doc}`testing`). Use the public fixtures in `test_data/`;
  never commit a real person's genotypes, sample identifiers or file names.
- Configuration is read with `os.getenv` or `app.utils.env.env_flag`. Document a new
  variable in `.env.example` and in {doc}`../user/configuration`.
- A schema change needs both the fresh schema and a migration
  ({doc}`development-setup`).

## Releases

The version is set in `pyproject.toml`. It is also stamped into the image tags, the `.env`
templates, the bootstrap and start scripts, `compose.yml`, `docker-bake.hcl` and a few
service wrappers; `git grep -n -F <old version>` lists them. Add the release's upgrade
notes to `UPGRADING.md`.

### Cutting a release

The version bump is a pull request like any other. Everything below happens once it is on
`main`.

**1. Confirm the merge by ancestry, not by the pull request's state.** A pull request based
on another branch rather than on `main` reports `MERGED` once it has gone into that branch,
which may itself still be behind:

```bash
git merge-base --is-ancestor \
  "$(gh pr view <number> --json mergeCommit --jq .mergeCommit.oid)" origin/main
```

**2. Tag it, annotated:**

```bash
git tag -a v<version> -m "ZaroPGx <version>"
git push origin v<version>
```

**3. Build and push the bake targets together.** `docker-bake.hcl`'s default group covers
app, pharmcat, nextflow, pypgx, gatk-api, genome-downloader and zarohla:

```bash
ZAROPGX_TAG=<version> docker buildx bake --push
```

**4. Publish `mtdna` by hand.** It is deliberately not a bake target — its base image is
around 4.5 GB and would exhaust a GitHub Actions runner's disk, and the reason is written
at the top of `docker-bake.hcl` — but it is still part of the release:

```bash
docker compose build mtdna
docker push zaromicsresearch/zaropgx-mtdna:<version>
```

**5. Check all eight images are on the registry** — the seven baked ones plus `mtdna` —
before anything updates against the new tag:

```bash
for i in app pharmcat nextflow pypgx gatk-api genome-downloader zarohla mtdna; do
  printf '  %-20s ' "$i"
  docker manifest inspect "zaromicsresearch/zaropgx-$i:<version>" >/dev/null 2>&1 \
    && echo ok || echo MISSING
done
```

**6. Create the GitHub release** against the tag, with the notes that went into
`UPGRADING.md`.

### Updating an installation

Set `ZAROPGX_TAG` in that installation's `.env` to the new version, then:

```bash
docker compose pull && docker compose up -d
```

**Pull before `up`, in that order.** A service that starts at the old tag can read newer
reference data as stale and rebuild it backwards: a pre-0.4.2 genome-downloader meeting a
version 3 annotation bundle rebuilds it down to version 2, and the new image then has to
build it again — about a gigabyte of downloads to arrive where it started.

Check that no job is in flight first, because restarting the app loses the one it is
running:

```bash
docker compose exec -T db psql -U "$(grep ^DB_USER= .env | cut -d= -f2-)" \
  -d "$(grep ^DB_NAME= .env | cut -d= -f2-)" \
  -tAc "select count(*) from jobs where status in ('pending','running','processing')"
```

An update that reaches an installation other people use is worth scripting rather than
typing, and the script is worth making refuse to run: a dirty checkout, a job in flight, an
upload that arrived in the last few minutes. Have it **compare the digest of each image it
pulled against what the registry published** for that tag, and take the pulled digest from
`RepoDigests` in `docker image inspect` — that is the manifest-list digest `docker pull`
records. It is not what the first entry of `docker manifest inspect -v` gives, which is a
per-platform sub-manifest: a real digest, a different string, and comparing the two reports
a mismatch on a correctly published image.

Rolling back is the tag before it, with `ZAROPGX_TAG` put back as well:

```bash
git checkout v<previous> && docker compose up -d
```

Read `UPGRADING.md` first for a schema or data migration, which decides whether the previous
release can read the current database at all.
