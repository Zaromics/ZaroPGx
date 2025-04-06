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
        git clone --branch 0.24.0 --depth 1 https://github.com/sbslee/pypgx-bundle
    fi
else
    echo "PyPGx bundle not found, cloning version $PYPGX_VERSION..."
    cd /root && git clone --branch $PYPGX_VERSION --depth 1 https://github.com/sbslee/pypgx-bundle || \
    git clone --branch 0.24.0 --depth 1 https://github.com/sbslee/pypgx-bundle
fi

# Create required directories
mkdir -p /data/temp

# Install dependencies for the wrapper
pip install --no-cache-dir fastapi uvicorn python-multipart

# Verify that PyPGx is working
echo "Testing PyPGx installation..."
pypgx -h

# Print supported genes
echo "PyPGx supported genes:"
pypgx list-genes 2>/dev/null || echo "Gene list not available, but proceeding with wrapper startup"

# Start the FastAPI wrapper
echo "Starting PyPGx wrapper API..."
cd /app
python3 pypgx_wrapper.py 