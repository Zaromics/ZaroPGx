"""Write app/genome/grch38_contig_names.tsv from NCBI's GRCh38 assembly reports.

Each GRCh38 sequence has a RefSeq accession (NC_000006.12), a GenBank accession
(CM000668.2) and a UCSC-style name (chr6). An alignment made against NCBI's own
FASTA names its contigs by accession; the pipeline reads UCSC names. The table maps
every accession of every sequence to its UCSC name and length, from the base release
(GCF_000001405.26) and the latest patch release (GCF_000001405.40, GRCh38.p14),
whose fix and novel patches the base lacks. Four base-release RefSeq accessions are
gone from p14's report, so both are read; where both name an accession they agree.

Run: python scripts/make_grch38_contig_names.py [REPORT ...]
With no arguments it downloads the two reports.
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

REPORTS = (
    "https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/001/405/"
    "GCF_000001405.26_GRCh38/GCF_000001405.26_GRCh38_assembly_report.txt",
    "https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/000/001/405/"
    "GCF_000001405.40_GRCh38.p14/GCF_000001405.40_GRCh38.p14_assembly_report.txt",
)
OUT = Path(__file__).resolve().parent.parent / "app/genome/grch38_contig_names.tsv"


def rows(text: str):
    header = None
    for line in text.splitlines():
        if line.startswith("# Sequence-Name"):
            header = line[2:].split("\t")
        elif line and not line.startswith("#"):
            yield dict(zip(header, line.split("\t")))


def main(sources) -> None:
    names = {}
    for source in sources:
        if source.startswith("https://"):
            with urllib.request.urlopen(source) as response:
                text = response.read().decode("utf-8")
        else:
            text = Path(source).read_text(encoding="utf-8")
        for row in rows(text):
            entry = (row["UCSC-style-name"], int(row["Sequence-Length"]))
            for key in ("RefSeq-Accn", "GenBank-Accn"):
                accession = row[key]
                if accession in ("", "na"):
                    continue
                if names.setdefault(accession, entry) != entry:
                    sys.exit(f"{accession}: {names[accession]} vs {entry}")
    lines = [
        "# GRCh38 sequence accessions (RefSeq and GenBank) and their UCSC names.",
        "# From NCBI's assembly reports for GCF_000001405.26 and GCF_000001405.40;",
        "# written by scripts/make_grch38_contig_names.py.",
        "accession\tucsc_name\tlength",
    ]
    lines += [f"{a}\t{n}\t{length}" for a, (n, length) in sorted(names.items())]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{OUT}: {len(names)} accessions")


if __name__ == "__main__":
    main(sys.argv[1:] or REPORTS)
