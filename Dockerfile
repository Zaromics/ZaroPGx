# Use a smaller base image with Python
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TZ=UTC

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    libpq-dev \
    # WeasyPrint dependencies
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    libglib2.0-0 \
    libjpeg-dev \
    libopenjp2-7-dev \
    && rm -rf /var/lib/apt/lists/*

# Set work directory
WORKDIR /app

# Copy requirements.txt and install dependencies
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    python-multipart \
    python-jose \
    sqlalchemy \
    psycopg2-binary \
    pandas \
    weasyprint \
    jinja2 \
    pydantic \
    python-dotenv \
    requests \
    aiohttp \
    werkzeug

# Create directories for data and reports
RUN mkdir -p /data/reports /data/uploads

# Copy application code
# Note: Reference genome files are not included in the build context (see .dockerignore)
# They are mounted as volumes at runtime
COPY . /app/

# Expose the port for the application
EXPOSE 8000

# Command to run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"] 