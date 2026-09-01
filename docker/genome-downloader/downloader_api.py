#!/usr/bin/env python3
"""
Reference Genome Downloader Service for ZaroPGx
Downloads and indexes reference genomes in the background with progress tracking
"""

from fastapi import FastAPI, HTTPException
import threading
import os
import time
import json
import subprocess
import requests
from tqdm import tqdm
import uvicorn

app = FastAPI(title="Genome Downloader API", version="0.3.1", description="REST API wrapper around genome downloader for the ZaroPGx pipeline")

# The PharmCAT release whose artefacts this service stages. Must track compose.yml's
# PHARMCAT_VERSION (which the pharmcat image is built from), because
# pharmcat_positions.vcf is version-specific: see the pharmcat_positions entry below.
# Defaulted rather than required so a bare `python downloader_api.py` still runs.
PHARMCAT_VERSION = os.environ.get("PHARMCAT_VERSION", "3.4.0")

# Global variable to track download progress
download_status = {
    "in_progress": False,
    "completed": False,
    "genomes": {
        "hg19": {"progress": 0, "size_mb": 850, "status": "pending"},
        "hg38": {"progress": 0, "size_mb": 920, "status": "pending"},
        "grch37": {"progress": 0, "size_mb": 810, "status": "pending"},
        "pharmcat_grch38": {"progress": 0, "size_mb": 150, "status": "pending"},
        "pharmcat_positions": {"progress": 0, "size_mb": 5, "status": "pending"},
        "pharmcat_regions": {"progress": 0, "size_mb": 1, "status": "pending"},
        "hg19_to_hg38_chain": {"progress": 0, "size_mb": 1, "status": "pending"}
    },
    "overall_progress": 0
}

def save_status():
    """Save the current status to a file"""
    with open('/reference/download_status.json', 'w') as f:
        json.dump(download_status, f)

def calculate_overall_progress():
    """Calculate and update overall progress"""
    total_genomes = len(download_status["genomes"])
    if total_genomes == 0:
        return 0
        
    total_progress = sum(genome["progress"] for genome in download_status["genomes"].values())
    overall = total_progress / total_genomes
    download_status["overall_progress"] = overall
    return overall

def download_file(url, dest_path, genome_name):
    """Download a file with progress tracking"""
    try:
        # Get file size
        response = requests.head(url, allow_redirects=True)
        file_size = int(response.headers.get('content-length', 0))
        file_size_mb = file_size / (1024 * 1024)
        
        download_status["genomes"][genome_name]["size_mb"] = round(file_size_mb, 1)
        download_status["genomes"][genome_name]["status"] = "downloading"
        save_status()
        
        # Download with progress tracking
        response = requests.get(url, stream=True)
        # raise_for_status() was missing entirely, and the pharmcat_positions entry made
        # that reachable: its URL is templated on PHARMCAT_VERSION, so a bump to a
        # release whose asset is named differently 404s -- and without this check the
        # 9-byte error body was written to /reference/pharmcat/pharmcat_positions.vcf,
        # returned True, and (because gz_path == fasta_path for that entry, so nothing
        # extracts or indexes) the genome was reported "ready". gatk-api's /gvcf-to-vcf
        # then found a file at the expected path and got an opaque GATK parse error
        # instead of the 400 that names the file and the fix. Fail here, where the
        # status field can say so.
        response.raise_for_status()
        downloaded = 0

        with open(dest_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=1024*1024):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    progress = (downloaded / file_size) * 100
                    download_status["genomes"][genome_name]["progress"] = progress
                    calculate_overall_progress()
                    save_status()
        
        return True
    except Exception as e:
        print(f"Error downloading {url}: {str(e)}")
        download_status["genomes"][genome_name]["status"] = "error"
        download_status["genomes"][genome_name]["error"] = str(e)
        save_status()
        return False

def extract_file(file_path, output_path, genome_name):
    """Extract gzipped file"""
    try:
        download_status["genomes"][genome_name]["status"] = "extracting"
        save_status()
        
        # Using gunzip to extract
        subprocess.run(["gunzip", "-c", file_path], stdout=open(output_path, "wb"))
        
        download_status["genomes"][genome_name]["status"] = "extracted"
        save_status()
        return True
    except Exception as e:
        print(f"Error extracting {file_path}: {str(e)}")
        download_status["genomes"][genome_name]["status"] = "error"
        download_status["genomes"][genome_name]["error"] = str(e)
        save_status()
        return False

def extract_tar_file(file_path, output_path, genome_name):
    """Extract tar file"""
    try:
        download_status["genomes"][genome_name]["status"] = "extracting"
        save_status()
        
        # Extract tar file to output directory
        subprocess.run(["tar", "-xf", file_path, "-C", os.path.dirname(output_path)], check=True)
        
        download_status["genomes"][genome_name]["status"] = "extracted"
        save_status()
        return True
    except Exception as e:
        print(f"Error extracting tar file {file_path}: {str(e)}")
        download_status["genomes"][genome_name]["status"] = "error"
        download_status["genomes"][genome_name]["error"] = str(e)
        save_status()
        return False

