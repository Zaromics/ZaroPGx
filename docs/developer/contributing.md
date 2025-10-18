---
title: Contributing Guide
---
# *[CURATION NEEDED]*

# Contributing Guide

How to contribute to ZaroPGx development.

## Getting Started

### Prerequisites

- Git
- Docker and Docker Compose
- Python 3.12+
- Basic understanding of pharmacogenomics
- Familiarity with FastAPI and SQLAlchemy

### Development Setup

1. **Fork the repository** on GitHub
2. **Clone your fork:**
   ```bash
   git clone https://github.com/your-username/ZaroPGx.git
   cd ZaroPGx
   ```

3. **Set up development environment:**
   ```bash
   cp .env.local .env
   docker compose up -d --build
   ```

4. **Verify setup:**
   ```bash
   curl http://localhost:8765/health
   ```

## Contribution Types

### Bug Reports

**Before reporting a bug:**
- Check existing issues
- Search closed issues
- Test with latest version
- Gather relevant information

**Bug report template:**
```markdown
## Bug Description
Brief description of the bug

## Steps to Reproduce
1. Step one
2. Step two
3. Step three

## Expected Behavior
What should happen

## Actual Behavior
What actually happens

## Environment
- OS: [e.g., Ubuntu 20.04]
- Docker version: [e.g., 20.10.7]
- ZaroPGx version: [e.g., 1.0.0]

## Additional Context
Screenshots, logs, or other relevant information
```

### Feature Requests

**Before requesting a feature:**
- Check existing feature requests
- Consider if it fits the project scope
- Think about implementation complexity
- Consider backward compatibility

**Feature request template:**
```markdown
## Feature Description
Brief description of the feature

## Use Case
Why is this feature needed?

## Proposed Solution
How should this feature work?

## Alternatives Considered
What other approaches were considered?

## Additional Context
Mockups, examples, or references
```

### Code Contributions

**Types of contributions:**
- Bug fixes
- New features
- Documentation improvements
- Performance optimizations
- Test coverage improvements

## Development Workflow

### 1. Create Feature Branch

```bash
# Update main branch
git checkout main
git pull origin main

# Create feature branch
git checkout -b feature/your-feature-name
```

**Branch naming conventions:**
- `feature/description`: New features
- `bugfix/description`: Bug fixes
- `docs/description`: Documentation
- `refactor/description`: Code refactoring
- `test/description`: Test improvements

### 2. Make Changes

**Code style guidelines:**
- Follow PEP 8 for Python code
- Use type hints
- Write docstrings for functions
- Use meaningful variable names
- Keep functions small and focused

**Example code style:**
```python
from typing import List, Optional
from sqlalchemy.orm import Session

def process_genomic_data(
    file_path: str,
    db: Session,
    sample_identifier: Optional[str] = None
) -> dict:
    """
    Process genomic data file and return analysis results.
    
    Args:
        file_path: Path to the genomic data file
        db: Database session
        sample_identifier: Optional sample identifier
        
    Returns:
        Dictionary containing analysis results
        
    Raises:
        ValueError: If file format is not supported
    """
    # Implementation here
    pass
```

### 3. Write Tests

**Test requirements:**
- Write tests for new functionality
- Maintain test coverage above 80%
- Use descriptive test names
- Test both success and failure cases

**Example test:**
```python
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_upload_genomic_data_success():
    """Test successful genomic data upload."""
    with open("test_data/sample.vcf", "rb") as f:
        response = client.post(
            "/upload/genomic-data",
            files={"file": f},
            data={"sample_identifier": "test_sample"}
        )
    
    assert response.status_code == 200
    assert "job_id" in response.json()
    assert response.json()["status"] == "uploaded"

def test_upload_genomic_data_invalid_file():
    """Test upload with invalid file format."""
    with open("test_data/invalid.txt", "rb") as f:
        response = client.post(
            "/upload/genomic-data",
            files={"file": f}
        )
    
    assert response.status_code == 400
    assert "error" in response.json()
```

### 4. Update Documentation

**Documentation requirements:**
- Update relevant documentation
- Add docstrings for new functions
- Update API documentation
- Add examples for new features

**Documentation types:**
- Code comments and docstrings
- API documentation
- User guides
- Developer guides
- README updates

### 5. Run Tests and Linting

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=html

# Format code
black app/
isort app/

# Lint code
flake8 app/
mypy app/
```

### 6. Commit Changes

```bash
# Stage changes
git add .

# Commit with descriptive message
git commit -m "feat: add support for CRAM file processing

- Add CRAM file detection and validation
- Implement GATK preprocessing for CRAM files
- Add tests for CRAM processing workflow
- Update documentation with CRAM support"
```

**Commit message format:**
```
type(scope): description

Longer description if needed

- Bullet point 1
- Bullet point 2

Closes #123
```

**Commit types:**
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `style`: Code style changes
- `refactor`: Code refactoring
- `test`: Test additions/changes
- `chore`: Maintenance tasks

### 7. Push and Create Pull Request

```bash
# Push branch
git push origin feature/your-feature-name

# Create pull request on GitHub
```

**Pull request template:**
```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
- [ ] Tests pass locally
- [ ] New tests added
- [ ] All tests pass in CI

