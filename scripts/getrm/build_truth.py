"""Write truth.tsv and samples.tsv: GeT-RM's consensus genotypes for the reference
samples whose 1000 Genomes 30x CRAM is public.

GeT-RM (CDC's Genetic Testing Reference Material program) has characterised 363 Coriell
cell lines across nine studies; its Consolidated PGx and HLA table (Scheinfeldt et al.,
J Mol Diagn 2025;27(6):457-464) gives each gene's call from the most recent study. 208 of
the samples are in the 1000 Genomes high-coverage set (NYGC, 30x, GRCh38), so their
reads can be streamed and run through ZaroPGx (scripts/getrm/cut.sh, run.py, score.py).
HLA-A, -B and -C come from IGSR's typing of the 1000 Genomes panel (Abi-Rached et al.,
PLoS One 2018, typed in silico from exomes), since GeT-RM's HLA study shares only one
sample with the 30x set.

Every value is copied as published: nomenclature as of the study that called it,
parentheses where only one assay confirmed an allele. score.py does the translating.

    python scripts/getrm/build_truth.py [--cache DIR]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
KGP = "https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/data_collections"
SOURCES = {
    "consolidated": (
        "https://www.cdc.gov/lab-quality/media/files/2025/08/"
        "Consolidated_PGx-HLA_table_1-22-25-V4.xlsx",
        "c66174befa7a7bbb6cfaf174cc500a269565407807467204bd7bc6eaae3be109",
    ),
    "index_2504": (
        f"{KGP}/1000G_2504_high_coverage/1000G_2504_high_coverage.sequence.index",
        "0ac5fdda0b60b6575b1867827f53e39d723f915b325b10bf023b2dc3d71c08f2",
    ),
    "index_698": (
        f"{KGP}/1000G_2504_high_coverage/1000G_698_related_high_coverage.sequence.index",
        "d76df08d0dc227ca7b6977e74c3e835f70598b300ed20e391299f6c5cb1b4dcc",
    ),
    "populations": (
        f"{KGP}/1000G_2504_high_coverage/20130606_g1k_3202_samples_ped_population.txt",
        "4e164b717433fc9bfc16e29b20e5b4659b4216185b3275322d421fe3d323c132",
    ),
    "hla": (
        f"{KGP}/HLA_types/20181129_HLA_types_full_1000_Genomes_Project_panel.txt",
        "570b5cc5523273826a5695452f18b77f8742e568e1e35a34aed5d2a313472e8e",
    ),
}
IGSR_HLA = "Abi-Rached et al. PLoS One 2018 doi:10.1371/journal.pone.0206512 (IGSR)"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

# Columns whose header is not just the gene's name: single variants, named by rsID.
SINGLE_VARIANTS = {
    "rs12777823": "CYP2C_rs12777823",
    "rs12714145": "GGCX_rs12714145",
    "rs11676382": "GGCX_rs11676382",
    "rs9923231": "VKORC1_rs9923231",
    "rs61742245": "VKORC1_rs61742245",
    "rs72547529": "VKORC1_rs72547529",
}


def fetch(name: str, cache: Path) -> Path:
    """The source file, downloaded once into the cache and checked against its pin."""
    url, sha256 = SOURCES[name]
    path = cache / url.rsplit("/", 1)[1]
    if not path.exists():
        cache.mkdir(parents=True, exist_ok=True)
        print(f"downloading {url}", file=sys.stderr)
        with urllib.request.urlopen(url, timeout=120) as response:
            path.write_bytes(response.read())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != sha256:
        sys.exit(f"{path}: sha256 {digest}, expected {sha256}; the source has changed")
    return path


def xlsx_sheets(path: Path) -> dict[str, list[list[str]]]:
    """Each sheet of an .xlsx as rows of strings, without a spreadsheet library."""
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(NS + "si"):
            shared.append("".join(t.text or "" for t in si.iter(NS + "t")))
    rels = {
        rel.get("Id"): rel.get("Target")
        for rel in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    }
    sheets = {}
    for sheet in ET.fromstring(z.read("xl/workbook.xml")).find(NS + "sheets"):
        target = rels[sheet.get(RNS + "id")]
        target = target if target.startswith("xl/") else "xl/" + target.lstrip("/")
        rows = []
        for row in ET.fromstring(z.read(target)).iter(NS + "row"):
            cells = {}
            for c in row.findall(NS + "c"):
                col = 0
                for ch in re.match(r"[A-Z]+", c.get("r")).group(0):
                    col = col * 26 + ord(ch) - 64
                v, inline = c.find(NS + "v"), c.find(NS + "is")
                if c.get("t") == "s" and v is not None:
                    text = shared[int(v.text)]
                elif c.get("t") == "inlineStr" and inline is not None:
                    text = "".join(t.text or "" for t in inline.iter(NS + "t"))
                else:
                    text = v.text if v is not None else ""
                cells[col - 1] = " ".join((text or "").split())
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
        sheets[sheet.get("name")] = rows
    return sheets


def study_pmids(rows: list[list[str]]) -> dict[str, str]:
    """The "Study Refs." sheet: study number -> PMID."""
    return {r[0]: r[1] for r in rows if len(r) > 1 and r[0].strip().isdigit()}


def gene_columns(header: list[str]) -> tuple[list, list]:
    """(gene, value column, reference column) for each gene; HLA loci take two value
    columns, one per allele, before their reference column."""
    genes, hla = [], []
    i = 0
    while i < len(header):
        h = header[i].strip()
        nxt = header[i + 1].strip() if i + 1 < len(header) else ""
        if h.startswith("HLA-") and "Reference" not in h:
            hla.append((h.split()[0], i, i + 1, i + 2))
            i += 3
        elif h and "Reference" in nxt:
            genes.append((label(h), i, i + 1))
            i += 2
        elif h.startswith("GGCX"):  # the two GGCX variants share one reference column
            genes.append((label(h), i, i + 2))
            i += 1
        else:
            i += 1
    return genes, hla


def label(header: str) -> str:
    for rsid, name in SINGLE_VARIANTS.items():
        if rsid in header:
            return name
    return header.split()[0]


def crams(index_files: list[Path]) -> dict[str, tuple[str, str]]:
    """Sample -> (run accession, https CRAM URL), from the NYGC sequence.index files."""
    out = {}
    for path in index_files:
        for line in path.read_text().splitlines():
            f = line.split("\t")
            if line.startswith("#") or len(f) < 10:
                continue
            out[f[9]] = (f[2], f[0].replace("ftp://", "https://", 1))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "zaropgx-getrm")
    args = ap.parse_args()

    cram = crams([fetch("index_2504", args.cache), fetch("index_698", args.cache)])
    population = {}
    for line in fetch("populations", args.cache).read_text().splitlines()[1:]:
        f = line.split()
        population[f[1]] = (f[5], f[6])

    sheets = xlsx_sheets(fetch("consolidated", args.cache))
    pmid = study_pmids(sheets["Study Refs."])
    rows = sheets["PGx HLA Genotypes"]
    top = next(i for i, r in enumerate(rows) if r and r[0].startswith("GeT-RM Characterization"))
    genes, hla = gene_columns(rows[top])

    truth, samples = [], {}
    for r in rows[top + 1 :]:
        sample = r[1].strip() if len(r) > 1 else ""
        if sample not in cram:
            continue
        samples[sample] = (*population.get(sample, ("", "")), *cram[sample])
        cell = lambda i: r[i].strip() if i < len(r) else ""  # noqa: E731
        for gene, value, ref in genes:
            if cell(value) and cell(value).lower() not in ("n/a", "-"):
                study = re.split(r"[,\s]+", cell(ref))[0]
                truth.append((sample, gene, cell(value), f"GeT-RM PMID:{pmid.get(study, '?')}"))
        for gene, a, b, ref in hla:
            if cell(a) or cell(b):
                study = re.split(r"[,\s]+", cell(ref))[0] or "8"
                value = "/".join(x for x in (cell(a), cell(b)) if x)
                truth.append((sample, gene, value, f"GeT-RM PMID:{pmid.get(study, '?')}"))

    lines = fetch("hla", args.cache).read_text().splitlines()
    head = lines[0].split("\t")
    for line in lines[1:]:
        f = dict(zip(head, line.split("\t")))
        if f["Sample ID"] not in samples:
            continue
        for locus in ("HLA-A", "HLA-B", "HLA-C"):
            pair = [f.get(f"{locus} {n}", "").strip() for n in (1, 2)]
            if all(pair):
                star = locus.split("-")[1]
                value = "/".join(f"{star}*{allele}" for allele in pair)
                truth.append((f["Sample ID"], locus, value, IGSR_HLA))

    with open(HERE / "truth.tsv", "w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(("sample", "gene", "truth", "source"))
        w.writerows(sorted(truth))
    with open(HERE / "samples.tsv", "w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(("sample", "population", "superpopulation", "run", "cram_url"))
        w.writerows((s, *samples[s]) for s in sorted(samples))
    print(f"{len(samples)} samples, {len(truth)} genotypes", file=sys.stderr)


if __name__ == "__main__":
    main()
