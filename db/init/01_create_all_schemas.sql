-- Comprehensive ZaroPGx Database Initialization
-- This script runs automatically on database initialization
-- It creates all necessary schemas and tables in the correct order
-- Following Docker PostgreSQL best practices

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create CPIC schema and tables
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

-- Create user_data schema
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

-- Create reports schema
CREATE SCHEMA IF NOT EXISTS reports;

-- Create reports table
CREATE TABLE reports.patient_reports (
    report_id SERIAL PRIMARY KEY,
    patient_id UUID REFERENCES user_data.patients(patient_id),
    report_type VARCHAR(50),
    report_path TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create job_monitoring schema
CREATE SCHEMA IF NOT EXISTS job_monitoring;

-- Main job status table
CREATE TABLE job_monitoring.jobs (
    job_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id UUID REFERENCES user_data.patients(patient_id),
    file_id UUID REFERENCES user_data.genetic_data(data_id),
    status VARCHAR(50) NOT NULL CHECK (status IN ('pending', 'processing', 'completed', 'failed', 'cancelled')),
    stage VARCHAR(50) NOT NULL CHECK (stage IN ('upload', 'analysis', 'gatk', 'pypgx', 'pharmcat', 'report', 'complete')),
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

-- Create indexes for performance
CREATE INDEX idx_guidelines_gene ON cpic.guidelines(gene);
CREATE INDEX idx_guidelines_drug ON cpic.guidelines(drug);
CREATE INDEX idx_alleles_gene_id ON cpic.alleles(gene_id);
CREATE INDEX idx_gene_drug_interactions_gene_id ON cpic.gene_drug_interactions(gene_id);
CREATE INDEX idx_gene_drug_interactions_drug_id ON cpic.gene_drug_interactions(drug_id);

CREATE INDEX idx_genetic_data_patient_id ON user_data.genetic_data(patient_id);
CREATE INDEX idx_genetic_data_parent_id ON user_data.genetic_data(parent_data_id);
CREATE INDEX idx_patient_alleles_patient_id ON user_data.patient_alleles(patient_id);
CREATE INDEX idx_patient_alleles_gene_id ON user_data.patient_alleles(gene_id);
CREATE INDEX idx_patient_reports_patient_id ON reports.patient_reports(patient_id);

CREATE INDEX idx_jobs_status ON job_monitoring.jobs(status);
CREATE INDEX idx_jobs_stage ON job_monitoring.jobs(stage);
CREATE INDEX idx_jobs_created_at ON job_monitoring.jobs(created_at);
CREATE INDEX idx_job_stages_job_id ON job_monitoring.job_stages(job_id);
CREATE INDEX idx_job_events_job_id ON job_monitoring.job_events(job_id);

-- Grant permissions
GRANT USAGE ON SCHEMA cpic TO cpic_user;
GRANT USAGE ON SCHEMA user_data TO cpic_user;
GRANT USAGE ON SCHEMA reports TO cpic_user;
GRANT USAGE ON SCHEMA job_monitoring TO cpic_user;

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA cpic TO cpic_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA user_data TO cpic_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA reports TO cpic_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA job_monitoring TO cpic_user;

GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA cpic TO cpic_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA job_monitoring TO cpic_user;

-- Set default privileges for future tables
ALTER DEFAULT PRIVILEGES IN SCHEMA cpic GRANT ALL ON TABLES TO cpic_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA user_data GRANT ALL ON TABLES TO cpic_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA reports GRANT ALL ON TABLES TO cpic_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA job_monitoring GRANT ALL ON TABLES TO cpic_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA cpic GRANT ALL ON SEQUENCES TO cpic_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA job_monitoring GRANT ALL ON SEQUENCES TO cpic_user;
