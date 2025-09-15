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
        "input_type": input_type,  # Store input type for stage determination
        "skip_hla": skip_hla,  # Store service toggle settings
        "skip_pypgx": skip_pypgx,
        "status": "starting",
        "start_time": datetime.now(timezone.utc).isoformat(),
        "progress": 0,
        "message": "Initializing Nextflow pipeline",
        "processes": {},
        "trace_file": f"{outdir}/nextflow_trace.txt",
        "log_file": f"{outdir}/nextflow.log"
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
        
        # Start monitoring thread
        monitor_thread = threading.Thread(target=monitor_nextflow_progress, args=(job_key, outdir))
        monitor_thread.daemon = True
        monitor_thread.start()
        
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
                    # Nextflow only handles up to 70% (PharmCAT), final stages are handled by app
                    # Don't set progress to 100% here - let the app handle final stages
                    if running_jobs[job_key]["progress"] < 70:
                        running_jobs[job_key]["progress"] = 70
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

def monitor_nextflow_progress(job_key: str, outdir: str):
    """Monitor Nextflow progress by parsing trace and log files."""
    trace_file = f"{outdir}/nextflow_trace.txt"
    log_file = f"{outdir}/nextflow.log"
    
    last_trace_position = 0
    last_log_position = 0
    
    while running_jobs.get(job_key, {}).get("status") in ["running", "starting"]:
        try:
            # Check trace file for process updates
            if os.path.exists(trace_file):
                with open(trace_file, 'r') as f:
                    f.seek(last_trace_position)
                    new_lines = f.readlines()
                    last_trace_position = f.tell()
                    
                if new_lines:
                    print(f"DEBUG: Found {len(new_lines)} new lines in trace file")
                    for line in new_lines:
                        print(f"DEBUG: Trace line: {line.strip()}")
                        parse_trace_line(job_key, line.strip())
                else:
                    print(f"DEBUG: No new lines in trace file")
            else:
                print(f"DEBUG: Trace file does not exist: {trace_file}")
            
            # Check log file for progress updates
            if os.path.exists(log_file):
                with open(log_file, 'r') as f:
                    f.seek(last_log_position)
                    new_lines = f.readlines()
                    last_log_position = f.tell()
                    
                for line in new_lines:
                    parse_log_line(job_key, line.strip())
            
            # Update overall progress
            update_job_progress(job_key)
            
            time.sleep(2)  # Check every 2 seconds
            
        except Exception as e:
            print(f"Error monitoring progress: {e}")
            time.sleep(5)

def parse_trace_line(job_key: str, line: str):
    """Parse a trace file line for process information."""
    if not line or line.startswith('#') or job_key not in running_jobs:
        return
        
    try:
        fields = line.split('\t')
        if len(fields) < 10:
            return
            
        task_id = fields[0].strip()
        process_name = fields[3].strip()  # process column
        status = fields[6].strip()
        submit_time = fields[11].strip() if len(fields) > 11 else None  # submit column
        start_time = fields[12].strip() if len(fields) > 12 else None   # start column
        complete_time = fields[13].strip() if len(fields) > 13 else None  # complete column
        duration = fields[14].strip() if len(fields) > 14 else None     # duration column
        exit_code = fields[7].strip() if len(fields) > 7 else None      # exit column
        
        # Skip header lines and invalid data
        if (task_id in ['task_id', 'hash', 'native_id', ''] or 
            process_name in ['process', 'tag', 'name', ''] or
            status in ['status', 'exit', 'module', '']):
            return
            
        # Skip lines with invalid task IDs (should be numeric or alphanumeric)
        if not task_id or len(task_id) < 1:
            return
        
        # Update process info
        if "processes" not in running_jobs[job_key]:
            running_jobs[job_key]["processes"] = {}
            
        running_jobs[job_key]["processes"][task_id] = {
            "process_name": process_name,
            "status": status,
            "submit_time": submit_time,
            "start_time": start_time,
            "complete_time": complete_time,
            "duration": duration,
            "exit_code": exit_code
        }
        
        print(f"Parsed process: {task_id} - {process_name} - {status}")
        
    except Exception as e:
        print(f"Error parsing trace line: {e}")

