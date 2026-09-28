"""Work out which sequencing platform produced a FASTQ, from the FASTQ.

``@RG PL:`` is required by GATK and consumed by PyPGx, and a bare FASTQ does not
announce its platform in any header field. The tempting answers are both bad:
stamping ``PL:ILLUMINA`` on everything is the confident-wrong-answer class this
codebase refuses elsewhere (gVCF, BCF, GRCh37 BAM all refuse rather than guess),
and asking the uploader pushes a question onto someone who often cannot answer it
and can always answer it wrongly.

But the platform *is* observable. Every major platform stamps its own structure
into the read identifier, because each derives the name from its own instrument
geometry:

* **Illumina**, CASAVA 1.8+: ``@<instrument>:<run>:<flowcell>:<lane>:<tile>:<x>:<y>``
  -- seven colon-separated fields, the last three integers (tile/x/y coordinates on
  the flowcell). Pre-1.8 Illumina used ``@<instrument>:<lane>:<tile>:<x>:<y>#<index>/<mate>``,
  five fields with the ``#``/``/`` suffixes.
* **Oxford Nanopore**: a UUID read name followed by ``key=value`` metadata --
  ``runid=``, ``ch=`` (channel), ``start_time=``, ``flow_cell_id=``. There are no
  flowcell coordinates because there is no flowcell image.
* **PacBio**: ``@<movie>/<zmw>/<qStart>_<qEnd>`` for subreads, or ``@<movie>/<zmw>/ccs``
  for circular-consensus reads -- the ZMW (zero-mode waveguide) number is the unit.
* **MGI / BGI DNBSEQ**: ``<flowcell>L<lane>C<column>R<row><read>``, e.g.
  ``FP200007900L1C025R03808007886`` -- the DNB is placed on a patterned array, so the
  name is a grid reference. Present because real consumer WGS arrives this way: the
  sample this was first tested against is DNBSEQ, and without this arm it was refused
  for having "no recognisable platform structure" when its platform is perfectly
  legible. ``DNBSEQ`` is a valid ``@RG PL:`` value in the SAM specification, so there
  is somewhere honest to put the answer.

The identifier alone is not enough, though, because an SRA round-trip rewrites read
names to ``@SRR000000.1`` and throws the structure away. So the name evidence is
corroborated against **read length**, which no re-naming can disguise: Illumina
reads are short and near-uniform, ONT and PacBio are long and highly variable. When
the two disagree -- an Illumina-shaped name on 15 kb reads -- that is not a platform
call, it is a reason to stop, and this module returns ``None`` with the conflict
named rather than picking a winner.

Returning ``None`` is a real outcome, not a failure: the caller refuses the upload
and says which platform it could not establish, which is the same shape as every
other refusal in this stack.
"""

from __future__ import annotations

import gzip
import re
import statistics
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# Seven colon-separated fields, the final three plain integers. Anchored on the
# whole first token so a name that merely contains colons does not match.
_ILLUMINA_CASAVA = re.compile(r"^[^\s:]+:\d+:[^\s:]+:\d+:\d+:\d+:\d+$")
# Pre-CASAVA-1.8: five fields, optional #index and /mate.
_ILLUMINA_LEGACY = re.compile(r"^[^\s:]+:\d+:\d+:\d+:\d+(#[^\s/]*)?(/[12])?$")
# PacBio: movie/zmw/range, or movie/zmw/ccs.
_PACBIO = re.compile(r"^[^\s/]+/\d+/(\d+_\d+|ccs)$", re.IGNORECASE)
# MGI/BGI DNBSEQ: ...L<lane>C<column>R<row><read>. Matched at the END of the token
# rather than anchored whole, because vendors prepend their own sample prefix
# ("NG1GNACLXB_..._FP200007900L1C025R03808007886") and the grid reference is the part
# that identifies the instrument. An optional /1 or /2 mate suffix is allowed after it:
# that is how the sequencer itself writes every read name, and anchoring the grid
# reference at the very end of the token refused genuine MGI output.
_DNBSEQ = re.compile(r"L\d+C\d{3}R\d{3}\d+(/[12])?$")
# Nanopore metadata keys. `ch=` and `start_time=` co-occur; `runid=` is decisive.
_ONT_KEYS = ("runid=", "flow_cell_id=", "start_time=")

