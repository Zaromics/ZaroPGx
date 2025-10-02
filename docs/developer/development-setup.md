---
title: Development Setup
---

# Development Setup

Complete guide for setting up a ZaroPGx development environment.

## Prerequisites

### System Requirements

**Minimum:**
- 8 GB DDR3 RAM
- 4 CPU cores
- 50 GB free SSD space
- Docker
- Docker Compose

**Recommended:**
- 64+ GB DDR4+ RAM
- 8+ CPU cores
- 1TB+ free SSD storage
- Docker Desktop with WSL2 (if on Windows)
    - Hint: git clone to your ~/ on the WSL virtual drive, instead of the windows filesystem.
    - Use WSL bash for work and check that your WSL is connected in Docker Desktop settings.  

### Development Tools

**Required:**
- Git
- Docker and Docker Compose
- Python 3.12+ recommended
- Node.js 18+ (for frontend)


## Environment Setup

### 1. Clone Repository

```bash
git clone https://github.com/Zaroganos/ZaroPGx.git
cd ZaroPGx
```

### 2. Environment Configuration

**Development Environment:**
```bash
cp .env.local .env
```

**Custom Development:**
```bash
cp .env.example .env
# Edit .env with your development settings
```

### 3. Development Environment Variables

Create `.env.dev` for development-specific settings:

```bash
# Development mode
ZAROPGX_DEV_MODE=true
LOG_LEVEL=DEBUG

# Database
DB_HOST=localhost
DB_PORT=5444
DB_NAME=cpic_db
DB_USER=cpic_user
DB_PASSWORD=test123

# Services
PHARMCAT_API_URL=http://localhost:5001
PYPGX_API_URL=http://localhost:5053
GATK_API_URL=http://localhost:5002
FHIR_SERVER_URL=http://localhost:8090/fhir

# Development features
GATK_ENABLED=true
PYPGX_ENABLED=true
OPTITYPE_ENABLED=true
KROKI_ENABLED=true

# Debug settings
DEBUG=true
VERBOSE_LOGGING=true
```

## Local Development

### Option 1: Full Docker Development

**Start services and view logs:**
```bash
docker compose up -d && docker compose logs -f
```

**Stop services:**
```bash
docker compose down
```

### Option 2: Hybrid Development

**Start supporting services:**
```bash
docker compose up -d db pharmcat pypgx gatk-api fhir-server
```

**Run FastAPI app locally:**
```bash
# Install dependencies
uv pip install -e .

# Run development server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Option 3: Minimal Development (NEEDS REVIEW)

**Start only database:**
```bash
docker compose up -d db
```

**Run everything locally:**
```bash
# Install all dependencies
uv pip install -e .
uv pip install -r requirements-dev.txt

# Run services
python -m app.services.pharmcat_service &
python -m app.services.pypgx_service &
python -m app.services.gatk_service &
uvicorn app.main:app --reload
```

## Development Workflow

### 1. Code Changes

**Make changes to Python code:**
- Changes are automatically reloaded with `--reload` flag
- Restart services if needed: `docker compose restart app`

**Make changes to Docker services:**
- Rebuild specific service: `docker compose up -d --build pharmcat`
- Rebuild all services: `docker compose up -d --build`

### 2. Database Changes

**Run migrations:**
```bash
# Using Docker
docker compose exec app alembic upgrade head

# Local development
alembic upgrade head
```

**Create new migration:**
```bash
# Using Docker
docker compose exec app alembic revision --autogenerate -m "Description"

# Local development
alembic revision --autogenerate -m "Description"
```

### 3. Testing

**Run tests:**

### 4. Code Quality

**Format code:**
```bash
black app/
isort app/
```

**Lint code:**
```bash
flake8 app/
mypy app/
```

**Type checking:**
```bash
mypy app/
```

## Service Development

### FastAPI App Development

**Project structure:**
```
app/
├── api/                 # API routes and models
│   ├── routes/         # Route handlers
│   ├── utils/ 
│   ├── models.py       # Pydantic models
│   └── db.py          # Database utilities
├── pharmcat/          # PharmCAT integration
├── reports/            # Report generation
├── services/           # Background services
└── templates/
└── utils/
└── visualizations/     # Kroki + mermaid diagrams
```

**Adding new endpoints:**
1. Create route in `app/api/routes/`
2. Add Pydantic models in `app/api/models.py`
3. Register route in `app/main.py`
4. Add tests in `tests/api/`

**Example new route:**
```python
# app/api/routes/new_feature.py
from fastapi import APIRouter, Depends
from app.api.models import NewFeatureResponse
from app.api.db import get_db

router = APIRouter(prefix="/new-feature", tags=["new-feature"])

