#!/usr/bin/env python3
"""
Reference Genome Downloader Service for ZaroPGx
Downloads and indexes reference genomes in the background with progress tracking
"""

from fastapi import FastAPI, HTTPException
import glob
import hashlib
import tempfile
import threading
from urllib.parse import urlparse
import os
import time
import json
import subprocess
import requests
from tqdm import tqdm
import uvicorn

app = FastAPI(title="Genome Downloader API", version="0.4.0", description="REST API wrapper around genome downloader for the ZaroPGx pipeline")

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
        "hg19_to_hg38_chain": {"progress": 0, "size_mb": 1, "status": "pending"},
        "ref_cache_ucsc_hg38": {"progress": 0, "size_mb": 0, "status": "pending"},
        "ref_cache_1kg_analysis_set": {"progress": 0, "size_mb": 3113, "status": "pending"}
    },
    "overall_progress": 0
}

REFERENCE_DIR = '/reference'
STATUS_FILE = '/reference/download_status.json'
# Written once every genome above is staged; the reference cache keeps its own markers.
DOWNLOAD_COMPLETE_FLAG = '/reference/.download_complete'

def save_status():
    """Save the current status to a file"""
    with open(STATUS_FILE, 'w') as f:
        json.dump(download_status, f)

def load_saved_status():
    """Restore the status a previous run saved.

    A saved in_progress=True is always stale at startup: the download thread that
    set it died with its process, and nothing else clears it. Trusting it made
    schedule_download() and /start-download both report a download as already
    running, so one cut off by a restart or reboot never resumed.

    Saved genome entries are merged into the defaults rather than replacing the
    whole dict, so an item added after the file was written (hg19_to_hg38_chain
    arrived in 0.3.1) still has the entry download_file() and the loop index into.
    """
    if not os.path.exists(STATUS_FILE):
        return
    try:
        with open(STATUS_FILE, 'r') as f:
            saved = json.load(f)
    except Exception as e:
        print(f"Error loading status file: {str(e)}")
        return
    saved_genomes = saved.pop("genomes", None)
    download_status.update(saved)
    if isinstance(saved_genomes, dict):
        for name, entry in saved_genomes.items():
            if name in download_status["genomes"] and isinstance(entry, dict):
                download_status["genomes"][name].update(entry)
    if download_status["in_progress"]:
        print("The previous run stopped mid-download; it can start again.")
        download_status["in_progress"] = False
        try:
            save_status()
        except OSError as e:
            # The in-memory flag is what gates a new run. A status file this
            # process cannot rewrite must not keep the service from starting:
            # under restart: unless-stopped that would be a crash loop.
            print(f"Could not rewrite {STATUS_FILE}: {e}")

def discard_interrupted_output(genome, prior_status):
    """Delete what a stage left behind if the previous process died during it.

    A finished stage always moves the genome's status on, so a status saved as
    downloading, extracting or indexing means that stage never completed. Before
    downloads and extraction went through a .part file, they wrote their output in
    place, and the loop trusts any file that exists: a truncated .fa.gz or FASTA
    was carried on, indexed and reported ready. VCF, BED and chain entries are
    small and land straight at their final path, so anything short of ready is
    fetched again.
    """
    paths = []
    if genome.get("is_vcf") or genome.get("is_bed") or genome.get("is_chain"):
        if prior_status != "ready":
            paths = [genome["fasta_path"]]
    elif prior_status == "downloading":
        paths = [genome["gz_path"]]
    elif prior_status == "extracting":
        paths = [genome["fasta_path"]]
        if genome.get("is_tar"):
            # The tarball ships its own index; a partial extraction can leave it.
            paths += [genome["fasta_path"] + ".fai", genome["fasta_path"] + ".gzi"]
    elif prior_status == "indexing":
        paths = [genome["fasta_path"] + ".fai"]
    for path in paths:
        if os.path.exists(path):
            print(f"Discarding {path}: the previous run stopped while {prior_status}")
            os.remove(path)

REF_CACHE_DIR = "/reference/ref_cache"
# Off where no CRAM is decoded and the 3.2 GB download and 5.3 GB cache would only
# cost disk: compose.e2e.yml (CI runs the VCF lane alone). Without the cache a CRAM
# still decodes against the staged FASTA when its MD5s match it.
REF_CACHE_ENABLED = os.environ.get("REF_CACHE_ENABLED", "true").lower() == "true"

