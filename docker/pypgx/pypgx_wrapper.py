#!/usr/bin/env python3
"""
PyPGx Wrapper Service for ZaroPGx
Provides REST API endpoints for calling PyPGx supported star alleles
"""

import os
import json
import logging
import tempfile
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional
import zipfile
import io
import csv
import time

import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("pypgx_wrapper")

# Directory setup
DATA_DIR = Path(os.getenv('DATA_DIR', '/data'))
TEMP_DIR = DATA_DIR / 'temp'
REFERENCE_DIR = Path(os.getenv('REFERENCE_DIR', '/reference'))
REPORT_DIR = Path(os.getenv('REPORT_DIR', '/data/reports'))

# Create necessary directories
os.makedirs(TEMP_DIR, exist_ok=True)

# Supported genes for PyPGx (complete list of 87 pharmacogenes)
SUPPORTED_GENES = [
    # Core focus genes for initial implementation
    'CYP2D6',
    'CYP2C19',
    'CYP2C9',
    'CYP3A4',
    'CYP3A5',
    
    # All other supported pharmacogenes
    'ABCB1', 'ABCG2', 'ACYP2', 'ADRA2A', 'ADRB2',
    'ANKK1', 'APOE', 'ATM', 'BCHE', 'BDNF',
    'CACNA1S', 'CFTR', 'COMT', 'CYP1A1', 'CYP1A2',
    'CYP1B1', 'CYP2A6', 'CYP2A7', 'CYP2A13', 'CYP2B6', 
    'CYP2B7', 'CYP2C8', 'CYP2E1', 'CYP2F1',
    'CYP2J2', 'CYP2R1', 'CYP2S1', 'CYP2W1',
    'CYP3A7', 'CYP3A43', 'CYP4A11', 'CYP4A22',
    'CYP4B1', 'CYP4F2', 'CYP17A1', 'CYP19A1', 'CYP26A1',
    'DBH', 'DPYD', 'DRD2', 'F2', 'F5',
    'G6PD', 'GRIK1', 'GRIK4', 'GRIN2B', 'GSTM1',
    'GSTP1', 'GSTT1', 'HTR1A', 'HTR2A', 'IFNL3',
    'ITGB3', 'ITPA', 'MTHFR', 'NAT1', 'NAT2',
    'NUDT15', 'OPRK1', 'OPRM1', 'POR', 'PTGIS',
    'RARG', 'RYR1', 'SLC6A4', 'SLC15A2', 'SLC22A2',
    'SLC28A3', 'SLC47A2', 'SLCO1B1', 'SLCO1B3', 'SLCO2B1',
    'SULT1A1', 'TBXAS1', 'TPMT', 'UGT1A1', 'UGT1A4',
    'UGT1A6', 'UGT2B7', 'UGT2B15', 'UGT2B17', 'VKORC1',
    'XPC'
]

