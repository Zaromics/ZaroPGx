#!/usr/bin/env python3
import csv
import os
import subprocess
import uuid
import time
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware

DATA_DIR = Path(os.getenv('DATA_DIR', '/data'))
TEMP_DIR = DATA_DIR / 'temp'
RESULTS_DIR = DATA_DIR / 'results'
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)

NEXTFLOW_RUN_VERSION = os.getenv('HLATYPING_PIPELINE_VERSION', '2.1.0')
NEXTFLOW_PROFILE = os.getenv('HLATYPING_PROFILE', 'docker')

app = FastAPI(title="nf-core/hlatyping Wrapper API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": "hlatyping-wrapper",
        "timestamp": time.time(),
    }


@app.post("/type")
async def hla_type(
    file1: Optional[UploadFile] = File(None),
    file2: Optional[UploadFile] = File(None),
    bam: Optional[UploadFile] = File(None),
    seq_type: str = Form("dna"),  # "dna" or "rna"
    sample_name: Optional[str] = Form(None),
    out_prefix: Optional[str] = Form(None),
) -> Dict[str, Any]:
    if seq_type not in ("dna", "rna"):
        raise HTTPException(status_code=400, detail="seq_type must be 'dna' or 'rna'")

    if not any([file1, bam]):
        raise HTTPException(status_code=400, detail="Provide either FASTQ (file1[/file2]) or a BAM file")

    job_id = str(uuid.uuid4())
    job_dir = TEMP_DIR / job_id
    os.makedirs(job_dir, exist_ok=True)

    try:
        saved_f1 = None
        saved_f2 = None
        saved_bam = None

        # Save uploads
        if file1 is not None:
            saved_f1 = job_dir / file1.filename
            with open(saved_f1, "wb") as f:
                f.write(await file1.read())
        if file2 is not None:
            saved_f2 = job_dir / file2.filename
            with open(saved_f2, "wb") as f:
                f.write(await file2.read())
        if bam is not None:
            saved_bam = job_dir / bam.filename
            with open(saved_bam, "wb") as f:
                f.write(await bam.read())

        run_out_dir = job_dir / "hlatyping_out"
        os.makedirs(run_out_dir, exist_ok=True)

        # Build samplesheet.csv per nf-core/hlatyping docs
        samplesheet_path = job_dir / "samplesheet.csv"
        effective_sample = sample_name or (out_prefix or "sample")

        # Columns: sample,fastq_1,fastq_2,seq_type, optionally bam
        # If BAM is provided, fastq columns must still exist (may be empty)
        with open(samplesheet_path, "w", newline="") as csvfile:
            fieldnames = ["sample", "fastq_1", "fastq_2", "seq_type"]
            include_bam = saved_bam is not None
            if include_bam:
                fieldnames.append("bam")
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            row: Dict[str, Any] = {
                "sample": effective_sample,
                "fastq_1": str(saved_f1) if saved_f1 else "",
                "fastq_2": str(saved_f2) if saved_f2 else "",
                "seq_type": seq_type,
            }
            if include_bam:
                row["bam"] = str(saved_bam)
            writer.writerow(row)

        # Prepare Nextflow command
        # Docs suggest: nextflow run nf-core/hlatyping -profile docker --input samplesheet.csv --outdir <OUTDIR>
        cmd = (
            f"nextflow run nf-core/hlatyping -r {NEXTFLOW_RUN_VERSION} "
            f"-profile {NEXTFLOW_PROFILE} "
            f"--input {samplesheet_path} "
            f"--outdir {run_out_dir} "
        )

        # Optional: add workflow reports
        cmd += (
            f"-with-report {run_out_dir}/execution_report.html "
            f"-with-timeline {run_out_dir}/execution_timeline.html "
            f"-with-trace {run_out_dir}/execution_trace.txt "
            f"-with-dag {run_out_dir}/pipeline_dag.svg "
        )

        env = os.environ.copy()
        # Ensure Nextflow caches under data to persist between runs (optional)
        env.setdefault('NXF_HOME', str(DATA_DIR / 'nextflow'))

        proc = subprocess.run(cmd, shell=True, text=True, capture_output=True, env=env)
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail=f"hlatyping failed: {proc.stderr}")

        # Move results to shared results dir
        final_dir = RESULTS_DIR / f"hlatyping_{job_id}"
        os.makedirs(final_dir, exist_ok=True)
        for child in run_out_dir.iterdir():
            dest = final_dir / child.name
            child.replace(dest)

        return {
            "success": True,
            "job_id": job_id,
            "results_dir": str(final_dir),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


