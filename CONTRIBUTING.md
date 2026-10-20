# Contributing to ZaroPGx

Contributions are welcome. The norms are in the documentation, not repeated here:

- [Contributing](docs/developer/contributing.md): branches, commits, pull requests, code and
  releases. In short: one branch per concern, based on `main` and named
  `<type>/<short-description>` (`doc/`, not `docs/`); a fix comes with a test that fails
  without it; a pull request needs CI green.
- [Development setup](docs/developer/development-setup.md): running the stack from source,
  formatting and lint, schema changes.
- [Testing](docs/developer/testing.md): the fast suite, which runs against a throwaway
  PostgreSQL, and the end-to-end lanes.

Use the public fixtures in `test_data/`. Never commit, attach or paste a real person's
genotypes, reports, sample identifiers or file names, in code, tests, issues or pull requests.

To report a bug or ask for a feature, open an issue from its template. To report a security
problem, do not open an issue: see [SECURITY.md](SECURITY.md).
