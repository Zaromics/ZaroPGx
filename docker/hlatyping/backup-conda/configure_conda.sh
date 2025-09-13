#!/bin/bash
# Configure conda for bioconda usage with proper channel order

set -euo pipefail

echo "Configuring conda for bioconda usage..."

# Create a .condarc file with the correct channel order
cat > ~/.condarc << 'EOF'
channels:
  - conda-forge
  - bioconda
channel_priority: flexible
ssl_verify: true
EOF

# Accept Terms of Service for default channels to avoid ToS errors
echo "Accepting Terms of Service for default channels..."
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main || true
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r || true

# Verify configuration
echo "Current conda configuration:"
conda config --show channels
conda config --show channel_priority

echo "Conda configuration completed successfully!"
