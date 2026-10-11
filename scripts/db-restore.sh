#!/bin/bash

# Script to restore PostgreSQL data from local directory to Docker volume
# This allows you to restore database files from a local backup

set -e

# Configuration
VOLUME_NAME="zaropgx_pgdata"
BACKUP_DIR="./db/data"
CONTAINER_NAME="pgx_db"

echo "🔄 Restoring PostgreSQL data from local directory to Docker volume..."

# Check if backup directory exists and has data
if [ ! -d "$BACKUP_DIR" ] || [ -z "$(ls -A "$BACKUP_DIR" 2>/dev/null)" ]; then
    echo "❌ No backup data found in $BACKUP_DIR"
    echo "💡 Run ./scripts/db-backup.sh first to create a backup"
    exit 1
fi

# Stop the database container if it's running
echo "⏹️  Stopping database container..."
docker compose stop db 2>/dev/null || true

# Remove existing volume if it exists
echo "🗑️  Removing existing volume..."
docker volume rm "$VOLUME_NAME" 2>/dev/null || true

# Create new volume
echo "📦 Creating new volume..."
docker volume create "$VOLUME_NAME"

# Copy data from local directory to volume
echo "📋 Copying database files to volume..."
docker run --rm -v "$VOLUME_NAME":/target -v "$(pwd)/$BACKUP_DIR":/source alpine sh -c "
    echo 'Copying files from local backup to volume...'
    cp -r /source/* /target/
    echo 'Setting proper permissions...'
    chown -R 999:999 /target
    echo '✅ Database files restored to volume'
"

echo "✅ Restore completed! Database volume has been updated with local data"
echo "🚀 You can now start the database with: docker compose up -d db"
