"""zaroalign -- the FASTQ lane's aligner sidecar.

Turns capped, targeted-panel FASTQ into a coordinate-sorted, indexed GRCh38 BAM
that the existing BAM lane can consume unchanged. Everything here is deliberately
narrow, and the narrowness is the product decision rather than an unfinished
edge:

**Panel-sized only, capped before bytes reach disk.** Peak RAM in alignment is set
by the index, not the read count, so a cap bounds time and disk but not memory --
which means a cap cannot make WGS safe on this hardware and must not pretend to.
What it does do is keep the refusal above it truthful. There is no other upload
cap anywhere in this stack, so this one is enforced here, while streaming, not by
trusting a Content-Length the client controls.

**PL: is detected, never assumed and never asked.** See fastq_platform.py. A
FASTQ whose platform cannot be established is refused, because ``PL:ILLUMINA``
stamped onto a Nanopore run is the confident-wrong-answer class this codebase
refuses everywhere else.

**SM: is the job id.** Using the uploader's ``sample_identifier`` would put free
user text into the VCF sample column for the first time -- today it is display-only
(docker/pharmcat/pharmcat.py) -- and that column is read by GATK, PyPGx and
PharmCAT alike. The job id is machine-generated and already the sample name the
BAM and VCF lanes produce, so this keeps every lane's output shaped the same and
adds no new injection surface.

**The reference is mounted, not baked.** zarohla is precedent for "a sidecar with
an aligner" but not for "a sidecar with a genome index": its reference is a 17.7 MB
HLA FASTA inside the image, whereas this one needs ~3 GB of FASTA and ~5.6 GB of
bwa index. Those live on the host and arrive through a bind mount, so the image
stays small and the index is built once rather than per image build.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

sys.path.insert(0, "/job-client")
sys.path.insert(0, "/app")

from fastq_platform import detect_fastq_platform  # noqa: E402

try:  # pragma: no cover - exercised only inside the container
    from job_client import JobClient
except Exception:  # pragma: no cover
    JobClient = None  # type: ignore[assignment]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("zaroalign")

app = FastAPI(title="ZaroPGx zaroalign", version="0.3.2")

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
# Same argument as zarohla's HLA_TEMP_DIR: the FASTQ, the unsorted SAM and
# samtools' sort spills are large and short-lived, and putting them on whichever
# disk has room is a deployment choice rather than a code one.
SCRATCH_DIR = Path(os.getenv("ALIGN_TEMP_DIR") or (DATA_DIR / "temp"))
REFERENCE_FASTA = Path(
    os.getenv("ALIGN_REFERENCE", "/reference/pypgx/pypgx_grch38.fasta")
)

# 20 GB across all uploaded reads. Comfortably above a targeted panel and a modest
# exome, far below the WGS sizes whose failure mode is memory rather than bytes.
MAX_UPLOAD_BYTES = int(os.getenv("ALIGN_MAX_UPLOAD_BYTES", str(20 * 1024**3)))
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024

ALIGN_THREADS = os.getenv("ALIGN_THREADS", "4")
SORT_THREADS = os.getenv("ALIGN_SORT_THREADS", "2")
SORT_MEMORY = os.getenv("ALIGN_SORT_MEMORY", "768M")

running_processes: Dict[str, Dict[str, Any]] = {}


@app.get("/health")
def health():
    """Healthy means the reference is actually usable, not merely that we booted.

    A missing or unindexed reference is the one failure this service cannot
    recover from at request time, and finding out at the first upload wastes the
    upload. `.bwt` is the bwa index's load-bearing file.
    """
    index_present = REFERENCE_FASTA.with_suffix(REFERENCE_FASTA.suffix + ".bwt").exists()
    return {
        "status": "healthy" if index_present else "degraded",
        "service": "zaroalign",
        "reference": str(REFERENCE_FASTA),
        "reference_present": REFERENCE_FASTA.exists(),
        "bwa_index_present": index_present,
    }


async def _stream_to_disk(upload: UploadFile, dest: Path, budget: list[int]) -> int:
    """Write an upload, aborting the moment it would exceed the shared budget.

    `budget` is a one-element list so paired mates draw down a single allowance:
    a 12 GB + 12 GB pair is 24 GB of reads however it is split across fields, and
    checking each file against the cap separately would let it through.

    The check is per chunk and the partial file is removed on breach, so an
    oversized upload costs the bytes already streamed rather than all of them --
    the point of enforcing here instead of trusting Content-Length.
    """
    written = 0
    with open(dest, "wb") as handle:
        while chunk := await upload.read(UPLOAD_CHUNK_BYTES):
            budget[0] -= len(chunk)
            if budget[0] < 0:
                handle.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"FASTQ upload exceeds the {MAX_UPLOAD_BYTES // 1024**3} GB "
                        "limit for this lane. ZaroPGx aligns targeted-panel and "
                        "exome-sized FASTQ only: whole-genome read sets exhaust "
                        "memory during alignment regardless of how long they are "
                        "given, so accepting one would buy a job that dies partway "
                        "through. Align whole-genome reads yourself (nf-core/sarek, "
                        "or bwa-mem against GRCh38) and upload the BAM or CRAM."
                    ),
                )
            handle.write(chunk)
            written += len(chunk)
    return written


async def _run(job_label: str, argv: list[str], job_id: Optional[str]) -> None:
    logger.info(f"Job {job_label}: {' '.join(argv)}")
    proc = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    if job_id:
        running_processes[job_id] = {"pid": proc.pid}
    _, stderr = await proc.communicate()
    if job_id and job_id not in running_processes:
        raise HTTPException(status_code=499, detail="Cancelled by user")
    if proc.returncode != 0:
        tail = stderr.decode(errors="replace")[-2000:]
        raise HTTPException(
            status_code=500, detail=f"{argv[0]} failed: {tail}"
        )


@app.post("/align-fastq")
async def align_fastq(
    file: Optional[UploadFile] = File(None),
    file1: Optional[UploadFile] = File(None),
    file2: Optional[UploadFile] = File(None),
    reference_genome: str = Form("hg38"),
    patient_id: Optional[str] = Form(None),
    report_id: Optional[str] = Form(None),
    job_id: Optional[str] = Form(None),
    step_name: Optional[str] = Form("fastq_to_bam"),
):
    """Align capped FASTQ to GRCh38 and return a sorted, indexed BAM."""
    if (reference_genome or "hg38").strip().lower() not in ("hg38", "grch38"):
        raise HTTPException(
            status_code=400,
            detail=(
                f"zaroalign aligns to GRCh38 only; got reference_genome="
                f"{reference_genome!r}. Everything downstream is GRCh38-only."
            ),
        )
    if not REFERENCE_FASTA.exists():
        raise HTTPException(
            status_code=503,
            detail=(
                f"The alignment reference is not present at {REFERENCE_FASTA}. It is "
                "a host-side bind mount, not part of this image, and must be built "
                "and indexed before this lane can run (scripts/build-align-index.sh)."
            ),
        )

    local_job_id = str(uuid.uuid4())
    work_dir = SCRATCH_DIR / f"align_{local_job_id}"
    work_dir.mkdir(parents=True, exist_ok=True)
    job_client = None
    if job_id and JobClient is not None:
        try:
            job_client = JobClient(job_id=job_id, step_name=step_name)
            await job_client.start_step("Aligning FASTQ to GRCh38")
        except Exception as exc:  # pragma: no cover
            logger.warning(f"No job server: {exc}")

    try:
        budget = [MAX_UPLOAD_BYTES]
        mates: list[Path] = []
        if file1 is not None and file2 is not None:
            for index, upload in enumerate((file1, file2), start=1):
                dest = work_dir / f"reads_{index}.fastq"
                await _stream_to_disk(upload, dest, budget)
                mates.append(dest)
        elif file is not None:
            dest = work_dir / "reads_1.fastq"
            await _stream_to_disk(file, dest, budget)
            mates.append(dest)
        else:
            raise HTTPException(
                status_code=400,
                detail="Provide either 'file' (single-end) or both 'file1' and 'file2'.",
            )

        # PL: comes from the reads. An undetermined platform is a refusal, not a
        # default -- see the module docstring and fastq_platform.py.
        call = detect_fastq_platform(mates[0])
        if not call.determined:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Could not establish which sequencing platform produced this "
                    f"FASTQ, so the read group's PL: field cannot be filled in "
                    f"honestly. Observed: {call.evidence}. {call.reason}. GATK and "
                    "PyPGx both read PL:, and guessing it would put a claim about "
                    "your data into the BAM that nothing verified. Upload reads with "
                    "their original instrument read names, or align them yourself and "
                    "upload the BAM."
                ),
            )

        read_group = (
            f"@RG\\tID:{local_job_id}\\tSM:{job_id or local_job_id}"
            f"\\tPL:{call.platform}\\tLB:{report_id or local_job_id}\\tPU:{local_job_id}"
        )
        logger.info(f"Job {local_job_id}: read group {read_group} ({call.evidence})")

        output_dir = DATA_DIR / "results" / (patient_id or "unknown") / local_job_id
        output_dir.mkdir(parents=True, exist_ok=True)
        output_bam = output_dir / f"aligned_{local_job_id}.bam"
        # .sam, because that is what it is: `bwa mem` writes SAM text to stdout.
        # `samtools sort` detects the format from the content either way, so naming
        # it .bam worked and lied.
        unsorted = work_dir / "aligned.unsorted.sam"

        if job_client:
            await job_client.log_progress(f"Running bwa mem ({call.platform})")

        # Two processes rather than a shell pipe: this sidecar takes attacker-
        # controlled filenames and runs no shell anywhere, the same rule gatk-api
        # and zarohla follow.
        bwa_argv = [
            "bwa", "mem", "-t", ALIGN_THREADS, "-R", read_group,
            str(REFERENCE_FASTA), *[str(p) for p in mates],
        ]
        with open(unsorted, "wb") as sink:
            proc = await asyncio.create_subprocess_exec(
                *bwa_argv, stdout=sink, stderr=asyncio.subprocess.PIPE
            )
            if job_id:
                running_processes[job_id] = {"pid": proc.pid}
            _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"bwa mem failed: {stderr.decode(errors='replace')[-2000:]}",
            )

        if job_client:
            await job_client.log_progress("Sorting and indexing the BAM")
        await _run(
            local_job_id,
            [
                "samtools", "sort", "-@", SORT_THREADS, "-m", SORT_MEMORY,
                "-T", str(work_dir / "sort"), "-o", str(output_bam), str(unsorted),
            ],
            job_id,
        )
        await _run(local_job_id, ["samtools", "index", str(output_bam)], job_id)

        # An empty BAM is a wrong input, not a negative result: shipping it reads
        # downstream as "no variants found". Same rule as gatk-api's conversions.
        count = await asyncio.create_subprocess_exec(
            "samtools", "view", "-c", str(output_bam), stdout=asyncio.subprocess.PIPE
        )
        counted, _ = await count.communicate()
        records = int(counted.decode().strip() or 0)
        if records == 0:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Alignment produced a valid but empty BAM (0 records). That is a "
                    "truncated or non-human FASTQ, not a sample with no variants."
                ),
            )

        if job_client:
            await job_client.complete_step("Alignment complete")
        return {
            "success": True,
            "job_id": local_job_id,
            "bam_path": str(output_bam),
            "bam": str(output_bam),
            "bam_index": f"{output_bam}.bai",
            "records": records,
            "platform": call.platform,
            "platform_evidence": call.evidence,
            "read_group": read_group.replace("\\t", "\t"),
            "message": f"Aligned {len(mates)} FASTQ file(s) to GRCh38 ({records} records)",
        }
    finally:
        if job_id:
            running_processes.pop(job_id, None)
        shutil.rmtree(work_dir, ignore_errors=True)
