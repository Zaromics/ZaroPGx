-- Create FHIR schema if it doesn't exist
CREATE SCHEMA IF NOT EXISTS fhir;

-- Grant appropriate permissions to the database user
GRANT ALL PRIVILEGES ON SCHEMA fhir TO cpic_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA fhir TO cpic_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA fhir GRANT ALL ON TABLES TO cpic_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA fhir GRANT ALL ON SEQUENCES TO cpic_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA fhir GRANT ALL ON FUNCTIONS TO cpic_user;

-- Comment to document the schema's purpose
COMMENT ON SCHEMA fhir IS 'Schema for HAPI FHIR server tables - separate from main application tables'; 