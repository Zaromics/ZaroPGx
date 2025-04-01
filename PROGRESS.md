# ZaroPGx Project Progress

## Overview
ZaroPGx is a pharmacogenomics analysis platform using Docker-based microservices architecture. The system processes genetic data (VCF files) to generate personalized medication recommendations based on patient genotypes.

## Project Status
- **Current Phase**: Phase 1 Implementation - Core Gene Groups
- **Last Updated**: March 30, 2025

## Components & Services

### 1. Core Services

| Service | Status | Description | Issues/Next Steps |
|---------|--------|-------------|------------------|
| **Main App (FastAPI)** | 🟢 Working | Main application handling UI, reports, user auth | New user-friendly web UI added |
| **PostgreSQL Database** | 🟢 Working | Stores CPIC guidelines, user data, reports | Gene grouping schema implemented |
| **PharmCAT Service** | 🟢 Working | Using official pgkb/pharmcat Docker image | Now running with proper command |
| **PharmCAT Wrapper** | 🟢 Working | Python wrapper providing REST API for PharmCAT | API accessible via port 5001 |
| **Aldy Service** | 🟡 Enhanced | Multi-gene genotype caller | Updated to support multiple PGx genes |

### 2. Docker Infrastructure

| Component | Status | Description | Issues/Next Steps |
|-----------|--------|-------------|------------------|
| **Docker Compose** | 🟢 Working | Multi-container orchestration | Core services up and running |
| **Network Config** | 🟢 Working | PGX-Network for service communication | Services communicating successfully |
| **Volumes** | 🟢 Working | Data persistence and sharing | Using volume mounts for JAR sharing |
| **Health Checks** | 🟡 In Progress | Container health monitoring | Temporarily simplified for debugging |

### 3. Features & Capabilities

| Feature | Status | Description | Issues/Next Steps |
|---------|--------|-------------|------------------|
| **VCF Processing** | 🟢 Working | Upload and analysis of genetic data | Basic UI form for uploading VCF files |
| **Genotype Extraction** | 🟡 Enhanced | Star allele calling from genetic data | Now supports multiple genes and groups |
| **Report Generation** | 🟢 Working | PDF reports for clinical use | Templates implemented with WeasyPrint |
| **User Authentication** | 🟡 Basic | Token-based authentication | Need real user database |
| **API Endpoints** | 🟢 Working | REST API for service interactions | API documentation accessible at /docs |
| **Web UI** | 🟢 Working | User-friendly interface | Simple Bootstrap UI for file uploads |
| **Gene Group Analysis** | 🟡 In Progress | Analyzing genes by functional groups | DB schema created, UI updates needed |

## Recent Changes

1. **Phase 1 Implementation**: 
   - Created database schema for gene groups and relationships
   - Enhanced Aldy wrapper to support multiple genes and gene grouping
   - Added gene group and multi-gene analysis endpoints
   - Updated Dockerfile to support additional gene definitions

2. **Major Enhancement**: 
   - Extended Aldy wrapper to support 25+ pharmacogenomic genes
   - Implemented gene grouping by functional pathway (CYP450, Phase II, etc.)
   - Added `/multi_genotype` endpoint for analyzing multiple genes in one request
   - Added support for gene group-based analysis

3. **Database Schema Update**:
   - Added `gene_groups` table for categorizing genes by function
   - Created `gene_group_members` table for gene-group relationships
   - Implemented functions for dynamic gene group membership

4. **Service Integration Fixes**:
   - Fixed service connection issues between main app and PharmCAT wrapper
   - Updated PharmCAT wrapper to use /genotype endpoint for processing
   - Fixed URL naming in Docker Compose networking
   - Properly passed environment variables between containers

5. **Report Generation Implementation**:
   - Created PDF report template with clean styling
   - Implemented interactive HTML report with visualizations
   - Added proper error handling for PDF generation
   - Created fallback mechanisms for report generation errors
   
6. **Sample Data Integration**:
   - Added sample VCF files for CYP2C19 and CYP2D6 genes
   - Created test data for verifying the pipeline
   - Implemented VCF upload and processing workflow