# The GRCh38 builds whose sequences go into the reference cache, so a CRAM written
# against any of them decodes exactly. Measured against 30x NA12878's CRAM and the
# builds themselves (2026-10): the 1000 Genomes analysis set with decoys and HLA
# holds all 3,366 sequences that CRAM names; UCSC hg38, staged above for analysis,
# adds 18 (the chromosomes where it keeps IUPAC codes the analysis sets write as N)
# and shares its other 437; NCBI's no-ALT analysis set adds none of its 195, so it
# is not fetched. 5.3 GB in all. The 1000 Genomes FASTA is only published
# uncompressed; it is removed once its sequences are cached.
REF_CACHE_SOURCES = [
    {
        "name": "ref_cache_ucsc_hg38",
        "fasta_path": "/reference/hg38/Homo_sapiens_assembly38.fasta",
    },
    {
        "name": "ref_cache_1kg_analysis_set",
        "url": (
            "https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/technical/reference/"
            "GRCh38_reference_genome/GRCh38_full_analysis_set_plus_decoy_hla.fa"
        ),
        "fasta_path": "/reference/ref_cache_sources/GRCh38_full_analysis_set_plus_decoy_hla.fa",
        "remove_after": True,
    },
]


def ref_cache_marker(source):
    """Written once a source's sequences are all in the cache."""
    return os.path.join(REF_CACHE_DIR, f".{source['name']}.done")


def ref_cache_pending():
    return [src for src in REF_CACHE_SOURCES if not os.path.exists(ref_cache_marker(src))]


def populate_reference_cache():
    """Stage every REF_CACHE_SOURCES build into the cache; True when all are in.

    Runs on installs whose genomes finished before the cache existed: it has its
    own per-source markers rather than /reference/.download_complete.
    """
    success = True
    for src in REF_CACHE_SOURCES:
        entry = download_status["genomes"][src["name"]]
        entry.pop("error", None)
        if os.path.exists(ref_cache_marker(src)):
            if src.get("remove_after") and os.path.exists(src["fasta_path"]):
                # The marker is written first; a stop between the two left it.
                os.remove(src["fasta_path"])
            entry.update(status="ready", progress=100)
            save_status()
            continue
        fasta = src["fasta_path"]
        if src.get("url") and not os.path.exists(fasta):
            os.makedirs(os.path.dirname(fasta), exist_ok=True)
            if not download_file(src["url"], fasta, src["name"]):
                success = False
                continue
        if not os.path.exists(fasta):
            entry.update(status="error", error=f"{fasta} is not staged")
            save_status()
            success = False
            continue
        try:
            entry["status"] = "caching"
            save_status()
            names = populate_ref_cache(fasta, REF_CACHE_DIR)
            with open(ref_cache_marker(src), "w") as fh:
                fh.write(f"{len(names)} sequences from {fasta}\n")
            if src.get("remove_after"):
                os.remove(fasta)
            entry.update(status="ready", progress=100)
            print(f"Reference cache: {len(names)} sequences from {fasta}")
        except Exception as e:
            print(f"Error caching {fasta}: {e}")
            entry.update(status="error", error=str(e))
            success = False
        save_status()
    return success


def ref_cache_path(cache_dir, md5):
    """htslib's REF_PATH layout, %2s/%2s/%s (see compose.yml). Each %Ns consumes
    the next N characters of the MD5 and %s takes what is left, so abcdef... lives
    at ab/cd/ef...: the file is named by the last 28, not the whole MD5."""
    return os.path.join(cache_dir, md5[:2], md5[2:4], md5[4:])


