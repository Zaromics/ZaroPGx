-- Convert user_data tables from integer IDs to UUIDs
-- This migration updates the existing user_data schema to use UUIDs

-- Enable UUID extension if not already enabled
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- First, drop existing foreign key constraints
ALTER TABLE user_data.genetic_data DROP CONSTRAINT IF EXISTS genetic_data_parent_data_id_fkey;
ALTER TABLE user_data.genetic_data DROP CONSTRAINT IF EXISTS genetic_data_patient_id_fkey;

-- Add new UUID columns
ALTER TABLE user_data.genetic_data ADD COLUMN data_uuid UUID DEFAULT uuid_generate_v4();
ALTER TABLE user_data.genetic_data ADD COLUMN patient_uuid UUID;
ALTER TABLE user_data.genetic_data ADD COLUMN parent_data_uuid UUID;

-- Update existing records to have UUIDs
UPDATE user_data.genetic_data SET data_uuid = uuid_generate_v4() WHERE data_uuid IS NULL;

-- Make data_uuid NOT NULL and drop the old integer primary key
ALTER TABLE user_data.genetic_data ALTER COLUMN data_uuid SET NOT NULL;
ALTER TABLE user_data.genetic_data DROP CONSTRAINT genetic_data_pkey;
ALTER TABLE user_data.genetic_data DROP COLUMN data_id;

-- Rename UUID columns to match the expected names
ALTER TABLE user_data.genetic_data RENAME COLUMN data_uuid TO data_id;
ALTER TABLE user_data.genetic_data RENAME COLUMN patient_uuid TO patient_id;
ALTER TABLE user_data.genetic_data RENAME COLUMN parent_data_uuid TO parent_data_id;

-- Add primary key constraint on the new UUID data_id
ALTER TABLE user_data.genetic_data ADD CONSTRAINT genetic_data_pkey PRIMARY KEY (data_id);

-- Update patients table to use UUIDs
ALTER TABLE user_data.patients ADD COLUMN patient_uuid UUID DEFAULT uuid_generate_v4();
UPDATE user_data.patients SET patient_uuid = uuid_generate_v4() WHERE patient_uuid IS NULL;
ALTER TABLE user_data.patients ALTER COLUMN patient_uuid SET NOT NULL;

-- Drop old integer primary key and rename
ALTER TABLE user_data.patients DROP CONSTRAINT patients_pkey;
ALTER TABLE user_data.patients DROP COLUMN patient_id;
ALTER TABLE user_data.patients RENAME COLUMN patient_uuid TO patient_id;
ALTER TABLE user_data.patients ADD CONSTRAINT patients_pkey PRIMARY KEY (patient_id);

-- Update foreign key references in genetic_data
UPDATE user_data.genetic_data 
SET patient_id = p.patient_id 
FROM user_data.patients p 
WHERE user_data.genetic_data.patient_id = p.patient_id;

-- Update self-referencing foreign keys
UPDATE user_data.genetic_data g1
SET parent_data_id = g2.data_id
FROM user_data.genetic_data g2
WHERE g1.parent_data_id = g2.data_id;

-- Add foreign key constraints back
ALTER TABLE user_data.genetic_data ADD CONSTRAINT genetic_data_patient_id_fkey 
    FOREIGN KEY (patient_id) REFERENCES user_data.patients(patient_id);
ALTER TABLE user_data.genetic_data ADD CONSTRAINT genetic_data_parent_data_id_fkey 
    FOREIGN KEY (parent_data_id) REFERENCES user_data.genetic_data(data_id);

-- Recreate indexes
DROP INDEX IF EXISTS idx_genetic_data_parent_id;
DROP INDEX IF EXISTS idx_genetic_data_patient_id;
CREATE INDEX idx_genetic_data_parent_id ON user_data.genetic_data(parent_data_id);
CREATE INDEX idx_genetic_data_patient_id ON user_data.genetic_data(patient_id);

-- Grant permissions
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA user_data TO cpic_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA user_data TO cpic_user;
