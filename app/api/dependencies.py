"""
Dependencies for FastAPI routes.

This module provides common dependencies used across the application.
"""

from app.api.db import get_db

# Re-export the database dependency
__all__ = ["get_db"]
