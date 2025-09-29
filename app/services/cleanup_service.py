"""
Centralized cleanup service for ZaroPGx workflows.

This service provides centralized cleanup functionality for temporary files
and directories created during workflow execution.
"""

import os
import shutil
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class CleanupService:
    """
    Centralized service for cleaning up temporary files and directories.
    
    This service provides methods to:
    - Clean up workflow-specific temporary files
    - Clean up old temporary files based on age
    - Clean up specific file paths
    - Provide cleanup statistics and reporting
    """
    
    def __init__(self):
        """Initialize the cleanup service."""
        self.data_dir = Path("/data")
        self.temp_dir = Path("/tmp")
        self.reports_dir = Path("/data/reports")
        self.uploads_dir = Path("/data/uploads")
        
        # Ensure directories exist
        for directory in [self.data_dir, self.temp_dir, self.reports_dir, self.uploads_dir]:
            directory.mkdir(parents=True, exist_ok=True)
    
    def cleanup_workflow_files(self, workflow_id: str, patient_id: Optional[str] = None, 
                             additional_paths: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        Clean up temporary files for a specific workflow.
        
        Args:
            workflow_id: The workflow ID to clean up
            patient_id: Optional patient ID for patient-specific cleanup
            additional_paths: Additional paths to clean up
            
        Returns:
            Dictionary with cleanup results and statistics
        """
        cleanup_stats = {
            "workflow_id": workflow_id,
            "patient_id": patient_id,
            "cleaned_paths": [],
            "failed_paths": [],
            "total_size_cleaned": 0,
            "total_items_cleaned": 0,
            "start_time": datetime.now().isoformat()
        }
        
        try:
            # Define cleanup paths based on workflow and patient ID
            cleanup_paths = []
            
            # Patient-specific paths
            if patient_id:
                cleanup_paths.extend([
                    f"/data/temp/{patient_id}",
                    f"/data/temp/{workflow_id}",
                    f"/data/uploads/{patient_id}",
                    f"/data/results/{patient_id}",
                    f"/data/results/{workflow_id}",
                ])
            
            # Workflow-specific paths
            cleanup_paths.extend([
                f"/tmp/pharmcat/{workflow_id}",
                f"/tmp/gatk_temp/{workflow_id}",
                f"/tmp/pypgx/{workflow_id}",
                f"/tmp/hlatyping/{workflow_id}",
                f"/data/temp/{workflow_id}",
            ])
            
            # Add any additional paths
            if additional_paths:
                cleanup_paths.extend(additional_paths)
            
            # Clean up each path
            for path_str in cleanup_paths:
                try:
                    path = Path(path_str)
                    if path.exists():
                        # Calculate size before deletion
                        size = self._calculate_path_size(path)
                        
                        # Remove the path
                        if path.is_dir():
                            shutil.rmtree(path, ignore_errors=True)
                        else:
                            path.unlink()
                        
                        cleanup_stats["cleaned_paths"].append(str(path))
                        cleanup_stats["total_size_cleaned"] += size
                        cleanup_stats["total_items_cleaned"] += 1
                        
                        logger.info(f"Cleaned up workflow {workflow_id}: {path} ({size} bytes)")
                    else:
                        logger.debug(f"Path does not exist, skipping: {path}")
                        
                except Exception as e:
                    cleanup_stats["failed_paths"].append({
                        "path": path_str,
                        "error": str(e)
                    })
                    logger.warning(f"Failed to cleanup {path_str}: {e}")
            
            cleanup_stats["end_time"] = datetime.now().isoformat()
            cleanup_stats["success"] = len(cleanup_stats["failed_paths"]) == 0
            
            logger.info(f"Workflow cleanup completed for {workflow_id}: "
                       f"{cleanup_stats['total_items_cleaned']} items, "
                       f"{cleanup_stats['total_size_cleaned']} bytes")
            
            return cleanup_stats
            
        except Exception as e:
            logger.error(f"Error during workflow cleanup for {workflow_id}: {e}")
            cleanup_stats["error"] = str(e)
            cleanup_stats["success"] = False
            return cleanup_stats
    
    def cleanup_old_temp_files(self, max_age_hours: int = 24) -> Dict[str, Any]:
        """
        Clean up old temporary files based on age.
        
        Args:
            max_age_hours: Maximum age in hours for files to keep
            
        Returns:
            Dictionary with cleanup results and statistics
        """
        cleanup_stats = {
            "max_age_hours": max_age_hours,
            "cleaned_paths": [],
            "failed_paths": [],
            "total_size_cleaned": 0,
            "total_items_cleaned": 0,
            "start_time": datetime.now().isoformat()
        }
        
        try:
            cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
            
            # Define temp directories to clean
            temp_dirs = [
                self.temp_dir,
                self.data_dir / "temp",
                Path("/tmp/pharmcat"),
                Path("/tmp/gatk_temp"),
                Path("/tmp/pypgx"),
                Path("/tmp/hlatyping"),
            ]
            
            for temp_dir in temp_dirs:
                if temp_dir.exists():
                    self._cleanup_directory_by_age(temp_dir, cutoff_time, cleanup_stats)
            
            cleanup_stats["end_time"] = datetime.now().isoformat()
            cleanup_stats["success"] = len(cleanup_stats["failed_paths"]) == 0
            
            logger.info(f"Old temp files cleanup completed: "
                       f"{cleanup_stats['total_items_cleaned']} items, "
                       f"{cleanup_stats['total_size_cleaned']} bytes")
            
            return cleanup_stats
            
        except Exception as e:
            logger.error(f"Error during old temp files cleanup: {e}")
            cleanup_stats["error"] = str(e)
            cleanup_stats["success"] = False
            return cleanup_stats
    
    def _cleanup_directory_by_age(self, directory: Path, cutoff_time: datetime, 
                                 cleanup_stats: Dict[str, Any]) -> None:
        """Clean up files in a directory that are older than cutoff_time."""
        try:
            for item in directory.iterdir():
                try:
                    # Check if item is older than cutoff
                    item_mtime = datetime.fromtimestamp(item.stat().st_mtime)
                    if item_mtime < cutoff_time:
                        # Calculate size before deletion
                        size = self._calculate_path_size(item)
                        
                        # Remove the item
                        if item.is_dir():
                            shutil.rmtree(item, ignore_errors=True)
                        else:
                            item.unlink()
                        
                        cleanup_stats["cleaned_paths"].append(str(item))
                        cleanup_stats["total_size_cleaned"] += size
                        cleanup_stats["total_items_cleaned"] += 1
                        
                        logger.debug(f"Cleaned up old item: {item} ({size} bytes)")
                        
                except Exception as e:
                    cleanup_stats["failed_paths"].append({
                        "path": str(item),
                        "error": str(e)
                    })
                    logger.warning(f"Failed to cleanup old item {item}: {e}")
                    
        except Exception as e:
            logger.warning(f"Failed to process directory {directory}: {e}")
    
    def _calculate_path_size(self, path: Path) -> int:
        """Calculate the total size of a file or directory."""
        try:
            if path.is_file():
                return path.stat().st_size
            elif path.is_dir():
                total_size = 0
                for item in path.rglob('*'):
                    if item.is_file():
                        total_size += item.stat().st_size
                return total_size
            else:
                return 0
        except Exception:
            return 0
    
    def get_cleanup_status(self) -> Dict[str, Any]:
        """
        Get current status of temporary directories.
        
        Returns:
            Dictionary with directory status and statistics
        """
        status = {
            "timestamp": datetime.now().isoformat(),
            "directories": {},
            "total_temp_size": 0,
            "total_temp_items": 0
        }
        
        # Define temp directories to check
        temp_dirs = [
            ("/tmp", self.temp_dir),
            ("/data/temp", self.data_dir / "temp"),
            ("/tmp/pharmcat", Path("/tmp/pharmcat")),
            ("/tmp/gatk_temp", Path("/tmp/gatk_temp")),
            ("/tmp/pypgx", Path("/tmp/pypgx")),
            ("/tmp/hlatyping", Path("/tmp/hlatyping")),
        ]
        
        for name, path in temp_dirs:
            if path.exists():
                try:
                    size = self._calculate_path_size(path)
                    item_count = len(list(path.rglob('*'))) if path.is_dir() else 1
                    
                    status["directories"][name] = {
                        "exists": True,
                        "size_bytes": size,
                        "item_count": item_count,
                        "last_modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat()
                    }
                    
                    status["total_temp_size"] += size
                    status["total_temp_items"] += item_count
                    
                except Exception as e:
                    status["directories"][name] = {
                        "exists": True,
                        "error": str(e)
                    }
            else:
                status["directories"][name] = {
                    "exists": False,
                    "size_bytes": 0,
                    "item_count": 0
                }
        
        return status


# Global cleanup service instance
cleanup_service = CleanupService()
