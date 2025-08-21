-- Simple Job Monitoring Schema
-- This creates a clean, simple system for tracking job status

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create the job monitoring schema
CREATE SCHEMA IF NOT EXISTS job_monitoring;

-- Main job status table
CREATE TABLE job_monitoring.jobs (
    job_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id UUID,
    file_id UUID,
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
