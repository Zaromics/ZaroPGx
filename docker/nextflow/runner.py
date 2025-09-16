import os
import subprocess
import threading
import time
import json
from flask import Flask, request, jsonify
from datetime import datetime, timezone
from typing import Dict, Optional

app = Flask(__name__)

# Global dictionary to track running jobs
running_jobs: Dict[str, Dict] = {}

def check_external_service_health(service_name: str) -> bool:
    """Check if an external service is healthy."""
    try:
        import requests
        response = requests.get(f"http://{service_name}:5000/health", timeout=2)
        return response.status_code == 200
    except:
        return False


@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})

@app.route('/run', methods=['POST'])
def run():
    data = request.form or request.json or {}
    input_path = data.get('input')
    input_type = data.get('input_type')
    patient_id = data.get('patient_id')
    report_id = data.get('report_id', patient_id)
    reference = data.get('reference', 'hg38')
    outdir = data.get('outdir', f"/data/reports/{patient_id}")
    job_id = data.get('job_id', patient_id)  # Use job_id if provided
    skip_hla = data.get('skip_hla', 'false')
    skip_pypgx = data.get('skip_pypgx', 'false')

    if not input_path or not input_type or not patient_id:
        return jsonify({"success": False, "error": "Missing required params: input, input_type, patient_id"}), 400

    # Create job tracking entry
    job_key = f"{patient_id}_{report_id}"
    running_jobs[job_key] = {
        "job_id": job_id,
        "patient_id": patient_id,
        "report_id": report_id,
        "status": "starting",
        "start_time": datetime.now(timezone.utc).isoformat(),
        "message": "Initializing Nextflow pipeline"
    }

    # Start Nextflow in a separate thread
    thread = threading.Thread(target=run_nextflow_job, args=(job_key, input_path, input_type, patient_id, report_id, reference, outdir, skip_hla, skip_pypgx))
    thread.daemon = True
    thread.start()

    return jsonify({
        "success": True,
        "job_id": job_id,
        "job_key": job_key,
        "outdir": outdir,
        "message": "Nextflow job started"
    })

def run_nextflow_job(job_key: str, input_path: str, input_type: str, patient_id: str, report_id: str, reference: str, outdir: str, skip_hla: str = 'false', skip_pypgx: str = 'false'):
    """Run Nextflow job in background thread."""
    try:
        # Update job status
        running_jobs[job_key]["status"] = "running"
        running_jobs[job_key]["message"] = "Nextflow pipeline started"

        cmd = [
            'nextflow', 'run', 'pipelines/pgx/main.nf', '-profile', 'docker',
            '--input', input_path,
            '--input_type', input_type,
            '--patient_id', str(patient_id),
            '--report_id', str(report_id),
            '--reference', reference,
            '--outdir', outdir,
            '--skip_hla', skip_hla,
            '--skip_pypgx', skip_pypgx,
            '-with-report', f"{outdir}/nextflow_report.html",
            '-with-trace', f"{outdir}/nextflow_trace.txt",
            '-with-timeline', f"{outdir}/nextflow_timeline.html",
            '-ansi-log', 'false'
        ]

        os.makedirs(outdir, exist_ok=True)
        
        # Run Nextflow (non-blocking)
        print(f"Running Nextflow command: {' '.join(cmd)}")
        print(f"Input file path: {input_path}")
        print(f"Input file exists: {os.path.exists(input_path)}")
        if os.path.exists(input_path):
            print(f"Input file size: {os.path.getsize(input_path)} bytes")
        
        # Start Nextflow process (non-blocking)
        proc = subprocess.Popen(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        running_jobs[job_key]["nextflow_process"] = proc
        
        # Wait for process to complete in a separate thread
        def wait_for_completion():
            try:
                stdout, stderr = proc.communicate()
                print(f"Nextflow stdout: {stdout}")
                print(f"Nextflow stderr: {stderr}")
                print(f"Nextflow return code: {proc.returncode}")
                
                # Update final status
                if proc.returncode == 0:
                    running_jobs[job_key]["status"] = "completed"
                    running_jobs[job_key]["message"] = "Nextflow pipeline completed successfully"
                else:
                    running_jobs[job_key]["status"] = "failed"
                    running_jobs[job_key]["message"] = f"Nextflow pipeline failed with return code {proc.returncode}"
                    running_jobs[job_key]["error"] = stderr[-1000:] if stderr else "Unknown error"
                
                running_jobs[job_key]["end_time"] = datetime.now(timezone.utc).isoformat()
                running_jobs[job_key]["returncode"] = proc.returncode
            except Exception as e:
                running_jobs[job_key]["status"] = "failed"
                running_jobs[job_key]["message"] = f"Nextflow job failed: {str(e)}"
                running_jobs[job_key]["error"] = str(e)
                running_jobs[job_key]["end_time"] = datetime.now(timezone.utc).isoformat()
        
        # Start completion monitoring in a separate thread
        completion_thread = threading.Thread(target=wait_for_completion)
        completion_thread.daemon = True
        completion_thread.start()
        
    except Exception as e:
        running_jobs[job_key]["status"] = "failed"
        running_jobs[job_key]["message"] = f"Nextflow job failed: {str(e)}"
        running_jobs[job_key]["error"] = str(e)
        running_jobs[job_key]["end_time"] = datetime.now(timezone.utc).isoformat()

# Progress monitoring removed - Nextflow container no longer participates in state/stage reporting

# Trace and log parsing functions removed - Nextflow container no longer participates in state/stage reporting

# Progress tracking function removed - Nextflow container no longer participates in state/stage reporting

@app.route('/status/<job_key>', methods=['GET'])
def get_job_status(job_key: str):
    """Get status of a running job."""
    if job_key not in running_jobs:
        return jsonify({"error": "Job not found"}), 404
        
    job = running_jobs[job_key]
    return jsonify({
        "job_id": job["job_id"],
        "patient_id": job["patient_id"],
        "report_id": job["report_id"],
        "status": job["status"],
        "message": job["message"],
        "start_time": job["start_time"],
        "end_time": job.get("end_time"),
        "error": job.get("error")
    })

@app.route('/status', methods=['GET'])
def get_all_jobs():
    """Get status of all jobs."""
    return jsonify({
        "jobs": {
            key: {
                "job_id": job["job_id"],
                "patient_id": job["patient_id"],
                "status": job["status"],
                "message": job["message"]
            }
            for key, job in running_jobs.items()
        }
    })

@app.route('/cleanup', methods=['POST'])
def cleanup_old_jobs():
    """Clean up old completed/failed jobs."""
    current_time = datetime.now(timezone.utc)
    cutoff_hours = 24  # Keep jobs for 24 hours
    
    jobs_to_remove = []
    for key, job in running_jobs.items():
        if job["status"] in ["completed", "failed"]:
            end_time = datetime.fromisoformat(job.get("end_time", job["start_time"]))
            if (current_time - end_time).total_seconds() > cutoff_hours * 3600:
                jobs_to_remove.append(key)
    
    for key in jobs_to_remove:
        del running_jobs[key]
    
    return jsonify({
        "cleaned_up": len(jobs_to_remove),
        "remaining_jobs": len(running_jobs)
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5055)


