"""
Centralized version management for ZaroPGx services.

This module provides a unified interface for retrieving version information
from all services in the ZaroPGx pipeline, including both version manifests
and docker-compose.yml fallbacks.
"""

import os
import json
import logging
import requests
from typing import Dict, List, Optional, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)


class VersionManager:
    """Centralized version management for ZaroPGx services."""
    
    def __init__(self, versions_dir: str = "/data/versions", project_root: Optional[str] = None):
        self.versions_dir = Path(versions_dir)
        self.project_root = Path(project_root) if project_root else Path(__file__).parent.parent.parent
        
    def get_all_versions(self) -> List[Dict[str, str]]:
        """Get versions from all available sources."""
        versions = []
        
        # 1. Load from version manifests (highest priority)
        manifest_versions = self._load_version_manifests()
        versions.extend(manifest_versions)
        
        # 2. Add service-specific version retrievers ONLY for services without manifests
        manifest_names = {v.get("name", "").lower() for v in manifest_versions}
        service_versions = self._get_service_versions(manifest_names)
        versions.extend(service_versions)
        
        # 3. Add docker-compose fallbacks ONLY for services without manifests
        compose_versions = self._get_compose_fallbacks(manifest_names)
        versions.extend(compose_versions)
        
        # 4. Remove duplicates (keep first occurrence)
        seen = set()
        unique_versions = []
        for version in versions:
            name_lower = version.get("name", "").lower()
            if name_lower not in seen:
                seen.add(name_lower)
                unique_versions.append(version)
        
        return unique_versions
    
    def _load_version_manifests(self) -> List[Dict[str, str]]:
        """Load version manifests from the shared versions directory."""
        versions = []
        
        if not self.versions_dir.exists():
            logger.debug(f"Versions directory {self.versions_dir} does not exist")
            return versions
            
        for manifest_file in self.versions_dir.glob("*.json"):
            try:
                with open(manifest_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                if isinstance(data, dict):
                    name = data.get("name", manifest_file.stem)
                    version = data.get("version", "N/A")
                    if name and version:
                        versions.append({
                            "name": str(name).strip(),
                            "version": str(version).strip(),
                            "source": "manifest"
                        })
            except Exception as e:
                logger.warning(f"Failed to load version manifest {manifest_file}: {e}")
                
        return versions
    
    def _get_service_versions(self, manifest_names: set) -> List[Dict[str, str]]:
        """Get versions from service-specific methods."""
        versions = []
        
        # HAPI FHIR version
        if "hapi fhir server" not in manifest_names:
            hapi_version = self._get_hapi_version()
            if hapi_version != "N/A":
                versions.append({
                    "name": "HAPI FHIR Server",
                    "version": hapi_version,
                    "source": "service"
                })
        
        # PostgreSQL version
        if "postgresql" not in manifest_names:
            postgres_version = self._get_postgres_version()
            if postgres_version != "N/A":
                versions.append({
                    "name": "PostgreSQL",
                    "version": postgres_version,
                    "source": "service"
                })
        
        return versions
    
    def _get_hapi_version(self) -> str:
        """Get HAPI FHIR server version."""
        # Check environment variable first
        env_ver = os.getenv("HAPI_FHIR_VERSION")
        if env_ver:
            return str(env_ver)
        
        # Try to fetch from FHIR metadata
        server_url = os.environ.get("FHIR_SERVER_URL", "http://fhir-server:8080/fhir")
        metadata_url = server_url.rstrip("/") + "/metadata"
        
        try:
            headers = {"Accept": "application/fhir+json"}
            resp = requests.get(metadata_url, headers=headers, timeout=3)
            if resp.status_code == 200:
                data = resp.json()
                software = data.get("software", {})
                version = software.get("version", "")
                if version:
                    return str(version)
        except Exception as e:
            logger.debug(f"Failed to fetch HAPI FHIR metadata: {e}")
        
        return "N/A"
    
    def _get_postgres_version(self) -> str:
        """Get PostgreSQL version."""
        # Check environment variable first
        env_ver = os.getenv("POSTGRES_VERSION")
        if env_ver:
            return str(env_ver)
        
        # Try to connect and query version
        try:
            import psycopg2
            db_url = os.environ.get("DATABASE_URL")
            if db_url:
                conn = psycopg2.connect(db_url)
                with conn.cursor() as cur:
                    cur.execute("SELECT version();")
                    version_str = cur.fetchone()[0]
                    # Extract version number from "PostgreSQL 15.5 on x86_64..."
                    if "PostgreSQL" in version_str:
                        version_match = version_str.split("PostgreSQL ")[1].split(" ")[0]
                        return version_match
                conn.close()
        except Exception as e:
            logger.debug(f"Failed to query PostgreSQL version: {e}")
        
        return "N/A"
    
    def _get_compose_fallbacks(self, manifest_names: set) -> List[Dict[str, str]]:
        """Get versions from docker-compose.yml as fallback ONLY for services without manifests."""
        versions = []
        
        compose_files = [
            "docker-compose.yml",
            "docker-compose-local-LAN.yml",
            "docker-compose.override.yml"
        ]
        
        for compose_file in compose_files:
            compose_path = self.project_root / compose_file
            if compose_path.exists():
                compose_versions = self._parse_compose_versions(compose_path, manifest_names)
                versions.extend(compose_versions)
                break  # Use first found compose file
        
        return versions
    
    def _parse_compose_versions(self, compose_path: Path, manifest_names: set) -> List[Dict[str, str]]:
        """Parse docker-compose.yml for service versions."""
        versions = []
        
        try:
            with open(compose_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            lines = content.split('\n')
            current_service = None
            
            for i, line in enumerate(lines):
                line = line.strip()
                
                # Check if this is a service definition
                if line and not line.startswith(('#', ' ', '\t')) and line.endswith(':'):
                    current_service = line.rstrip(':')
                    continue
                
                # Look for image lines in current service
                if current_service and line.startswith('image:'):
                    image_line = line.split(':', 1)[1].strip()
                    if ':' in image_line:
                        service_name, version = image_line.rsplit(':', 1)
                        
                        # Map service names to display names
                        display_names = {
                            'postgres': 'PostgreSQL',
                            'hapiproject/hapi': 'HAPI FHIR Server',
                            'hapiproject/hapi:v6.8.0': 'HAPI FHIR Server',
                        }
                        
                        display_name = display_names.get(service_name, service_name)
                        
                        # Only add if the service name is not already in manifest_names
                        if display_name.lower() not in manifest_names:
                            versions.append({
                                "name": display_name,
                                "version": version,
                                "source": "compose"
                            })
                        
                        current_service = None  # Reset for next service
                        
        except Exception as e:
            logger.warning(f"Failed to parse compose file {compose_path}: {e}")
        
        return versions
    
    def get_version_by_name(self, service_name: str) -> Optional[str]:
        """Get version for a specific service by name."""
        all_versions = self.get_all_versions()
        
        for version_info in all_versions:
            if version_info.get("name", "").lower() == service_name.lower():
                return version_info.get("version")
        
        return None
    
    def get_versions_dict(self) -> Dict[str, str]:
        """Get versions as a dictionary mapping service names to versions."""
        all_versions = self.get_all_versions()
        return {v.get("name", ""): v.get("version", "N/A") for v in all_versions}


# Global instance for easy access
version_manager = VersionManager()


def get_all_versions() -> List[Dict[str, str]]:
    """Get all service versions."""
    return version_manager.get_all_versions()


def get_version_by_name(service_name: str) -> Optional[str]:
    """Get version for a specific service."""
    return version_manager.get_version_by_name(service_name)


def get_versions_dict() -> Dict[str, str]:
    """Get versions as a dictionary."""
    return version_manager.get_versions_dict()
