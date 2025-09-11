"""
Nextflow Job Monitoring Service

This service provides real-time monitoring of Nextflow pipeline execution by:
1. Parsing Nextflow trace files for process status updates
2. Monitoring Nextflow log files for progress information
3. Bridging Nextflow progress to the existing JobStatusService
4. Providing real-time progress updates via WebSocket/SSE
"""

import os
import json
import time
import asyncio
import logging
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime, timezone
from pathlib import Path
import uuid
import threading
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class NextflowProcessStatus(Enum):
    """Nextflow process status values"""
    SUBMITTED = "SUBMITTED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    RETRY = "RETRY"


@dataclass
class NextflowProcessInfo:
    """Information about a Nextflow process"""
    task_id: str
    process_name: str
    status: NextflowProcessStatus
    submit_time: Optional[datetime] = None
    start_time: Optional[datetime] = None
    complete_time: Optional[datetime] = None
    duration: Optional[float] = None
    exit_code: Optional[int] = None
    memory_usage: Optional[float] = None
    cpu_usage: Optional[float] = None
    tag: Optional[str] = None
    attempt: int = 1


@dataclass
class NextflowProgressUpdate:
    """Progress update from Nextflow monitoring"""
    job_id: str
    timestamp: datetime
    total_processes: int
    completed_processes: int
    running_processes: int
    failed_processes: int
    current_process: Optional[str] = None
    progress_percentage: float = 0.0
    message: str = ""
    processes: List[NextflowProcessInfo] = None


