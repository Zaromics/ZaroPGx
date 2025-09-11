-- Fix Job Monitoring Schema to match SQLAlchemy models
-- Drop existing tables and recreate with correct schema

-- Drop existing tables
DROP TABLE IF EXISTS job_monitoring.job_events CASCADE;
DROP TABLE IF EXISTS job_monitoring.job_stages CASCADE;
DROP TABLE IF EXISTS job_monitoring.jobs CASCADE;

-- Recreate jobs table with all required columns
CREATE TABLE job_monitoring.jobs (
    job_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id UUID,
    file_id UUID,
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

-- Recreate job_stages table with all required columns
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

-- Recreate job_events table
CREATE TABLE job_monitoring.job_events (
    event_id SERIAL PRIMARY KEY,
    job_id UUID REFERENCES job_monitoring.jobs(job_id) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL,
    message TEXT NOT NULL,
    event_metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes
CREATE INDEX idx_jobs_status ON job_monitoring.jobs(status);
CREATE INDEX idx_jobs_stage ON job_monitoring.jobs(stage);
CREATE INDEX idx_jobs_created_at ON job_monitoring.jobs(created_at);
CREATE INDEX idx_job_stages_job_id ON job_monitoring.job_stages(job_id);
CREATE INDEX idx_job_events_job_id ON job_monitoring.job_events(job_id);

-- Grant permissions
GRANT USAGE ON SCHEMA job_monitoring TO cpic_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA job_monitoring TO cpic_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA job_monitoring TO cpic_user;
