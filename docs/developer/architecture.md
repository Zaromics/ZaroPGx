---
title: System Architecture
curation: partial
---

# System Architecture

Detailed technical architecture and design principles of ZaroPGx.

> **Quick Reference**: For a high-level overview of components and port mappings, see the [Architecture Overview](../architecture.md).

## High-Level Architecture

ZaroPGx is built as a microservices architecture using both reference and API wrapper Docker containers, orchestrated with Docker Compose. The system is designed for extensibility, maintainability, and ensures PHI data privacy when run locally "on premises".

### Core Components

```mermaid
graph TB
    subgraph "Client Layer"
        UI[Web UI]
        API[REST API]
        CLI[CLI Tools]
    end
    
    subgraph "Application Layer"
        APP[FastAPI App]
        AUTH[Authentication]
        WORKFLOW[Workflow Engine]
    end
    
    subgraph "Processing Layer"
        PHARMCAT[PharmCAT Service]
        PYPGX[PyPGx Service]
        GATK[GATK API]
        HLA[HLA Typing]
    end
    
    subgraph "Data Layer"
        DB[(PostgreSQL)]
        FHIR[HAPI FHIR]
        STORAGE[File Storage]
    end
    
    subgraph "Infrastructure Layer"
        DOCKER[Docker Engine]
        NETWORK[Network Bridge]
        VOLUMES[Data Volumes]
    end
    
    UI --> APP
    API --> APP
    CLI --> APP
    
    APP --> WORKFLOW
    APP --> AUTH
    APP --> DB
    
    WORKFLOW --> PHARMCAT
    WORKFLOW --> PYPGX
    WORKFLOW --> GATK
    WORKFLOW --> HLA
    
    APP --> FHIR
    APP --> STORAGE
    
    DOCKER --> NETWORK
    DOCKER --> VOLUMES
```

## Service Architecture

### Core FastAPI Application (`app`)

**Purpose**: Main orchestrator and web user interface
**Technology**: Python 3.12, FastAPI, SQLAlchemy, psycopg, sam,bcftools
**Port**: 8765 → 8000

**Key Responsibilities:**
- Web UI and API endpoints
- Workflow orchestration
- Database management
- Report generation
- Authentication and authorization

**Key Modules:**
- `app/api/`: API routes and models (`data_id` on upload/status; jobs under `/api/v1/jobs`)
- `app/services/`: Background processing (WebSocket envelope type `job_update`)
- `app/reports/`: Report generation (artifacts at `/data/reports/{patient_id}/{job_id}/`; display `report_id` = `job_id`)
- `app/pharmcat/`: PharmCAT integration
- `app/core/`: Core utilities
- `app/utils/job_client.py`: Shared JobClient (containers mount at `/job-client`)

### PostgreSQL Database (`db`)

**Purpose**: Primary data storage
**Technology**: PostgreSQL 18 (`postgres:18`, per `compose.yml`)
**Port**: 5444 → 5432

**Schemas:**
- `public`: Core application data
- `cpic`: CPIC guidelines and data
- `fhir`: HAPI FHIR server tables. **FHIR R4** — `app/services/fhir_export_service.py` emits
  HL7 Genomics Reporting IG **R4** bundles and `docs/samples/sample_fhir_r4_pgx_bundle.json`
  is an R4 bundle. Nothing in the repo targets R5.
- `user_data`: User and patient data
- `reports`: Generated reports metadata
- `phenopackets`: In progress

**Key Tables:**
- `patients`: Patient information
- `genetic_data`: Genomic file metadata
- `workflows`: Analysis workflows
- `workflow_steps`: Individual processing steps
- `reports`: Generated report metadata

### PharmCAT Service (`pharmcat`)

**Purpose**: Pharmacogenomic analysis engine
**Technology**: Java 17, FastAPI wrapper
**Port**: 5001 → 5000

**Key Features:**
- Star allele calling for 23 core pharmacogenes
- CPIC, DWPG, FDA guidelines integration
- HTML Report generation
- Outside call integration for uncallable genes

**API Endpoints:**
- `POST /analyze`: Analyze VCF file
- `GET /status/{job_id}`: Check analysis status
- `GET /results/{job_id}`: Get analysis results

### PyPGx Service (`pypgx`)

**Purpose**: Comprehensive allele calling
**Technology**: Python, PyPGx affordances
**Port**: 5053 → 5000

