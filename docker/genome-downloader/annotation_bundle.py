"""Build the annotation the unlisted-variant check reads: what is known about each
position of the pharmacogenes' coding exons, cut from public sources to those exons.

The check (docker/pypgx/unlisted_variants.py) looks at every variant a run finds in
these genes that no PharmCAT or PyPGx allele definition lists, and says what it does
to the protein, how common it is and what ClinVar says of it. It runs offline: this
bundle is all it reads. The genome downloader builds it once, into
/reference/annotation (ANNOTATION_DIR), after the reference files.

Every source is free to use, commercial use included, so a company running ZaroPGx
can use it as it is:

- Ensembl's GFF3 (release 116), its MANE Select transcript of each gene: the
  consequence, through bcftools csq. MANE's summary (v1.5) gives each its RefSeq name.
- AlphaMissense (Cheng et al., Science 2023), DeepMind's missense scores, CC BY 4.0
  by its README; the header inside the file still carries an earlier licence, so the
  README is kept beside it.
- gnomAD v4.1's joint frequencies, CC0, read by region from the public bucket. The
  joint release holds frequencies only; the exome and genome releases also carry CADD,
  REVEL and SpliceAI scores, which are not free for commercial use.
- ClinVar (NCBI; no restrictions on use), a dated weekly release.
- GIAB's v3.6 stratifications (NIST): segmental duplications and low mappability,
  where a variant call is less sure.

Not CADD, REVEL, SpliceAI, dbNSFP or ClinPGx's annotations: none is free for
commercial use.

Sources are fetched once into a cache, checked against pinned SHA-256s, and the cut
files and manifest.json (each source's version, URL and checksum) written to OUT_DIR.
About 1 GB is fetched (AlphaMissense 643 MB, ClinVar 201 MB, the GFF3 108 MB) plus
gnomAD's ranges; about 20 MB is kept.

    python annotation_bundle.py OUT_DIR [--cache DIR]

Standard library only: gnomAD's ranges are read through its tabix index (remote_records).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import struct
import sys
import time
import urllib.request
import zlib
from bisect import bisect_right
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

# Bump when the genes, a source or a file below changes: the downloader rebuilds a
# bundle whose manifest names another version.
BUNDLE_VERSION = "1"

# The genes checked: drug-metabolising enzymes and transporters, and the other genes
# whose reduced function changes a drug's effect (G6PD, VKORC1, NUDT15, TPMT, BCHE).
# Left out on purpose, though PyPGx calls them: genes where a damaging variant means a
# disease rather than a drug response (ATM, APOE, CFTR, F2, F5, RYR1 and CACNA1S, whose
# malignant hyperthermia variants PharmCAT already reads, CYP1B1, CYP17A1, CYP19A1,
# CYP26A1, CYP2R1, ITPA, POR, PTGIS, TBXAS1, XPC), so the check cannot report an
# incidental finding; and the receptors and other genes PyPGx types by a single
# association variant (ADRA2A, ADRB2, ANKK1, BDNF, COMT, DBH, DRD2, GRIK1, GRIK4,
# GRIN2B, HTR1A, HTR2A, IFNL3, IFNL4, ITGB3, MTHFR, OPRK1, OPRM1, RARG, SLC28A3,
# SLC47A2, SLC6A4, ACYP2), where a rare variant's effect on a drug is not known.
GENES = (
    "ABCB1", "ABCG2", "BCHE", "CYP1A1", "CYP1A2", "CYP2A6", "CYP2A13", "CYP2B6",
    "CYP2C8", "CYP2C9", "CYP2C19", "CYP2D6", "CYP2E1", "CYP2F1", "CYP2J2", "CYP2S1",
    "CYP2W1", "CYP3A4", "CYP3A5", "CYP3A7", "CYP3A43", "CYP4A11", "CYP4A22", "CYP4B1",
    "CYP4F2", "DPYD", "G6PD", "GSTM1", "GSTP1", "GSTT1", "NAT1", "NAT2", "NUDT15",
    "SLC15A2", "SLC22A2", "SLCO1B1", "SLCO1B3", "SLCO2B1", "SULT1A1", "TPMT",
    "UGT1A1", "UGT1A4", "UGT1A6", "UGT2B7", "UGT2B15", "UGT2B17", "VKORC1",
)
# Each coding exon is read this far either side: the splice region (8 bp into the
# intron) and the splice sites inside it.
FLANK = 20

ENSEMBL = "https://ftp.ensembl.org/pub/release-116/gff3/homo_sapiens"
GIAB = (
    "https://ftp-trace.ncbi.nlm.nih.gov/ReferenceSamples/giab/release/"
    "genome-stratifications/v3.6/GRCh38@all"
)
GNOMAD = (
    "https://storage.googleapis.com/gcp-public-data--gnomad/release/4.1/vcf/joint/"
    "gnomad.joint.v4.1.sites.{chrom}.vcf.bgz"
)
# name: (url, sha256, version, licence)
SOURCES = {
    "gff3": (
        f"{ENSEMBL}/Homo_sapiens.GRCh38.116.chr.gff3.gz",
        "0857407b4a8be8792089a69fd961737256fb7129b6b799f8a8920ebea4fb134f",
        "Ensembl release 116",
        "Ensembl: no restrictions on use",
    ),
    "mane": (
        "https://ftp.ncbi.nlm.nih.gov/refseq/MANE/MANE_human/release_1.5/"
        "MANE.GRCh38.v1.5.summary.txt.gz",
        "d10ace2720681a3b2e0eefd9da4f551274a6b4141ac9bfd6a2565dfb6e9ad55c",
        "MANE v1.5",
        "NCBI and EMBL-EBI: no restrictions on use",
    ),
    "alphamissense": (
        "https://storage.googleapis.com/dm_alphamissense/AlphaMissense_hg38.tsv.gz",
        "0516cfd71c0767ac8f9c469252d429000e94e02c008b6e3a46d4b4646fcd3475",
        "AlphaMissense (2023), Zenodo 10.5281/zenodo.10813168",
        "CC BY 4.0",
    ),
    "alphamissense_readme": (
        "https://raw.githubusercontent.com/google-deepmind/alphamissense/"
        "main/README.md",
        "55af68037f873fdfe3944a099ed5b008319aa6d476be29c68d2e3c68cb41e7d5",
        "the README stating the predictions' licence",
        "Apache 2.0",
    ),
    "clinvar": (
        "https://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/archive_2.0/2026/"
        "clinvar_20261011.vcf.gz",
        "05d9481dd7217cc4df15803362d4a4c9eae5875b27b1750867d6bfd2643b3d10",
        "ClinVar 2026-10-11",
        "NCBI: no restrictions on use",
    ),
    "segdups": (
        f"{GIAB}/SegmentalDuplications/GRCh38_segdups.bed.gz",
        "09ac029a7d0d681a9f0ed6581a6bc10fc708a834bc3f91c46a9bef504f6055c5",
        "GIAB stratifications v3.6",
        "NIST: public domain",
    ),
    "lowmap": (
        f"{GIAB}/Union/GRCh38_alllowmapandsegdupregions.bed.gz",
        "dfcfbbe52e10daa94522087f0e7c4d183da5595379c47845e2c38e594bcb9252",
        "GIAB stratifications v3.6",
        "NIST: public domain",
    ),
}
GNOMAD_SOURCE = {
    "url": GNOMAD.format(chrom="chrN"),
    "version": "gnomAD v4.1 joint",
    "licence": "CC0",
}
GNOMAD_FIELDS = (
    "AF_joint", "AF_grpmax_joint", "grpmax_joint", "fafmax_faf95_max_joint",
    "nhomalt_joint", "nhomalt_joint_XY",
)
CLINVAR_FIELDS = ("CLNSIG", "CLNREVSTAT", "CLNDN", "GENEINFO")


def log(message: str) -> None:
    print(f"annotation bundle: {message}", file=sys.stderr, flush=True)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(name: str, cache: Path) -> Path:
    """The source, downloaded once into the cache and checked against its pin."""
    url, sha256 = SOURCES[name][:2]
    path = cache / url.rsplit("/", 1)[1]
    if not path.exists() or sha256_of(path) != sha256:
        cache.mkdir(parents=True, exist_ok=True)
        log(f"fetching {url}")
        partial = path.with_name(path.name + ".part")
        with urllib.request.urlopen(url, timeout=300) as response, open(partial, "wb") as f:
            shutil.copyfileobj(response, f, 1 << 20)
        partial.replace(path)
    found = sha256_of(path)
    if found != sha256:
        raise RuntimeError(f"{path.name}: sha256 {found}, pinned {sha256}")
    return path


def attributes(column: str) -> Dict[str, str]:
    return dict(a.split("=", 1) for a in column.strip().split(";") if "=" in a)


def mane_transcripts(gff3: Path, mane: Path, out_gff: Path) -> List[dict]:
    """Write the GFF3 of each gene's MANE Select transcript, with chr-named contigs as
    bcftools csq and the run's VCF use, and return the transcripts' coordinates."""
    refseq = {}
    with gzip.open(mane, "rt") as f:
        for line in f:
            if line.startswith("#"):
                continue
            col = line.rstrip("\n").split("\t")
            if col[9] == "MANE Select":
                refseq[col[7]] = col[5]  # ENST00000370192.8 -> NM_000110.4
    genes, chosen, kept, models = {}, set(), [], {}
    wanted = set(GENES)
    with gzip.open(gff3, "rt") as f:
        for line in f:
            if line.startswith("#"):
                continue
            col = line.rstrip("\n").split("\t")
            if len(col) < 9:
                continue
            a = attributes(col[8])
            if col[2] == "gene" and a.get("Name") in wanted:
                genes[a["ID"]] = a["Name"]
                kept.append(col)
            elif a.get("Parent") in genes and "MANE_Select" in a.get("tag", ""):
                chosen.add(a["ID"])
                kept.append(col)
                tid = a["ID"].split(":", 1)[1]
                models[a["ID"]] = {
                    "gene": genes[a["Parent"]],
                    "transcript": f"{tid}.{a.get('version', '')}".rstrip("."),
                    "refseq": refseq.get(f"{tid}.{a.get('version', '')}"),
                    "chrom": "chr" + col[0],
                    "strand": col[6],
                    "exons": [],
                    "cds": [],
                }
            elif a.get("Parent") in chosen:
                kept.append(col)
                model = models[a["Parent"]]
                span = [int(col[3]), int(col[4])]
                if col[2] == "exon":
                    model["exons"].append(span)
                elif col[2] == "CDS":
                    model["cds"].append(span)
    with gzip.open(out_gff, "wt") as out:
        out.write("##gff-version 3\n")
        for col in kept:
            out.write("\t".join(["chr" + col[0]] + col[1:]) + "\n")
    found = sorted(models.values(), key=lambda m: m["gene"])
    for m in found:
        m["exons"].sort()
        m["cds"].sort()
    return found


def coding_regions(models: List[dict]) -> Dict[str, List[Tuple[int, int]]]:
    """Each chromosome's coding exons, FLANK either side, merged: 1-based, inclusive."""
    spans: Dict[str, List[Tuple[int, int]]] = {}
    for m in models:
        for start, end in m["cds"]:
            spans.setdefault(m["chrom"], []).append((start - FLANK, end + FLANK))
    merged = {}
    for chrom, items in spans.items():
        out: List[List[int]] = []
        for start, end in sorted(items):
            if out and start <= out[-1][1] + 1:
                out[-1][1] = max(out[-1][1], end)
            else:
                out.append([start, end])
        merged[chrom] = [tuple(x) for x in out]
    return merged


