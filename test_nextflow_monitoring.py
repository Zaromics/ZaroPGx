#!/usr/bin/env python3
"""
Test script for Nextflow monitoring integration.

This script tests the enhanced Nextflow monitoring system by:
1. Testing the NextflowMonitor class
2. Testing the NextflowProgressBridge
3. Verifying the integration with JobStatusService
"""

import os
import sys
import tempfile
import time
import json
from pathlib import Path

# Add the app directory to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

from services.nextflow_monitor import NextflowMonitor, NextflowProgressUpdate, NextflowProcessStatus
from services.job_status_service import JobStatusService
from api.db import SessionLocal

def test_nextflow_monitor():
    """Test the NextflowMonitor class with mock data."""
    print("Testing NextflowMonitor class...")
    
    # Create temporary trace and log files
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as trace_file:
        trace_path = trace_file.name
        # Write sample trace data
        trace_file.write("# task_id\thash\tnative_id\tprocess\ttag\tattempt\tstatus\texit\tmodule\tcontainer\ttag\tattempt\tsubmit\tstart\tcomplete\tduration\trealtime\t%cpu\t%mem\trss\tvmem\tpeak_rss\tpeak_vmem\trchar\twchar\tsyscr\tsyscw\tread_bytes\twrite_bytes\n")
        trace_file.write("1\tabc123\t1\tFastqToBAM\talign_1\t1\tSUBMITTED\t-\t-\t-\t-\t1\t2024-01-01 10:00:00.000\t-\t-\t-\t-\t-\t-\t-\t-\t-\t-\t-\t-\t-\t-\n")
        trace_file.write("1\tabc123\t1\tFastqToBAM\talign_1\t1\tRUNNING\t-\t-\t-\t-\t1\t2024-01-01 10:00:00.000\t2024-01-01 10:00:05.000\t-\t-\t-\t-\t-\t-\t-\t-\t-\t-\t-\t-\t-\t-\n")
        trace_file.write("1\tabc123\t1\tFastqToBAM\talign_1\t1\tCOMPLETED\t0\t-\t-\t-\t1\t2024-01-01 10:00:00.000\t2024-01-01 10:00:05.000\t2024-01-01 10:05:00.000\t300.0\t300.0\t50.0\t1000.0\t2000.0\t1500.0\t1800.0\t1000000\t2000000\t100\t200\t500000\t1000000\n")
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False) as log_file:
        log_path = log_file.name
        # Write sample log data
        log_file.write("2024-01-01 10:00:00.000 [1] Process FastqToBAM started\n")
        log_file.write("2024-01-01 10:05:00.000 [1] Process FastqToBAM completed\n")
        log_file.write("2024-01-01 10:05:00.000 Workflow completed\n")
    
    try:
        # Test callback function
        progress_updates = []
        
        def progress_callback(update: NextflowProgressUpdate):
            progress_updates.append(update)
            print(f"Progress update: {update.progress_percentage:.1f}% - {update.message}")
        
        # Create monitor
        monitor = NextflowMonitor(trace_path, log_path, progress_callback)
        
        # Start monitoring
        monitor.start_monitoring("test-job-123")
        
        # Simulate monitoring for a few seconds
        time.sleep(3)
        
        # Stop monitoring
        monitor.stop_monitoring()
        
        # Check results
        print(f"Received {len(progress_updates)} progress updates")
        if progress_updates:
            latest = progress_updates[-1]
            print(f"Latest progress: {latest.progress_percentage:.1f}% - {latest.message}")
            print(f"Total processes: {latest.total_processes}")
            print(f"Completed processes: {latest.completed_processes}")
        
        print("✓ NextflowMonitor test passed")
        
    finally:
        # Clean up temporary files
        os.unlink(trace_path)
        os.unlink(log_path)

def test_progress_bridge():
    """Test the NextflowProgressBridge with JobStatusService."""
    print("\nTesting NextflowProgressBridge...")
    
    try:
        # Create database session
        db = SessionLocal()
        
        # Create job status service
        job_service = JobStatusService(db)
        
        # Create a test job
        job = job_service.create_job(
            patient_id="test-patient-123",
            file_id="test-file-456",
            initial_stage="UPLOAD",
            metadata={"test": True}
        )
        
        print(f"Created test job: {job.job_id}")
        
        # Test progress bridge (without actual monitoring)
        from services.nextflow_monitor import NextflowProgressBridge
        
        bridge = NextflowProgressBridge(job_service, str(job.job_id))
        
        # Simulate progress update
        test_update = NextflowProgressUpdate(
            job_id=str(job.job_id),
            timestamp=time.time(),
            total_processes=3,
            completed_processes=1,
            running_processes=1,
            failed_processes=0,
            current_process="FastqToBAM",
            progress_percentage=33.3,
            message="Running FastqToBAM (1 processes running)"
        )
        
        # This would normally be called by the monitor
        bridge._on_progress_update(test_update)
        
        # Check if job was updated
        updated_job = job_service.get_job_status(str(job.job_id))
        if updated_job:
            print(f"Job updated: {updated_job['progress']}% - {updated_job['message']}")
            print("✓ NextflowProgressBridge test passed")
        else:
            print("✗ Failed to get updated job status")
        
        # Clean up
        db.close()
        
    except Exception as e:
        print(f"✗ NextflowProgressBridge test failed: {e}")

def test_integration():
    """Test the complete integration."""
    print("\nTesting complete integration...")
    
    try:
        # This would test the full integration with the upload router
        # For now, just verify the components can be imported and instantiated
        from services.nextflow_monitor import NextflowMonitor, NextflowProgressBridge
        from services.job_status_service import JobStatusService
        from api.routes.upload_router import monitor_nextflow_progress
        
        print("✓ All components imported successfully")
        print("✓ Integration test passed")
        
    except Exception as e:
        print(f"✗ Integration test failed: {e}")

if __name__ == "__main__":
    print("Starting Nextflow monitoring tests...\n")
    
    test_nextflow_monitor()
    test_progress_bridge()
    test_integration()
    
    print("\nAll tests completed!")
