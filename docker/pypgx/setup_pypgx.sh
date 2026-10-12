#!/bin/bash
# Setup and run the PyPGx wrapper

# Get the version of PyPGx
PYPGX_VERSION=$(pip show pypgx | grep -oP 'Version: \K.*')
echo "Using PyPGx version: $PYPGX_VERSION"

# Check if bundle exists and is the correct version
if [ -d "/root/pypgx-bundle" ]; then
    BUNDLE_VERSION=$(cd /root/pypgx-bundle && git describe --tags || echo "unknown")
    echo "Found PyPGx bundle version: $BUNDLE_VERSION"
    
    # Update if version doesn't match
    if [ "$BUNDLE_VERSION" != "$PYPGX_VERSION" ]; then
        echo "Updating PyPGx bundle to version $PYPGX_VERSION..."
        cd /root && rm -rf pypgx-bundle
        git clone --branch $PYPGX_VERSION --depth 1 https://github.com/sbslee/pypgx-bundle || \
        git clone --branch 0.27.0 --depth 1 https://github.com/sbslee/pypgx-bundle
    fi
else
    echo "PyPGx bundle not found, cloning version $PYPGX_VERSION..."
    cd /root && git clone --branch $PYPGX_VERSION --depth 1 https://github.com/sbslee/pypgx-bundle || \
    git clone --branch 0.27.0 --depth 1 https://github.com/sbslee/pypgx-bundle
fi

# Create required directories
mkdir -p /data/temp

# Install dependencies for the wrapper (use uv if available)
if command -v uv >/dev/null 2>&1; then
  uv pip install --system fastapi uvicorn python-multipart
else
  pip install --no-cache-dir fastapi uvicorn python-multipart
fi

# Verify that PyPGx is working
echo "Testing PyPGx installation..."
pypgx -h

# Print supported genes
echo "PyPGx supported genes:"
python3 - <<'PY'
try:
    from pypgx.api import core
    genes = core.list_genes()
    if not genes:
        print("No genes found")
    else:
        for gene in genes:
            print(gene)
except Exception as e:
    print(f"Gene list not available, but proceeding with wrapper startup: {e}")
PY

# Write version manifest. The components are what PyPGx runs on the way to a call:
# fuc and pysam's bcftools call variants from reads, Beagle phases, scikit-learn
# runs the copy-number models, and the bundle holds the 1000 Genomes panels and the
# models. The report credits each at the version found here.
mkdir -p /data/versions
python3 - <<'PY'
import glob
import json
import os
import re
import subprocess
from importlib.metadata import version


def first_match(cmd, pattern):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception:
        return "unknown"
    found = re.search(pattern, out.stdout + out.stderr)
    return found.group(1) if found else "unknown"


def distribution(name):
    try:
        return version(name)
    except Exception:
        return "unknown"


import pypgx

jars = glob.glob(os.path.join(os.path.dirname(pypgx.__file__), "api", "beagle.*.jar"))
components = {
    "fuc": distribution("fuc"),
    "pysam": distribution("pysam"),
    "scikit-learn": distribution("scikit-learn"),
    # "beagle.22Jul22.46e.jar (version 5.4)": the version and its build.
    "beagle": (
        first_match(["java", "-jar", jars[0]], r"\(version ([0-9.]+)\)")
        + " (" + os.path.basename(jars[0])[len("beagle."):-len(".jar")] + ")"
        if jars
        else "unknown"
    ),
    "bcftools": first_match(["bcftools", "--version"], r"^bcftools ([0-9.]+)"),
    "pypgx-bundle": first_match(
        ["git", "-C", "/root/pypgx-bundle", "describe", "--tags"], r"^(\S+)"
    ),
}
with open("/data/versions/pypgx.json", "w", encoding="utf-8") as fh:
    json.dump(
        {
            "name": "PyPGx",
            "version": os.environ.get("PYPGX_VERSION") or distribution("pypgx"),
            "components": components,
        },
        fh,
    )
PY

# Start the FastAPI wrapper
echo "Starting PyPGx wrapper API..."
cd /app
python3 pypgx_wrapper.py 