**Key Features:**
- Star allele calling for 87 pharmacogenes
- Difficult to type genes such as CYP2D6
- Considers SVs and CNVs
- Diplotype and phenotype prediction

**Supported Genes:**
- `config/genes.json` is the machine-readable authority: 91 genes total, `sets.pypgx` = 87,
  `sets.pharmcat_all` = 23, `sets.pypgx_minus_pharmcat` = 68. Quote those numbers rather than
  restating them from prose elsewhere.

### GATK API (`gatk-api`)

**Purpose**: Multiple functions
**Technology**: Java, GATK affordances
**Port**: 5002 → 5000

**Key Features:**
- BAM/SAM/CRAM to VCF conversion
- Variant calling and filtering
- Quality control metrics
- Reference genome processing

**Processing Pipeline:**
1. Input validation
2. Reference genome preparation
3. Variant calling
4. Quality filtering
5. VCF output generation

### ZaroHLA Typing Service (`zarohla`)

**Purpose**: HLA allele calling
**Technology**: FastAPI wrapper around OptiType v1.5
**Port**: 5060 → 5000 (loopback-bound; gated on `OPTITYPE_ENABLED`)

**Key Features:**
- HLA-A, HLA-B, HLA-C typing
- OptiType core

### HAPI FHIR Server (`fhir-server`)

**Purpose**: Healthcare data interoperability
**Technology**: Java, HAPI FHIR
**Port**: 8090 → 8080

**Key Features:**
- FHIR compliance
- Groundwork laid for enterprise expansion
- Observation resource storage
- Structured semantic FHIR query capability

## Data Flow Architecture

### Upload and Processing Flow

```mermaid
sequenceDiagram
    participant U as User
    participant A as FastAPI App
    participant F as File Processor
    participant W as Workflow Engine
    participant P as PharmCAT
    participant Py as PyPGx
    participant G as GATK
    participant R as Report Generator
    
    U->>A: Upload file
    A->>F: Process file
    F->>A: File analysis
    A->>W: Create workflow
    W->>G: Preprocess (if needed)
    G->>W: VCF output
    W->>Py: PyPGx analysis
    Py->>W: PyPGx results
    W->>P: PharmCAT analysis
    P->>W: PharmCAT results
    W->>R: Generate reports
    R->>A: Report URLs
    A->>U: Analysis complete
```

### Database Schema Design

```mermaid
erDiagram
    PATIENTS ||--o{ GENETIC_DATA : has
    PATIENTS ||--o{ WORKFLOWS : creates
    WORKFLOWS ||--o{ WORKFLOW_STEPS : contains
    WORKFLOWS ||--o{ REPORTS : generates
    GENETIC_DATA ||--o{ WORKFLOWS : processes
    
    PATIENTS {
        int id PK
        string identifier
        string name
        datetime created_at
        datetime updated_at
    }
    
    GENETIC_DATA {
        int id PK
        int patient_id FK
        string file_type
        string file_path
        json metadata
        boolean is_supplementary
        datetime created_at
    }
    
    WORKFLOWS {
        string id PK
        int patient_id FK
        string status
        json workflow_metadata
        datetime created_at
        datetime updated_at
    }
    
    WORKFLOW_STEPS {
        int id PK
        string workflow_id FK
        string step_name
        string status
        int step_order
        json output_data
        datetime created_at
        datetime updated_at
    }
    
    REPORTS {
        int id PK
        string workflow_id FK
        string report_type
        string file_path
        json metadata
        datetime created_at
    }
```

## Container Architecture

### Docker Compose Structure

```yaml
see `compose.yml`
```

### Supporting Services

Not every service has its own section above. The full inventory in `compose.yml` also includes
`genome-downloader` (5050, reference fetcher), `kroki` + `mermaid` (8001, diagram rendering),
`nextflow` (pipeline executor, **never published to the host**) and `docs` (5070, behind the
`optional` profile). The per-service host bindings are tabulated in
[Architecture Overview](../architecture.md).

### Network Architecture

**Bridge Network**: `pgx-network`
- **Subnet**: `${NETWORK_SUBNET:-172.28.0.0/16}` — the tracked `.env.local` overrides this to
  `172.20.0.0/16`, `.env.production` uses the `172.28.0.0/16` default

