# Security policy

## Reporting a vulnerability

Report it privately, through GitHub: on the repository's **Security** tab, choose **Report a
vulnerability** (<https://github.com/Zaromics/ZaroPGx/security/advisories/new>). Please do
not open a public issue, pull request or discussion for it.

Say which version or image tag is affected, how to reproduce the problem, and what an attacker
could do with it. Fixes are made on `main` and ship in the next release; the advisory says
which release has the fix.

**Never include genetic data or real sample identifiers in a report.** No VCF, BCF, gVCF,
BAM, CRAM, SAM or FASTQ files, no reports made from them, and no sample IDs, patient IDs or
file names that belong to a real person, yours included. Reproduce the problem with the public
files in `test_data/` or with **Run Demo** (NA12878, a public sample). Before you paste a log,
replace paths, IDs, passwords, tokens and cookies with placeholders.

## Scope

In scope:

- **The app**: the web app and its API (`app/`), the Nextflow pipeline (`pipelines/`), the
  service wrappers under `docker/`, and the reports and FHIR bundles they write.
- **The images**: the `zaromicsresearch/zaropgx-*` images, as built from this repository.
- **The bootstrap and start scripts**: `bootstrap.sh`, `bootstrap.ps1`, `start-docker.sh` and
  `start-docker.ps1`.

Out of scope: vulnerabilities in the tools ZaroPGx runs but did not write (PharmCAT, PyPGx,
GATK, OptiType, mtDNA-Server 2 and the others in [the credits](docs/user/credits.md)) and in
the third-party images it pulls (PostgreSQL, HAPI FHIR, Kroki); please report those to their
projects. How ZaroPGx packages, configures or calls them is in scope.

By default the app listens on `127.0.0.1:8765` and asks for no login
(`ZAROPGX_AUTH_MODE=open`); that is documented behaviour, not a vulnerability. A way past the
shared password (`ZAROPGX_AUTH_MODE=password`) is in scope, and so is a way to reach, from
another machine, a service the stack binds to `127.0.0.1`.

## Logs written before 0.4.0 may contain the database password

Before 0.4.0, the app wrote the database password to its log (`docker logs pgx_app`): on
every call to `GET /services-status`, which the web page polls, and, at the default
`LOG_LEVEL=DEBUG`, on every start, in an error from its PostgreSQL version lookup.
`GET /services-status` also logged each request's headers, the login cookie included. 0.4.0 keeps both out of the log and out of the
API's answers ([#155](https://github.com/Zaromics/ZaroPGx/pull/155)).

What to do:

1. **Update to 0.4.0** ([UPGRADING.md](UPGRADING.md)). The update recreates the app's
   container, and the container's old log goes with it. To be sure it has gone:
   `docker compose up -d --force-recreate app`.
2. **Delete the copies** of the old log you can reach: saved files, a log collector,
   anything pasted into an issue or a chat.
3. **If anyone else could read that log**, change the database password. Change it in
   PostgreSQL first, because PostgreSQL reads `DB_PASSWORD` only when it creates an empty
   database ([UPGRADING.md](UPGRADING.md), *Per-install secrets*):

   ```bash
   docker compose exec db psql -U zaropgx_user -d zaropgx_db
   # at the psql prompt; it asks for the new password twice:
   \password zaropgx_user
   ```

   (Use your `DB_USER` and `DB_NAME` from `.env` if you changed them.) Then put the same
   password in `.env` as `DB_PASSWORD` and run `docker compose up -d`. If you use the shared
   password, also set a new `SECRET_KEY` in `.env`: the login cookie is signed with it, so a
   new key signs everyone out and makes a logged cookie useless.

The database port is bound to `127.0.0.1:5444` unless you set `INTERNAL_BIND_ADDRESS`, so the
password alone does not open the database to your network.
