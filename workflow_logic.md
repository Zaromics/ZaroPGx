
### Workflow pipeline progress markers
- 0% -- File uploading starts
- 5% -- File info and Header inspection finished
- 10% -- File upload finished
- 20% -- Conversion to BAM with GATK (skip if n/a)
- 30% -- OptiType/hlatyping step (skip if n/a)
- 40% -- Conversion to BAM from FASTQ (skip if n/a)
- 50% -- PyPGx step
- 60% -- PyPGx bam2vcf conversion step (skip if n/a)
- 70% -- PharmCAT step
- 80% -- Generating workflow diagram
- 90% -- Generating PDF and HTML reports
- 100% -- Processing complete!
- Once everything is complete, that's when the report section is displayed in the UI (already coded in, can be refined)

### Real-time Nextflow Monitoring
- **Enhanced Progress Tracking**: The system now provides real-time progress updates during Nextflow pipeline execution
- **Process-Level Visibility**: Users can see individual Nextflow processes (FastqToBAM, OptiTypeHLA, PyPGx, PharmCAT) with their status, duration, and resource usage
- **Live Updates**: Progress updates are streamed to the UI every 5 seconds, showing:
  - Current running process
  - Process completion status (SUBMITTED, RUNNING, COMPLETED, FAILED)
  - Process duration and resource usage
  - Overall pipeline progress percentage
- **Stage Mapping**: Nextflow processes are mapped to job stages:
  - FastqToBAM/alignment processes → GATK stage
  - OptiTypeHLA processes → HLA stage  
  - PyPGx processes → PyPGx stage
  - PharmCAT processes → PharmCAT stage
- **Error Handling**: Failed processes are immediately reported with error details
- **UI Integration**: New "Nextflow Process Details" panel shows real-time process information


### Input file -> check size, filetype, inspect header
- If the size is within limits and filetype among knowns:
- [ ] check header with [[pysam]], [[bcftools]], or [[BioPython]] as appropriate
- [ ] save header and file info to the Postgres DB, probably as JSONB, should be kept together with the other entries for a sample


### FASTQ -> hg38 reference to be indexed and aligned *OPTIMAL STARTING FORMAT*
- [ ] Optimal input format for OptiType/hlatyping; start there, then convert to BAM after
- depending on type of read (check the header data)
- Long-read --> use [[minimap2]]
- Short-read --> use [[bwa-mem2]] if machine has >= 64GB RAM;
	- otherwise --> use [[BWA (Burrows-Wheeler Aligner)]]
- [ ] need to get BAM file as output, aligned to hg38
- [ ] afterward, BAM goes to PyPGx

### CRAM -> to be converted to BAM (lossy)
- [ ] GATK --> samtools
- [ ] see https://pharmcat.clinpgx.org/using/Calling-HLA/
- converted the CRAM files to BAM files using Samtools. CRAM files are a lot smaller than BAM files, but do not contain all the information in a BAM file and therefore requires the reference FASTA file used in the alignment process. Converting a CRAM to BAM is easy, but be prepared for a much larger disk space footprint of the BAM file:
```shell
samtools view -b -T <refgenome.fa> -o <output_file.bam> <input_file.cram>
```
For a 1000 genome use case scenario, such as HG00140.final.cram, we can convert it as:
```shell
samtools view -b -T GRCh38_full_analysis_set_plus_decoy_hla.fa -o HG02420.final.bam HG02420.final.cram
```
- samtools conversion didn't work for me: The issue persists because the CRAM file is still looking for the specific reference it was created with
- THIS CAN BE SOLVED!! This can be solved by editing the header and changing the path of the reference genome to the existing reference file
- [[nf-core]] has a pipeline for this, may be preferable for BAM/CRAM to FASTQ: 
	- # nf-core/bamtofastq

Converts bam or cram files to fastq format and does quality control.
- https://nf-co.re/bamtofastq/2.2.0/
- https://github.com/nf-core/bamtofastq/tree/2.2.0

### SAM -> to be converted to BAM
- [ ] GATK? samtools?

### BAM -> *can enter pipeline directly, but OptiType will internally convert it to FASTQ* (~100GB)
- [ ] Run OptiType / hlatyper and receive HLA data; store it in the appropriate db object
	- [ ] https://pharmcat.clinpgx.org/using/Calling-HLA/
		- [ ] They even recommend [[hlatyping (software)]] themselves 
- [ ] continue on to PyPGx -- run the pipeline with BAM as input (check to make sure code is implemented.) This will be more accurate than running it with VCF. 
- [ ] PyPGx should still output a VCF, however... "create-input-vcf    Call SNVs/indels from BAM files for all target genes."
- [ ] *When creating a VCF file (containing SNVs/indels) from BAM files, users have a choice to either use the pypgx create-input-vcf*
	- [ ] https://pypgx.readthedocs.io/en/latest/cli.html#run-ngs-pipeline see
- [ ] PharmCAT will accept the VCF output from PyPGx as outside calls (including the HLA outside calls!), and produce and ideal report with 23/23 highest clinical evidence pharmacogenes accounted for. 
- [ ] 

### VCF (“quick pipeline”)
- [ ] skip OptiType (HLA) and go directly to PyPGx
	- [ ] if for any reason PyPGx fails or is unavailable, bypass it and go directly to PharmCAT instead
- [ ] continue onto PharmCAT with outside calls from PyPGx

### GVCF — ?? treat as VCF for now?
- [ ] not sure what this looks like in practice 
### BCF — zipped VCF??
- [ ] does this need special attention or is it interchangeable with VCF?
### TXT ([[23andme format]])
- [ ] Say sorry, not supported yet.
- [ ] Find schema reference and create translation 

### BED 
- [ ] Say sorry, not supported yet.
- [ ] Find schema references and create translations