app = FastAPI(
    title="PyPGx Wrapper API",
    description="REST API for PyPGx supported star allele calling",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health_check():
    """Simple health check endpoint"""
    return {
        'status': 'healthy',
        'service': 'pypgx-wrapper',
        'supported_genes': ["CYP2D6"],
        'timestamp': time.time()
    }

@app.get("/")
def root():
    """API root endpoint"""
    return {
        "message": "PyPGx Wrapper API",
        "usage": "POST to /genotype with a VCF file to call alleles",
        "version": "1.0.0"
    }

@app.post("/create-input-vcf")
async def create_input_vcf(
    file: UploadFile = File(...),
    reference_genome: str = Form("hg38"),
    patient_id: Optional[str] = Form(None),
    report_id: Optional[str] = Form(None),
):
    """
    Create an input VCF (SNVs/indels) from a BAM/CRAM/SAM using PyPGx's recommended method.

    Returns JSON with the path to the generated VCF (bgzipped) and its index.
    """
    if reference_genome not in ["hg19", "hg38", "GRCh37", "GRCh38"]:
        raise HTTPException(status_code=400, detail=f"Reference genome {reference_genome} is not supported. Use hg19/GRCh37 or hg38/GRCh38.")

    # Normalize to GRCh37/GRCh38 wording for PyPGx
    pypgx_assembly = "GRCh37" if reference_genome in ("hg19", "GRCh37") else "GRCh38"

    job_id = str(uuid.uuid4())
    job_dir = TEMP_DIR / job_id
    os.makedirs(job_dir, exist_ok=True)

    try:
        # Save uploaded alignment file
        input_path = job_dir / file.filename
        with open(input_path, "wb") as f:
            content = await file.read()
            f.write(content)

        # Determine output VCF path
        output_vcf_gz = job_dir / (Path(file.filename).stem + ".vcf.gz")

        res = run_pypgx_create_input_vcf(str(input_path), str(output_vcf_gz), pypgx_assembly)
        if not res.get("success"):
            raise HTTPException(status_code=500, detail=res.get("error", "PyPGx create-input-vcf failed"))

        payload: Dict[str, Any] = {
            "success": True,
            "job_id": job_id,
            "input_file": str(input_path),
            "vcf_path": str(output_vcf_gz),
            "tbi_path": str(output_vcf_gz) + ".tbi",
            "assembly": pypgx_assembly,
        }
        if patient_id:
            payload["patient_id"] = patient_id
        if report_id:
            payload["report_id"] = report_id
        return payload

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error creating VCF from alignment with PyPGx")
        raise HTTPException(status_code=500, detail=f"Error creating VCF from alignment with PyPGx: {str(e)}")

def run_pypgx_create_input_vcf(alignment_path: str, output_vcf_gz: str, assembly: str) -> Dict[str, Any]:
    """Run PyPGx create-input-vcf to generate a VCF from an alignment file.

    Tries the documented invocation first; if it fails, tries a fallback form.
    Ensures the output is bgzipped and tabix-indexed.
    """
    try:
        # Primary, recommended form (assumed):
        # pypgx create-input-vcf --assembly GRCh38 --bam <bam> --output <out.vcf.gz>
        cmd_primary = f"pypgx create-input-vcf --assembly {assembly} --bam {alignment_path} --output {output_vcf_gz}"
        logger.info(f"Running PyPGx (primary) create-input-vcf: {cmd_primary}")
        proc = subprocess.run(cmd_primary, shell=True, text=True, capture_output=True)
        if proc.returncode != 0:
            logger.warning(f"Primary create-input-vcf failed (rc={proc.returncode}). stderr: {proc.stderr}\nTrying fallback invocation form.")
            # Fallback form in case of different CLI signature
            cmd_fallback = f"pypgx create-input-vcf {alignment_path} {output_vcf_gz} --assembly {assembly}"
            logger.info(f"Running PyPGx (fallback) create-input-vcf: {cmd_fallback}")
            proc = subprocess.run(cmd_fallback, shell=True, text=True, capture_output=True)
            if proc.returncode != 0:
                logger.error(f"PyPGx create-input-vcf failed. stderr: {proc.stderr}")
                return {"success": False, "error": proc.stderr or "create-input-vcf failed"}

        # Ensure bgzip + tabix index present
        if not os.path.exists(output_vcf_gz):
            # Some PyPGx versions may output .vcf (uncompressed) – try to find and compress
            raw_vcf = output_vcf_gz[:-3] if output_vcf_gz.endswith('.gz') else output_vcf_gz
            if os.path.exists(raw_vcf):
                logger.info(f"bgzip compressing raw VCF: {raw_vcf}")
                subprocess.run(f"bgzip -f {raw_vcf}", shell=True, check=True)
            else:
                return {"success": False, "error": "Expected VCF output not found"}

        tbi_path = output_vcf_gz + ".tbi"
        if not os.path.exists(tbi_path):
            logger.info(f"Indexing VCF with tabix: {output_vcf_gz}")
            subprocess.run(f"tabix -p vcf {output_vcf_gz}", shell=True, check=True)

        return {"success": True, "vcf": output_vcf_gz, "tbi": tbi_path}
    except subprocess.CalledProcessError as cpe:
        logger.exception("Subprocess error running create-input-vcf")
        return {"success": False, "error": str(cpe)}
    except Exception as e:
        logger.exception("Unexpected error running create-input-vcf")
        return {"success": False, "error": str(e)}

@app.post("/genotype")
async def genotype(
    file: UploadFile = File(...),
    gene: str = Form(None),
    genes: str = Form("ALL"),
    reference_genome: str = Form("hg19"),
    patient_id: Optional[str] = Form(None),
    report_id: Optional[str] = Form(None),
):
    """
    Run PyPGx on a VCF file to determine alleles
    
    Args:
        file: The VCF file to analyze
        gene: Gene to analyze (default: CYP2D6)
        reference_genome: Reference genome (hg19 or hg38)
    
    Returns:
        Genotyping results
    """
    # Normalize requested genes: support single gene, comma-separated list, or ALL
    requested_genes: List[str]
    if genes and genes.strip().upper() == "ALL":
        requested_genes = SUPPORTED_GENES
    else:
        # Merge legacy single `gene` with `genes` list if provided
        gene_list = []
        if genes:
            gene_list.extend([g.strip().upper() for g in genes.split(',') if g.strip()])
        if gene:
            gene_list.append(gene.strip().upper())
        # De-duplicate and validate
        requested_genes = sorted(set(g for g in gene_list))
        unsupported = [g for g in requested_genes if g not in SUPPORTED_GENES]
        if unsupported:
            raise HTTPException(status_code=400, detail=f"Unsupported genes: {unsupported}. Supported genes: {SUPPORTED_GENES}")
        if not requested_genes:
            requested_genes = ["CYP2D6"]
    
    if reference_genome not in ["hg19", "hg38", "GRCh37", "GRCh38"]:
        raise HTTPException(status_code=400, detail=f"Reference genome {reference_genome} is not supported. Use hg19/GRCh37 or hg38/GRCh38.")
    
    # Determine assembly string for PyPGx (expects GRCh37/GRCh38 columns like 'GRCh38Region')
    if reference_genome in ("hg19", "GRCh37"):
        pypgx_assembly = "GRCh37"
    else:
        pypgx_assembly = "GRCh38"
    
    # Create a unique job directory
    job_id = str(uuid.uuid4())
    job_dir = TEMP_DIR / job_id
    os.makedirs(job_dir, exist_ok=True)
    
    try:
        # Save the uploaded VCF file
        input_filepath = job_dir / file.filename
        with open(input_filepath, "wb") as f:
            content = await file.read()
            f.write(content)
        
        logger.info(f"Processing PyPGx genotyping for genes: {requested_genes}")
        aggregated: Dict[str, Any] = {"success": True, "results": {}, "job_id": job_id}
        if patient_id:
            aggregated["patient_id"] = patient_id
        if report_id:
            aggregated["report_id"] = report_id
        for g in requested_genes:
            try:
                res = run_pypgx(input_filepath, job_dir, g, pypgx_assembly)
                aggregated["results"][g] = res
            except Exception as e:
                logger.exception(f"PyPGx failed for {g}")
                aggregated["results"][g] = {"success": False, "error": str(e)}
                aggregated["success"] = False
        # Move per-gene pipeline folders into per-patient reports dir if patient_id provided
        try:
            if patient_id:
                dest_dir = REPORT_DIR / str(patient_id) / f"pypgx_{job_id}"
                dest_dir.mkdir(parents=True, exist_ok=True)
                for item in os.listdir(job_dir):
                    src_path = job_dir / item
                    if src_path.is_dir() and item.endswith("-pipeline"):
                        import shutil
                        shutil.move(str(src_path), str(dest_dir / item))
                aggregated["work_dir"] = str(dest_dir)
        except Exception as mv_e:
            logger.warning(f"Failed to move PyPGx work dirs: {mv_e}")
        # Optionally persist a summary JSON
        # Prefer writing into a per-patient reports directory when patient_id is provided
        try:
            if patient_id:
                dest_dir = REPORT_DIR / str(patient_id)
                dest_dir.mkdir(parents=True, exist_ok=True)
                output_path = dest_dir / f"{job_id}_pypgx_results.json"
            else:
                output_path = DATA_DIR / f"{job_id}_pypgx_results.json"
        except Exception:
            # Fallback to DATA_DIR on any error creating the reports dir
            output_path = DATA_DIR / f"{job_id}_pypgx_results.json"
        output_file = str(output_path)
        try:
            with open(output_file, "w") as f:
                json.dump(aggregated, f, indent=2)
            aggregated["output_file"] = output_file
        except Exception:
            logger.warning("Failed to persist aggregated PyPGx results file")
        return aggregated
    
    except Exception as e:
        logger.exception("Error processing VCF with PyPGx")
        raise HTTPException(
            status_code=500,
            detail=f"Error processing VCF with PyPGx: {str(e)}"
        )

def run_pypgx(vcf_path: str, output_dir: str, gene: str, reference_genome: str = 'hg19') -> Dict[str, Any]:
    """Run PyPGx for star allele calling on the input VCF"""
    try:
        # Ensure the VCF is bgzipped and indexed; if not, create temporary gz + tbi
        vcf_path = str(vcf_path)
        vcf_gz = vcf_path if vcf_path.endswith('.gz') else f"{vcf_path}.gz"
        tbi_path = f"{vcf_gz}.tbi"
        if not os.path.exists(vcf_gz):
            logger.info(f"bgzip compressing VCF for tabix: {vcf_path} -> {vcf_gz}")
            subprocess.run(f"bgzip -c {vcf_path} > {vcf_gz}", shell=True, check=True)
        if not os.path.exists(tbi_path):
            logger.info(f"Indexing VCF with tabix: {vcf_gz}")
            subprocess.run(f"tabix -p vcf {vcf_gz}", shell=True, check=True)

        # Determine a pipeline output directory that does NOT pre-exist
        # PyPGx creates the output directory itself; avoid FileExistsError if already present
        pipeline_dir = Path(output_dir) / f"{gene}-pipeline"
        if pipeline_dir.exists():
            safe_dir = Path(output_dir) / f"{gene}-pipeline-{uuid.uuid4().hex[:6]}"
            pipeline_dir = safe_dir
        
        # Use the appropriate command for NGS pipeline
        # Use the compressed/indexed VCF for PyPGx
        pypgx_cmd = f"pypgx run-ngs-pipeline {gene} {pipeline_dir} --variants {vcf_gz} --assembly {reference_genome}"
        
        logger.info(f"Running PyPGx command: {pypgx_cmd}")
        
        # Execute PyPGx
        process = subprocess.run(
            pypgx_cmd,
            shell=True,
            text=True,
            capture_output=True,
            check=False
        )
        
        # Check if the command was successful
        if process.returncode != 0:
            logger.error(f"PyPGx failed: {process.stderr}")
            return {
                'success': False,
                'error': f"PyPGx failed: {process.stderr}"
            }
        
        # Extract genotype information from the results
        diplotype, details = parse_pypgx_results(pipeline_dir, gene)
        
        return {
            'success': True,
            'gene': gene,
            'diplotype': diplotype,
            'details': details,
            'job_id': os.path.basename(output_dir)
        }
        
    except Exception as e:
        logger.exception("Error running PyPGx")
        return {
            'success': False,
            'error': f"Error running PyPGx: {str(e)}"
        }

def parse_pypgx_results(pipeline_dir: Path, gene: str) -> tuple:
    """Parse the PyPGx results to extract diplotype and details for a gene.

    Strategy:
    - Prefer genotypes.zip if present; look for TSV/CSV containing rows per gene
    - Fallback to results.zip; scan for TSV/CSV with gene and diplotype fields
    - If nothing parseable is found, return (None, {})
    """
    try:
        gene_upper = gene.upper()

        def parse_zip_for_calls(zip_path: Path) -> Optional[tuple]:
            if not os.path.exists(zip_path):
                return None
            with zipfile.ZipFile(zip_path, 'r') as zf:
                # Iterate members; prefer tsv/csv
                members = zf.namelist()
                for name in members:
                    lower = name.lower()
                    if not (lower.endswith('.tsv') or lower.endswith('.csv')):
                        continue
                    try:
                        with zf.open(name, 'r') as fh:
                            raw = fh.read()
                        text = raw.decode('utf-8', errors='replace')
                        # Detect delimiter
                        delimiter = '\t' if '\t' in text.splitlines()[0] else ','
                        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
                        # Try common column names
                        for row in reader:
                            # Match by gene column if present; else try file-scoped gene
                            row_gene = (row.get('gene') or row.get('Gene') or row.get('GENE') or '').strip().upper()
                            if row_gene and row_gene != gene_upper:
                                continue
                            diplotype = (row.get('diplotype') or row.get('Diplotype') or row.get('DIPLOTYPE') or row.get('genotype') or row.get('Genotype'))
                            phenotype = (row.get('phenotype') or row.get('Phenotype') or row.get('PHENOTYPE'))
                            activity = (row.get('activity_score') or row.get('Activity_Score') or row.get('activityScore') or row.get('ActivityScore'))
                            if diplotype or phenotype or activity:
                                details = {}
                                if phenotype:
                                    details['phenotype'] = str(phenotype).strip()
                                if activity is not None and str(activity).strip() != '':
                                    details['activity_score'] = str(activity).strip()
                                return (str(diplotype).strip() if diplotype else None, details)
                    except Exception:
                        continue
            return None

        # Try genotypes.zip first
        parsed = parse_zip_for_calls(pipeline_dir / 'genotypes.zip')
        if parsed:
            return parsed
        # Fallback to results.zip
        parsed = parse_zip_for_calls(pipeline_dir / 'results.zip')
        if parsed:
            return parsed
        # Nothing found
        return None, {}
    except Exception as e:
        logger.exception(f"Error parsing PyPGx results for {gene}: {str(e)}")
        return None, {}

if __name__ == "__main__":
    uvicorn.run("pypgx_wrapper:app", host="0.0.0.0", port=5000, reload=True) 