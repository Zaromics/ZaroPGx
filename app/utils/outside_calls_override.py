"""
Outside Calls Override Utility

This module provides utilities for handling outside calls TSV file overrides.
When OUTSIDECALLSOVERRIDE=true, the system will look for a manual override file
at lexicon/outside_calls.tsv and use it instead of the generated outside calls.

This is useful when:
- PyPGx and HLA calling are disabled in the pipeline
- Manual genotype calls need to be provided (e.g., from external testing)
- CYP2D6, HLA-A, HLA-B, MT-RNR1 calls from orthogonal methods
"""

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default override file path - in lexicon directory for version control
DEFAULT_OVERRIDE_PATH = "lexicon/outside_calls.tsv"


def get_override_file_path() -> Optional[str]:
    """
    Get the path to the outside calls override file if it exists and override is enabled.

    Returns:
        Path to the override file if it exists and override is enabled, None otherwise
    """
    # Check if override is enabled
    override_enabled = os.getenv("OUTSIDECALLSOVERRIDE", "false").lower() in {
        "true",
        "1",
        "yes",
        "on",
    }

    if not override_enabled:
        logger.debug("Outside calls override is disabled")
        return None

    # Try multiple path resolution strategies
    possible_paths = [
        DEFAULT_OVERRIDE_PATH,
        "/data/lexicon/outside_calls.tsv",
        os.path.join(os.getcwd(), DEFAULT_OVERRIDE_PATH),
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            DEFAULT_OVERRIDE_PATH,
        ),
    ]

    for path in possible_paths:
        if os.path.exists(path):
            logger.info(f"Found outside calls override file: {path}")
            return path

    logger.warning(
        f"Outside calls override is enabled but no override file found at any of these locations: {possible_paths}"
    )
    return None


def is_override_enabled() -> bool:
    """
    Check if outside calls override is enabled.

    Returns:
        True if override is enabled, False otherwise
    """
    return os.getenv("OUTSIDECALLSOVERRIDE", "false").lower() in {
        "true",
        "1",
        "yes",
        "on",
    }


def validate_override_file(file_path: str) -> bool:
    """
    Validate that the override file has the correct PharmCAT outside call format.

    PharmCAT format:
    - Tab-separated file
    - Optional header row starting with 'Gene'
    - Each data line: Gene<tab>Diplotype<tab>Phenotype<tab>ActivityScore
    - Not all columns required - can omit empty trailing columns

    Args:
        file_path: Path to the override file

    Returns:
        True if file is valid, False otherwise
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = [
                line.strip()
                for line in f.readlines()
                if line.strip() and not line.startswith("#")
            ]

        if len(lines) < 1:
            logger.error(
                f"Override file {file_path} is empty or contains only comments"
            )
            return False

        # Check if first line is a header
        first_line = lines[0].split("\t")
        has_header = first_line[0].lower() == "gene"

        if has_header:
            if len(lines) < 2:
                logger.error(f"Override file {file_path} has header but no data lines")
                return False
            data_lines = lines[1:]
            logger.info(f"Override file has header: {first_line}")
        else:
            data_lines = lines
            logger.info("Override file has no header (headerless PharmCAT format)")

        # Validate data lines - each should have at least Gene column
        valid_genes = []
        for i, line in enumerate(data_lines):
            parts = line.split("\t")
            if len(parts) < 1 or not parts[0]:
                logger.warning(f"Line {i+1}: Missing gene name")
                continue
            gene = parts[0]
            valid_genes.append(gene)

        if not valid_genes:
            logger.error(f"Override file {file_path} has no valid gene entries")
            return False

        logger.info(
            f"Override file {file_path} validation passed with {len(valid_genes)} gene entries: {valid_genes}"
        )
        return True

    except Exception as e:
        logger.error(f"Error validating override file {file_path}: {e}")
        return False


def get_override_file_content(file_path: str) -> Optional[str]:
    """
    Get the content of the override file if it's valid.

    Args:
        file_path: Path to the override file

    Returns:
        File content as string if valid, None otherwise
    """
    if not validate_override_file(file_path):
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        logger.info(
            f"Successfully read override file content ({len(content)} characters)"
        )
        return content
    except Exception as e:
        logger.error(f"Error reading override file {file_path}: {e}")
        return None
