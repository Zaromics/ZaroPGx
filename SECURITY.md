# Security Policy

## Reporting a vulnerability

Please report it privately, through GitHub: **Security** tab → **Report a vulnerability**
(<https://github.com/Zaromics/ZaroPGx/security/advisories/new>). Do not open a public issue.

Include the affected version or image tag, how to reproduce it, and its impact.

**Never include genetic data or real sample identifiers.** Reproduce with the public files in
`test_data/` or with **Run Demo** (NA12878). Redact paths, IDs, passwords and cookies from logs.

## Scope

In scope: the app and its API, the Nextflow pipeline, the service wrappers, the
`zaromicsresearch/zaropgx-*` images, and the bootstrap and start scripts. Vulnerabilities in
the third-party tools and images ZaroPGx runs (PharmCAT, PyPGx, GATK, PostgreSQL, HAPI FHIR
and others) belong to those projects; how ZaroPGx configures or calls them is in scope.

## Logs from before 0.4.0

Before 0.4.0, the app could write the database password to its log (fixed in
[#155](https://github.com/Zaromics/ZaroPGx/pull/155)). After updating:

1. Delete any saved or shared copies of the old `pgx_app` log. Updating recreates the
   container, which discards its own log.
2. If anyone else could read that log, change the database password: in PostgreSQL first
   (`docker compose exec db psql -U zaropgx_user -d zaropgx_db`, then `\password zaropgx_user`),
   then `DB_PASSWORD` in `.env`, then `docker compose up -d`. With the shared password on, also
   set a new `SECRET_KEY`.