class Regions:
    """Membership of a position in the merged regions of its chromosome."""

    def __init__(self, regions: Dict[str, List[Tuple[int, int]]]):
        self.regions = regions
        self.starts = {c: [s for s, _ in r] for c, r in regions.items()}

    def __contains__(self, key: Tuple[str, int]) -> bool:
        chrom, pos = key
        starts = self.starts.get(chrom)
        if not starts:
            return False
        i = bisect_right(starts, pos) - 1
        return i >= 0 and self.regions[chrom][i][1] >= pos

    def overlaps(self, chrom: str, start: int, end: int) -> bool:
        starts = self.starts.get(chrom)
        if not starts:
            return False
        i = bisect_right(starts, end) - 1
        return i >= 0 and self.regions[chrom][i][1] >= start


def write_regions_bed(regions: Dict[str, List[Tuple[int, int]]], path: Path, prefix: str = "chr") -> None:
    with open(path, "w") as out:
        for chrom in sorted(regions, key=chrom_order):
            name = chrom if prefix == "chr" else chrom.removeprefix("chr")
            for start, end in regions[chrom]:
                out.write(f"{name}\t{start - 1}\t{end}\n")


def chrom_order(chrom: str) -> Tuple[int, str]:
    tail = chrom.removeprefix("chr")
    return (int(tail), "") if tail.isdigit() else (100, tail)


