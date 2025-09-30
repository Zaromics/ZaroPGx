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
import subprocess
import shlex
import re
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)


class VersionManager:
    """Centralized version management for ZaroPGx services."""
    
    def __init__(self, versions_dir: str = "/data/versions", project_root: Optional[str] = None, include_all_compose_services: Optional[bool] = None):
        self.versions_dir = Path(versions_dir)
        self.project_root = Path(project_root) if project_root else Path(__file__).parent.parent.parent
        # When True, include all services from compose even if a manifest exists
        env_include_all = os.getenv("VERSION_MANAGER_INCLUDE_ALL_COMPOSE", "false").lower() in {"1", "true", "yes"}
        self.include_all_compose_services = include_all_compose_services if include_all_compose_services is not None else env_include_all
        
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
            import psycopg
            db_url = os.environ.get("DATABASE_URL")
            if db_url:
                with psycopg.connect(db_url) as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT version();")
                        version_str = cur.fetchone()[0]
                        # Extract version number from "PostgreSQL 15.5 on x86_64..."
                        if "PostgreSQL" in version_str:
                            version_match = version_str.split("PostgreSQL ")[1].split(" ")[0]
                            return version_match
        except Exception as e:
            logger.debug(f"Failed to query PostgreSQL version: {e}")
        
        return "N/A"
    
    def _get_compose_fallbacks(self, manifest_names: set) -> List[Dict[str, str]]:
        """Get versions from docker compose as fallback. Optionally include all services."""
        versions: List[Dict[str, str]] = []
        
        compose_files = [
            "docker-compose.yml",
            "docker-compose.override.yml",
            "compose.yml",
            "compose.override.yml",
        ]
        existing_files = [str(self.project_root / f) for f in compose_files if (self.project_root / f).exists()]
        if not existing_files:
            return versions
        
        # Prefer docker compose CLI to render merged, env-substituted config
        config = self._load_compose_config(existing_files)
        if config is None:
            # Fallback: try the legacy line parser on the first compose file for minimal coverage
            first_path = self.project_root / compose_files[0]
            if first_path.exists():
                versions.extend(self._parse_compose_versions(first_path, manifest_names))
            return versions
        
        services = config.get("services", {}) if isinstance(config, dict) else {}
        if not isinstance(services, dict):
            return versions
        
        for service_name, service_def in services.items():
            try:
                if not isinstance(service_def, dict):
                    continue
                info = self._extract_service_version_info(service_name, service_def)
                if not info:
                    continue
                display_name_lower = info["name"].lower()
                if self.include_all_compose_services or display_name_lower not in manifest_names:
                    versions.append(info)
            except Exception as e:
                logger.debug(f"Failed to extract version for compose service {service_name}: {e}")
        
        return versions

    def _load_compose_config(self, compose_files: List[str]) -> Optional[Dict[str, Any]]:
        """Load merged docker compose config using CLI; fallback to YAML merge if CLI is unavailable."""
        try:
            # Try docker compose v2
            cmd = ["docker", "compose"]
            if self._is_executable_available(cmd[0]):
                args = cmd + sum((['-f', f] for f in compose_files), []) + ["config"]
                result = subprocess.run(args, cwd=str(self.project_root), check=False, capture_output=True, text=True, timeout=8)
                if result.returncode == 0 and result.stdout.strip():
                    try:
                        import yaml  # type: ignore
                        return yaml.safe_load(result.stdout)  # type: ignore
                    except Exception as e:  # noqa: F841
                        logger.debug("PyYAML not available or failed to parse docker compose CLI output")
                else:
                    logger.debug(f"docker compose config failed: rc={result.returncode}, err={result.stderr[:200]}")
        except Exception as e:
            logger.debug(f"docker compose config invocation failed: {e}")
        
        # Try legacy docker-compose
        try:
            cmd = ["docker-compose"]
            if self._is_executable_available(cmd[0]):
                args = cmd + sum((['-f', f] for f in compose_files), []) + ["config"]
                result = subprocess.run(args, cwd=str(self.project_root), check=False, capture_output=True, text=True, timeout=8)
                if result.returncode == 0 and result.stdout.strip():
                    try:
                        import yaml  # type: ignore
                        return yaml.safe_load(result.stdout)  # type: ignore
                    except Exception as e:  # noqa: F841
                        logger.debug("PyYAML not available or failed to parse docker-compose CLI output")
                else:
                    logger.debug(f"docker-compose config failed: rc={result.returncode}, err={result.stderr[:200]}")
        except Exception as e:
            logger.debug(f"docker-compose config invocation failed: {e}")
        
        # Final fallback: attempt to read and merge via PyYAML directly
        try:
            import yaml  # type: ignore
            merged: Dict[str, Any] = {}
            for f in compose_files:
                try:
                    with open(f, "r", encoding="utf-8") as fh:
                        content = fh.read()
                    # Basic env substitution for ${VAR} or ${VAR:-default}
                    content = self._basic_env_substitution(content)
                    data = yaml.safe_load(content)  # type: ignore
                    if isinstance(data, dict):
                        merged = self._deep_merge_dicts(merged, data)
                except FileNotFoundError:
                    continue
            return merged if merged else None
        except Exception as e:
            logger.debug(f"Failed to parse compose YAML directly: {e}")
            return None

    def _is_executable_available(self, executable: str) -> bool:
        from shutil import which
        return which(executable) is not None

    def _deep_merge_dicts(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(base)
        for k, v in override.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = self._deep_merge_dicts(result[k], v)
            else:
                result[k] = v
        return result

    def _basic_env_substitution(self, content: str) -> str:
        """Very basic ${VAR} and ${VAR:-default} substitution to make YAML parseable off-Docker."""
        pattern = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")

        def repl(match: re.Match[str]) -> str:
            var = match.group(1)
            default = match.group(2) if match.lastindex and match.lastindex >= 2 else None
            return os.getenv(var, default or "")

        return pattern.sub(repl, content)

    def _extract_service_version_info(self, service_name: str, service_def: Dict[str, Any]) -> Optional[Dict[str, str]]:
        image = service_def.get("image")
        labels = service_def.get("labels") or {}
        if isinstance(labels, list):
            # Convert ["key=value", ...] to dict if needed
            label_dict: Dict[str, str] = {}
            for item in labels:
                if isinstance(item, str) and "=" in item:
                    k, v = item.split("=", 1)
                    label_dict[k.strip()] = v.strip()
            labels = label_dict
        if not isinstance(labels, dict):
            labels = {}
        
        # Prefer OCI image labels for version/name when available
        oci_title = labels.get("org.opencontainers.image.title") or labels.get("org.label-schema.name")
        oci_version = labels.get("org.opencontainers.image.version") or labels.get("org.label-schema.version")
        
        image_repo = None
        image_tag = None
        image_digest = None
        if isinstance(image, str):
            image_repo, image_tag, image_digest = self._extract_image_info(image)
        
        display_name = self._derive_display_name(service_name, image_repo, oci_title)
        version = (
            (oci_version or "").strip()
            or (image_tag or "").strip()
            or (image_digest or "").strip()
        )
        if not version:
            # No version data; skip returning version for this service
            return None
        
        return {
            "name": display_name,
            "version": version,
            "source": "compose",
        }

    def _extract_image_info(self, image: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Split image into (repo, tag, digest). Handles registry ports and digests."""
        digest = None
        repo_and_tag = image
        if "@" in image:
            repo_and_tag, digest = image.split("@", 1)
        tag = None
        # rsplit only on last ':' to preserve registry:port
        if ":" in repo_and_tag and not repo_and_tag.endswith(":"):
            repo, maybe_tag = repo_and_tag.rsplit(":", 1)
            # If maybe_tag looks like a sha256 or path (unlikely), keep as tag regardless
            tag = maybe_tag
        else:
            repo = repo_and_tag
        return repo, tag, digest

    def _derive_display_name(self, service_name: str, image_repo: Optional[str], oci_title: Optional[str]) -> str:
        if oci_title:
            return str(oci_title).strip()
        if image_repo:
            normalized = image_repo.lower()
            # Friendly mappings
            known_map = {
                "postgres": "PostgreSQL",
                "postgresql": "PostgreSQL",
                "hapiproject/hapi": "HAPI FHIR Server",
                "hapiproject/hapi-vnext": "HAPI FHIR Server",
            }
            for key, friendly in known_map.items():
                if key in normalized:
                    return friendly
            # Otherwise, use repo name part
            repo_part = normalized.split("/")[-1]
            return repo_part.replace("-", " ").replace("_", " ").title()
        # Fallback to service key
        return service_name.replace("-", " ").replace("_", " ").title()
    
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
