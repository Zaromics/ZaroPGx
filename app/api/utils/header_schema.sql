-- Simple PostgreSQL 17 schema for genomic file headers only

CREATE TABLE genomic_file_headers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_path TEXT NOT NULL,
    file_format VARCHAR(10) NOT NULL CHECK (file_format IN ('BAM', 'SAM', 'CRAM', 'VCF', 'BCF', 'FASTA', 'FASTQ')),
    
    -- Header information as JSONB
    header_info JSONB NOT NULL,
    
    -- Timestamp when header was extracted
    extracted_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for efficient queries
CREATE INDEX idx_file_format ON genomic_file_headers(file_format);
CREATE INDEX idx_header_gin ON genomic_file_headers USING GIN (header_info);

-- Example JSONB structure for header_info:
COMMENT ON COLUMN genomic_file_headers.header_info IS 
'Standard structure:
{
  "file_info": {
    "path": "/path/to/file.bam",
    "format": "BAM",
    "size": 1073741824,
    "compressed": true,
    "has_index": true
  },
  "metadata": {
    "version": "1.6",
    "created_by": "bwa mem", 
    "reference_genome": "GRCh38",
    "reference_genome_path": "/ref/GRCh38.fa"
  },
  "sequences": [
    {"name": "chr1", "length": 249250621},
    {"name": "chr2", "length": 242193529}
  ],
  "sample": "sampleID",
  "format_specific": {
    "sam_header_lines": ["@HD\tVN:1.6\tSO:coordinate"],
    "programs": [{"ID": "bwa", "VN": "0.7.17"}],
    "vcf_info_fields": {"DP": "Total Depth", "AF": "Allele Frequency"},
    "vcf_format_fields": {"GT": "Genotype", "DP": "Read Depth"}
  }
}';
