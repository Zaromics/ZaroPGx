### Header info
- filename / filetype / size ( all in one line )
- reference genome (version only)
- sequencing type
- num. of samples -- if >1, pipeline will not work (designed only for one sample at a time)
- etc

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