def cut_alphamissense(path: Path, regions: Regions, out: Path) -> int:
    n = 0
    with gzip.open(path, "rt") as f, gzip.open(out, "wt") as w:
        w.write("chrom\tpos\tref\talt\tprotein_variant\tam_pathogenicity\tam_class\n")
        for line in f:
            if line.startswith("#"):
                continue
            col = line.split("\t", 10)
            if (col[0], int(col[1])) in regions:
                w.write("\t".join((col[0], col[1], col[2], col[3], col[7], col[8], col[9].strip())) + "\n")
                n += 1
    return n


def vcf_records(lines: Iterable[str]):
    for line in lines:
        if line.startswith("#"):
            continue
        col = line.rstrip("\n").split("\t")
        if len(col) >= 8:
            yield col


def info_fields(column: str) -> Dict[str, str]:
    out = {}
    for item in column.split(";"):
        key, _, value = item.partition("=")
        out[key] = value
    return out


def _overlaps(regions: Regions, chrom: str, pos: str, ref: str) -> bool:
    """A record's reference bases overlap the regions: a deletion that starts outside
    an exon's flank and runs into its splice site is in, as the check reads it
    (bcftools view --targets-overlap 1)."""
    start = int(pos)
    return regions.overlaps(chrom, start, start + len(ref) - 1)


