-- Gene Groups Schema for ZaroPGx
-- This file defines the tables for organizing genes into functional groups

-- Gene Groups table for categorizing genes by function
CREATE TABLE IF NOT EXISTS gene_groups (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE,
    description TEXT,
    clinical_relevance TEXT,
    display_order INTEGER NOT NULL DEFAULT 0,
    color_code VARCHAR(7) -- Hex color code for UI display
);

-- Gene to Group relationship table
CREATE TABLE IF NOT EXISTS gene_group_members (
    id SERIAL PRIMARY KEY,
    gene_symbol VARCHAR(20) NOT NULL,
    group_id INTEGER REFERENCES gene_groups(id),
    description TEXT,
    primary_group BOOLEAN DEFAULT false, -- Whether this is the primary group for the gene
    UNIQUE(gene_symbol, group_id)
);

-- Pre-populated gene groups data
INSERT INTO gene_groups (name, description, clinical_relevance, display_order, color_code) 
VALUES 
    ('CYP450_Enzymes', 'Phase I drug-metabolizing enzymes involved in oxidation, reduction, and hydrolysis reactions', 'Responsible for the metabolism of approximately 75% of all prescription drugs', 1, '#4285F4'),
    ('Phase_II_Enzymes', 'Enzymes responsible for conjugation reactions in drug metabolism', 'Important for detoxification and elimination of drugs', 2, '#34A853'),
    ('Drug_Transporters', 'Membrane proteins that facilitate movement of drugs across biological membranes', 'Impact drug absorption, distribution, and elimination', 3, '#FBBC05'),
    ('Drug_Targets', 'Proteins that are the direct targets of medication action', 'Direct impact on pharmacodynamic response', 4, '#EA4335'),
    ('Other_PGx_Genes', 'Other genes with pharmacogenomic relevance', 'Miscellaneous pharmacogenomic markers', 5, '#9C27B0');

-- Pre-populated gene group memberships for currently supported genes
INSERT INTO gene_group_members (gene_symbol, group_id, description, primary_group)
VALUES
    -- CYP450 Enzymes
    ('CYP2D6', (SELECT id FROM gene_groups WHERE name = 'CYP450_Enzymes'), 'Metabolizes many antidepressants, antipsychotics, and opioids', true),
    ('CYP2C19', (SELECT id FROM gene_groups WHERE name = 'CYP450_Enzymes'), 'Metabolizes clopidogrel, many SSRIs, and PPIs', true),
    
    -- Drug Targets (for future implementation)
    ('VKORC1', (SELECT id FROM gene_groups WHERE name = 'Drug_Targets'), 'Target of warfarin, impacts dose requirements', true);

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