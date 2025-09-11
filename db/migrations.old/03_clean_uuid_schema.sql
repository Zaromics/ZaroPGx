-- Clean UUID Schema - Drop everything and recreate with UUIDs
-- No backward compatibility needed

-- Enable UUID extension first
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Drop existing schemas and tables
DROP SCHEMA IF EXISTS user_data CASCADE;
DROP SCHEMA IF EXISTS job_monitoring CASCADE;

-- Recreate user_data schema
CREATE SCHEMA user_data;

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

-- Recreate job_monitoring schema
CREATE SCHEMA job_monitoring;

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

-- Create indexes
CREATE INDEX idx_genetic_data_patient_id ON user_data.genetic_data(patient_id);
CREATE INDEX idx_genetic_data_parent_id ON user_data.genetic_data(parent_data_id);

CREATE INDEX idx_patient_alleles_patient_id ON user_data.patient_alleles(patient_id);
CREATE INDEX idx_patient_alleles_gene_id ON user_data.patient_alleles(gene_id);

CREATE INDEX idx_jobs_status ON job_monitoring.jobs(status);
CREATE INDEX idx_jobs_stage ON job_monitoring.jobs(stage);
CREATE INDEX idx_jobs_created_at ON job_monitoring.jobs(created_at);
CREATE INDEX idx_job_stages_job_id ON job_monitoring.job_stages(job_id);
CREATE INDEX idx_job_events_job_id ON job_monitoring.job_events(job_id);

-- Grant permissions
GRANT USAGE ON SCHEMA user_data TO cpic_user;
GRANT USAGE ON SCHEMA job_monitoring TO cpic_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA user_data TO cpic_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA job_monitoring TO cpic_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA job_monitoring TO cpic_user;