def populate_ref_cache(fasta_path, cache_dir):
    """Write every sequence of a FASTA into an MD5-keyed reference cache.

    The cache is what htslib reads a CRAM through: a CRAM records each reference
    sequence's MD5 (the @SQ M5 tag) and stores only differences from it, so it can
    be decoded with the exact sequence it was written against and no other. The
    format is samtools' seq_cache_populate.pl: one file per sequence, named by the
    MD5 of the sequence uppercased with whitespace removed, holding exactly those
    bytes. Keyed by content, the cache holds several GRCh38 builds at once with
    shared sequences stored once.

    Each file is written to a .part name and renamed, so an interrupted run leaves
    no partial sequence under a real MD5. Returns {name: md5} for the FASTA.
    """
    import hashlib

    written = {}

    def finish(name, part, digest):
        md5 = digest.hexdigest()
        dest = ref_cache_path(cache_dir, md5)
        if os.path.exists(dest):
            os.remove(part)
        else:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            os.replace(part, dest)
        written[name] = md5

    os.makedirs(cache_dir, exist_ok=True)
    part = os.path.join(cache_dir, ".populate.part")
    name, out, digest = None, None, None
    with open(fasta_path, "rb") as fasta:
        for line in fasta:
            if line.startswith(b">"):
                if out is not None:
                    out.close()
                    finish(name, part, digest)
                name = line[1:].split()[0].decode("ascii", "replace") if line[1:].split() else ""
                out, digest = open(part, "wb"), hashlib.md5()
                continue
            if out is None:
                continue
            seq = b"".join(line.split()).upper()
            out.write(seq)
            digest.update(seq)
    if out is not None:
        out.close()
        finish(name, part, digest)
    return written


def calculate_overall_progress():
    """Calculate and update overall progress"""
    total_genomes = len(download_status["genomes"])
    if total_genomes == 0:
        return 0
        
    total_progress = sum(genome["progress"] for genome in download_status["genomes"].values())
    overall = total_progress / total_genomes
    download_status["overall_progress"] = overall
    return overall