def parse_log_line(job_key: str, line: str):
    """Parse a log file line for progress information."""
    if not line or job_key not in running_jobs:
        return
        
    # Look for specific log patterns - but don't override detailed progress messages
    # Let update_job_progress() handle all messaging based on actual process status
    if "Workflow completed" in line:
        # Only set message for workflow completion, not individual process completions
        running_jobs[job_key]["message"] = "Workflow completed successfully"

def update_job_progress(job_key: str):
    """Update overall job progress based on process status and workflow stages."""
    if job_key not in running_jobs:
        return
        
    job = running_jobs[job_key]
    processes = job.get("processes", {})
    
    if not processes:
        return
    
    # Map processes to workflow stages with progress percentages
    # Note: Nextflow only handles up to 70% (PharmCAT), final stages are handled by app
    stage_progress = {
        "gatk_conversion": 20,
        "hla_typing": 30,
        "pypgx_analysis": 50,
        "pypgx_bam2vcf": 60,
        "pharmcat_analysis": 70
    }
    
    # Adjust progress based on disabled services
    # If HLA is disabled, PyPGx starts earlier
    # If PyPGx is disabled, PharmCAT starts earlier
    if job.get("skip_hla", "false").lower() == "true":
        stage_progress["pypgx_analysis"] = 30  # Move PyPGx earlier
        stage_progress["pypgx_bam2vcf"] = 40
        stage_progress["pharmcat_analysis"] = 50
    elif job.get("skip_pypgx", "false").lower() == "true":
        stage_progress["pharmcat_analysis"] = 40  # Move PharmCAT earlier
    
    # Initialize with starting state based on input type
    max_progress = 10  # Starting progress
    input_type = job.get("input_type", "vcf")  # Default to vcf if not available
    
    # Determine the next logical stage based on input type and disabled services
    skip_hla = job.get("skip_hla", "false").lower() == "true"
    skip_pypgx = job.get("skip_pypgx", "false").lower() == "true"
    
    if input_type == "vcf":
        # VCF goes directly to PyPGx analysis (no HLA, no conversion)
        if skip_pypgx:
            current_stage = "pharmcat_analysis"
            current_message = "Starting PharmCAT analysis..."
        else:
            current_stage = "pypgx_analysis"
            current_message = "Starting PyPGx analysis..."
    elif input_type in ["cram", "sam"]:
        # These need conversion first, then HLA, then PyPGx
        current_stage = "gatk_conversion"
        current_message = "Starting file conversion..."
    elif input_type in ["bam", "fastq"]:
        # FASTQ or BAM goes to HLA first (if enabled)
        if skip_hla:
            if skip_pypgx:
                current_stage = "pharmcat_analysis"
                current_message = "Starting PharmCAT analysis..."
            else:
                current_stage = "pypgx_analysis"
                current_message = "Starting PyPGx analysis..."
        else:
            current_stage = "hla_typing"
            current_message = "Starting HLA typing..."
    else:
        # Fallback
        current_stage = "upload_complete"
        current_message = "Starting analysis ..."
    
    # Debug logging
    print(f"DEBUG: Processing {len(processes)} processes")
    for task_id, process in processes.items():
        process_name = process.get("process_name", "").lower()
        status = process.get("status", "")
        print(f"DEBUG: Process {task_id}: {process_name} - {status}")
        
        # Additional debugging for PyPGx and PharmCAT processes
        if "pypgx" in process_name:
            print(f"DEBUG: Found PyPGx process: {process_name} with status: {status}")
        if "pharmcat" in process_name:
            print(f"DEBUG: Found PharmCAT process: {process_name} with status: {status}")
    
    # Track process stages and their completion status
    process_stages = {}
    for process in processes.values():
        process_name = process.get("process_name", "").lower()
        status = process.get("status", "")
        
        # Map process to stage based on process name
        # Match actual Nextflow process names from main.nf
        if "fastqtobam" in process_name or "cramtobam" in process_name or "samtobam" in process_name:
            stage = "gatk_conversion"
        elif "optitype" in process_name or "hlafrom" in process_name:
            # Skip HLA processes if disabled
            if skip_hla:
                print(f"DEBUG: Skipping HLA process (disabled): {process_name}")
                continue
            stage = "hla_typing"
        elif ("pypgxgenotypeall" in process_name or "pypgxgenotype" in process_name or 
              "pypgx" in process_name and "genotype" in process_name):
            # Skip PyPGx processes if disabled
            if skip_pypgx:
                print(f"DEBUG: Skipping PyPGx process (disabled): {process_name}")
                continue
            # This is the actual PyPGx analysis process
            stage = "pypgx_analysis"
        elif ("pypgxbam2vcf" in process_name or 
              ("bam2vcf" in process_name and "pypgx" in process_name) or
              ("pypgx" in process_name and "bam2vcf" in process_name)):
            # Skip PyPGx processes if disabled
            if skip_pypgx:
                print(f"DEBUG: Skipping PyPGx BAM2VCF process (disabled): {process_name}")
                continue
            # This is the PyPGx BAM to VCF conversion process
            stage = "pypgx_bam2vcf"
        elif ("pharmcatrun" in process_name or "pharmcat" in process_name or
              "pharmcat" in process_name and "run" in process_name):
            stage = "pharmcat_analysis"
        elif "createemptyfile" in process_name:
            # Skip empty file creation - it's not a meaningful workflow stage
            print(f"DEBUG: Skipping empty file creation process: {process_name}")
            continue
        else:
            print(f"DEBUG: No stage mapping for process: {process_name}")
            # For debugging, show what we're looking for
            if "pypgx" in process_name:
                print(f"DEBUG: Process contains 'pypgx' but didn't match - name: '{process_name}'")
            if "pharmcat" in process_name:
                print(f"DEBUG: Process contains 'pharmcat' but didn't match - name: '{process_name}'")
            continue
        
        print(f"DEBUG: Mapped {process_name} ({status}) to stage {stage} ({stage_progress[stage]}%)")
        
        # Track this stage and its status
        if stage not in process_stages:
            process_stages[stage] = {"running": False, "completed": False, "failed": False}
        
        if status == "RUNNING":
            process_stages[stage]["running"] = True
        elif status == "COMPLETED":
            process_stages[stage]["completed"] = True
        elif status == "FAILED":
            process_stages[stage]["failed"] = True
    
    # Determine current stage and progress based on sequential workflow logic
    # For VCF input: PyPGx (50%) → PharmCAT (70%) - SEQUENTIAL, not parallel
    if input_type == "vcf":
        if not skip_pypgx:
            # Check PyPGx status first (it runs first in the sequence)
            if "pypgx_analysis" in process_stages:
                if process_stages["pypgx_analysis"]["running"]:
                    current_stage = "pypgx_analysis"
                    current_message = "Running PyPGx analysis..."
                    max_progress = 50
                    print(f"DEBUG: VCF workflow - PyPGx is running (50%)")
                elif process_stages["pypgx_analysis"]["completed"]:
                    # PyPGx completed, now check PharmCAT (it runs after PyPGx)
                    if "pharmcat_analysis" in process_stages:
                        if process_stages["pharmcat_analysis"]["running"]:
                            current_stage = "pharmcat_analysis"
                            current_message = "PyPGx completed, running PharmCAT..."
                            max_progress = 70
                            print(f"DEBUG: VCF workflow - PyPGx completed, PharmCAT running (70%)")
                        elif process_stages["pharmcat_analysis"]["completed"]:
                            current_stage = "pharmcat_analysis"
                            current_message = "PharmCAT completed"
                            max_progress = 70
                            print(f"DEBUG: VCF workflow - PharmCAT completed (70%)")
                        else:
                            # PharmCAT not started yet, but PyPGx is done
                            current_stage = "pypgx_analysis"
                            current_message = "PyPGx completed, starting PharmCAT..."
                            max_progress = 50
                            print(f"DEBUG: VCF workflow - PyPGx completed, waiting for PharmCAT")
                    else:
                        # PharmCAT process not created yet, but PyPGx is done
                        current_stage = "pypgx_analysis"
                        current_message = "PyPGx completed, starting PharmCAT..."
                        max_progress = 50
                        print(f"DEBUG: VCF workflow - PyPGx completed, waiting for PharmCAT")
                elif process_stages["pypgx_analysis"]["failed"]:
                    # PyPGx failed, skip to PharmCAT
                    current_stage = "pypgx_analysis"
                    current_message = "PyPGx failed, skipping to PharmCAT..."
                    max_progress = 50
                    print(f"DEBUG: VCF workflow - PyPGx failed, skipping to PharmCAT")
            else:
                # PyPGx not started yet
                current_stage = "pypgx_analysis"
                current_message = "Starting PyPGx analysis..."
                max_progress = 10
                print(f"DEBUG: VCF workflow - PyPGx not started yet (10%)")
        else:
            # PyPGx disabled, go directly to PharmCAT
            if "pharmcat_analysis" in process_stages:
                if process_stages["pharmcat_analysis"]["running"]:
                    current_stage = "pharmcat_analysis"
                    current_message = "Running PharmCAT analysis..."
                    max_progress = 70
                    print(f"DEBUG: VCF workflow - PharmCAT running (PyPGx disabled)")
                elif process_stages["pharmcat_analysis"]["completed"]:
                    current_stage = "pharmcat_analysis"
                    current_message = "PharmCAT completed"
                    max_progress = 70
                    print(f"DEBUG: VCF workflow - PharmCAT completed (PyPGx disabled)")
            else:
                current_stage = "pharmcat_analysis"
                current_message = "Starting PharmCAT analysis..."
                max_progress = 10
                print(f"DEBUG: VCF workflow - PharmCAT not started yet (PyPGx disabled)")
    
    elif input_type in ["fastq", "bam", "cram", "sam"]:
        # Complex workflows: HLA → GATK → PyPGx → PharmCAT (varies by input type)
        # These are also SEQUENTIAL - only one stage runs at a time
        
        # Determine the current stage based on what's running/completed in sequence
        if not skip_hla and "hla_typing" in process_stages:
            if process_stages["hla_typing"]["running"]:
                current_stage = "hla_typing"
                current_message = "Running HLA typing..."
                max_progress = 30
                print(f"DEBUG: {input_type.upper()} workflow - HLA running (30%)")
            elif process_stages["hla_typing"]["completed"]:
                # HLA completed, check next stage in sequence
                if "gatk_conversion" in process_stages and process_stages["gatk_conversion"]["running"]:
                    current_stage = "gatk_conversion"
                    current_message = "HLA completed, running file conversion..."
                    max_progress = 20
                    print(f"DEBUG: {input_type.upper()} workflow - HLA completed, GATK running (20%)")
                elif not skip_pypgx and "pypgx_analysis" in process_stages and process_stages["pypgx_analysis"]["running"]:
                    current_stage = "pypgx_analysis"
                    current_message = "HLA completed, running PyPGx analysis..."
                    max_progress = 50
                    print(f"DEBUG: {input_type.upper()} workflow - HLA completed, PyPGx running (50%)")
                elif "pharmcat_analysis" in process_stages and process_stages["pharmcat_analysis"]["running"]:
                    current_stage = "pharmcat_analysis"
                    current_message = "HLA completed, running PharmCAT analysis..."
                    max_progress = 70
                    print(f"DEBUG: {input_type.upper()} workflow - HLA completed, PharmCAT running (70%)")
                else:
                    # HLA completed, waiting for next stage
                    current_stage = "hla_typing"
                    current_message = "HLA completed, starting next stage..."
                    max_progress = 30
                    print(f"DEBUG: {input_type.upper()} workflow - HLA completed, waiting for next stage")
        elif "gatk_conversion" in process_stages:
            if process_stages["gatk_conversion"]["running"]:
                current_stage = "gatk_conversion"
                current_message = "Running file conversion..."
                max_progress = 20
                print(f"DEBUG: {input_type.upper()} workflow - GATK conversion running (20%)")
            elif process_stages["gatk_conversion"]["completed"]:
                # GATK completed, check next stage
                if not skip_pypgx and "pypgx_analysis" in process_stages and process_stages["pypgx_analysis"]["running"]:
                    current_stage = "pypgx_analysis"
                    current_message = "GATK completed, running PyPGx analysis..."
                    max_progress = 50
                    print(f"DEBUG: {input_type.upper()} workflow - GATK completed, PyPGx running (50%)")
                elif "pharmcat_analysis" in process_stages and process_stages["pharmcat_analysis"]["running"]:
                    current_stage = "pharmcat_analysis"
                    current_message = "GATK completed, running PharmCAT analysis..."
                    max_progress = 70
                    print(f"DEBUG: {input_type.upper()} workflow - GATK completed, PharmCAT running (70%)")
                else:
                    current_stage = "gatk_conversion"
                    current_message = "GATK completed, starting next stage..."
                    max_progress = 20
                    print(f"DEBUG: {input_type.upper()} workflow - GATK completed, waiting for next stage")
        elif not skip_pypgx and "pypgx_analysis" in process_stages:
            if process_stages["pypgx_analysis"]["running"]:
                current_stage = "pypgx_analysis"
                current_message = "Running PyPGx analysis..."
                max_progress = 50
                print(f"DEBUG: {input_type.upper()} workflow - PyPGx running (50%)")
            elif process_stages["pypgx_analysis"]["completed"]:
                # PyPGx completed, check PharmCAT
                if "pharmcat_analysis" in process_stages and process_stages["pharmcat_analysis"]["running"]:
                    current_stage = "pharmcat_analysis"
                    current_message = "PyPGx completed, running PharmCAT analysis..."
                    max_progress = 70
                    print(f"DEBUG: {input_type.upper()} workflow - PyPGx completed, PharmCAT running (70%)")
                else:
                    current_stage = "pypgx_analysis"
                    current_message = "PyPGx completed, starting PharmCAT..."
                    max_progress = 50
                    print(f"DEBUG: {input_type.upper()} workflow - PyPGx completed, waiting for PharmCAT")
        elif "pharmcat_analysis" in process_stages:
            if process_stages["pharmcat_analysis"]["running"]:
                current_stage = "pharmcat_analysis"
                current_message = "Running PharmCAT analysis..."
                max_progress = 70
                print(f"DEBUG: {input_type.upper()} workflow - PharmCAT running (70%)")
            elif process_stages["pharmcat_analysis"]["completed"]:
                current_stage = "pharmcat_analysis"
                current_message = "PharmCAT completed"
                max_progress = 70
                print(f"DEBUG: {input_type.upper()} workflow - PharmCAT completed (70%)")
        else:
            # No meaningful processes started yet, determine what should be first
            if input_type == "fastq" and not skip_hla:
                current_stage = "hla_typing"
                current_message = "Starting HLA typing..."
                max_progress = 10
            elif input_type in ["cram", "sam"]:
                current_stage = "gatk_conversion"
                current_message = "Starting file conversion..."
                max_progress = 10
            elif input_type == "bam" and not skip_hla:
                current_stage = "hla_typing"
                current_message = "Starting HLA typing..."
                max_progress = 10
            else:
                current_stage = "pypgx_analysis"
                current_message = "Starting PyPGx analysis..."
                max_progress = 10
    
    # Check if all processes are completed
    total_processes = len(processes)
    completed_processes = sum(1 for p in processes.values() if p.get("status") == "COMPLETED")
    running_processes = sum(1 for p in processes.values() if p.get("status") == "RUNNING")
    failed_processes = sum(1 for p in processes.values() if p.get("status") == "FAILED")
    
    # If no meaningful processes have been processed yet, maintain initial state
    # This handles the case where only CreateEmptyFile has run but no actual workflow processes
    if max_progress == 10 and completed_processes == total_processes and total_processes > 0:
        # Only CreateEmptyFile completed, but no actual workflow processes have run yet
        # This can happen when services are disabled or for simple workflows
        # Keep the initial state until actual processes start
        max_progress = 10  # Stay at initial progress until processes actually run
        current_message = "Starting analysis..."
    
    # Handle case where all services are disabled - jump to PharmCAT
    if skip_hla and skip_pypgx and max_progress == 10:
        max_progress = 40  # Jump to PharmCAT stage
        current_stage = "pharmcat_analysis"
        current_message = "Starting PharmCAT analysis (services disabled)..."
    
    # Debug final values
    print(f"DEBUG: Final calculated values - stage: {current_stage}, progress: {max_progress}, message: {current_message}")
    print(f"DEBUG: Total processes: {total_processes}, completed: {completed_processes}, running: {running_processes}, failed: {failed_processes}")
    print(f"DEBUG: Process stages: {process_stages}")
    
    # Additional debugging for VCF input
    if input_type == "vcf":
        print(f"DEBUG: VCF input - PyPGx should run first (50%), then PharmCAT (70%)")
        print(f"DEBUG: Current stage mapping: {current_stage} = {max_progress}%")
    
    # Update job with calculated values
    job["progress"] = max_progress
    job["stage"] = current_stage
    job["message"] = current_message

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
        "stage": job.get("stage", "upload_complete"), # Use upload_complete as default, not pypgx_analysis
        "progress": job["progress"],
        "message": job["message"],
        "start_time": job["start_time"],
        "end_time": job.get("end_time"),
        "processes": job.get("processes", {}),
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
                "progress": job["progress"],
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


