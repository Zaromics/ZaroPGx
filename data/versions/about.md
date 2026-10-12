# data/versions

Each service writes `<service>.json` here at startup, `{"name": ..., "version": ...}`.
The report's methods and citations read them. The JSON files are runtime output and
are not tracked.

A manifest also names the versions of the tools inside the service, under
`"components"` (`{"name": "PyPGx", "version": "0.27.0", "components": {"beagle":
"5.4 (22Jul22.46e)", ...}}`). The report credits each tool at that version:

| Manifest | Components |
|---|---|
| pharmcat.json | bcftools, htslib (PharmCAT's VCF preprocessor) |
| pypgx.json | fuc, pysam, scikit-learn, beagle, bcftools, pypgx-bundle |
| gatk.json | picard, htsjdk, samtools, bcftools, htslib |
| optitype.json | yara, seqan, glpk, pyomo, samtools |
| mtdna-server-2.json | mutserve, haplogrep3, haplocheck, phylotree, bcftools, samtools, gatk |
| nextflow.json | (none) |

Kroki's and Mermaid's versions come from Kroki's `/health`, not from a manifest.