7. **Major Update**: Added user-friendly web interface:
   - Created Bootstrap-based web UI
   - Added file upload form for VCF processing
   - Added service status display
   - Implemented Jinja2 templates in FastAPI

8. **GATK API Enhancements**: 
   - Implemented intelligent reference genome detection from file headers
   - Added support for handling non-human contigs (viral, mitochondrial)
   - Enhanced progress tracking with real-time memory usage monitoring
   - Improved error handling and automatic retry logic for contig issues
   - Added comprehensive diagnostic endpoint for system monitoring

9. **Reference Genome Handling**:
   - Added automatic detection of reference genome from file headers
   - Implemented fallback mechanisms for reference detection
   - Added support for both hg38 and hg19 reference genomes
   - Enhanced error reporting for reference genome mismatches

10. **Memory Management**:
   - Implemented dynamic memory allocation based on input file size
   - Added real-time memory usage tracking during GATK execution
   - Optimized Java memory settings for large file processing
   - Added memory usage reporting in job status updates

11. **Job Status Tracking**:
   - Enhanced progress reporting with chromosome-level tracking
   - Added detailed memory usage information to status updates
   - Improved error reporting and recovery mechanisms
   - Added support for tracking non-human contigs in job results

## Current Challenges

1. **Gene Definition Files**: 
   - Aldy requires specific gene definition files for each supported gene
   - Not all genes have readily available definition files in the Aldy repository
   - Need to create or adapt definition files for some genes

2. **System Robustness**:
   - Need to improve error handling for gene-specific analysis failures 
   - Add comprehensive input validation for file uploads
   - Implement better monitoring and logging
   - Complete abstraction layer for gene callers

3. **Database Integration**:
   - Connect the enhanced gene analysis with PostgreSQL for storing results
   - Implement proper patient data storage and retrieval
   - Update report generation to include gene group information

4. **Memory Management**:
   - Monitor and optimize memory usage for large genomic files
   - Implement better cleanup of temporary files
   - Add memory usage alerts for system administrators

5. **Reference Genome Handling**:
   - Ensure consistent reference genome usage across all tools
   - Add validation for reference genome compatibility
   - Implement reference genome conversion tools if needed

6. **GATK Contig Handling**:
   - Fix contig exclusion logic for non-human sequences (e.g., EBV)
   - Implement proper interval list handling for excluded contigs
   - Add validation for contig presence in reference genome
   - Improve error recovery for contig-related issues

## Upcoming Tasks

1. **Immediate Next Steps**:
   - Verify all Aldy gene definition files are correctly loaded
   - Test multi-gene analysis with sample VCF files
   - Update report templates to display gene groups
   - Integrate gene group analysis with main application

2. **Phase 1 Completion**:
   - Test and verify CYP450 enzymes functionality
   - Test and verify Phase II enzymes functionality
   - Test and verify Drug Transporter functionality
   - Update UI to display gene group analysis results

3. **GATK Integration Improvements**:
   - Add support for additional GATK tools and workflows
   - Implement parallel processing for large files
   - Add support for GATK best practices pipeline
   - Enhance progress reporting with more detailed metrics
   - Fix contig exclusion mechanism using proper interval lists
   - Add pre-processing step to validate contigs against reference
   - Implement proper handling of viral and mitochondrial sequences

4. **Report Enhancements**: 
   - Group results by gene family in reports
   - Add gene group-specific visualizations
   - Add pathway-based color coding and contextual information
   - Include non-human contig information in reports

5. **API Documentation**: 
   - Document new multi-gene and group-based endpoints
   - Create usage examples for gene group analysis

6. **Testing Scripts**: 
   - Create testing scripts for multi-gene analysis
   - Create testing scripts for gene group functionality

## Implementation Plan for Phase 1

1. **Database Schema** ✅
   - Create gene_groups table
   - Create gene_group_members table
   - Add indexing for performance
   - Implement helper functions

