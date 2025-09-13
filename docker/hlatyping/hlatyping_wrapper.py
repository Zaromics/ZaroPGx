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
    reference_genome: str = Form("hg38"),
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
        # Docs suggest: nextflow run nf-core/hlatyping -profile docker --input samplesheet.csv --outdir <OUTDIR> --genome <GENOME>
        # Map reference genome to nf-core/hlatyping genome parameter
        genome_mapping = {
            'hg38': 'GRCh38',
            'hg37': 'GRCh37', 
            'GRCh38': 'GRCh38',
            'GRCh37': 'GRCh37'
        }
        genome_param = genome_mapping.get(reference_genome, 'GRCh38')
        
        cmd = (
            f"nextflow run nf-core/hlatyping -r {NEXTFLOW_RUN_VERSION} "
            f"-profile {NEXTFLOW_PROFILE} "
            f"--input {samplesheet_path} "
            f"--outdir {run_out_dir} "
            f"--genome {genome_param} "
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

        print(f"Running hlatyping command: {cmd}")
        print(f"Working directory: {job_dir}")
        print(f"Output directory: {run_out_dir}")
        
        proc = subprocess.run(cmd, shell=True, text=True, capture_output=True, env=env, cwd=job_dir)
        print(f"Command return code: {proc.returncode}")
        print(f"Command stdout: {proc.stdout}")
        print(f"Command stderr: {proc.stderr}")
        
        # Check if output directory was created and has content
        if run_out_dir.exists():
            print(f"Output directory contents: {list(run_out_dir.iterdir())}")
            for item in run_out_dir.rglob("*"):
                print(f"  {item.relative_to(run_out_dir)}")
        else:
            print("Output directory was not created!")
        
        if proc.returncode != 0:
            error_msg = f"hlatyping failed with return code {proc.returncode}\n"
            error_msg += f"STDOUT: {proc.stdout}\n"
            error_msg += f"STDERR: {proc.stderr}"
            raise HTTPException(status_code=500, detail=error_msg)

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


@app.post("/call-hla")
async def call_hla(
    file: UploadFile = File(...),
    file2: Optional[UploadFile] = File(None),  # Optional second FASTQ file for paired-end
    reference_genome: str = Form(...),
    patient_id: str = Form(...),
    report_id: str = Form(...),
    seq_type: str = Form("dna")
) -> Dict[str, Any]:
    """
    HLA calling endpoint for nextflow pipeline integration.
    This endpoint wraps the existing /type endpoint to provide the expected interface.
    Supports both single-end and paired-end FASTQ files, as well as BAM files.
    """
    try:
        # Determine file type based on filename
        is_bam = file.filename.lower().endswith('.bam')
        is_fastq = any(file.filename.lower().endswith(ext) for ext in ['.fastq', '.fq', '.fastq.gz', '.fq.gz'])
        
        if not (is_bam or is_fastq):
            raise HTTPException(status_code=400, detail="File must be BAM or FASTQ format")
        
        # Call the existing /type endpoint internally
        if is_bam:
            # For BAM files, call /type with bam parameter
            result = await hla_type(
                file1=None,
                file2=None, 
                bam=file,
                seq_type=seq_type,
                sample_name=patient_id,
                out_prefix=patient_id,
                reference_genome=reference_genome
            )
        else:
            # For FASTQ files, call /type with file1 (and optionally file2 for paired-end)
            result = await hla_type(
                file1=file,
                file2=file2,  # Will be None for single-end, provided for paired-end
                bam=None,
                seq_type=seq_type,
                sample_name=patient_id,
                out_prefix=patient_id,
                reference_genome=reference_genome
            )
        
        if not result.get("success"):
            raise HTTPException(status_code=500, detail="HLA typing failed")
        
        # Parse results from the hlatyping output
        results_dir = Path(result["results_dir"])
        
        # Look for HLA results in the expected format
        # Based on nf-core/hlatyping docs, results are typically in:
        # - results_dir/hlatyping/sample_name/hlatyping/sample_name.hla_calls.tsv
        # - or similar structure
        
        hla_results = {}
        
        # First, try to find any HLA-related files
        print(f"Searching for HLA results in: {results_dir}")
        all_files = list(results_dir.rglob("*"))
        print(f"All files found: {[str(f.relative_to(results_dir)) for f in all_files]}")
        
        # Look for various possible HLA output files
        hla_files = []
        for pattern in ["*.hla_calls.tsv", "*hla*.tsv", "*HLA*.tsv", "*.tsv"]:
            hla_files.extend(list(results_dir.rglob(pattern)))
        
        print(f"HLA files found: {[str(f.relative_to(results_dir)) for f in hla_files]}")
        
        if hla_files:
            # Parse the HLA calls TSV file
            hla_file = hla_files[0]
            print(f"Parsing HLA file: {hla_file}")
            with open(hla_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        parts = line.split('\t')
                        if len(parts) >= 2:
                            gene = parts[0]
                            call = parts[1]
                            hla_results[gene] = call
                            print(f"Found HLA call: {gene} -> {call}")
        else:
            print("No HLA files found - this may indicate the pipeline failed to produce results")
        
        # Return in the format expected by nextflow pipeline
        return {
            "success": True,
            "results": hla_results,
            "job_id": result.get("job_id"),
            "results_dir": str(results_dir)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"HLA calling failed: {str(e)}")


