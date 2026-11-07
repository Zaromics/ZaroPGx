import gzip
import logging
import os
import struct
from pathlib import Path

logger = logging.getLogger(__name__)


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


# An uploaded index is a file the user chose, not one made from the data. An index of
# another version of the same file, under the same name, sends htslib to offsets that
# are not this file's: a step reading through it fails, or finds no reads where there
# are some. So it is read against the data before any step is handed it.
MAX_UPLOADED_INDEX_BYTES = 256 * 1024**2
# Reads counted through the index on one sequence; a few hundred thousand is a second.
INDEX_CHECK_COUNT_LIMIT = 200_000


def _bam_index_reference_count(index: Path) -> int:
    """How many sequences a BAI or CSI index covers; ValueError if it is neither."""
    with open(index, "rb") as handle:
        head = handle.read(8)
    if head[:4] == b"BAI\x01" and len(head) == 8:
        return struct.unpack("<i", head[4:])[0]
    # A CSI is BGZF-compressed, which gzip reads.
    with gzip.open(index, "rb") as handle:
        if handle.read(4) != b"CSI\x01":
            raise ValueError("not a BAI or CSI index")
        _min_shift, _depth, l_aux = struct.unpack("<iii", handle.read(12))
        if l_aux < 0 or len(handle.read(l_aux)) != l_aux:
            raise ValueError("a CSI header that cannot be read")
        return struct.unpack("<i", handle.read(4))[0]


def bam_index_mismatch(bam: str | Path, index: str | Path) -> str | None:
    """Why ``index`` is not an index of ``bam``; None when it is.

    It must cover as many sequences as the BAM's header names; read through it, the
    file's first read must be the first read of its sequence, and every sequence it
    holds reads for must yield one of that sequence's reads; and on one sequence the
    reads counted through it must be as many as it says. An index of another file
    sends htslib to offsets that are not this file's, which fails or yields reads of
    another sequence, or none.
    """
    bam, index = Path(bam), Path(index)
    if index.stat().st_size > MAX_UPLOADED_INDEX_BYTES:
        return "it is too large to be an index"
    try:
        covered = _bam_index_reference_count(index)
    except (OSError, ValueError, EOFError, struct.error):
        return "it is not a BAM index"
    try:
        import pysam
    except ImportError:  # pragma: no cover - the app image has it
        return "it could not be checked here"
    try:
        with pysam.AlignmentFile(str(bam), "rb", index_filename=str(index)) as reads:
            if covered != reads.nreferences:
                return (
                    f"it covers {covered} sequences and the file's header names "
                    f"{reads.nreferences}"
                )
            # First, before any fetch moves the file's position. It opens its
            # sequence's block, so its sequence's reads start with it. Not "the first
            # sequence the header names": htslib indexes a file whose sequences'
            # blocks come in another order (a cut of chr22 then chr10, say).
            first = next(reads.fetch(until_eof=True), None)
            if first is not None and first.reference_id >= 0:
                again = next(reads.fetch(first.reference_name), None)
                if again is None or (
                    again.query_name,
                    again.reference_start,
                    again.flag,
                ) != (first.query_name, first.reference_start, first.flag):
                    return (
                        f"it does not find the file's first read, on "
                        f"{first.reference_name}"
                    )
            held = [s for s in reads.get_index_statistics() if s.total]
            for stat in held:
                read = next(reads.fetch(stat.contig), None)
                if read is None or read.reference_name != stat.contig:
                    return f"it holds reads on {stat.contig} that are not there"
            countable = [s for s in held if s.total <= INDEX_CHECK_COUNT_LIMIT]
            if countable:
                stat = max(countable, key=lambda s: s.total)
                found = reads.count(stat.contig)
                if found != stat.total:
                    return (
                        f"it holds {stat.total} reads on {stat.contig} and the file "
                        f"has {found} there"
                    )
    except (OSError, ValueError) as exc:
        # Not the exception's text, which names the stored upload's path: the reason
        # is shown on the page.
        logger.info("Reading %s through %s failed: %s", bam, index, exc)
        return "reading the file through it failed"
    return None
