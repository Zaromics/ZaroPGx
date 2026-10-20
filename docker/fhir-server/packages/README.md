# FHIR packages for the bundled HAPI FHIR server

The HL7 Genomics Reporting Implementation Guide 3.0.0 and the two packages it depends on,
as published on the FHIR package registry (https://packages.fhir.org), unmodified. The
`fhir-server` service in `compose.yml` mounts this directory at `/fhir-packages` and
installs them from these files on its first start, so its `$validate` checks ZaroPGx's
FHIR bundles against the IG's profiles. Nothing is downloaded.

| File | Package | From |
|---|---|---|
| `hl7.fhir.uv.genomics-reporting-3.0.0.tgz` | Genomics Reporting Implementation Guide 3.0.0 (STU3) | https://packages.fhir.org/hl7.fhir.uv.genomics-reporting/3.0.0 |
| `hl7.terminology.r4-6.1.0.tgz` | HL7 Terminology (THO) 6.1.0 | https://packages.fhir.org/hl7.terminology.r4/6.1.0 |
| `hl7.fhir.uv.extensions.r4-5.1.0.tgz` | FHIR Extensions Pack 5.1.0 | https://packages.fhir.org/hl7.fhir.uv.extensions.r4/5.1.0 |

HL7 publishes all three under CC0 1.0. `SHA256SUMS` holds their hashes; check them with
`sha256sum -c SHA256SUMS` in this directory.

How the server uses them, how to call its `$validate` and how to update the packages:
`docs/developer/architecture.md`, "The FHIR server's implementation guide".
