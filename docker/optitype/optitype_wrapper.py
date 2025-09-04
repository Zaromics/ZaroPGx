#!/usr/bin/env python3
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

app = FastAPI(title="OptiType Wrapper API", version="1.0.0")
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
        "service": "optitype-wrapper",
        "timestamp": time.time(),
    }

@app.post("/type")
async def hlaclass1_type(
    file1: UploadFile = File(...),
    file2: Optional[UploadFile] = File(None),
    mode: str = Form("dna"),  # "dna" or "rna"
    out_prefix: Optional[str] = Form(None),
) -> Dict[str, Any]:
    if mode not in ("dna", "rna"):
        raise HTTPException(status_code=400, detail="mode must be 'dna' or 'rna'")

    job_id = str(uuid.uuid4())
    job_dir = TEMP_DIR / job_id
    os.makedirs(job_dir, exist_ok=True)

    try:
        # Save uploads
        f1_path = job_dir / file1.filename
        with open(f1_path, "wb") as f:
            f.write(await file1.read())
        f2_path = None
        if file2 is not None:
            f2_path = job_dir / file2.filename
            with open(f2_path, "wb") as f:
                f.write(await file2.read())

        # Prepare output dir
        run_out_dir = job_dir / "optitype_out"
        os.makedirs(run_out_dir, exist_ok=True)

        # Build command
        mode_flag = "--dna" if mode == "dna" else "--rna"
        inputs = f"{f1_path} {f2_path}" if f2_path else f"{f1_path}"
        cmd = (
            f"conda run -n optitype-py27 python /opt/OptiType/OptiTypePipeline.py "
            f"-i {inputs} {mode_flag} -o {run_out_dir}"
        )
        if out_prefix:
            cmd += f" -p {out_prefix}"

        proc = subprocess.run(cmd, shell=True, text=True, capture_output=True)
        if proc.returncode != 0:
            raise HTTPException(status_code=500, detail=f"OptiType failed: {proc.stderr}")

        # Move results to shared results dir
        final_dir = RESULTS_DIR / f"optitype_{job_id}"
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


