# ZaroPGx - Pharmacogenomic Report Generator

![Screenshot 2025-05-02 011020](https://github.com/user-attachments/assets/f352c10a-23ba-4583-a6b1-196ae32c06ae)

A containerized pharmacogenomic report generator that processes genetic data and generates clinical reports based on CPIC guidelines. The system will eventually offer many types of reports, starting with a complete general report and a report oriented toward neurological pgx.

# Notice

This software is currently being built and is not near completion. It is less likely to work than not.

## Features

- Process genetic data from various sources (23andMe, VCF)
- Call variants using GATK and PyPGx
- Call alleles using PharmCAT and PyPGx
- Generate PDF and interactive HTML reports
- HIPAA-compliant data handling
- RESTful API with authentication
- Option to export reports via HAPI FHIR server
- User-friendly web interface for file uploads
- Comprehensive gene coverage including CYP2D6, CYP2C19, CYP2C9, and others
- Interactive HTML reports with visualizations

## Architecture

The application consists of several containerized services:

1. **PostgreSQL Database**: Stores CPIC guidelines, gene-drug interactions, and patient data
2. **Main App with FastAPI Backend**: Handles file uploads, report generation, and API endpoints
3. **GATK Service**: Handles variant calling and converts WGS to VCF format
4. **PharmCAT Service**: Performs allele calling for VCF files, with REST API wrapper
5. **PyPGx Service**: Specialized in CYP2D6 calling from VCF files

## Setup

### Prerequisites

- Docker and Docker Compose
- Git
- Internet connection is required during first run while the genome downloader downloads reference genome FASTA sequence, and while other containers download and install their dependencies. After this is complete the program does not connection to the internet and can be run locally - see the reference docker-composeLOCAL.yml optimized for local-only use.
- 8GB RAM minimum. 16GB RAM minimum if using GATK as it is very RAM-heavy.
- 20GB free disk space. 100GB+ free disk space if using WGS files.

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/Zaroganos/ZaroPGx.git
   cd ZaroPGx
   ```

2. Create and configure the `.env` file:
   ```bash
   cp .env.example .env
   # Edit .env file with your desired settings
   ```

3. Build and start the containers:
   ```bash
   docker-compose up -d
   ```

4. Access the application:
   - Web UI: http://localhost:8765
   - API documentation: http://localhost:8765/docs

## Usage

### Using the Web Interface

1. Open the web interface at http://localhost:8765
2. Upload a genoset file using the upload form
3. View and download the generated reports

### Uploading genetic data via API

```bash
curl -X POST -F 'file=@sample.txt' -F 'patient_identifier=patient123' \
  -H "Authorization: Bearer YOUR_TOKEN" \
  http://localhost:8000/upload/23andme
```

### Generating a report via API

```bash
curl -X POST -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"patient_id":"1","file_id":"1","report_type":"comprehensive"}' \
  http://localhost:8000/reports/generate
```

### Sample Data

The repository includes sample VCF files for testing:

- `sample_cyp2c19.vcf` - Sample with CYP2C19 variants
- `sample_cyp2d6.vcf` - Sample with CYP2D6 variants

## Development

### Project Structure

```
ZaroPGx/
├── app/                    # Python application code
│   ├── api/                # API endpoints and database access
│   ├── pharmcat/   # PharmCAT integration
│   └── reports/            # Report generation logic
├── db/                     # Database migrations
│   └── migrations/
│       └── cpic/           # CPIC schema and seed data
├── docker/                 # Additional Dockerfiles
└── docker-compose.yml      # Service orchestration
```

### Adding new genes or drugs

1. Edit the SQL seed files in `db/migrations/cpic/`
2. Restart the containers with `docker-compose restart`

## License

[AGPLv3](LICENSE)

## Acknowledgements

- [CPIC](https://cpicpgx.org/) for pharmacogenomic guidelines
- [PharmCAT](https://pharmcat.org/) for allele calling algorithms
- [PyPGx](https://github.com/...) for CYP2D6 calling

## Dependency Management

### Container Dependencies

The services are organized in a Docker Compose configuration with clear dependency chains:

1. The PostgreSQL database is the foundation service
2. The PharmCAT service runs independently
3. The FastAPI application depends on all other services

### Service-Specific Dependencies

Each service manages its dependencies within its own container:

- **PostgreSQL**: Version 15 with initialization scripts in `db/init`
- **PharmCAT Service**: Java 17 with PharmCAT v3.0.0 JAR file. Python 3.12 with Flask; Python deps installed via uv in Docker.
- **GATK Service**:
- **FastAPI Backend**: Python 3.12 with dependencies defined in `pyproject.toml` and locked by `uv.lock` (installed via uv)
- **HAPI-FHIR**: 

### Data Sharing

Services share data through:

1. **Shared Volumes**: The `/data` volume is mounted across containers for file exchange
2. **Network Communication**: All services communicate over the `pgx-network` bridge network
3. **Environment Variables**: Container configuration is managed through environment variables

## Getting Started

### Prerequisites

- Docker and Docker Compose
- Git

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/Zaroganos/ZaroPGx.git
   cd ZaroPGx
   ```

