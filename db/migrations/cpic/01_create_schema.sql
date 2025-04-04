-- Create CPIC schema
CREATE SCHEMA IF NOT EXISTS cpic;

-- Create guidelines table
CREATE TABLE cpic.guidelines (
    guideline_id SERIAL PRIMARY KEY,
    gene VARCHAR(20) NOT NULL,  -- e.g., CYP2D6
    drug VARCHAR(100) NOT NULL, -- e.g., Sertraline
    allele_combination JSONB,   -- e.g., {"diplotypes": ["*1/*1", "*1/*2"]}
    recommendation TEXT,        -- e.g., "Increased risk of QT prolongation"
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
    allele_name VARCHAR(50) NOT NULL, -- e.g., *1, *2, etc.
    function_status VARCHAR(50), -- e.g., "Normal Function", "No Function", etc.
    activity_score FLOAT,
    clinical_significance TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create drugs table
CREATE TABLE cpic.drugs (
    drug_id SERIAL PRIMARY KEY,
    drug_name VARCHAR(100) NOT NULL UNIQUE,
    drug_class VARCHAR(100),
    atc_code VARCHAR(10), -- Anatomical Therapeutic Chemical Classification
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create gene-drug relationships table
CREATE TABLE cpic.gene_drug_interactions (
    interaction_id SERIAL PRIMARY KEY,
    gene_id INTEGER REFERENCES cpic.genes(gene_id),
    drug_id INTEGER REFERENCES cpic.drugs(drug_id),
    guideline_id INTEGER REFERENCES cpic.guidelines(guideline_id),
    strength_of_evidence VARCHAR(20), -- e.g., "Strong", "Moderate", etc.
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(gene_id, drug_id)
);

-- Create user_data schema for patient information (HIPAA-compliant)
CREATE SCHEMA IF NOT EXISTS user_data;

-- Create patients table with encryption for PII
CREATE TABLE user_data.patients (
    patient_id SERIAL PRIMARY KEY,
    patient_identifier VARCHAR(255) UNIQUE, -- Encrypted external identifier
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create patient genetic data table
CREATE TABLE user_data.genetic_data (
    data_id SERIAL PRIMARY KEY,
    patient_id INTEGER REFERENCES user_data.patients(patient_id),
    file_type VARCHAR(20) NOT NULL, -- e.g., "23andme", "vcf", etc.
    file_path TEXT NOT NULL, -- Path to encrypted file
    is_supplementary BOOLEAN DEFAULT FALSE, -- Indicates if this is a supplementary file (e.g., original BAM alongside VCF)
    parent_data_id INTEGER REFERENCES user_data.genetic_data(data_id), -- Reference to primary data file if this is supplementary
    processed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create patient allele calls table
CREATE TABLE user_data.patient_alleles (
    patient_allele_id SERIAL PRIMARY KEY,
    patient_id INTEGER REFERENCES user_data.patients(patient_id),
    gene_id INTEGER REFERENCES cpic.genes(gene_id),
    diplotype VARCHAR(50), -- e.g., "*1/*2"
    phenotype VARCHAR(50), -- e.g., "Normal Metabolizer"
    activity_score FLOAT,
    confidence_score FLOAT,
    calling_method VARCHAR(50), -- e.g., "PharmCAT", "Aldy", etc.
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create reports schema
CREATE SCHEMA IF NOT EXISTS reports;

-- Create reports table
CREATE TABLE reports.patient_reports (
    report_id SERIAL PRIMARY KEY,
    patient_id INTEGER REFERENCES user_data.patients(patient_id),
    report_type VARCHAR(50), -- e.g., "Comprehensive", "Drug-specific", etc.
    report_path TEXT, -- Path to generated report
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Add indexes for performance
CREATE INDEX idx_guidelines_gene ON cpic.guidelines(gene);
CREATE INDEX idx_guidelines_drug ON cpic.guidelines(drug);
CREATE INDEX idx_alleles_gene_id ON cpic.alleles(gene_id);
CREATE INDEX idx_gene_drug_interactions_gene_id ON cpic.gene_drug_interactions(gene_id);
CREATE INDEX idx_gene_drug_interactions_drug_id ON cpic.gene_drug_interactions(drug_id);
CREATE INDEX idx_patient_alleles_patient_id ON user_data.patient_alleles(patient_id);
CREATE INDEX idx_patient_alleles_gene_id ON user_data.patient_alleles(gene_id);
CREATE INDEX idx_genetic_data_patient_id ON user_data.genetic_data(patient_id);
CREATE INDEX idx_genetic_data_parent_id ON user_data.genetic_data(parent_data_id);
CREATE INDEX idx_patient_reports_patient_id ON reports.patient_reports(patient_id); 