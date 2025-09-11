-- Insert sample genes
INSERT INTO cpic.genes (gene_symbol, full_name, chromosome, description)
VALUES 
('CYP2D6', 'Cytochrome P450 Family 2 Subfamily D Member 6', '22', 'Major enzyme involved in drug metabolism'),
('CYP2C19', 'Cytochrome P450 Family 2 Subfamily C Member 19', '10', 'Involved in metabolism of several drug classes'),
('SLCO1B1', 'Solute Carrier Organic Anion Transporter Family Member 1B1', '12', 'Mediates transport of organic anions'),
('DPYD', 'Dihydropyrimidine Dehydrogenase', '1', 'Fluoropyrimidine metabolism');

-- Insert sample alleles for CYP2D6
INSERT INTO cpic.alleles (gene_id, allele_name, function_status, activity_score, clinical_significance)
VALUES 
((SELECT gene_id FROM cpic.genes WHERE gene_symbol = 'CYP2D6'), '*1', 'Normal Function', 1.0, 'Normal enzyme activity'),
((SELECT gene_id FROM cpic.genes WHERE gene_symbol = 'CYP2D6'), '*2', 'Normal Function', 1.0, 'Normal enzyme activity'),
((SELECT gene_id FROM cpic.genes WHERE gene_symbol = 'CYP2D6'), '*4', 'No Function', 0.0, 'Non-functional enzyme'),
((SELECT gene_id FROM cpic.genes WHERE gene_symbol = 'CYP2D6'), '*10', 'Decreased Function', 0.5, 'Reduced enzyme activity');

-- Insert sample alleles for CYP2C19
INSERT INTO cpic.alleles (gene_id, allele_name, function_status, activity_score, clinical_significance)
VALUES 
((SELECT gene_id FROM cpic.genes WHERE gene_symbol = 'CYP2C19'), '*1', 'Normal Function', 1.0, 'Normal enzyme activity'),
((SELECT gene_id FROM cpic.genes WHERE gene_symbol = 'CYP2C19'), '*2', 'No Function', 0.0, 'Non-functional enzyme'),
((SELECT gene_id FROM cpic.genes WHERE gene_symbol = 'CYP2C19'), '*17', 'Increased Function', 2.0, 'Increased enzyme activity');

-- Insert sample drugs
INSERT INTO cpic.drugs (drug_name, drug_class, atc_code)
VALUES 
('Sertraline', 'SSRI', 'N06AB06'),
('Clopidogrel', 'Antiplatelet', 'B01AC04'),
('Codeine', 'Opioid', 'R05DA04'),
('Simvastatin', 'Statin', 'C10AA01'),
('Fluorouracil', 'Fluoropyrimidine', 'L01BC02');

-- Insert sample guidelines
INSERT INTO cpic.guidelines (gene, drug, allele_combination, recommendation, activity_score)
VALUES 
('CYP2D6', 'Codeine', '{"diplotypes": ["*1/*1", "*1/*2", "*2/*2"]}', 'Normal metabolizer. Use labeled dosage.', 1.5),
('CYP2D6', 'Codeine', '{"diplotypes": ["*1/*4", "*2/*4"]}', 'Intermediate metabolizer. Consider alternate drug or reduced dose.', 0.5),
('CYP2D6', 'Codeine', '{"diplotypes": ["*4/*4", "*4/*10"]}', 'Poor metabolizer. Avoid codeine due to lack of efficacy.', 0.0),
('CYP2C19', 'Clopidogrel', '{"diplotypes": ["*1/*1"]}', 'Normal metabolizer. Use labeled dosage.', 1.0),
('CYP2C19', 'Clopidogrel', '{"diplotypes": ["*2/*2"]}', 'Poor metabolizer. Consider alternate antiplatelet therapy.', 0.0),
('CYP2C19', 'Clopidogrel', '{"diplotypes": ["*1/*17", "*17/*17"]}', 'Ultrarapid metabolizer. Use labeled dosage.', 2.0),
('SLCO1B1', 'Simvastatin', '{"variants": ["rs4149056 TT"]}', 'Normal function. Use standard dosing.', null),
('SLCO1B1', 'Simvastatin', '{"variants": ["rs4149056 TC"]}', 'Intermediate function. Consider lower dose.', null),
('SLCO1B1', 'Simvastatin', '{"variants": ["rs4149056 CC"]}', 'Low function. Consider alternate statin.', null),
('DPYD', 'Fluorouracil', '{"variants": ["normal"]}', 'Normal risk. Standard dosing.', null),
('DPYD', 'Fluorouracil', '{"variants": ["rs3918290 GA"]}', 'Intermediate DPYD activity. Reduce starting dose by 50%.', null),
('DPYD', 'Fluorouracil', '{"variants": ["rs3918290 AA"]}', 'Complete DPYD deficiency. Avoid fluoropyrimidines.', null);

-- Insert gene-drug interactions
INSERT INTO cpic.gene_drug_interactions (gene_id, drug_id, guideline_id, strength_of_evidence)
VALUES 
((SELECT gene_id FROM cpic.genes WHERE gene_symbol = 'CYP2D6'), 
 (SELECT drug_id FROM cpic.drugs WHERE drug_name = 'Codeine'),
 (SELECT guideline_id FROM cpic.guidelines WHERE gene = 'CYP2D6' AND drug = 'Codeine' AND activity_score = 1.5),
 'Strong'),
 
((SELECT gene_id FROM cpic.genes WHERE gene_symbol = 'CYP2C19'), 
 (SELECT drug_id FROM cpic.drugs WHERE drug_name = 'Clopidogrel'),
 (SELECT guideline_id FROM cpic.guidelines WHERE gene = 'CYP2C19' AND drug = 'Clopidogrel' AND activity_score = 1.0),
 'Strong'),
 
((SELECT gene_id FROM cpic.genes WHERE gene_symbol = 'SLCO1B1'), 
 (SELECT drug_id FROM cpic.drugs WHERE drug_name = 'Simvastatin'),
 (SELECT guideline_id FROM cpic.guidelines WHERE gene = 'SLCO1B1' AND drug = 'Simvastatin' LIMIT 1),
 'Moderate'),
 
((SELECT gene_id FROM cpic.genes WHERE gene_symbol = 'DPYD'), 
 (SELECT drug_id FROM cpic.drugs WHERE drug_name = 'Fluorouracil'),
 (SELECT guideline_id FROM cpic.guidelines WHERE gene = 'DPYD' AND drug = 'Fluorouracil' LIMIT 1),
 'Strong'); 