**Service Communication:**
- All services communicate over the internal Compose network by service name
- The `app` is the only service whose host binding is operator-controlled (`BIND_ADDRESS`);
  every other published port defaults to `${INTERNAL_BIND_ADDRESS:-127.0.0.1}`, and `nextflow`
  is not published at all
- No direct internet access is needed by the processing services once references are fetched

### Volume Management

**Bind mounts:**
- `./data`: Shared data directory (uploads, reports, inter-service artifacts)
- `./reference`: Reference genome data

**Named volumes** (as declared in `compose.yml`):
- `pgdata`: PostgreSQL persistence, mounted at `/var/lib/postgresql`
- `pharmcat-references`: PharmCAT's GRCh38 reference cache, mounted at `/pharmcat-references`
  (deliberately *not* `/pharmcat`, which would mask the image's own pipeline binaries)
- `fhir-data`: declared but currently unused — `fhir-server` bind-mounts `./data/fhir-data`

**Volume Mounts:**
- Host directories mounted into containers
- Persistent data across container restarts
- Shared access between services

## Security Architecture

### Authentication and Authorization

**Development Mode:**
- Authentication disabled by default
- All endpoints publicly accessible
- Debug logging enabled

**Production Mode:**
- JWT-based authentication
- Role-based access control
- Secure session management
- Audit logging

### Data Privacy

**Local Processing:**
- All analysis happens locally
- No external data transmission
- Complete data control
- Offline capability

**Data Encryption:**
- Data at rest encryption (configurable)
- TLS for API communication
- Secure file storage
- Encrypted database connections

### Network Security

**Internal Communication:**
- Services communicate via internal network
- No external network access for processing
- Firewall rules for port access
- VPN support for remote access

## Scalability Architecture

### Horizontal Scaling

**Application Layer:**
- Multiple FastAPI instances
- Load balancer distribution
- Session affinity for workflows
- Shared database backend

**Processing Layer:**
- Multiple PharmCAT instances
- Queue-based job distribution
- Resource-aware scheduling
- Auto-scaling based on load

### Vertical Scaling

**Resource Allocation:**
- Configurable CPU/memory limits
- Dynamic resource adjustment
- Priority-based scheduling
- Resource monitoring

### Storage Scaling

**Database Scaling:**
- Read replicas for queries
- Connection pooling
- Query optimization
- Indexing strategies

**File Storage:**
- Distributed file systems
- Object storage integration
- Backup and replication
- Data lifecycle management

## Monitoring and Observability

### Logging Architecture

**Centralized Logging:**
- Structured JSON logs
- Log aggregation and analysis
- Error tracking and alerting
- Performance monitoring

**Log Levels:**
- DEBUG: Detailed debugging information
- INFO: General information
- WARNING: Warning messages
- ERROR: Error conditions
- CRITICAL: Critical errors

### Metrics and Monitoring

**Application Metrics:**
- Request/response times
- Error rates
- Throughput metrics
- Resource utilization

**System Metrics:**
- CPU and memory usage
- Disk I/O performance
- Network traffic
- Container health

### Health Checks

**Service Health:**
- HTTP health endpoints
- Database connectivity
- External service availability
- Resource availability

**Workflow Health:**
- Processing status
- Queue depth
- Error rates
- Performance metrics

## Development Architecture

### Code Organization

**Module Structure:**
```
app/
├── api/           # API routes and models
├── core/          # Core utilities
├── pharmcat/      # PharmCAT integration
├── reports/       # Report generation
├── services/      # Background services
├── utils/         # Utility functions
└── visualizations/ # Workflow diagrams
```

**Design Patterns:**
- Dependency injection
- Service layer pattern
- Repository pattern
- Factory pattern
- Observer pattern

### Testing Architecture

**Test Types:**
- TO DO

**Test Infrastructure:**
- TO DO

## Deployment Architecture

### Environment Management

**Development:**
- Local Docker Compose
- Debug logging enabled
- Hot reloading
- Test data included

**Staging:**
- Production-like environment
- Real data testing
- Performance validation
- Security testing

**Production:**
- Optimized configuration
- Security hardening
- Monitoring and alerting
- Backup and recovery

### CI/CD Pipeline

**Build Process:**
- Docker image building
- Dependency scanning
- Security scanning
- Image optimization


## Next Steps

- **API Reference**: {doc}`api-reference`
- **Development Setup**: {doc}`development-setup`
- **Contributing**: {doc}`contributing`
- **Deployment**: {doc}`deployment`