def index_genome(fasta_path, genome_name):
    """Create genome index files using samtools"""
    try:
        download_status["genomes"][genome_name]["status"] = "indexing"
        save_status()
        
        # Create samtools index
        subprocess.run(["samtools", "faidx", fasta_path], check=True)
        
        # Note: We no longer create GATK dictionary here
        # GATK dictionary creation will be handled by the GATK API service when needed
        
        download_status["genomes"][genome_name]["status"] = "ready"
        download_status["genomes"][genome_name]["progress"] = 100
        save_status()
        return True
    except Exception as e:
        print(f"Error indexing {fasta_path}: {str(e)}")
        download_status["genomes"][genome_name]["status"] = "error"
        download_status["genomes"][genome_name]["error"] = str(e)
        save_status()
        return False

def download_genomes():
    """Main function to download and process all genomes"""
    global download_status
    download_status["in_progress"] = True
    save_status()
    
    # Create required directories
    for dir_name in ["hg19", "hg38", "grch37", "grch38", "pharmcat", "chain"]:
        os.makedirs(f"/reference/{dir_name}", exist_ok=True)
    
    # Start downloads
    genomes = [
        {
            "name": "hg19",
            "url": "http://hgdownload.cse.ucsc.edu/goldenPath/hg19/bigZips/hg19.fa.gz",
            "gz_path": "/reference/hg19/ucsc.hg19.fasta.gz",
            "fasta_path": "/reference/hg19/ucsc.hg19.fasta"
        },
        {
            "name": "hg38",
            "url": "http://hgdownload.cse.ucsc.edu/goldenPath/hg38/bigZips/hg38.fa.gz",
            "gz_path": "/reference/hg38/Homo_sapiens_assembly38.fasta.gz",
            "fasta_path": "/reference/hg38/Homo_sapiens_assembly38.fasta"
        },
        {
            "name": "grch37",
            "url": "ftp://ftp.1000genomes.ebi.ac.uk/vol1/ftp/technical/reference/human_g1k_v37.fasta.gz",
            "gz_path": "/reference/grch37/human_g1k_v37.fasta.gz",
            "fasta_path": "/reference/grch37/human_g1k_v37.fasta"
        },
        {
            "name": "pharmcat_grch38",
            "url": "https://zenodo.org/record/7288118/files/GRCh38_reference_fasta.tar",
            "gz_path": "/reference/pharmcat/GRCh38_reference_fasta.tar",
            "fasta_path": "/reference/pharmcat/GRCh38_reference_fasta",
            "is_tar": True
        },
        {
            # PharmCAT's own position list. Consumed by gatk-api's /gvcf-to-vcf as the
            # interval list its --include-non-variant-sites pass is emitted over, so a
            # gVCF upload's reference genotypes land at exactly the positions PharmCAT
            # genotypes -- see PHARMCAT_POSITIONS_PATH in docker/gatk-api/gatk_api.py,
            # which points at exactly this path.
            #
            # PINNED TO THE PHARMCAT VERSION THE STACK RUNS, not to the `development`
            # branch it used to fetch. This file is the matcher's definition of which
            # positions matter and it changes between releases: fetching `development`
            # against a pinned 3.4.0 image means the reference calls are emitted at one
            # release's positions while PharmCAT genotypes another's, and the
            # difference surfaces as no-calls nobody ordered rather than as an error.
            # The release asset carries the version in its own filename
            # (pharmcat_positions_3.4.0.vcf), which is why the URL and the destination
            # differ here where they match everywhere else in this list.
            #
            # RE-STAGE THIS ON EVERY PHARMCAT BUMP. Bumping PHARMCAT_VERSION alone is
            # not enough on an existing deployment: download_genomes() is short-
            # circuited by /reference/.download_complete (see schedule_download), so a
            # reference tree populated before this entry existed -- or before the bump
            # -- keeps whatever it already has, or nothing. Delete
            # /reference/.download_complete to force a re-fetch, or copy the file out
            # of the pgx_pharmcat image, where it lives at
            # /pharmcat/pharmcat_positions.vcf.
            "name": "pharmcat_positions",
            "url": (
                f"https://github.com/PharmGKB/PharmCAT/releases/download/"
                f"v{PHARMCAT_VERSION}/pharmcat_positions_{PHARMCAT_VERSION}.vcf"
            ),
            "gz_path": "/reference/pharmcat/pharmcat_positions.vcf",
            "fasta_path": "/reference/pharmcat/pharmcat_positions.vcf",
            "is_vcf": True
        },
        {
            "name": "pharmcat_regions",
            "url": "https://github.com/PharmGKB/PharmCAT/raw/development/pharmcat_regions.bed",
            "gz_path": "/reference/pharmcat/pharmcat_regions.bed",
            "fasta_path": "/reference/pharmcat/pharmcat_regions.bed",
            "is_bed": True
        },
        {
            # UCSC hg19 -> hg38 liftover chain, consumed by gatk-api's /liftover-vcf
            # (Picard LiftoverVcf). Stays gzipped: htsjdk reads .gz chains directly,
            # and gatk_api.py's LIFTOVER_CHAIN_PATHS points at exactly this path. No
            # extraction and no indexing, so it is flagged is_chain and treated like
            # the VCF/BED entries above: downloaded straight to its final path.
            "name": "hg19_to_hg38_chain",
            "url": "https://hgdownload.soe.ucsc.edu/goldenPath/hg19/liftOver/hg19ToHg38.over.chain.gz",
            "gz_path": "/reference/chain/hg19ToHg38.over.chain.gz",
            "fasta_path": "/reference/chain/hg19ToHg38.over.chain.gz",
            "is_chain": True
        }
    ]
    
    success = True
    
    for genome in genomes:
        # Skip if already completed
        if os.path.exists(genome["fasta_path"] + ".fai"):
            download_status["genomes"][genome["name"]]["status"] = "ready"
            download_status["genomes"][genome["name"]]["progress"] = 100
            save_status()
            continue
            
        # Download
        if not os.path.exists(genome["gz_path"]):
            if not download_file(genome["url"], genome["gz_path"], genome["name"]):
                success = False
                continue
        
        # Extract
        if not os.path.exists(genome["fasta_path"]):
            if genome.get("is_tar"):
                # Handle tar files (PharmCAT GRCh38 reference)
                if not extract_tar_file(genome["gz_path"], genome["fasta_path"], genome["name"]):
                    success = False
                    continue
            elif genome.get("is_vcf") or genome.get("is_bed") or genome.get("is_chain"):
                # VCF, BED and chain files don't need extraction, just copy
                # (the chain deliberately stays gzipped - htsjdk reads it as-is)
                if not os.path.exists(genome["fasta_path"]):
                    import shutil
                    shutil.copy2(genome["gz_path"], genome["fasta_path"])
                    download_status["genomes"][genome["name"]]["status"] = "ready"
                    download_status["genomes"][genome["name"]]["progress"] = 100
                    save_status()
                    continue
            else:
                # Handle regular gzipped files
                if not extract_file(genome["gz_path"], genome["fasta_path"], genome["name"]):
                    success = False
                    continue
        
        # Index
        if genome.get("is_vcf") or genome.get("is_bed") or genome.get("is_chain"):
            # VCF, BED and chain files don't need indexing
            continue
        elif not index_genome(genome["fasta_path"], genome["name"]):
            success = False
            continue
    
    # Create symlink for GRCh38
    if success:
        try:
            if not os.path.exists("/reference/grch38/Homo_sapiens_assembly38.fasta"):
                os.symlink(
                    "/reference/hg38/Homo_sapiens_assembly38.fasta", 
                    "/reference/grch38/Homo_sapiens_assembly38.fasta"
                )
        except Exception as e:
            print(f"Error creating symlink: {str(e)}")
    
    # Update status
    download_status["in_progress"] = False
    download_status["completed"] = success
    
    # Create a flag file to indicate completion
    if success:
        with open('/reference/.download_complete', 'w') as f:
            f.write('Completed')
    
    save_status()

