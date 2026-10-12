import os
from pathlib import Path


def is_compressed_file(file_path: str | Path) -> bool:
    """Best-effort compression detection by extension and magic bytes."""
    path = Path(file_path)
    lower_name = str(path).lower()

    # Extension-based quick check
    if any(lower_name.endswith(ext) for ext in [".gz", ".bgz", ".zip", ".bz2"]):
        return True

    # Magic bytes check for a few common formats
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
            if magic.startswith(b"\x1f\x8b"):  # gzip
                return True
            if magic.startswith(b"PK"):  # zip
                return True
            if magic.startswith(b"BZ"):  # bzip2
                return True
    except Exception:
        # If file can't be read, fall back to extension-only signal
        return any(lower_name.endswith(ext) for ext in [".gz", ".bgz", ".zip", ".bz2"])

    return False


# What each data format is indexed with. An index goes by one of two names: the data
# file's with the index suffix added (sample.bam.bai, sample.cram.crai,
# sample.vcf.gz.tbi), the samtools/htslib default, or with the format's extension
# replaced (sample.bai, sample.crai). Longest extension first, so sample.vcf.gz is a
# bgzipped VCF and not a ".gz".
_INDEX_SUFFIXES_FOR = (
    (".vcf.bgz", (".tbi", ".csi")),
    (".gvcf.gz", (".tbi", ".csi")),
    (".vcf.gz", (".tbi", ".csi")),
    (".cram", (".crai",)),
    (".bam", (".bai", ".csi")),
    (".bcf", (".csi",)),
    (".gvcf", (".idx",)),
    (".vcf", (".idx",)),
)


def index_names(data_name: str) -> list[str]:
    """The names an index of the file ``data_name`` goes by, in both conventions."""
    lower = data_name.lower()
    for extension, suffixes in _INDEX_SUFFIXES_FOR:
        if lower.endswith(extension):
            stem = data_name[: -len(extension)]
            return [data_name + s for s in suffixes] + [stem + s for s in suffixes]
    return []


def index_suffixes(data_name: str) -> tuple[str, ...]:
    """The index suffixes a file of this format takes, if it takes any."""
    lower = data_name.lower()
    for extension, suffixes in _INDEX_SUFFIXES_FOR:
        if lower.endswith(extension):
            return suffixes
    return ()


def matching_index(data_name: str, candidates: list[str]) -> str | None:
    """The candidate (a file name) that is an index of ``data_name``, if any."""
    wanted = {name.lower() for name in index_names(data_name)}
    return next((c for c in candidates if c.lower() in wanted), None)


# TODO: check inside zipped file for index file
def has_index_file(file_path: str | Path) -> bool:
    """Whether an index of this file sits beside it, by either name.

    It looked only for the data file's stem plus a suffix (sample.bai), so the
    names samtools and tabix write by default -- sample.bam.bai, sample.cram.crai,
    sample.vcf.gz.tbi -- were never found.
    """
    path = Path(file_path)
    return any((path.parent / name).exists() for name in index_names(path.name))