def sha256_of(path):
    """The SHA-256 of a file, read in 1 MB blocks."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def discard_unverified(genome):
    """Remove a pinned file already on disk that does not match its SHA-256.

    The loop below trusts any file it finds at a genome's path, so a wrong chain
    left by an earlier run (or put there by anything else) would be kept. One that
    fails its hash goes, and is fetched again and checked like a new download.
    """
    expected = genome.get("sha256")
    path = genome["gz_path"]
    if not expected or not os.path.exists(path):
        return
    actual = sha256_of(path)
    if actual != expected:
        print(f"Removing {path}: its SHA-256 is {actual}, expected {expected}")
        os.remove(path)


# One run at a time. A POST /start-download during the startup delay, or two quick
# POSTs, started a second download_genomes() beside the first: two writers on one
# .part file. download_genomes() and the cache-only run take this without waiting
# and return if it is held.
_download_lock = threading.Lock()

# (connect, read) seconds: a stalled connection fails the file instead of hanging
# the run with in_progress stuck at true.
REQUEST_TIMEOUT = (30, 300)


def verify_staged_pins():
    """At startup, check the pinned files that stay where they were downloaded (the
    liftover chain), even on an install whose .download_complete says it is done:
    the run that would check them is skipped then, and gatk-api and the mtDNA
    service read the chain from the shared /reference. One that fails its hash is
    removed, and .download_complete with it, so the next run fetches it again.
    Returns True when something was removed."""
    removed = False
    for genome in GENOMES:
        path = genome["gz_path"]
        if not genome.get("sha256") or path != genome["fasta_path"]:
            continue
        if not os.path.exists(path):
            continue
        discard_unverified(genome)
        if not os.path.exists(path):
            removed = True
            entry = download_status["genomes"][genome["name"]]
            entry.update(status="pending", progress=0)
            entry.pop("sha256_verified", None)
    if removed:
        if os.path.exists(DOWNLOAD_COMPLETE_FLAG):
            os.remove(DOWNLOAD_COMPLETE_FLAG)
        download_status["completed"] = False
        save_status()
    return removed


def download_file(url, dest_path, genome_name, sha256=None, what=None):
    """Download a file with progress tracking.

    With sha256, the download is hashed as it streams and checked before it is
    moved into place; one that does not match is discarded: no file at dest_path
    is better than a wrong one. The genome's status then says why (`what` names
    the file there).
    """
    # Into place only once the body has arrived in full. Written straight to
    # dest_path, a download cut off by a restart left a truncated file there, and
    # the next run's exists() check took it for a finished one. A unique name per
    # download (and one run at a time, _download_lock), so no two writers share
    # one; a stopped run's leftovers are removed first.
    for stale in glob.glob(glob.escape(dest_path) + ".*.part") + [dest_path + ".part"]:
        if os.path.exists(stale):
            os.remove(stale)
    part_path = None
    try:
        # Get file size
        response = requests.head(url, allow_redirects=True, timeout=REQUEST_TIMEOUT)
        file_size = int(response.headers.get('content-length', 0))
        file_size_mb = file_size / (1024 * 1024)
        
        download_status["genomes"][genome_name]["size_mb"] = round(file_size_mb, 1)
        download_status["genomes"][genome_name]["status"] = "downloading"
        save_status()
        
        # Download with progress tracking
        response = requests.get(url, stream=True, timeout=REQUEST_TIMEOUT)
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
        digest = hashlib.sha256() if sha256 else None

        fd, part_path = tempfile.mkstemp(
            dir=os.path.dirname(dest_path) or ".",
            prefix=os.path.basename(dest_path) + ".",
            suffix=".part",
        )
        # mkstemp makes the file 0600; the other services (the mtDNA one runs as its
        # own user) read these from the shared /reference.
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, 'wb') as f:
            for chunk in response.iter_content(chunk_size=1024*1024):
                if chunk:
                    f.write(chunk)
                    if digest is not None:
                        digest.update(chunk)
                    downloaded += len(chunk)
                    # Capped: file_size is the HEAD's Content-Length, which for a
                    # gzip-encoded response (GitHub raw) is the compressed size, while
                    # iter_content counts decoded bytes -- pharmcat_regions read 189%.
                    progress = min((downloaded / file_size) * 100, 100) if file_size else 0
                    download_status["genomes"][genome_name]["progress"] = progress
                    calculate_overall_progress()
                    save_status()

        if digest is not None:
            actual = digest.hexdigest()
            if actual != sha256:
                where = urlparse(url)
                raise ValueError(
                    f"SHA-256 mismatch for {url}: expected {sha256}, got {actual}. "
                    f"The download was discarded; {what or genome_name} was not "
                    f"installed. Restart genome-downloader to retry; if it repeats, "
                    f"something between this host and {where.netloc} is altering "
                    f"{where.scheme} downloads (a proxy or captive portal)."
                )
        os.replace(part_path, dest_path)
        return True
    except Exception as e:
        print(f"Error downloading {url}: {str(e)}")
        if part_path and os.path.exists(part_path):
            os.remove(part_path)
        download_status["genomes"][genome_name]["status"] = "error"
        download_status["genomes"][genome_name]["error"] = str(e)
        save_status()
        return False

def extract_file(file_path, output_path, genome_name):
    """Extract gzipped file"""
    try:
        download_status["genomes"][genome_name]["status"] = "extracting"
        save_status()
        
        # Through a .part file for the same reason as download_file(), and the exit
        # status checked: it was ignored, so a truncated .gz extracted to a truncated
        # FASTA that the next steps indexed and reported ready. gzip exits 1 on an
        # error and 2 on a warning; human_g1k_v37.fasta.gz always draws "trailing
        # garbage ignored" (exit 2) and decompresses in full, so only 1 fails.
        part_path = output_path + ".part"
        with open(part_path, "wb") as out:
            result = subprocess.run(["gunzip", "-c", file_path], stdout=out)
        if result.returncode not in (0, 2):
            os.remove(part_path)
            # Corrupt or truncated: remove it so the next run fetches it again
            # instead of failing on the same file forever.
            os.remove(file_path)
            raise RuntimeError(f"gunzip exited {result.returncode}; removed {file_path}")
        os.replace(part_path, output_path)

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
        
        # Extract tar file to output directory. --no-same-owner: tar running as root
        # otherwise chowns to the archive's uid (197609 in PharmCAT's tarball), which a
        # rootless/userns-remapped daemon cannot map -- tar exits 2 with every file
        # already written, and this step used to report that as a failed download.
        subprocess.run(
            ["tar", "--no-same-owner", "-xf", file_path, "-C", os.path.dirname(output_path)],
            check=True,
        )
        
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


# Everything the downloader stages, in order.
GENOMES = [
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
        # https, not ftp: requests has no FTP adapter, so the ftp:// URL failed on
        # every install. EBI serves the same tree over HTTPS.
        "url": "https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/technical/reference/human_g1k_v37.fasta.gz",
        "gz_path": "/reference/grch37/human_g1k_v37.fasta.gz",
        "fasta_path": "/reference/grch37/human_g1k_v37.fasta"
    },
    {
        "name": "pharmcat_grch38",
        "url": "https://zenodo.org/record/7288118/files/GRCh38_reference_fasta.tar",
        "gz_path": "/reference/pharmcat/GRCh38_reference_fasta.tar",
        # The file the tarball actually contains. It ships its own .fai/.gzi, so
        # the ".fai exists" skip check below works and no indexing step runs.
        # (This used to name a path the tarball never creates, so indexing it
        # failed on every host and .download_complete was never written.)
        "fasta_path": "/reference/pharmcat/reference.fna.bgz",
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
        # At the release's tag, like the positions above, not the moving
        # `development` branch it used to come from (the same 756 bytes today).
        "name": "pharmcat_regions",
        "url": f"https://github.com/PharmGKB/PharmCAT/raw/v{PHARMCAT_VERSION}/pharmcat_regions.bed",
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
        #
        # Its integrity is the pinned SHA-256, checked after every download, not
        # TLS, so it is fetched over plain http. hgdownload.soe.ucsc.edu's
        # certificate chains through InCommon to emSign Root TLS CA - G3 and emSign
        # ECC Root CA - G3. eMudhra revoked G3 intermediates for CA compromise on
        # 2026-10-18, Mozilla removed the G3 roots from NSS on 2026-10-20 (bug
        # 2084101), and certifi 2026.10.19 no longer carries them, so https to UCSC
        # fails verification. Trusting another CA bundle that still has them would
        # re-trust a distrusted root. The file has not changed since 2014 (227,698
        # bytes). The SHA-256 is that of the Internet Archive's 2017 and 2021
        # captures, fetched over https from web.archive.org (a different CA), and of
        # UCSC's copy fetched over https with a CA store that still verified it; the
        # copy fetched over http matches too. UCSC's md5sum.txt for the directory
        # (MD5 35887f73fe5e2231656504d1f6430900) agrees, but it comes from the same
        # http host, so it is not an independent check.
        "name": "hg19_to_hg38_chain",
        "what": "the liftover chain",
        "url": "http://hgdownload.soe.ucsc.edu/goldenPath/hg19/liftOver/hg19ToHg38.over.chain.gz",
        "sha256": "5c0598e500ceb5a78c73086929e8ef993aec309bcafb595139b53d440b125a1d",
        "gz_path": "/reference/chain/hg19ToHg38.over.chain.gz",
        "fasta_path": "/reference/chain/hg19ToHg38.over.chain.gz",
        "is_chain": True
    }
]


def discard_archive(genome):
    """Remove a genome's downloaded archive once the FASTA it held is extracted and
    indexed: the .fai is what the loop below takes to mean finished. The archives
    sat beside their FASTAs for good, 3.7 GB (hg38 984 MB, hg19 949 MB, grch37
    892 MB, PharmCAT's tarball 883 MB) that nothing reads. VCF, BED and chain files
    download straight to their final path and have no archive."""
    if genome.get("is_vcf") or genome.get("is_bed") or genome.get("is_chain"):
        return
    archive = genome["gz_path"]
    if os.path.exists(archive) and os.path.exists(genome["fasta_path"] + ".fai"):
        os.remove(archive)
        print(f"Removed {archive}: {genome['fasta_path']} is extracted and indexed")


def sweep_archives(complete_flag='/reference/.download_complete'):
    """At startup, remove the archives an install staged before they were removed
    still holds. Only once every genome is staged: a restart during an extraction
    leaves a FASTA and .fai that look finished (PharmCAT's tarball unpacks in
    place), and download_genomes() discards those and extracts again, from the
    archive."""
    if not os.path.exists(complete_flag):
        return
    for genome in GENOMES:
        discard_archive(genome)


def download_genomes():
    """Main function to download and process all genomes. One run at a time: a
    call while another runs returns at once."""
    if not _download_lock.acquire(blocking=False):
        print("A download run is already in progress; not starting another.")
        return
    try:
        _download_genomes()
    finally:
        _download_lock.release()


def _download_genomes():
    global download_status
    download_status["in_progress"] = True
    save_status()
    
    # Create required directories
    for dir_name in ["hg19", "hg38", "grch37", "grch38", "pharmcat", "chain"]:
        os.makedirs(os.path.join(REFERENCE_DIR, dir_name), exist_ok=True)

    success = True
    
    for genome in GENOMES:
        discard_interrupted_output(genome, download_status["genomes"][genome["name"]].get("status"))

        # Status is reloaded from disk at startup, so a previous run's error would
        # otherwise ride along on an item this run completes.
        download_status["genomes"][genome["name"]].pop("error", None)
        download_status["genomes"][genome["name"]].pop("sha256_verified", None)

        # Skip if already completed
        if os.path.exists(genome["fasta_path"] + ".fai"):
            discard_archive(genome)
            download_status["genomes"][genome["name"]]["status"] = "ready"
            download_status["genomes"][genome["name"]]["progress"] = 100
            save_status()
            continue
            
        # Download (a pinned file already there is checked first)
        discard_unverified(genome)
        if not os.path.exists(genome["gz_path"]):
            if not download_file(
                genome["url"],
                genome["gz_path"],
                genome["name"],
                sha256=genome.get("sha256"),
                what=genome.get("what"),
            ):
                success = False
                continue
        if genome.get("sha256"):
            download_status["genomes"][genome["name"]]["sha256_verified"] = genome["sha256"]
        
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
        if genome.get("is_tar"):
            # The PharmCAT tarball ships its own .fai/.gzi alongside the bgzipped FASTA.
            discard_archive(genome)
            download_status["genomes"][genome["name"]]["status"] = "ready"
            download_status["genomes"][genome["name"]]["progress"] = 100
            save_status()
            continue
        elif genome.get("is_vcf") or genome.get("is_bed") or genome.get("is_chain"):
            # VCF, BED and chain files don't need indexing. They download straight to
            # their final path, so the copy branch above (the only other place that
            # marks them ready) never runs and they otherwise sit at "downloading".
            download_status["genomes"][genome["name"]]["status"] = "ready"
            download_status["genomes"][genome["name"]]["progress"] = 100
            save_status()
            continue
        elif not index_genome(genome["fasta_path"], genome["name"]):
            success = False
            continue
        discard_archive(genome)
    
    # Create symlink for GRCh38
    if success:
        try:
            grch38 = os.path.join(REFERENCE_DIR, "grch38", "Homo_sapiens_assembly38.fasta")
            if not os.path.exists(grch38):
                os.symlink(
                    os.path.join(REFERENCE_DIR, "hg38", "Homo_sapiens_assembly38.fasta"),
                    grch38,
                )
        except Exception as e:
            print(f"Error creating symlink: {str(e)}")
    
    finish_download(success)


def finish_download(success):
    """Mark the genomes staged, then build the CRAM reference cache.

    The cache needs UCSC hg38 above; it keeps its own markers, so
    .download_complete stays about the genomes alone. The run is in progress until
    the cache is built too: it is a 3.2 GB download of its own.
    """
    if success:
        with open(DOWNLOAD_COMPLETE_FLAG, 'w') as f:
            f.write('Completed')
    try:
        if success and REF_CACHE_ENABLED:
            success = populate_reference_cache()
    finally:
        download_status["in_progress"] = False
        download_status["completed"] = success
        save_status()

def schedule_download(delay_seconds=5):
    """Schedule download to start after a delay"""
    def delayed_start():
        print(f"Waiting {delay_seconds} seconds before starting downloads...")
        time.sleep(delay_seconds)
        print("Starting delayed genome downloads...")
        download_genomes()
    
    # Check if downloads were already completed
    if os.path.exists(DOWNLOAD_COMPLETE_FLAG):
        if REF_CACHE_ENABLED and ref_cache_pending():
            # Genomes staged before the reference cache existed: add just the cache.
            print("Reference genomes already downloaded; building the CRAM reference cache.")

            def cache_only():
                if not _download_lock.acquire(blocking=False):
                    print("A download run is already in progress; not starting another.")
                    return
                download_status["in_progress"] = True
                save_status()
                try:
                    download_status["completed"] = populate_reference_cache()
                finally:
                    download_status["in_progress"] = False
                    save_status()
                    _download_lock.release()

            threading.Thread(target=cache_only).start()
            return
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
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, 'r') as f:
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
    load_saved_status()

    sweep_archives()

    # Even on a finished install: a pinned file that fails its hash is removed with
    # .download_complete, and a run is scheduled to fetch it again.
    repinned = verify_staged_pins()

    # Schedule downloads to start after the server has fully initialized
    if repinned or os.environ.get('DOWNLOAD_ON_STARTUP', 'true').lower() == 'true':
        # Schedule downloads to start after a delay (10 seconds after server startup)
        print("Scheduling reference genome downloads to start shortly after server startup")
        threading.Thread(target=lambda: schedule_download(10)).start()
    
    # Start the FastAPI server
    uvicorn.run(app, host='0.0.0.0', port=5050) 