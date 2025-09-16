"""
Nextflow Job Monitoring Service

This service previously provided real-time monitoring of Nextflow pipeline execution.
The monitoring functionality has been removed as Nextflow container no longer 
participates in state/stage reporting.
"""

import logging

logger = logging.getLogger(__name__)

# All monitoring functionality removed - Nextflow container no longer participates in state/stage reporting