2. **Aldy Service Enhancement** ✅
   - Update aldy_wrapper.py to support multiple genes
   - Add gene group information to API responses
   - Create multi-gene analysis endpoint
   - Add gene group-based analysis capability

3. **Docker Configuration Update** ✅
   - Modify Dockerfile to include additional gene definitions
   - Update container build process

4. **Main Application Integration** 🔄
   - Update routes to use enhanced Aldy capabilities
   - Modify report generation to include gene groups
   - Add UI elements for gene group selection

5. **Testing** 🔜
   - Test multi-gene analysis with sample VCF files
   - Verify gene grouping in reports
   - Test performance with large VCF files

## Notes

- Using Docker version 28.0.4
- Using official PharmCAT Docker image (pgkb/pharmcat)
- Directly calling PharmCAT JAR file as per documentation: https://pharmcat.org/using/Running-PharmCAT/
- Python 3.10 for Flask/FastAPI services
- PostgreSQL 15 for database
- The FastAPI application is accessible at http://localhost:8765
- API documentation is available at http://localhost:8765/docs
- Web UI is now available at http://localhost:8765 

## Expanded Roadmap: Comprehensive Pharmacogenomic Coverage

### Phase 1: Core Gene Groups Implementation ⏱️ In Progress
- Implement database schema for gene grouping ✅
- Modify Aldy service to support multiple genes ✅
- Group genes by functional pathways ✅:
  - **CYP450 Enzymes**: CYP2D6, CYP2C19, CYP2C9, CYP2C8, CYP2B6, CYP3A4/5
  - **Phase II Enzymes**: UGT1A1, NAT1/2, TPMT, DPYD
  - **Drug Transporters**: SLCO1B1
  - **Drug Targets**: VKORC1

### Phase 2: Extended Gene Coverage
- Add support for additional genes currently available in Aldy:
  - CYP1A2, CYP2A6, CYP2E1, CYP3A7, CYP4F2
  - NUDT15, GSTM1, GSTP1, ABCG2
  - CACNA1S, RYR1, CFTR, G6PD, IFNL3, UGT2B7

### Phase 3: Abstracted Caller Architecture
- Create caller abstraction layer to support multiple genotyping engines
- Support for neurotransmitter and vitamin metabolism genes:
  - COMT (via Aldy)
  - MAOA, MTHFR (via custom or third-party callers)
- Allow integration of custom or third-party genotype callers

### Phase 4: Advanced Clinical Reports
- Enhanced clinical decision support
- Interactive pathway visualizations
- Integration with drug interaction databases
- Phenotype prediction based on multi-gene interactions 

## VCF Upload Enhancements

For genomic analyses that require indexed VCF files (like Aldy):

1. Add option for users to upload both VCF and index files:
   - Primary upload slot for VCF file (required)
   - Secondary upload slot for index file (.tbi or .csi, optional)

2. If user doesn't provide an index file, implement automatic indexing:
   - Use bgzip to compress the VCF if not already compressed
   - Generate index with tabix
   - Display progress indicator during indexing for large files

3. Handle errors gracefully:
   - Validate that uploaded index matches the VCF file
   - Provide clear error messages if indexing fails
   - Include informative documentation on index file requirements

## Reference Genome Selection

For accurate pharmacogenomic analysis:

1. Add reference genome selection to the user interface:
   - Default to hg38 (GRCh38) as the most current reference
   - Provide option to select hg19/GRCh37 for backward compatibility
   - Clearly indicate which reference was used in the report

2. Implement intelligent reference genome detection:
   - Auto-detect from VCF header by searching for "GRCh38", "hg38", "GRCh37", or "hg19"
   - Parse GATK command line or other header information for reference path
   - Provide clear indication when auto-detection is used and what was detected

3. Ensure all analysis tools receive the correct reference genome parameter:
   - Pass `--genome` parameter to Aldy for star allele calling
   - Configure PharmCAT with the appropriate reference
   - Add validation to check VCF headers for genome build indicators

