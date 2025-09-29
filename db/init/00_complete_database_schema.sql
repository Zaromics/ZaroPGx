-- Complete ZaroPGx Database Schema
-- This is the single source of truth for database initialization
-- Replaces all other migration files and consolidates everything

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
-- Note: PostGIS extension not available in standard PostgreSQL container
-- CREATE EXTENSION IF NOT EXISTS "postgis" CASCADE;

-- ============================================================================
-- CPIC SCHEMA - Pharmacogenomic guidelines and reference data
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS cpic;

-- Create guidelines table
CREATE TABLE cpic.guidelines (
    guideline_id SERIAL PRIMARY KEY,
    gene VARCHAR(20) NOT NULL,
    drug VARCHAR(100) NOT NULL,
    allele_combination JSONB,
    recommendation TEXT,
    activity_score FLOAT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create genes table
CREATE TABLE cpic.genes (
    gene_id SERIAL PRIMARY KEY,
    gene_symbol VARCHAR(20) NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    chromosome VARCHAR(5),
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create alleles table
CREATE TABLE cpic.alleles (
    allele_id SERIAL PRIMARY KEY,
    gene_id INTEGER REFERENCES cpic.genes(gene_id),
    allele_name VARCHAR(50) NOT NULL,
    function_status VARCHAR(50),
    activity_score FLOAT,
    clinical_significance TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create drugs table
CREATE TABLE cpic.drugs (
    drug_id SERIAL PRIMARY KEY,
    drug_name VARCHAR(100) NOT NULL UNIQUE,
    drug_class VARCHAR(100),
    atc_code VARCHAR(10),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create gene-drug relationships table
CREATE TABLE cpic.gene_drug_interactions (
    interaction_id SERIAL PRIMARY KEY,
    gene_id INTEGER REFERENCES cpic.genes(gene_id),
    drug_id INTEGER REFERENCES cpic.drugs(drug_id),
    guideline_id INTEGER REFERENCES cpic.guidelines(guideline_id),
    strength_of_evidence VARCHAR(20),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(gene_id, drug_id)
);

-- ============================================================================
-- USER_DATA SCHEMA - Patient and genetic data (HIPAA-compliant)
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS user_data;

-- Create patients table with UUID primary key
CREATE TABLE user_data.patients (
    patient_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_identifier VARCHAR(255) UNIQUE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create genetic_data table with UUID primary key
CREATE TABLE user_data.genetic_data (
    data_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id UUID REFERENCES user_data.patients(patient_id),
    file_type VARCHAR(20) NOT NULL,
    file_path TEXT NOT NULL,
    is_supplementary BOOLEAN DEFAULT FALSE,
    parent_data_id UUID REFERENCES user_data.genetic_data(data_id),
    processed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create patient_alleles table with UUID primary key
CREATE TABLE user_data.patient_alleles (
    patient_allele_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id UUID REFERENCES user_data.patients(patient_id),
    gene_id INTEGER REFERENCES cpic.genes(gene_id),
    diplotype VARCHAR(255),
    phenotype VARCHAR(255),
    activity_score DECIMAL(5,2),
    confidence_score DECIMAL(5,2),
    calling_method VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- REPORTS SCHEMA - Generated reports and outputs
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS reports;

-- Create reports table
CREATE TABLE reports.patient_reports (
    report_id SERIAL PRIMARY KEY,
    patient_id UUID REFERENCES user_data.patients(patient_id),
    report_type VARCHAR(50),
    report_path TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- JOB_MONITORING SCHEMA - Workflow and job tracking
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS job_monitoring;

-- Main job status table
CREATE TABLE job_monitoring.jobs (
    job_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id UUID REFERENCES user_data.patients(patient_id),
    file_id UUID REFERENCES user_data.genetic_data(data_id),
    status VARCHAR(50) NOT NULL CHECK (status IN ('pending', 'processing', 'completed', 'failed', 'cancelled')),
    stage VARCHAR(50) NOT NULL CHECK (stage IN (
        'upload_start', 'header_inspection', 'upload_complete',
        'gatk_conversion', 'hla_typing', 'fastq_conversion',
        'pypgx_analysis', 'pypgx_bam2vcf', 'pharmcat_analysis',
        'workflow_diagram', 'report_generation', 'complete',
        'upload', 'analysis', 'gatk', 'pypgx', 'pharmcat', 'report'
    )),
    progress INTEGER DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
    message TEXT,
    error_message TEXT,
    job_metadata JSONB DEFAULT '{}',
    started_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE,
    timeout_at TIMESTAMP WITH TIME ZONE,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Job stage history
CREATE TABLE job_monitoring.job_stages (
    stage_id SERIAL PRIMARY KEY,
    job_id UUID REFERENCES job_monitoring.jobs(job_id) ON DELETE CASCADE,
    stage VARCHAR(50) NOT NULL,
    status VARCHAR(50) NOT NULL CHECK (status IN ('started', 'completed', 'failed', 'skipped')),
    progress INTEGER DEFAULT 0,
    message TEXT,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE,
    duration_ms INTEGER,
    stage_metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Job events
CREATE TABLE job_monitoring.job_events (
    event_id SERIAL PRIMARY KEY,
    job_id UUID REFERENCES job_monitoring.jobs(job_id) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL,
    message TEXT NOT NULL,
    event_metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Job dependencies
CREATE TABLE job_monitoring.job_dependencies (
    dependency_id SERIAL PRIMARY KEY,
    job_id UUID REFERENCES job_monitoring.jobs(job_id) ON DELETE CASCADE,
    depends_on_job_id UUID REFERENCES job_monitoring.jobs(job_id) ON DELETE CASCADE,
    dependency_type VARCHAR(50) DEFAULT 'sequential',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- WORKFLOW MONITORING SCHEMA - Enhanced workflow tracking system
-- ============================================================================

-- ============================================================================
-- FHIR SCHEMA - HAPI FHIR server tables
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS fhir;

-- Grant all necessary permissions for HAPI FHIR to create its own tables
GRANT ALL PRIVILEGES ON SCHEMA fhir TO cpic_user;
GRANT CREATE ON SCHEMA fhir TO cpic_user;
GRANT USAGE ON SCHEMA fhir TO cpic_user;

-- Set default privileges for future tables that HAPI will create
ALTER DEFAULT PRIVILEGES IN SCHEMA fhir GRANT ALL ON TABLES TO cpic_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA fhir GRANT ALL ON SEQUENCES TO cpic_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA fhir GRANT ALL ON FUNCTIONS TO cpic_user;

-- ============================================================================
-- GENE GROUPS SCHEMA - Gene categorization for UI
-- ============================================================================
-- Gene Groups table for categorizing genes by function
CREATE TABLE gene_groups (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE,
    description TEXT,
    clinical_relevance TEXT,
    display_order INTEGER NOT NULL DEFAULT 0,
    color_code VARCHAR(7) -- Hex color code for UI display
);

-- Gene to Group relationship table
CREATE TABLE gene_group_members (
    id SERIAL PRIMARY KEY,
    gene_symbol VARCHAR(20) NOT NULL,
    group_id INTEGER REFERENCES gene_groups(id),
    description TEXT,
    primary_group BOOLEAN DEFAULT false, -- Whether this is the primary group for the gene
    UNIQUE(gene_symbol, group_id)
);

-- ============================================================================
-- GENOMIC FILE HEADERS - File metadata storage
-- ============================================================================
-- Create genomic_file_headers table in public schema
CREATE TABLE genomic_file_headers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    file_path TEXT NOT NULL,
    file_format VARCHAR(10) NOT NULL CHECK (file_format IN ('BAM','SAM','CRAM','VCF','BCF','FASTA','FASTQ')),
    header_info JSONB NOT NULL,
    extracted_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);

-- ============================================================================
-- INDEXES FOR PERFORMANCE
-- ============================================================================
-- CPIC indexes
CREATE INDEX idx_guidelines_gene ON cpic.guidelines(gene);
CREATE INDEX idx_guidelines_drug ON cpic.guidelines(drug);
CREATE INDEX idx_alleles_gene_id ON cpic.alleles(gene_id);
CREATE INDEX idx_gene_drug_interactions_gene_id ON cpic.gene_drug_interactions(gene_id);
CREATE INDEX idx_gene_drug_interactions_drug_id ON cpic.gene_drug_interactions(drug_id);

-- User data indexes
CREATE INDEX idx_genetic_data_patient_id ON user_data.genetic_data(patient_id);
CREATE INDEX idx_genetic_data_parent_id ON user_data.genetic_data(parent_data_id);
CREATE INDEX idx_patient_alleles_patient_id ON user_data.patient_alleles(patient_id);
CREATE INDEX idx_patient_alleles_gene_id ON user_data.patient_alleles(gene_id);
CREATE INDEX idx_patient_reports_patient_id ON reports.patient_reports(patient_id);

-- Job monitoring indexes
CREATE INDEX idx_jobs_status ON job_monitoring.jobs(status);
CREATE INDEX idx_jobs_stage ON job_monitoring.jobs(stage);
CREATE INDEX idx_jobs_created_at ON job_monitoring.jobs(created_at);
CREATE INDEX idx_job_stages_job_id ON job_monitoring.job_stages(job_id);
CREATE INDEX idx_job_events_job_id ON job_monitoring.job_events(job_id);
CREATE INDEX idx_job_dependencies_job_id ON job_monitoring.job_dependencies(job_id);


-- Genomic file headers indexes
CREATE INDEX idx_gfh_file_format ON genomic_file_headers(file_format);
CREATE INDEX idx_gfh_header_info_gin ON genomic_file_headers USING GIN (header_info);

-- ============================================================================
-- SAMPLE DATA
-- ============================================================================
-- Insert sample genes
INSERT INTO cpic.genes (gene_symbol, full_name, chromosome, description)
VALUES 
('CYP2D6', 'Cytochrome P450 Family 2 Subfamily D Member 6', '22', 'Major enzyme involved in drug metabolism'),
('CYP2C19', 'Cytochrome P450 Family 2 Subfamily C Member 19', '10', 'Involved in metabolism of several drug classes'),
('SLCO1B1', 'Solute Carrier Organic Anion Transporter Family Member 1B1', '12', 'Mediates transport of organic anions'),
('DPYD', 'Dihydropyrimidine Dehydrogenase', '1', 'Fluoropyrimidine metabolism');

-- Insert sample alleles for CYP2D6
INSERT INTO cpic.alleles (gene_id, allele_name, function_status, activity_score, clinical_significance)
VALUES 
((SELECT gene_id FROM cpic.genes WHERE gene_symbol = 'CYP2D6'), '*1', 'Normal Function', 1.0, 'Normal enzyme activity'),
((SELECT gene_id FROM cpic.genes WHERE gene_symbol = 'CYP2D6'), '*2', 'Normal Function', 1.0, 'Normal enzyme activity'),
((SELECT gene_id FROM cpic.genes WHERE gene_symbol = 'CYP2D6'), '*4', 'No Function', 0.0, 'Non-functional enzyme'),
((SELECT gene_id FROM cpic.genes WHERE gene_symbol = 'CYP2D6'), '*10', 'Decreased Function', 0.5, 'Reduced enzyme activity');

-- Insert sample alleles for CYP2C19
INSERT INTO cpic.alleles (gene_id, allele_name, function_status, activity_score, clinical_significance)
VALUES 
((SELECT gene_id FROM cpic.genes WHERE gene_symbol = 'CYP2C19'), '*1', 'Normal Function', 1.0, 'Normal enzyme activity'),
((SELECT gene_id FROM cpic.genes WHERE gene_symbol = 'CYP2C19'), '*2', 'No Function', 0.0, 'Non-functional enzyme'),
((SELECT gene_id FROM cpic.genes WHERE gene_symbol = 'CYP2C19'), '*17', 'Increased Function', 2.0, 'Increased enzyme activity');

-- Insert sample drugs
INSERT INTO cpic.drugs (drug_name, drug_class, atc_code)
VALUES 
('Sertraline', 'SSRI', 'N06AB06'),
('Clopidogrel', 'Antiplatelet', 'B01AC04'),
('Codeine', 'Opioid', 'R05DA04'),
('Simvastatin', 'Statin', 'C10AA01'),
('Fluorouracil', 'Fluoropyrimidine', 'L01BC02');

-- Insert sample guidelines
INSERT INTO cpic.guidelines (gene, drug, allele_combination, recommendation, activity_score)
VALUES 
('CYP2D6', 'Codeine', '{"diplotypes": ["*1/*1", "*1/*2", "*2/*2"]}', 'Normal metabolizer. Use labeled dosage.', 1.5),
('CYP2D6', 'Codeine', '{"diplotypes": ["*1/*4", "*2/*4"]}', 'Intermediate metabolizer. Consider alternate drug or reduced dose.', 0.5),
('CYP2D6', 'Codeine', '{"diplotypes": ["*4/*4", "*4/*10"]}', 'Poor metabolizer. Avoid codeine due to lack of efficacy.', 0.0),
('CYP2C19', 'Clopidogrel', '{"diplotypes": ["*1/*1"]}', 'Normal metabolizer. Use labeled dosage.', 1.0),
('CYP2C19', 'Clopidogrel', '{"diplotypes": ["*2/*2"]}', 'Poor metabolizer. Consider alternate antiplatelet therapy.', 0.0),
('CYP2C19', 'Clopidogrel', '{"diplotypes": ["*1/*17", "*17/*17"]}', 'Ultrarapid metabolizer. Use labeled dosage.', 2.0),
('SLCO1B1', 'Simvastatin', '{"variants": ["rs4149056 TT"]}', 'Normal function. Use standard dosing.', null),
('SLCO1B1', 'Simvastatin', '{"variants": ["rs4149056 TC"]}', 'Intermediate function. Consider lower dose.', null),
('SLCO1B1', 'Simvastatin', '{"variants": ["rs4149056 CC"]}', 'Low function. Consider alternate statin.', null),
('DPYD', 'Fluorouracil', '{"variants": ["normal"]}', 'Normal risk. Standard dosing.', null),
('DPYD', 'Fluorouracil', '{"variants": ["rs3918290 GA"]}', 'Intermediate DPYD activity. Reduce starting dose by 50%.', null),
('DPYD', 'Fluorouracil', '{"variants": ["rs3918290 AA"]}', 'Complete DPYD deficiency. Avoid fluoropyrimidines.', null);

-- Insert gene-drug interactions
INSERT INTO cpic.gene_drug_interactions (gene_id, drug_id, guideline_id, strength_of_evidence)
VALUES 
((SELECT gene_id FROM cpic.genes WHERE gene_symbol = 'CYP2D6'), 
 (SELECT drug_id FROM cpic.drugs WHERE drug_name = 'Codeine'),
 (SELECT guideline_id FROM cpic.guidelines WHERE gene = 'CYP2D6' AND drug = 'Codeine' AND activity_score = 1.5),
 'Strong'),
 
((SELECT gene_id FROM cpic.genes WHERE gene_symbol = 'CYP2C19'), 
 (SELECT drug_id FROM cpic.drugs WHERE drug_name = 'Clopidogrel'),
 (SELECT guideline_id FROM cpic.guidelines WHERE gene = 'CYP2C19' AND drug = 'Clopidogrel' AND activity_score = 1.0),
 'Strong'),
 
((SELECT gene_id FROM cpic.genes WHERE gene_symbol = 'SLCO1B1'), 
 (SELECT drug_id FROM cpic.drugs WHERE drug_name = 'Simvastatin'),
 (SELECT guideline_id FROM cpic.guidelines WHERE gene = 'SLCO1B1' AND drug = 'Simvastatin' LIMIT 1),
 'Moderate'),
 
((SELECT gene_id FROM cpic.genes WHERE gene_symbol = 'DPYD'), 
 (SELECT drug_id FROM cpic.drugs WHERE drug_name = 'Fluorouracil'),
 (SELECT guideline_id FROM cpic.guidelines WHERE gene = 'DPYD' AND drug = 'Fluorouracil' LIMIT 1),
 'Strong');

-- Insert gene groups data
INSERT INTO gene_groups (name, description, clinical_relevance, display_order, color_code) 
VALUES 
('CYP450_Enzymes', 'Phase I drug-metabolizing enzymes involved in oxidation, reduction, and hydrolysis reactions', 'Responsible for the metabolism of approximately 75% of all prescription drugs', 1, '#4285F4'),
('Phase_II_Enzymes', 'Enzymes responsible for conjugation reactions in drug metabolism', 'Important for detoxification and elimination of drugs', 2, '#34A853'),
('Drug_Transporters', 'Membrane proteins that facilitate movement of drugs across biological membranes', 'Impact drug absorption, distribution, and elimination', 3, '#FBBC05'),
('Drug_Targets', 'Proteins that are the direct targets of medication action', 'Direct impact on pharmacodynamic response', 4, '#EA4335'),
('Other_PGx_Genes', 'Other genes with pharmacogenomic relevance', 'Miscellaneous pharmacogenomic markers', 5, '#9C27B0');

-- Insert gene group memberships
INSERT INTO gene_group_members (gene_symbol, group_id, description, primary_group)
VALUES
('CYP2D6', (SELECT id FROM gene_groups WHERE name = 'CYP450_Enzymes'), 'Metabolizes many antidepressants, antipsychotics, and opioids', true),
('CYP2C19', (SELECT id FROM gene_groups WHERE name = 'CYP450_Enzymes'), 'Metabolizes clopidogrel, many SSRIs, and PPIs', true),
('VKORC1', (SELECT id FROM gene_groups WHERE name = 'Drug_Targets'), 'Target of warfarin, impacts dose requirements', true);

-- ============================================================================
-- PERMISSIONS
-- ============================================================================
-- Grant permissions to the database user
GRANT USAGE ON SCHEMA cpic TO cpic_user;
GRANT USAGE ON SCHEMA user_data TO cpic_user;
GRANT USAGE ON SCHEMA reports TO cpic_user;
GRANT USAGE ON SCHEMA job_monitoring TO cpic_user;
GRANT USAGE ON SCHEMA fhir TO cpic_user;

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA cpic TO cpic_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA user_data TO cpic_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA reports TO cpic_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA job_monitoring TO cpic_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA fhir TO cpic_user;
GRANT ALL PRIVILEGES ON TABLE genomic_file_headers TO cpic_user;
GRANT ALL PRIVILEGES ON TABLE gene_groups TO cpic_user;
GRANT ALL PRIVILEGES ON TABLE gene_group_members TO cpic_user;

GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA cpic TO cpic_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA job_monitoring TO cpic_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO cpic_user;

-- Set default privileges for future tables
ALTER DEFAULT PRIVILEGES IN SCHEMA cpic GRANT ALL ON TABLES TO cpic_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA user_data GRANT ALL ON TABLES TO cpic_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA reports GRANT ALL ON TABLES TO cpic_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA job_monitoring GRANT ALL ON TABLES TO cpic_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA fhir GRANT ALL ON TABLES TO cpic_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO cpic_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA cpic GRANT ALL ON SEQUENCES TO cpic_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA job_monitoring GRANT ALL ON SEQUENCES TO cpic_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO cpic_user;

-- ============================================================================
-- UTILITY FUNCTIONS
-- ============================================================================
-- Function to add a gene to a group if it doesn't exist
CREATE OR REPLACE FUNCTION add_gene_to_group(
    gene VARCHAR(20),
    group_name VARCHAR(50),
    gene_description TEXT DEFAULT NULL,
    is_primary BOOLEAN DEFAULT false
) RETURNS VOID AS $$
BEGIN
    INSERT INTO gene_group_members (gene_symbol, group_id, description, primary_group)
    SELECT gene, id, gene_description, is_primary
    FROM gene_groups
    WHERE name = group_name
    ON CONFLICT (gene_symbol, group_id) DO UPDATE
    SET description = EXCLUDED.description,
        primary_group = EXCLUDED.primary_group;
END;
$$ LANGUAGE plpgsql;

-- Comments for documentation
COMMENT ON SCHEMA cpic IS 'CPIC pharmacogenomic guidelines and reference data';
COMMENT ON SCHEMA user_data IS 'Patient and genetic data (HIPAA-compliant)';
COMMENT ON SCHEMA reports IS 'Generated reports and analysis outputs';
COMMENT ON SCHEMA job_monitoring IS 'Workflow and job tracking system';
COMMENT ON SCHEMA fhir IS 'HAPI FHIR server tables';
COMMENT ON TABLE genomic_file_headers IS 'Stores parsed header information from genomic files (BAM, VCF, etc.)';
COMMENT ON COLUMN genomic_file_headers.header_info IS 'Normalized JSON structure containing file format-specific header metadata';

-- ============================================================================
-- WORKFLOW MONITORING TABLES
-- ============================================================================

-- Create enums for workflow monitoring system
CREATE TYPE workflow_status_enum AS ENUM ('pending', 'running', 'completed', 'failed', 'cancelled');
CREATE TYPE step_status_enum AS ENUM ('pending', 'running', 'completed', 'failed', 'skipped');
CREATE TYPE log_level_enum AS ENUM ('debug', 'info', 'warn', 'error');

-- Primary workflow orchestration
CREATE TABLE workflows (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR NOT NULL,
    description TEXT,
    status workflow_status_enum DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    total_steps INTEGER,
    completed_steps INTEGER,
    workflow_metadata JSONB,
    created_by VARCHAR,
    CONSTRAINT workflows_status_check CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled'))
);

-- Individual step tracking
CREATE TABLE workflow_steps (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workflow_id UUID REFERENCES workflows(id) ON DELETE CASCADE,
    step_name VARCHAR NOT NULL,
    step_order INTEGER NOT NULL,
    status step_status_enum DEFAULT 'pending',
    container_name VARCHAR,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    duration_seconds INTEGER,
    output_data JSONB,
    error_details JSONB,
    retry_count INTEGER DEFAULT 0,
    UNIQUE(workflow_id, step_name),
    CONSTRAINT workflow_steps_status_check CHECK (status IN ('pending', 'running', 'completed', 'failed', 'skipped'))
);

-- Execution logs for debugging
CREATE TABLE workflow_logs (
    id BIGSERIAL PRIMARY KEY,
    workflow_id UUID REFERENCES workflows(id) ON DELETE CASCADE,
    step_name VARCHAR,
    log_level log_level_enum DEFAULT 'info',
    message TEXT NOT NULL,
    log_metadata JSONB,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT workflow_logs_level_check CHECK (log_level IN ('debug', 'info', 'warn', 'error'))
);

-- Create indexes for performance
CREATE INDEX idx_workflows_status ON workflows(status);
CREATE INDEX idx_workflows_created_at ON workflows(created_at);
CREATE INDEX idx_workflow_steps_workflow_id ON workflow_steps(workflow_id);
CREATE INDEX idx_workflow_steps_step_order ON workflow_steps(workflow_id, step_order);
CREATE INDEX idx_workflow_logs_workflow_id ON workflow_logs(workflow_id);
CREATE INDEX idx_workflow_logs_timestamp ON workflow_logs(timestamp);

-- Grant permissions
GRANT ALL PRIVILEGES ON TABLE workflows TO cpic_user;
GRANT ALL PRIVILEGES ON TABLE workflow_steps TO cpic_user;
GRANT ALL PRIVILEGES ON TABLE workflow_logs TO cpic_user;

-- Comments for documentation
COMMENT ON TABLE workflows IS 'Primary workflow orchestration table for enhanced monitoring system';
COMMENT ON TABLE workflow_steps IS 'Individual step tracking within workflows';
COMMENT ON TABLE workflow_logs IS 'Execution logs for debugging and monitoring workflows';