def cut_clinvar(path: Path, regions: Regions, out: Path) -> int:
    """ClinVar's records in the regions: its contigs are named 1, 2, ..., X."""
    seen, n = set(), 0
    with gzip.open(path, "rt") as f, gzip.open(out, "wt") as w:
        w.write("chrom\tpos\tref\talt\tvariation_id\t" + "\t".join(f.lower() for f in CLINVAR_FIELDS) + "\n")
        for col in vcf_records(f):
            key = ("chr" + col[0], col[1], col[3], col[4])
            if col[4] in (".", "") or key in seen or not _overlaps(regions, key[0], col[1], col[3]):
                continue
            seen.add(key)
            info = info_fields(col[7])
            w.write("\t".join((*key, col[2], *(info.get(f, "") for f in CLINVAR_FIELDS))) + "\n")
            n += 1
    return n


# --- reading a tabix-indexed file by range, over HTTP ------------------------------
# gnomAD's joint chromosome files are 2 to 39 GB; the regions need a few MB of each.
# This reads what its .tbi index points to (SAM/BAM spec, section 5.2: the binning and
# linear index) with HTTP range requests, and inflates the BGZF blocks with zlib. In
# Python because the downloader's image has no htslib, and Debian's tabix cannot open
# an https URL ("Protocol not supported").

BGZF_MAX_BLOCK = 65536


