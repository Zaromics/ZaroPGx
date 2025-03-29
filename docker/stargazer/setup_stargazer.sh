#!/bin/bash
set -e

# This script sets up Stargazer from a local zip file

# Check if Stargazer is already installed
if [ -f "/stargazer/.installed" ]; then
  echo "Stargazer is already installed. Skipping setup."
  exit 0
fi

# Check if the zip file exists
if [ -f "/tmp/stargazer.zip" ]; then
  echo "Found Stargazer zip file. Extracting..."
  unzip -q /tmp/stargazer.zip -d /tmp/stargazer_extract
  
  # Find the directory created inside the extracted folder (might be something like stargazer-main)
  STARGAZER_DIR=$(find /tmp/stargazer_extract -maxdepth 1 -type d | grep -v "^/tmp/stargazer_extract$" | head -1)
  
  if [ -z "$STARGAZER_DIR" ]; then
    echo "Error: Could not find Stargazer directory in the extracted zip"
    exit 1
  fi
  
  echo "Found Stargazer directory: $STARGAZER_DIR"
  
  # Copy all contents to /stargazer
  cp -r $STARGAZER_DIR/* /stargazer/
  
  # Install Stargazer
  echo "Installing Stargazer..."
  cd /stargazer
  pip3 install -e .
  
  # Mark as installed
  touch /stargazer/.installed
  
  echo "Stargazer installation complete!"
else
  echo "Error: Stargazer zip file not found at /tmp/stargazer.zip"
  echo "Please make sure to mount the zip file in docker-compose.yml"
  exit 1
fi 