class NextflowMonitor:
    """
    Real-time Nextflow pipeline monitoring service.
    
    This service monitors Nextflow execution by:
    1. Watching trace files for process status updates
    2. Parsing log files for progress information
    3. Providing callbacks for progress updates
    4. Managing monitoring sessions
    """
    
    def __init__(self, trace_file_path: str, log_file_path: str, 
                 progress_callback: Optional[Callable[[NextflowProgressUpdate], None]] = None):
        """
        Initialize Nextflow monitor.
        
        Args:
            trace_file_path: Path to Nextflow trace file
            log_file_path: Path to Nextflow log file
            progress_callback: Callback function for progress updates
        """
        self.trace_file_path = Path(trace_file_path)
        self.log_file_path = Path(log_file_path)
        self.progress_callback = progress_callback
        self.monitoring = False
        self.monitor_thread = None
        self.processes: Dict[str, NextflowProcessInfo] = {}
        self.last_trace_position = 0
        self.last_log_position = 0
        
    def start_monitoring(self, job_id: str) -> None:
        """
        Start monitoring Nextflow execution.
        
        Args:
            job_id: Job ID to associate with this monitoring session
        """
        if self.monitoring:
            logger.warning("Monitoring already started")
            return
            
        self.job_id = job_id
        self.monitoring = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        logger.info(f"Started Nextflow monitoring for job {job_id}")
    
    def stop_monitoring(self) -> None:
        """Stop monitoring Nextflow execution."""
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        logger.info("Stopped Nextflow monitoring")
    
    def _monitor_loop(self) -> None:
        """Main monitoring loop that watches trace and log files."""
        while self.monitoring:
            try:
                self._check_trace_file()
                self._check_log_file()
                time.sleep(2)  # Check every 2 seconds
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                time.sleep(5)  # Wait longer on error
    
    def _check_trace_file(self) -> None:
        """Check trace file for new process updates."""
        if not self.trace_file_path.exists():
            return
            
        try:
            with open(self.trace_file_path, 'r') as f:
                f.seek(self.last_trace_position)
                new_lines = f.readlines()
                self.last_trace_position = f.tell()
                
            for line in new_lines:
                self._parse_trace_line(line.strip())
                
        except Exception as e:
            logger.error(f"Error reading trace file: {e}")
    
    def _check_log_file(self) -> None:
        """Check log file for progress information."""
        if not self.log_file_path.exists():
            return
            
        try:
            with open(self.log_file_path, 'r') as f:
                f.seek(self.last_log_position)
                new_lines = f.readlines()
                self.last_log_position = f.tell()
                
            for line in new_lines:
                self._parse_log_line(line.strip())
                
        except Exception as e:
            logger.error(f"Error reading log file: {e}")
    
    def _parse_trace_line(self, line: str) -> None:
        """Parse a trace file line for process information."""
        if not line or line.startswith('#'):
            return
            
        try:
            # Parse tab-separated trace file
            fields = line.split('\t')
            if len(fields) < 10:
                return
                
            task_id = fields[0]
            process_name = fields[2]
            status_str = fields[6]
            submit_time = self._parse_timestamp(fields[8]) if len(fields) > 8 else None
            start_time = self._parse_timestamp(fields[9]) if len(fields) > 9 else None
            complete_time = self._parse_timestamp(fields[10]) if len(fields) > 10 else None
            duration = float(fields[11]) if len(fields) > 11 and fields[11] != '-' else None
            exit_code = int(fields[12]) if len(fields) > 12 and fields[12] != '-' else None
            memory_usage = float(fields[15]) if len(fields) > 15 and fields[15] != '-' else None
            cpu_usage = float(fields[16]) if len(fields) > 16 and fields[16] != '-' else None
            tag = fields[4] if len(fields) > 4 else None
            attempt = int(fields[5]) if len(fields) > 5 else 1
            
            # Convert status string to enum
            try:
                status = NextflowProcessStatus(status_str)
            except ValueError:
                logger.warning(f"Unknown process status: {status_str}")
                return
            
            # Create or update process info
            process_info = NextflowProcessInfo(
                task_id=task_id,
                process_name=process_name,
                status=status,
                submit_time=submit_time,
                start_time=start_time,
                complete_time=complete_time,
                duration=duration,
                exit_code=exit_code,
                memory_usage=memory_usage,
                cpu_usage=cpu_usage,
                tag=tag,
                attempt=attempt
            )
            
            self.processes[task_id] = process_info
            
            # Send progress update
            self._send_progress_update()
            
        except Exception as e:
            logger.error(f"Error parsing trace line: {e}")
    
    def _parse_log_line(self, line: str) -> None:
        """Parse a log file line for progress information."""
        if not line:
            return
            
        # Look for specific log patterns that indicate progress
        if "Process" in line and "started" in line:
            logger.debug(f"Process started: {line}")
        elif "Process" in line and "completed" in line:
            logger.debug(f"Process completed: {line}")
        elif "Process" in line and "failed" in line:
            logger.warning(f"Process failed: {line}")
        elif "Workflow completed" in line:
            logger.info("Nextflow workflow completed")
            self._send_progress_update()
    
    def _parse_timestamp(self, timestamp_str: str) -> Optional[datetime]:
        """Parse timestamp from trace file."""
        if not timestamp_str or timestamp_str == '-':
            return None
            
        try:
            # Nextflow timestamps are in format: 2024-01-01 12:00:00.000
            return datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S.%f')
        except ValueError:
            try:
                return datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                logger.warning(f"Could not parse timestamp: {timestamp_str}")
                return None
    
    def _send_progress_update(self) -> None:
        """Send progress update via callback."""
        if not self.progress_callback:
            return
            
        try:
            # Calculate progress statistics
            total_processes = len(self.processes)
            completed_processes = sum(1 for p in self.processes.values() 
                                    if p.status == NextflowProcessStatus.COMPLETED)
            running_processes = sum(1 for p in self.processes.values() 
                                  if p.status == NextflowProcessStatus.RUNNING)
            failed_processes = sum(1 for p in self.processes.values() 
                                 if p.status == NextflowProcessStatus.FAILED)
            
            # Calculate progress percentage
            if total_processes > 0:
                progress_percentage = (completed_processes / total_processes) * 100
            else:
                progress_percentage = 0.0
            
            # Find current running process
            current_process = None
            for process in self.processes.values():
                if process.status == NextflowProcessStatus.RUNNING:
                    current_process = process.process_name
                    break
            
            # Create progress message
            if current_process:
                message = f"Running {current_process} ({running_processes} processes running)"
            elif completed_processes > 0:
                message = f"Completed {completed_processes}/{total_processes} processes"
            else:
                message = "Pipeline starting..."
            
            # Create progress update
            progress_update = NextflowProgressUpdate(
                job_id=self.job_id,
                timestamp=datetime.now(timezone.utc),
                total_processes=total_processes,
                completed_processes=completed_processes,
                running_processes=running_processes,
                failed_processes=failed_processes,
                current_process=current_process,
                progress_percentage=progress_percentage,
                message=message,
                processes=list(self.processes.values())
            )
            
            # Send update
            self.progress_callback(progress_update)
            
        except Exception as e:
            logger.error(f"Error sending progress update: {e}")
    
    def get_current_progress(self) -> Optional[NextflowProgressUpdate]:
        """Get current progress without triggering callback."""
        if not self.processes:
            return None
            
        # Calculate current progress
        total_processes = len(self.processes)
        completed_processes = sum(1 for p in self.processes.values() 
                                if p.status == NextflowProcessStatus.COMPLETED)
        running_processes = sum(1 for p in self.processes.values() 
                              if p.status == NextflowProcessStatus.RUNNING)
        failed_processes = sum(1 for p in self.processes.values() 
                             if p.status == NextflowProcessStatus.FAILED)
        
        progress_percentage = (completed_processes / total_processes) * 100 if total_processes > 0 else 0.0
        
        current_process = None
        for process in self.processes.values():
            if process.status == NextflowProcessStatus.RUNNING:
                current_process = process.process_name
                break
        
        message = f"Completed {completed_processes}/{total_processes} processes"
        if current_process:
            message = f"Running {current_process} ({running_processes} processes running)"
        
        return NextflowProgressUpdate(
            job_id=self.job_id,
            timestamp=datetime.now(timezone.utc),
            total_processes=total_processes,
            completed_processes=completed_processes,
            running_processes=running_processes,
            failed_processes=failed_processes,
            current_process=current_process,
            progress_percentage=progress_percentage,
            message=message,
            processes=list(self.processes.values())
        )