def schedule_download(delay_seconds=5):
    """Schedule download to start after a delay"""
    def delayed_start():
        print(f"Waiting {delay_seconds} seconds before starting downloads...")
        time.sleep(delay_seconds)
        print("Starting delayed genome downloads...")
        download_genomes()
    
    # Check if downloads were already completed
    if os.path.exists('/reference/.download_complete'):
        print("Reference genomes already downloaded.")
        download_status["completed"] = True
        return
        
    # Check if downloads are in progress
    if download_status["in_progress"]:
        print("Downloads already in progress.")
        return
        
    # Start the download in a new thread after delay
    threading.Thread(target=delayed_start).start()
    print(f"Scheduled genome downloads to start in {delay_seconds} seconds.")

@app.get('/health')
def health():
    """Health check endpoint"""
    return {"status": "healthy"}

@app.get('/status')
def status():
    """Return current download status"""
    # Load from file if exists
    if os.path.exists('/reference/download_status.json'):
        try:
            with open('/reference/download_status.json', 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error reading status file: {str(e)}")
    
    # Otherwise return current status
    return download_status

@app.post('/start-download')
def start_download():
    """Start the download process"""
    if not download_status["in_progress"] and not download_status["completed"]:
        threading.Thread(target=download_genomes).start()
        return {"status": "started"}
    return {"status": "already_running" if download_status["in_progress"] else "already_completed"}

if __name__ == "__main__":
    # Load existing status if available
    if os.path.exists('/reference/download_status.json'):
        try:
            with open('/reference/download_status.json', 'r') as f:
                download_status.update(json.load(f))
        except Exception as e:
            print(f"Error loading status file: {str(e)}")
    
    # Schedule downloads to start after the server has fully initialized
    if os.environ.get('DOWNLOAD_ON_STARTUP', 'true').lower() == 'true':
        # Schedule downloads to start after a delay (10 seconds after server startup)
        print("Scheduling reference genome downloads to start shortly after server startup")
        threading.Thread(target=lambda: schedule_download(10)).start()
    
    # Start the FastAPI server
    uvicorn.run(app, host='0.0.0.0', port=5050) 