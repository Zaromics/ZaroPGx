#!/bin/sh

echo "Starting PharmCAT wrapper..."
echo "Checking Java version:"
java -version

echo "Checking PharmCAT installation:"
command -v pharmcat_pipeline
if [ $? -ne 0 ]; then
    echo "ERROR: pharmcat_pipeline command not found!"
    echo "Creating the command now..."
    echo '#!/bin/sh' > /pharmcat/pharmcat_pipeline
    echo 'java -jar /pharmcat/pharmcat.jar pipeline "$@"' >> /pharmcat/pharmcat_pipeline
    chmod +x /pharmcat/pharmcat_pipeline
    ln -sf /pharmcat/pharmcat_pipeline /usr/local/bin/pharmcat_pipeline
fi

# Execute the command to verify it works
echo "Testing pharmcat_pipeline command:"
pharmcat_pipeline --help | head -n 5

echo "Starting Flask app..."
python3 /app/pharmcat_wrapper.py 