def _reg2bins(beg: int, end: int) -> List[int]:
    """The bins overlapping [beg, end), 0-based (SAM spec 5.3)."""
    end -= 1
    bins = [0]
    for shift, offset in ((26, 1), (23, 9), (20, 73), (17, 585), (14, 4681)):
        bins += range(offset + (beg >> shift), offset + (end >> shift) + 1)
    return bins


def tabix_index(data: bytes) -> Dict[str, Tuple[Dict[int, List[Tuple[int, int]]], List[int]]]:
    """A .tbi file: {contig: ({bin: [(start, end) virtual offsets]}, linear index)}."""
    raw = gzip.decompress(data)
    if raw[:4] != b"TBI\x01":
        raise ValueError("not a tabix index")
    (n_ref,) = struct.unpack_from("<i", raw, 4)
    (l_nm,) = struct.unpack_from("<i", raw, 32)
    names = raw[36 : 36 + l_nm].split(b"\0")[:n_ref]
    pos = 36 + l_nm
    index = {}
    for name in names:
        (n_bin,) = struct.unpack_from("<i", raw, pos)
        pos += 4
        bins = {}
        for _ in range(n_bin):
            bin_id, n_chunk = struct.unpack_from("<Ii", raw, pos)
            pos += 8
            chunks = list(struct.iter_unpack("<QQ", raw[pos : pos + 16 * n_chunk]))
            pos += 16 * n_chunk
            bins[bin_id] = chunks
        (n_intv,) = struct.unpack_from("<i", raw, pos)
        pos += 4
        linear = [x for (x,) in struct.iter_unpack("<Q", raw[pos : pos + 8 * n_intv])]
        pos += 8 * n_intv
        index[name.decode()] = (bins, linear)
    return index


