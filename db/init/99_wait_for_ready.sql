-- Wait for database to be fully ready
-- This script runs last to ensure all schemas are properly initialized

-- Verify all schemas exist
DO $$
DECLARE
    schema_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO schema_count
    FROM information_schema.schemata 
    WHERE schema_name IN ('cpic', 'user_data', 'reports', 'job_monitoring', 'fhir');
    
    IF schema_count < 5 THEN
        RAISE EXCEPTION 'Not all required schemas were created. Found % schemas, expected 5.', schema_count;
    END IF;
    
    RAISE NOTICE 'All required schemas verified: cpic, user_data, reports, job_monitoring, fhir';
END $$;

-- Verify permissions are set correctly
DO $$
DECLARE
    has_fhir_perms BOOLEAN;
BEGIN
    SELECT EXISTS (
        SELECT 1 
        FROM information_schema.role_table_grants 
        WHERE grantee = 'cpic_user' 
        AND table_schema = 'fhir'
        AND privilege_type = 'INSERT'
    ) INTO has_fhir_perms;
    
    IF NOT has_fhir_perms THEN
        RAISE WARNING 'FHIR schema permissions may not be fully set for cpic_user';
    ELSE
        RAISE NOTICE 'FHIR schema permissions verified for cpic_user';
    END IF;
END $$;

-- Final verification message
SELECT 'Database initialization complete - ready for HAPI FHIR' as status;
