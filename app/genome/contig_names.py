"""GRCh38 contigs named by accession, and the UCSC names the pipeline reads.

An alignment made against NCBI's own GRCh38 FASTA names its contigs by accession:
chromosome 6 is NC_000006.12 (RefSeq) or CM000668.2 (GenBank), the mitochondrion
NC_012920.1 or J01415.2. Every step of the pipeline asks for UCSC names -- chr6, chrM,
chr6_GL000250v2_alt -- and found none of them in such a file: on 2026-10-13 a
whole-genome BAM named this way was read as having no chromosome 6 (zarohla then
converted all of it to FASTQ and filled the disk) and no mitochondrial reads.

The sequences are the same; only the names differ. grch38_contig_names.tsv maps each
accession to its UCSC name and length (scripts/make_grch38_contig_names.py, from
NCBI's assembly reports), and a contig is renamed only when its length matches.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

TABLE = Path(__file__).with_name("grch38_contig_names.tsv")

# The chromosomes' accession numbers, whatever the version. The version is the
# build (NC_000006.12 is GRCh38's chromosome 6, NC_000006.11 GRCh37's), and the
# contig's length already says which build it is, so build detection only needs the
# chromosome. RefSeq numbers them 1-24 (23 = X, 24 = Y); GenBank's CM000663-CM000686
# run in the same order.
_REFSEQ_CHROMOSOME = re.compile(r"^NC_0000(\d\d)\.\d+$")
_GENBANK_CHROMOSOME = re.compile(r"^CM0006(\d\d)\.\d+$")
_MITOCHONDRION = {"NC_012920.1", "J01415.2"}


def _chromosome(number: int) -> Optional[str]:
    if 1 <= number <= 22:
        return str(number)
    return {23: "X", 24: "Y"}.get(number)


def accession_chromosome(name: str) -> Optional[str]:
    """`NC_000006.12` / `CM000668.2` -> `6`, `NC_012920.1` -> `MT`; else None."""
    if name in _MITOCHONDRION:
        return "MT"
    refseq = _REFSEQ_CHROMOSOME.match(name)
    if refseq:
        return _chromosome(int(refseq.group(1)))
    genbank = _GENBANK_CHROMOSOME.match(name)
    if genbank:
        return _chromosome(int(genbank.group(1)) - 62)
    return None


@lru_cache(maxsize=1)
def grch38_accessions() -> Dict[str, Tuple[str, int]]:
    """accession -> (UCSC name, length), for every GRCh38 sequence."""
    table = {}
    for line in TABLE.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or line.startswith("accession\t"):
            continue
        accession, ucsc_name, length = line.split("\t")
        table[accession] = (ucsc_name, int(length))
    return table


def grch38_ucsc_names(contigs: Mapping[str, Optional[int]]) -> Dict[str, str]:
    """The UCSC name of each contig that is a GRCh38 accession of the same length.

    `contigs` is name -> length, as a SAM header's @SQ lines or a VCF's ##contig
    lines give them. A contig whose length is missing or differs is left out: it is
    not the sequence the accession names, whatever it is called.
    """
    table = grch38_accessions()
    renamed = {}
    for name, length in contigs.items():
        known = table.get(name)
        if known and length == known[1]:
            renamed[name] = known[0]
    return renamed


CHROMOSOMES = frozenset(f"chr{n}" for n in [*range(1, 23), "X", "Y", "M"])


def rename_plan(sequences) -> Dict[str, str]:
    """Old name -> UCSC name for a header's accession-named GRCh38 contigs.

    `sequences` is the header's [{"name", "length"}], as inspect_header reports it.
    Empty unless a chromosome is among them: a file named the pipeline's way is left
    alone, whatever odd contigs it also carries.
    """
    contigs = {
        s.get("name"): s.get("length")
        for s in sequences or []
        if isinstance(s, dict) and s.get("name")
    }
    plan = grch38_ucsc_names(contigs)
    return plan if CHROMOSOMES & set(plan.values()) else {}


def renamed_path(path: str) -> str:
    """Where the renamed copy of `path` goes: beside it, under the same suffix, which
    the sidecars read the format from."""
    original = Path(path)
    return str(original.with_name(f"ucsc_{original.name}"))


def _renamed_sam_header(header: str, plan: Mapping[str, str]) -> str:
    lines = []
    for line in header.splitlines():
        if line.startswith("@SQ\t"):
            fields = line.split("\t")
            for i, field in enumerate(fields):
                if field.startswith("SN:") and field[3:] in plan:
                    fields[i] = "SN:" + plan[field[3:]]
            line = "\t".join(fields)
        lines.append(line)
    return "\n".join(lines) + "\n"


def _check_renamed_alignment(source: str, copy: str, header: str) -> None:
    """The copy carries the planned contigs and the source's records.

    Reheadering copies the records unchanged, so the copy differs in size from the
    source by the header alone; anything more means records went missing.
    """
    import pysam

    wanted = [line for line in header.splitlines() if line.startswith("@SQ\t")]
    found = [
        line for line in pysam.view("-H", copy).splitlines() if line.startswith("@SQ\t")
    ]
    if found != wanted:
        raise RuntimeError(f"renamed copy {copy} does not carry the planned contigs")
    difference = abs(Path(copy).stat().st_size - Path(source).stat().st_size)
    if difference > len(header.encode()) + 65536:
        raise RuntimeError(
            f"renamed copy {copy} differs from {source} by {difference} bytes"
        )


def write_renamed_copy(path: str, file_type: str, plan: Mapping[str, str]) -> str:
    """Write `path` with its contigs renamed per `plan`; return the copy's path.

    Only names change. A BAM or CRAM keeps its records byte for byte (`samtools
    reheader`), so a 100 GB genome takes minutes; a CRAM still decodes from the
    reference by each contig's M5. The copy is indexed here, once: the upload's own
    index does not fit it (the header's new length moves every offset), and without
    one the HLA, mtDNA and PyPGx steps each indexed the whole genome themselves,
    about twenty minutes apiece for a 107 GB BAM. A SAM is rewritten record by record, a VCF, gVCF
    or BCF by `bcftools annotate --rename-chrs`. Text that names contigs inside a
    record -- an alignment's SA and XA tags -- keeps the accessions; nothing in the
    pipeline reads them.
    """
    import subprocess
    import sys
    import tempfile

    import pysam

    out = renamed_path(path)
    kind = (file_type or "").lower()
    if kind in ("bam", "cram"):
        header = _renamed_sam_header(pysam.view("-H", path), plan)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".sam", dir=Path(out).parent, delete=False
        ) as handle:
            handle.write(header)
        # In a child process, which keeps a minutes-long copy off the app's GIL, and
        # each format the one way pysam 0.24 writes it to a file: a BAM through
        # save_stdout, a CRAM only to the process's own stdout (it ignores
        # save_stdout and opens fd 1 itself). Each was checked; the header and size
        # check below would catch either going wrong.
        if kind == "bam":
            call = "save_stdout=sys.argv[3]"
        else:
            call = "catch_stdout=False"
        try:
            with open(out, "wb") as target:
                subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "import pysam, sys; pysam.samtools.reheader("
                        f"sys.argv[1], sys.argv[2], {call})",
                        handle.name,
                        path,
                        out,
                    ],
                    stdout=target if kind == "cram" else subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    check=True,
                )
        finally:
            Path(handle.name).unlink(missing_ok=True)
        _check_renamed_alignment(path, out, header)
        # <copy>.bai or <copy>.crai, newer than the copy, so app/utils/shared_input.py
        # hands it to each step with the file. Also in a child process.
        subprocess.run(
            [
                sys.executable,
                "-c",
                "import pysam, sys; pysam.index('-@', '4', sys.argv[1])",
                out,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
        )
    elif kind == "sam":
        with pysam.AlignmentFile(path, "r") as source:
            header = source.header.to_dict()
            for sq in header.get("SQ", []):
                sq["SN"] = plan.get(sq["SN"], sq["SN"])
            with pysam.AlignmentFile(out, "wh", header=header) as target:
                for record in source:
                    target.write(record)
    elif kind in ("vcf", "gvcf", "bcf"):
        name = path.lower()
        output_type = (
            "b" if name.endswith(".bcf") else "v" if name.endswith(".vcf") else "z"
        )
        with tempfile.NamedTemporaryFile(
            "w", suffix=".tsv", dir=Path(out).parent, delete=False
        ) as handle:
            handle.writelines(f"{old}\t{new}\n" for old, new in plan.items())
        try:
            subprocess.run(
                [
                    "bcftools",
                    "annotate",
                    "--rename-chrs",
                    handle.name,
                    f"-O{output_type}",
                    "-o",
                    out,
                    path,
                ],
                stderr=subprocess.PIPE,
                check=True,
            )
        finally:
            Path(handle.name).unlink(missing_ok=True)
    else:
        raise ValueError(f"cannot rename the contigs of a {file_type!r} file")
    return out


_PLAIN_CHROMOSOMES = frozenset([*(str(n) for n in range(1, 23)), "X", "Y", "M", "MT"])


def names_a_chromosome(name: str) -> bool:
    """True when the pipeline reads `name` as a human chromosome: chr1 to chr22, chrX,
    chrY and chrM, the same without `chr` (MT too), or a chromosome's accession."""
    name = (name or "").strip()
    if accession_chromosome(name):
        return True
    bare = name[3:] if name.lower().startswith("chr") else name
    return bare.upper() in _PLAIN_CHROMOSOMES
