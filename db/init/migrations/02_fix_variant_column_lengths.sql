-- Migration: Fix variant column lengths for complex indels
-- This migration increases the column sizes for genotype_call and dbsnp_id
-- to accommodate complex variants with longer values

-- Increase genotype_call from VARCHAR(20) to VARCHAR(100)
ALTER TABLE pharmcat.variants 
ALTER COLUMN genotype_call TYPE VARCHAR(100);

-- Increase dbsnp_id from VARCHAR(20) to VARCHAR(30)
ALTER TABLE pharmcat.variants 
ALTER COLUMN dbsnp_id TYPE VARCHAR(30);

-- Verify the changes
SELECT column_name, data_type, character_maximum_length 
FROM information_schema.columns 
WHERE table_schema = 'pharmcat' 
AND table_name = 'variants'
AND column_name IN ('genotype_call', 'dbsnp_id');