def _chunks(index_entry, spans: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """The virtual-offset chunks holding the records over the spans (1-based,
    inclusive), merged where they touch."""
    bins, linear = index_entry
    found = []
    for start, end in spans:
        beg = start - 1
        floor = linear[min(beg >> 14, len(linear) - 1)] if linear else 0
        for b in _reg2bins(beg, end):
            found += [(s, e) for s, e in bins.get(b, ()) if e > floor]
    merged: List[List[int]] = []
    for s, e in sorted(found):
        if merged and (s >> 16) <= (merged[-1][1] >> 16) + 4 * BGZF_MAX_BLOCK:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def _get(url: str, headers: Optional[Dict[str, str]] = None) -> bytes:
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(request, timeout=300) as response:
                return response.read()
        except OSError as e:
            if attempt == 3:
                raise
            log(f"{url}: {e}; retrying")
            time.sleep(10 * (attempt + 1))
    raise AssertionError("unreachable")


def _inflate_bgzf(data: bytes) -> Tuple[bytes, List[int]]:
    """The text of the whole BGZF blocks in data, and where each block's text starts."""
    out, starts, pos = [], [], 0
    while pos + 18 <= len(data):
        (bsize,) = struct.unpack_from("<H", data, pos + 16)
        block = data[pos : pos + bsize + 1]
        if len(block) < bsize + 1:
            break  # cut off by the range's end
        starts.append(sum(len(x) for x in out))
        out.append(zlib.decompress(block, 31))
        pos += bsize + 1
    return b"".join(out), starts


def remote_records(url: str, index_entry, spans: List[Tuple[int, int]]) -> Iterable[List[str]]:
    """The VCF records of a remote, tabix-indexed file over the spans (and some beside
    them, which the caller filters out)."""
    for start, end in _chunks(index_entry, spans):
        first, last = start >> 16, (end >> 16) + BGZF_MAX_BLOCK
        data = _get(url, {"Range": f"bytes={first}-{last}"})
        text, _ = _inflate_bgzf(data)
        text = text[start & 0xFFFF :]
        yield from vcf_records(text.decode().splitlines())


def cut_gnomad(regions: Dict[str, List[Tuple[int, int]]], out: Path) -> int:
    """gnomAD's records over the regions, read by range from the public bucket."""
    inside = Regions(regions)
    n = 0
    with gzip.open(out, "wt") as w:
        w.write("chrom\tpos\tref\talt\tfilter\t" + "\t".join(GNOMAD_FIELDS) + "\n")
        for chrom in sorted(regions, key=chrom_order):
            url = GNOMAD.format(chrom=chrom)
            index = tabix_index(_get(url + ".tbi"))
            seen = set()
            for col in remote_records(url, index[chrom], regions[chrom]):
                key = (col[0], col[1], col[3], col[4])
                if key in seen or not _overlaps(inside, col[0], col[1], col[3]):
                    continue
                seen.add(key)
                info = info_fields(col[7])
                w.write("\t".join((*key, col[6], *(info.get(f, "") for f in GNOMAD_FIELDS))) + "\n")
                n += 1
            log(f"gnomAD {chrom}: {len(seen)} records")
    return n


def cut_bed(path: Path, regions: Regions, label: str, w) -> int:
    n = 0
    with gzip.open(path, "rt") as f:
        for line in f:
            if line.startswith(("#", "track", "browser")):
                continue
            col = line.split("\t")
            start, end = int(col[1]), int(col[2])
            if regions.overlaps(col[0], start + 1, end):
                w.write(f"{col[0]}\t{start}\t{end}\t{label}\n")
                n += 1
    return n


def build(out_dir: Path, cache: Path) -> dict:
    staging = out_dir.with_name(out_dir.name + ".building")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        paths = {name: fetch(name, cache) for name in SOURCES}
        models = mane_transcripts(paths["gff3"], paths["mane"], staging / "transcripts.gff3.gz")
        regions = coding_regions(models)
        inside = Regions(regions)
        write_regions_bed(regions, staging / "regions.bed")
        counts = {
            "transcripts": len(models),
            "alphamissense": cut_alphamissense(paths["alphamissense"], inside, staging / "alphamissense.tsv.gz"),
            "clinvar": cut_clinvar(paths["clinvar"], inside, staging / "clinvar.tsv.gz"),
            "gnomad": cut_gnomad(regions, staging / "gnomad.tsv.gz"),
        }
        with gzip.open(staging / "lowconf.bed.gz", "wt") as w:
            counts["segdups"] = cut_bed(paths["segdups"], inside, "segmental duplication", w)
            counts["lowmap"] = cut_bed(paths["lowmap"], inside, "low mappability or segmental duplication", w)
        shutil.copy(paths["alphamissense_readme"], staging / "AlphaMissense_README.md")
        (staging / "transcripts.json").write_text(json.dumps(models, indent=1))
        manifest = {
            "bundle_version": BUNDLE_VERSION,
            "built": time.strftime("%Y-%m-%d", time.gmtime()),
            "genes": list(GENES),
            "genes_without_transcript": sorted(set(GENES) - {m["gene"] for m in models}),
            "flank": FLANK,
            "counts": counts,
            "sources": {
                name: {"url": url, "sha256": sha, "version": version, "licence": licence}
                for name, (url, sha, version, licence) in SOURCES.items()
            }
            | {"gnomad": GNOMAD_SOURCE},
            "files": {
                p.name: sha256_of(p) for p in sorted(staging.iterdir()) if p.is_file()
            },
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=1))
        shutil.rmtree(out_dir, ignore_errors=True)
        staging.replace(out_dir)
        return manifest
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def current(out_dir: Path) -> Optional[dict]:
    """The built bundle's manifest, or None when there is none of this version."""
    try:
        manifest = json.loads((out_dir / "manifest.json").read_text())
    except (OSError, ValueError):
        return None
    return manifest if manifest.get("bundle_version") == BUNDLE_VERSION else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--cache", type=Path, help="where sources are kept (default: OUT_DIR/../annotation-cache)")
    args = ap.parse_args()
    cache = args.cache or args.out_dir.parent / "annotation-cache"
    manifest = build(args.out_dir, cache)
    log(f"built {args.out_dir}: {manifest['counts']}")


if __name__ == "__main__":
    os.umask(0o022)
    main()