2. Create a `.env` file with necessary environment variables:
   ```bash
   POSTGRES_PASSWORD=your_secure_password
   SECRET_KEY=your_secret_key
   ```

3. Build and start the containers:
   ```bash
   docker-compose up -d
   ```

4. The services will be available at:
   - FastAPI Application: http://localhost:8000
   - PharmCAT Service: http://localhost:8080
   - PharmCAT Wrapper: http://localhost:5001
   - Aldy Service: http://localhost:5002
   - Web UI: http://localhost:8765

## Technical Details

### PharmCAT Report System

The application includes a robust system for handling PharmCAT report JSON data:

1. **Report File Management**: 
   - Each PharmCAT analysis generates multiple JSON files:
     - `{job_id}_pgx_report.json`: Standard job-specific report
     - `{job_id}_raw_report.json`: Unmodified raw report for debugging
     - `latest_pharmcat_report.json`: Always updated with most recent report data

2. **Reliability Mechanism**:
   - The `latest_pharmcat_report.json` file serves as a persistent reference
   - This file is always available for gene and drug data extraction
   - Each successful PharmCAT run updates this file automatically

3. **Fallback System**:
   - If PharmCAT fails to generate a report, the system falls back to `latest_pharmcat_report.json`
   - A sample report with valid structure can be copied to this location if needed
   - This ensures report generation can proceed even with limited gene data

4. **Maintenance Considerations**:
   - The `latest_pharmcat_report.json` file should be preserved across container restarts
   - It may need occasional updates to include additional genes or drugs
   - New versions of PharmCAT may require adjustments to JSON parsing logic

## Development

### Adding New Dependencies

1. For Python services, add new dependencies to `pyproject.toml` and run `uv lock` (or update service-specific installs in Dockerfiles where applicable)
2. For system dependencies, add them to the appropriate Dockerfile
3. Rebuild the affected containers:
   ```bash
   docker-compose build <service_name>
   docker-compose up -d <service_name>
   ```

### Service Communication

Services communicate with each other using their service names as hostnames:

- Database: `db:5432`
- PharmCAT: `pharmcat:8080`
- GATK: 
- PyPGx
- HAPI-FHIR:

## Troubleshooting

- **Service Connection Issues**: Check Docker network configuration
- **File Processing Errors**: Ensure VCF file format is valid
- **Report Generation Fails**: Check weasyprint dependencies

## Contributing

1. Create a feature branch (`git checkout -b feature/amazing-feature`)
2. Commit your changes (`git commit -m 'Add some amazing feature'`)
3. Push to the branch (`git push origin feature/amazing-feature`)
4. Open a Pull Request

## License

This project is licensed under the AGPLv3 License.

## Emergency Report Access System

For cases where the automatic report display in the UI fails, we've implemented a robust emergency access system:

### Direct Report Access

1. After uploading your VCF file, if reports don't appear automatically:
   - Note your job ID (typically "1" for the first upload after restart)
   - Find the "Manual Complete" button at the bottom of the page
   - Enter your job ID and click the button

2. A new tab will open showing:
   - Direct links to your PDF and HTML reports
   - Detailed troubleshooting information
   - Job status details for debugging

### Using the Emergency System

The emergency system provides three ways to access your reports:

1. **Direct Links**: Click the PDF or HTML report buttons in the new tab
2. **Main UI Update**: The main page should also update to show report links
3. **Manual Status Check**: Use the "Check Reports" button with your job ID

This ensures you can always access your reports even if the normal UI flow encounters issues with the Server-Sent Events (SSE) progress monitoring system.