@router.get("/", response_model=NewFeatureResponse)
async def get_new_feature(db: Session = Depends(get_db)):
    return NewFeatureResponse(message="Hello World")
```

### Service Integration

**Adding new service:**
1. Create service directory in `docker/`
2. Add Dockerfile and configuration
3. Update `docker-compose.yml`
4. Add service client in `app/services/`
5. Update environment variables

**Example service client:**
```python
# app/services/new_service_client.py
import httpx
from app.core.config import settings

class NewServiceClient:
    def __init__(self):
        self.base_url = settings.NEW_SERVICE_URL
        self.client = httpx.AsyncClient()
    
    async def call_service(self, data: dict):
        response = await self.client.post(
            f"{self.base_url}/analyze",
            json=data
        )
        return response.json()
```

## Database Development

### Database Schema

**Schema organization:**
- `public`: Core application tables
- `cpic`: CPIC guidelines and data
- `fhir`: FHIR resources
- `user_data`: User and patient data
- `reports`: Generated reports metadata

**Adding new tables:**
1. Create SQLAlchemy model in `app/api/models.py`
2. Generate migration: `alembic revision --autogenerate`
3. Review migration file
4. Apply migration: `alembic upgrade head`

**Example model:**
```python
# app/api/models.py
from sqlalchemy import Column, Integer, String, DateTime
from app.api.db import Base

class NewTable(Base):
    __tablename__ = "new_table"
    
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
```

### Database Migrations

**Migration workflow:**
1. Modify SQLAlchemy models
2. Generate migration: `alembic revision --autogenerate -m "Description"`
3. Review generated migration
4. Apply migration: `alembic upgrade head`
5. Test migration with sample data

**Rollback migration:**
```bash
alembic downgrade -1
```

## Frontend Development

### Web UI Development

**Frontend structure:**
```
app/templates/
├── index.html          # Main page
├── static/             # Static assets
│   ├── css/           # Stylesheets
│   ├── js/            # JavaScript
│   └── images/        # Images
└── components/         # Reusable components
```

**Adding new features:**
1. Create HTML template
2. Add JavaScript functionality
3. Add CSS styling
4. Update navigation
5. Test in browser

### API Integration

**Frontend API calls:**
```javascript
// Upload file
async function uploadFile(file, sampleId) {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('sample_identifier', sampleId);
    
    const response = await fetch('/upload/genomic-data', {
        method: 'POST',
        body: formData
    });
    
    return await response.json();
}

// Check status
async function checkStatus(jobId) {
    const response = await fetch(`/upload/status/${jobId}`);
    return await response.json();
}
```

## Testing Development

### Test Structure

**Test organization:**

**Writing tests:**


### Test Data


## Debugging

### Debugging Tools

**Docker debugging:**
```bash
# Debug specific container
docker compose exec app python -m pdb app/main.py

# View container logs
docker compose logs -f app

# Access container shell
docker compose exec app bash
```

### Logging

**Development logging:**
```python
import logging

logger = logging.getLogger(__name__)

logger.debug("Debug message")
logger.info("Info message")
logger.warning("Warning message")
logger.error("Error message")
```

**Log configuration:**
```python
# app/core/logging.py
import logging
import sys

def setup_logging():
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('app.log')
        ]
    )
```

## Performance Development

### Profiling

**Python profiling:**
```python
import cProfile
import pstats

# Profile specific function
cProfile.run('my_function()', 'profile_output.prof')

# Analyze results
p = pstats.Stats('profile_output.prof')
p.sort_stats('cumulative').print_stats(10)
```

**Memory profiling:**
```python
from memory_profiler import profile

@profile
def my_function():
    # Function code here
    pass
```

### Optimization

**Database optimization:**
- Use database indexes
- Optimize queries
- Use connection pooling
- Monitor query performance

**API optimization:**
- Use async/await
- Implement caching
- Optimize serialization
- Use background tasks

## Deployment Development

### Local Production Testing

**Test production configuration:**
```bash
# Use production environment
cp .env.production .env

# Start with production settings
docker compose up -d --build

# Test production features
curl http://localhost:8765/health
```

### Container Development

**Build specific container:**
```bash
# Build app container
docker build -t zaro-pgx-app:dev ./docker/app

# Run with custom image
docker compose up -d --build app
```

**Multi-stage builds:**
```dockerfile
# Development stage
FROM python:3.12-slim as development
COPY requirements-dev.txt .
RUN uv pip install -r requirements-dev.txt

# Production stage
FROM python:3.12-slim as production
COPY requirements.txt .
RUN uv pip install -r requirements.txt
```

## Next Steps

- **Architecture Overview**: {doc}`architecture`
- **API Reference**: {doc}`api-reference`
- **Contributing**: {doc}`contributing`
- **Deployment**: {doc}`deployment`
