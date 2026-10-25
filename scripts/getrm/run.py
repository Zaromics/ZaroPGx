"""Run each cut sample through a ZaroPGx instance and keep what score.py reads.

    python scripts/getrm/run.py --base-url http://127.0.0.1:18765 --cuts CUTS --out RESULTS \\
        [SAMPLE ...]

CUTS holds SAMPLE.pgx.cram and its .crai for each sample (cut.sh writes them). With no
SAMPLE given, every sample in core.txt is run. A sample whose results are already in
RESULTS is skipped, so an interrupted run picks up where it stopped. Runs one job at a
time: the services each do one alignment at a time anyway.

Point it at a test instance, never at one holding anyone's own data: every sample is
uploaded as a patient named after it. The instance needs ZAROPGX_AUTH_MODE=open.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
# What score.py reads, and what a person checking a discordance wants beside it.
KEEP = {
    "{job}_pgx_pharmcat.json": "pharmcat.json",
    "pypgx_result.json": "pypgx_result.json",
    "hla_result.json": "hla_result.json",
    "pharmcat/pharmcat_position_status.tsv": "pharmcat_position_status.tsv",
    "{job}_pgx_report.pdf": "report.pdf",
    "trace.txt": "trace.txt",
}


def get(url: str, timeout: int = 60) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()


def submit(base: str, cram: Path, sample: str) -> str:
    cmd = [
        "curl", "-sS", "--fail-with-body", "-X", "POST", f"{base}/upload/genomic-data",
        "-F", f"files=@{cram}", "-F", f"files=@{cram}.crai",
        "-F", f"sample_identifier={sample}", "-F", "reference_genome=hg38",
    ]
    for toggle in ("optitype", "gatk", "pypgx", "report", "mtdna"):
        cmd += ["-F", f"{toggle}_enabled=true"]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"upload failed: {out.stdout or out.stderr}")
    return json.loads(out.stdout)["job_id"]


def wait(base: str, job: str, poll: int = 15) -> dict:
    while True:
        try:
            status = json.loads(get(f"{base}/upload/status/{job}"))
        except OSError as e:  # a busy instance can miss a poll; keep waiting
            print(f"  status: {e}", file=sys.stderr)
            time.sleep(poll)
            continue
        if status["status"] in ("completed", "failed", "cancelled", "error"):
            return status
        time.sleep(poll)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("samples", nargs="*")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--cuts", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    base = args.base_url.rstrip("/")
    samples = args.samples or (HERE / "core.txt").read_text().split()

    failed = []
    for sample in samples:
        dest = args.out / sample
        if (dest / "pharmcat.json").exists():
            continue
        cram = args.cuts / f"{sample}.pgx.cram"
        if not cram.exists():
            print(f"{sample}: no cut at {cram}", file=sys.stderr)
            failed.append(sample)
            continue
        started = time.time()
        job = submit(base, cram, sample)
        print(f"{sample}: job {job}", file=sys.stderr)
        status = wait(base, job)
        minutes = (time.time() - started) / 60
        if status["status"] != "completed":
            print(f"{sample}: {status['status']} after {minutes:.0f} min: {status.get('message')}", file=sys.stderr)
            failed.append(sample)
            continue
        patient = status["data"]["patient_id"]
        dest.mkdir(parents=True, exist_ok=True)
        for remote, local in KEEP.items():
            url = f"{base}/reports/{patient}/{job}/{remote.format(job=job)}"
            try:
                (dest / local).write_bytes(get(url))
            except OSError as e:
                print(f"{sample}: {remote}: {e}", file=sys.stderr)
        (dest / "job.json").write_text(json.dumps({"job_id": job, "minutes": round(minutes, 1)}))
        print(f"{sample}: done in {minutes:.0f} min", file=sys.stderr)
    if failed:
        sys.exit(f"not run: {' '.join(failed)}")


if __name__ == "__main__":
    main()