class NextflowProgressBridge:
    """
    Bridge between Nextflow monitoring and JobStatusService.
    
    This class connects Nextflow progress updates to the existing job monitoring system.
    """
    
    def __init__(self, job_status_service, job_id: str):
        """
        Initialize progress bridge.
        
        Args:
            job_status_service: Instance of JobStatusService
            job_id: Job ID to update
        """
        self.job_status_service = job_status_service
        self.job_id = job_id
        self.monitor = None
        
    def start_monitoring(self, trace_file_path: str, log_file_path: str) -> None:
        """
        Start monitoring Nextflow execution.
        
        Args:
            trace_file_path: Path to Nextflow trace file
            log_file_path: Path to Nextflow log file
        """
        self.monitor = NextflowMonitor(
            trace_file_path=trace_file_path,
            log_file_path=log_file_path,
            progress_callback=self._on_progress_update
        )
        self.monitor.start_monitoring(self.job_id)
        logger.info(f"Started Nextflow progress bridge for job {self.job_id}")
    
    def stop_monitoring(self) -> None:
        """Stop monitoring Nextflow execution."""
        if self.monitor:
            self.monitor.stop_monitoring()
            self.monitor = None
        logger.info(f"Stopped Nextflow progress bridge for job {self.job_id}")
    
    def _on_progress_update(self, progress_update: NextflowProgressUpdate) -> None:
        """
        Handle progress update from Nextflow monitor.
        
        Args:
            progress_update: Progress update from Nextflow
        """
        try:
            # Map Nextflow progress to job stages
            stage = self._map_progress_to_stage(progress_update)
            
            # Update job progress
            self.job_status_service.update_job_progress(
                job_id=self.job_id,
                stage=stage,
                progress=int(progress_update.progress_percentage),
                message=progress_update.message,
                metadata={
                    "nextflow_progress": {
                        "total_processes": progress_update.total_processes,
                        "completed_processes": progress_update.completed_processes,
                        "running_processes": progress_update.running_processes,
                        "failed_processes": progress_update.failed_processes,
                        "current_process": progress_update.current_process,
                        "timestamp": progress_update.timestamp.isoformat()
                    }
                }
            )
            
            logger.debug(f"Updated job {self.job_id} progress: {progress_update.progress_percentage:.1f}% - {progress_update.message}")
            
        except Exception as e:
            logger.error(f"Error handling progress update: {e}")
    
    def _map_progress_to_stage(self, progress_update: NextflowProgressUpdate) -> str:
        """
        Map Nextflow progress to job stage.
        
        Args:
            progress_update: Progress update from Nextflow
            
        Returns:
            Job stage string
        """
        # Map based on current process or progress
        if progress_update.current_process:
            process_name = progress_update.current_process.lower()
            
            if "fastq" in process_name or "align" in process_name:
                return "GATK"  # Alignment stage
            elif "hla" in process_name or "optitype" in process_name:
                return "HLA"   # HLA typing stage
            elif "pypgx" in process_name:
                return "PYPGX" # PyPGx genotyping stage
            elif "pharmcat" in process_name:
                return "PHARMCAT" # PharmCAT analysis stage
            else:
                return "ANALYSIS" # Generic analysis stage
        else:
            # Fallback based on progress percentage
            if progress_update.progress_percentage < 25:
                return "GATK"
            elif progress_update.progress_percentage < 50:
                return "HLA"
            elif progress_update.progress_percentage < 75:
                return "PYPGX"
            elif progress_update.progress_percentage < 100:
                return "PHARMCAT"
            else:
                return "ANALYSIS"
    
    def get_current_progress(self) -> Optional[NextflowProgressUpdate]:
        """Get current progress from monitor."""
        if self.monitor:
            return self.monitor.get_current_progress()
        return None
