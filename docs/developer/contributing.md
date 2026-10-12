---
title: Contributing
curation: not
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