# Above this mean length a read set is not short-read Illumina. Illumina's longest
# routine chemistry is 2x300 (MiSeq v3); 1000 leaves a wide margin above it and sits
# far below ONT/PacBio means, which run to tens of kilobases.
_SHORT_READ_MEAN_CEILING = 1000
# Long-read sets vary hugely in length; short-read sets barely vary at all. Used
# only to corroborate, never on its own.
_SHORT_READ_CV_CEILING = 0.30

ILLUMINA = "ILLUMINA"
ONT = "ONT"
PACBIO = "PACBIO"
DNBSEQ = "DNBSEQ"

# Which platforms produce short reads. Drives the length corroboration below, so a
# new short-read platform must be added here as well as to _classify_name or it will
# be refused for having "long" reads it does not have.
#
# Public because it is also the alignment lane's admission rule. Detecting ONT or
# PacBio correctly is not the same as being able to use it: the lane aligns with
# bwa-mem (a short-read aligner, run through GATK's BwaSpark with no long-read preset)
# and types HLA with OptiType (short-read only), so a long-read FASTQ it accepted would
# come back as confident output from tools that were never meant to read it.
SHORT_READ_PLATFORMS = frozenset({ILLUMINA, DNBSEQ})
_SHORT_READ_PLATFORMS = SHORT_READ_PLATFORMS


@dataclass(frozen=True)
class PlatformCall:
    """What the reads say about their origin.

    ``platform`` is None when the evidence is absent or self-contradictory; the
    caller must then refuse rather than default. ``evidence`` always describes what
    was actually observed, so a refusal can quote it back to the uploader.
    """

    platform: Optional[str]
    evidence: str
    reason: Optional[str] = None

    @property
    def determined(self) -> bool:
        return self.platform is not None


def _open_text(path: Path):
    """FASTQ, gzipped or not. Sniffed by magic, not by filename."""
    with open(path, "rb") as probe:
        magic = probe.read(2)
    if magic == b"\x1f\x8b":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "rt", encoding="utf-8", errors="replace")


def _read_records(path: Path, max_records: int):
    """(identifiers, sequence lengths, sequence digests) from the first reads.

    Bounded deliberately: a FASTQ can be 20 GB and the answer is settled by the
    first handful of reads. Bad/truncated records simply end the scan -- validating
    the FASTQ is not this function's job. That includes a gzip stream cut short,
    which raises from inside readline(). Digests rather than sequences, because a
    thousand long reads would otherwise be held in memory to compare them.
    """
    names: List[str] = []
    lengths: List[int] = []
    digests: List[int] = []
    with _open_text(path) as handle:
        try:
            while len(names) < max_records:
                header = handle.readline()
                if not header:
                    break
                sequence = handle.readline()
                plus = handle.readline()
                handle.readline()  # quality
                if not header.startswith("@") or not plus.startswith("+"):
                    break
                names.append(header[1:].rstrip("\n"))
                lengths.append(len(sequence.strip()))
                digests.append(hash(sequence.strip()))
        except (EOFError, zlib.error, gzip.BadGzipFile):
            pass
    return names, lengths, digests


def _classify_name(name: str) -> Optional[str]:
    lowered = name.lower()
    if any(key in lowered for key in _ONT_KEYS):
        return ONT
    first = name.split()[0] if name.split() else ""
    if _PACBIO.match(first):
        return PACBIO
    if _ILLUMINA_CASAVA.match(first) or _ILLUMINA_LEGACY.match(first):
        return ILLUMINA
    # After Illumina, because an Illumina legacy name is colon-delimited and could
    # not reach here anyway, and checking the looser pattern last keeps it from
    # shadowing a stricter match.
    if _DNBSEQ.search(first):
        return DNBSEQ
    return None


def _length_profile(lengths: List[int]) -> tuple[Optional[str], str]:
    """What the read lengths alone imply: 'short', 'long', or unknown."""
    usable = [n for n in lengths if n > 0]
    if not usable:
        return None, "no readable sequence lines"
    mean = statistics.fmean(usable)
    cv = (statistics.pstdev(usable) / mean) if mean else 0.0
    summary = f"mean read length {mean:.0f} bp, CV {cv:.2f} over {len(usable)} reads"
    if mean <= _SHORT_READ_MEAN_CEILING and cv <= _SHORT_READ_CV_CEILING:
        return "short", summary
    if mean > _SHORT_READ_MEAN_CEILING:
        return "long", summary
    return None, summary


