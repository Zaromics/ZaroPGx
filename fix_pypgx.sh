#!/bin/bash
# Fix the PyPGx API URL issue

echo "Setting PYPGX_API_URL environment variable to use port 5000..."
docker exec -it pgx_app bash -c 'echo "export PYPGX_API_URL=http://pypgx:5000" > /tmp/pypgx_fix.sh'
docker exec -it pgx_app bash -c 'chmod +x /tmp/pypgx_fix.sh && source /tmp/pypgx_fix.sh'

echo "Restarting app container..."
docker compose restart app

echo "Waiting for app to initialize..."
sleep 10

echo "Checking services status..."
curl -s http://localhost:8765/services-status

echo "Done!" 