## Checklist
- [ ] Code follows style guidelines
- [ ] Self-review completed
- [ ] Documentation updated
- [ ] No breaking changes
```

## Code Review Process

### Review Criteria

**Code quality:**
- Follows coding standards
- Has appropriate tests
- Handles errors gracefully
- Is well-documented

**Functionality:**
- Solves the intended problem
- Doesn't break existing functionality
- Is performant
- Is secure

**Documentation:**
- Code is self-documenting
- Docstrings are present
- README is updated if needed
- API docs are updated

### Review Process

1. **Automated checks** must pass
2. **At least one reviewer** must approve
3. **All conversations** must be resolved
4. **CI/CD pipeline** must pass
5. **Maintainer approval** for significant changes

### Responding to Reviews

**Be responsive:**
- Address feedback promptly
- Ask questions if unclear
- Explain your reasoning
- Be open to suggestions

**Common responses:**
- "Done" - Simple fix applied
- "Good catch, fixed" - Bug found and fixed
- "I disagree because..." - Explain reasoning
- "Can you clarify..." - Ask for more details

## Testing Guidelines

### Test Types

**Unit tests:**
- Test individual functions
- Mock external dependencies
- Test edge cases
- Aim for high coverage

**Integration tests:**
- Test service interactions
- Use test database
- Test API endpoints
- Test error handling

**End-to-end tests:**
- Test complete workflows
- Use real data (anonymized)
- Test user scenarios
- Validate outputs

### Test Data

**Test data requirements:**
- Use anonymized data only
- Include edge cases
- Cover different file formats
- Include invalid data

**Test data organization:**
```
tests/
├── fixtures/           # Test fixtures
├── data/              # Test data files
│   ├── valid/         # Valid test files
│   ├── invalid/       # Invalid test files
│   └── edge_cases/    # Edge case files
└── conftest.py        # Test configuration
```

## Documentation Guidelines

### Code Documentation

**Docstring format:**
```python
def process_file(file_path: str, options: dict) -> dict:
    """
    Process a genomic file and return analysis results.
    
    This function handles file validation, preprocessing,
    and analysis orchestration.
    
    Args:
        file_path: Path to the genomic file to process
        options: Dictionary of processing options
        
    Returns:
        Dictionary containing analysis results with keys:
        - status: Processing status
        - results: Analysis results
        - metadata: File metadata
        
    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If file format is not supported
        
    Example:
        >>> results = process_file("sample.vcf", {"reference": "hg38"})
        >>> print(results["status"])
        "completed"
    """
```

### API Documentation

**Endpoint documentation:**
```python
@router.post("/upload/genomic-data", response_model=UploadResponse)
async def upload_genomic_data(
    files: List[UploadFile] = File(...),
    sample_identifier: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """
    Upload genomic data files for pharmacogenomic analysis.
    
    This endpoint accepts various genomic file formats (VCF, BAM, CRAM, SAM, FASTQ)
    and initiates the Nextflow-based processing pipeline.
    
    Args:
        files: List of genomic data files to upload
        sample_identifier: Optional patient/sample identifier
        db: Database session dependency
        
    Returns:
        UploadResponse containing job information
        
    Raises:
        HTTPException: If upload fails or file format is invalid
        
    Example:
        ```bash
        curl -X POST \\
          -F "file=@sample.vcf" \\
          -F "sample_identifier=patient_001" \\
          http://localhost:8765/upload/genomic-data
        ```
    """
```

## Release Process

### Version Numbering

**Semantic versioning:**
- `MAJOR.MINOR.PATCH`
- `MAJOR`: Breaking changes
- `MINOR`: New features (backward compatible)
- `PATCH`: Bug fixes (backward compatible)

**Examples:**
- `1.0.0`: Initial release
- `1.1.0`: New features added
- `1.1.1`: Bug fixes
- `2.0.0`: Breaking changes

### Release Checklist

**Before release:**
- [ ] All tests pass
- [ ] Documentation updated
- [ ] Version numbers updated
- [ ] CHANGELOG updated
- [ ] Release notes prepared

**Release process:**
1. Update version in `pyproject.toml`
2. Update `CHANGELOG.md`
3. Create release branch
4. Tag release
5. Create GitHub release
6. Update documentation

## Community Guidelines

### Code of Conduct

**Be respectful:**
- Use welcoming language
- Be respectful of differing viewpoints
- Accept constructive criticism
- Focus on what's best for the community

**Be collaborative:**
- Help others learn
- Share knowledge
- Be patient with newcomers
- Work together constructively

### Getting Help

**Resources:**
- GitHub Discussions
- Issue tracker
- Documentation
- Code comments

**Asking questions:**
- Search existing issues first
- Provide context and examples
- Be specific about the problem
- Include relevant logs or error messages

## Recognition

### Contributors

**Contributor recognition:**
- Listed in CONTRIBUTORS.md
- Mentioned in release notes
- GitHub contributor status
- Community recognition

**Types of contributions:**
- Code contributions
- Documentation improvements
- Bug reports
- Feature requests
- Community support

## Next Steps

- **Development Setup**: {doc}`development-setup`
- **Architecture**: {doc}`architecture`
- **API Reference**: {doc}`api-reference`
- **Deployment**: {doc}`deployment`