def detect_fastq_platform(path, max_records: int = 1000) -> PlatformCall:
    """Establish ``@RG PL:`` from the reads, or decline to.

    The read-name structure decides, and read length is required to agree. A
    disagreement returns None with both observations named: it means the file is
    not what its read names claim, and stamping either answer onto the BAM would
    propagate a falsehood into every downstream call.
    """
    path = Path(path)
    names, lengths, _ = _read_records(path, max_records)
    if not names:
        return PlatformCall(
            None, "no FASTQ records could be read", "the file carries no readable reads"
        )

    votes = [p for p in (_classify_name(n) for n in names) if p]
    shape, length_summary = _length_profile(lengths)

    if not votes:
        return PlatformCall(
            None,
            f"read names carry no recognisable platform structure (e.g. {names[0][:60]!r}); "
            f"{length_summary}",
            "the read names do not identify one (SRA re-exports lose the "
            "instrument's naming)",
        )

    winner = max(set(votes), key=votes.count)
    agreement = votes.count(winner) / len(votes)
    if agreement < 0.9:
        return PlatformCall(
            None,
            f"read names disagree: {sorted(set(votes))}; {length_summary}",
            "the file mixes reads from more than one platform",
        )

    expected_shape = "short" if winner in _SHORT_READ_PLATFORMS else "long"
    if shape is not None and shape != expected_shape:
        return PlatformCall(
            None,
            f"read names look like {winner} but {length_summary}",
            f"read names and read lengths disagree: {winner} implies {expected_shape} "
            f"reads and these are {shape}",
        )

    return PlatformCall(winner, f"{winner} read-name structure; {length_summary}")


def _mate_key(name: str) -> str:
    """A read name reduced to what both mates share.

    The first whitespace token only -- CASAVA 1.8+ puts the mate number after a space
    ("... 1:N:0:ATCACG") -- with a trailing /1 or /2 removed, which is how MGI and
    pre-1.8 Illumina mark the mate instead.
    """
    token = name.split()[0] if name.split() else ""
    return re.sub(r"/[12]$", "", token)


def mate_names_agree(
    path1, path2, max_records: int = 1000, min_agreement: float = 0.95
):
    """Whether two FASTQs are the two halves of one paired-end run.

    Paired FASTQs list the same reads in the same order, one mate per file, so their
    read names match record for record once the mate marker is stripped. That is a
    fact about the files, checked directly -- which matters because the alternative is
    trusting filenames ("_R1" / "_R2"), and two unrelated single-end runs uploaded
    together would otherwise be aligned as pairs, inventing mate relationships between
    reads that never shared a fragment. FastqToSam repeats the check on every record
    later; this is the cheap version that lets the upload be refused with a reason
    before any alignment work is spent.

    Names alone cannot tell a pair from one file uploaded twice: stripping the mate
    marker is what makes R1 and R2 agree, and it makes a file agree with its own copy
    too. The sequences can -- the mates read opposite ends of each fragment, so a
    real pair almost never repeats a sequence across the two files. A duplicated file
    repeats every one, and would be aligned as a "pair" that is single-end data.

    Bounded to the first `max_records` of each file, like platform detection. Returns
    (agrees, evidence) so a refusal can say what was compared.
    """
    names1, _, digests1 = _read_records(Path(path1), max_records)
    names2, _, digests2 = _read_records(Path(path2), max_records)
    compared = min(len(names1), len(names2))
    if compared == 0:
        return False, "one of the two files has no readable reads to pair"
    same = sum(
        1
        for a, b in zip(names1[:compared], names2[:compared])
        if _mate_key(a) == _mate_key(b)
    )
    share = same / compared
    evidence = f"{same} of the first {compared} read names pair up ({share:.0%})"
    if share < min_agreement:
        return False, (
            f"read names do not pair: {evidence}; paired files list the same reads "
            "in the same order"
        )
    repeated = sum(1 for a, b in zip(digests1, digests2) if a == b)
    if repeated > compared // 2:
        return False, (
            f"the two files hold the same reads ({repeated} of the first {compared} "
            "sequences are identical), where mates read opposite ends of each fragment"
        )
    return True, evidence


# "_R1", "_2", ".R2", "-R1_001" (bcl2fastq) directly before the FASTQ extension.
_MATE_FILENAME = re.compile(r"[._-]R?[12](_\d{3})?\.(fastq|fq)(\.gz)?$", re.IGNORECASE)


def looks_like_one_mate(filename) -> bool:
    """Whether a FASTQ's filename marks it as R1 or R2 of a paired-end run.

    A hint, used only to warn when one mate is uploaded alone. The reads cannot
    answer this: "1:N:0" and a trailing "/1" appear in true single-end runs too.
    """
    return bool(_MATE_FILENAME.search(str(filename or "")))
