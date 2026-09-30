#!/bin/bash

# Script to backup PostgreSQL data from Docker volume to local directory
# This allows you to have a local copy of the database files

set -e

# Configuration
VOLUME_NAME="zaropgx_pgdata"
BACKUP_DIR="./db/data"
CONTAINER_NAME="pgx_db"

echo "🔄 Backing up PostgreSQL data from Docker volume to local directory..."

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

# Stop the database container if it's running
echo "⏹️  Stopping database container..."
docker compose stop db 2>/dev/null || true

# Create a temporary container to copy data from volume
echo "📦 Creating temporary container to access volume data..."
docker run --rm -v "$VOLUME_NAME":/source -v "$(pwd)/$BACKUP_DIR":/backup alpine sh -c "
    echo '📋 Copying database files...'
    cp -r /source/* /backup/ 2>/dev/null || echo 'No data to copy (first run)'
    echo '✅ Database files copied to $BACKUP_DIR'
"

echo "✅ Backup completed! Database files are now available in $BACKUP_DIR"
echo "📁 You can now access your database files at: $(pwd)/$BACKUP_DIR"
