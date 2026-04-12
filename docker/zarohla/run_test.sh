#!/bin/bash
echo "Building ZaroHLA image..."
docker build -t zarohla:latest .

echo "Starting ZaroHLA container..."
docker run -d -p 5000:5000 --name zarohla -v $(pwd)/data:/data zarohla:latest

echo "Waiting for healthcheck..."
sleep 5
curl http://localhost:5000/health

echo "Test complete."