4. Document reference genome recommendations:
   - Include warnings about potential discrepancies when using incorrect reference
   - Provide guidance on converting between reference builds if needed

## Phase 1 Implementation Status (Updated)

### Completed:
- ✅ Created database schema for gene groups and gene-group relationships
- ✅ Added CYP450 enzymes and other PGx gene groups to database
- ✅ Updated Aldy service to support multiple genes and gene groups
- ✅ Implemented Aldy 4.x integration with proper indexing and genome detection
- ✅ Successfully tested CYP2D6 genotyping with sample VCF file
- ✅ Added proper reference genome detection from VCF headers
- ✅ Integrated comprehensive VCF file validation and indexing

### Current Results:
- CYP2D6 genotyping successfully produces *1/*4.021 calls with 100% confidence
- Detailed variant information available for clinical interpretation
- Phenotype predictions available (normal function / no function alleles)

### Next Steps:
- Integrate Aldy genotyping results with API endpoints for the frontend
- Update PharmCAT wrapper to use the same VCF processing pipeline
- Implement drug recommendation engine based on multiple gene results
- Add remaining SNPs and group star alleles by enzyme family
- Design comprehensive report templates using newly available data
- Create user interface for VCF upload with indexing capabilities

## Completed Tasks

### Infrastructure
- [x] Set up Docker Compose configuration with all required services
- [x] Configured network communication between services
- [x] Set up volume mounts for data persistence
- [x] Implemented health checks for all services
- [x] Configured proper service dependencies and startup order

### Authentication & Security
- [x] Implemented JWT-based authentication
- [x] Added token refresh mechanism
- [x] Secured API endpoints with authentication
- [x] Added proper session handling

### File Processing
- [x] Implemented file upload with progress tracking
- [x] Added file type detection and validation
- [x] Implemented VCF header analysis
- [x] Added support for compressed files
- [x] Implemented proper file cleanup

### Analysis Pipeline
- [x] Set up GATK service integration
- [x] Configured Stargazer service
- [x] Set up PharmCAT service
- [x] Implemented proper error handling between services
- [x] Added progress tracking across all stages

### Frontend
- [x] Created responsive UI with Bootstrap
- [x] Implemented real-time progress updates
- [x] Added file analysis display
- [x] Implemented proper error handling and user feedback
- [x] Added service status indicators

## In Progress / Needs Fixing

### Frontend Issues
- [ ] Fix progress monitoring after PharmCAT completion
  - [ ] Ensure proper display of results after analysis
  - [ ] Fix token refresh mechanism during long-running operations
  - [ ] Improve error handling for connection timeouts

### Service Integration
- [ ] Improve error handling between services
  - [ ] Add better error messages for service failures
  - [ ] Implement retry mechanisms for failed service calls
  - [ ] Add proper cleanup on service failures

### Performance
- [ ] Optimize file processing pipeline
  - [ ] Implement parallel processing where possible
  - [ ] Add caching for frequently accessed data
  - [ ] Optimize memory usage during analysis

### Documentation
- [ ] Add API documentation
  - [ ] Document all endpoints
  - [ ] Add request/response examples
  - [ ] Document error codes and handling

### Testing
- [ ] Add comprehensive test suite
  - [ ] Unit tests for core functionality
  - [ ] Integration tests for service communication
  - [ ] End-to-end tests for complete workflow

## Next Steps

1. Fix the progress monitoring and results display after PharmCAT completion
2. Implement proper error handling and recovery mechanisms
3. Add comprehensive logging for debugging
4. Improve the user experience with better feedback
5. Add proper cleanup mechanisms for failed jobs

## Known Issues

1. Progress monitoring stops after PharmCAT completion
2. Results not displaying properly after analysis
3. Token refresh mechanism needs improvement
4. Service health checks could be more robust
5. Error handling between services needs improvement

## Notes

- The basic infrastructure and service integration is working
- Authentication and security are properly implemented
- The main issue currently is with the progress monitoring and results display
- Need to focus on improving error handling and user feedback
- Consider adding more detailed logging for debugging