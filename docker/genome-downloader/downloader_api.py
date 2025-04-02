#!/usr/bin/env python3
"""
Reference Genome Downloader Service for ZaroPGx
Downloads and indexes reference genomes in the background with progress tracking
"""

from flask import Flask, jsonify, request
import threading
import os
import time
import json
import subprocess
import requests
from tqdm import tqdm

app = Flask(__name__)

# Global variable to track download progress
download_status = {
    "in_progress": False,
    "completed": False,
    "genomes": {
        "hg19": {"progress": 0, "size_mb": 850, "status": "pending"},
        "hg38": {"progress": 0, "size_mb": 920, "status": "pending"},
        "grch37": {"progress": 0, "size_mb": 810, "status": "pending"}
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
    for dir_name in ["hg19", "hg38", "grch37", "grch38"]:
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
            if not extract_file(genome["gz_path"], genome["fasta_path"], genome["name"]):
                success = False
                continue
        
        # Index
        if not index_genome(genome["fasta_path"], genome["name"]):
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

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({"status": "healthy"})

@app.route('/status')
def status():
    """Return current download status"""
    # Load from file if exists
    if os.path.exists('/reference/download_status.json'):
        try:
            with open('/reference/download_status.json', 'r') as f:
                return jsonify(json.load(f))
        except Exception as e:
            print(f"Error reading status file: {str(e)}")
    
    # Otherwise return current status
    return jsonify(download_status)

@app.route('/start-download', methods=['POST'])
def start_download():
    """Start the download process"""
    if not download_status["in_progress"] and not download_status["completed"]:
        threading.Thread(target=download_genomes).start()
        return jsonify({"status": "started"})
    return jsonify({"status": "already_running" if download_status["in_progress"] else "already_completed"})

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
    
    # Start the Flask server
    app.run(host='0.0.0.0', port=5050) 