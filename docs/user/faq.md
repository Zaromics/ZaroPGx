---
title: Frequently Asked Questions
---

# Frequently Asked Questions

Common questions and answers about ZaroPGx.

## General Questions

### What is ZaroPGx?

ZaroPGx is a containerized pharmacogenomics platform that processes genomic data and generates clinical reports. It integrates multiple bioinformatics tools (PharmCAT, PyPGx, GATK, OptiType) to provide comprehensive pharmacogene analysis.

### Who can use ZaroPGx?

ZaroPGx is designed for:
- **Researchers**: Academic and clinical researchers studying pharmacogenomics
- **Healthcare Providers**: Clinicians implementing pharmacogenomic testing
- **Laboratories**: Clinical laboratories offering PGx testing services
- **Developers**: Bioinformaticians building PGx analysis pipelines

### Is ZaroPGx free to use?

Yes, ZaroPGx is open-source software licensed under AGPLv3. You can use, modify, and distribute it freely, subject to the license terms.

### What makes ZaroPGx different from other PGx tools?

- **Comprehensive Coverage**: Analyzes 23+ core pharmacogenes with additional coverage for 90+ genes
- **Integrated Pipeline**: Combines multiple tools (PharmCAT, PyPGx, OptiType) in one platform
- **Self-Hosted**: Complete data privacy with no external data transmission
- **Clinical Focus**: Generates actionable clinical reports with CPIC guidelines
- **Easy Deployment**: Docker-based setup with minimal configuration

## Technical Questions

### What file formats are supported?

ZaroPGx supports:
- **VCF**: Variant Call Format (direct processing)
- **BAM**: Binary Alignment Map (HLA typing → analysis)
- **CRAM**: Compressed BAM (GATK → HLA typing → analysis)
- **SAM**: Sequence Alignment Map (GATK → HLA typing → analysis)
- **FASTQ**: Raw sequencing data (HLA typing → GATK → analysis)

### What reference genomes are supported?

- **GRCh38/hg38**: Fully supported (recommended)
- **GRCh37/hg19**: Supported with automatic liftover
- **Custom references**: Supported with proper configuration

### How much computing power do I need?

**Minimum Requirements:**
- 4 CPU cores
- 8 GB RAM
- 30 GB storage

**Recommended:**
- 8+ CPU cores
- 32+ GB RAM
- 1+ TB SSD storage

### How long does analysis take?

Analysis time depends on:
- **File size**: Larger files take longer
- **File type**: VCF is fastest, FASTQ is slowest
- **System resources**: More CPU/RAM = faster processing
- **Reference genome**: hg38 is faster than hg19

**Typical times:**
- **VCF (exome)**: 5-15 minutes
- **VCF (whole genome)**: 30-60 minutes
- **BAM (exome)**: 15-30 minutes
- **FASTQ (exome)**: 30-60 minutes

## Clinical Questions

### What pharmacogenes are analyzed?

**Core 23 Pharmacogenes (PharmCAT):**
- CYP2D6, CYP2C19, CYP2C9, CYP3A4, CYP3A5
- TPMT, DPYD, UGT1A1, COMT
- SLC6A4, SLC19A1, ABCB1, ABCC2
- VKORC1, CYP4F2, CYP2C8, CYP2B6
- And more...

**Additional Genes (PyPGx):**
- 60+ additional pharmacogenes
- Including difficult-to-call genes like CYP2D6
- Comprehensive star allele calling

### What clinical guidelines are used?

ZaroPGx uses **CPIC (Clinical Pharmacogenomics Implementation Consortium)** guidelines:
- Evidence-based recommendations
- Regularly updated with new evidence
- Graded by strength of evidence
- Specific to gene-drug pairs

### How accurate are the results?

Accuracy depends on:
- **Input data quality**: Higher coverage = better accuracy
- **Gene complexity**: Some genes are harder to call accurately
- **Tool limitations**: Each tool has specific strengths/weaknesses
- **Reference genome**: hg38 generally more accurate than hg19

**Typical accuracy:**
- **High-confidence calls**: >95% accuracy
- **Medium-confidence calls**: 80-95% accuracy
- **Low-confidence calls**: <80% accuracy

### Can I use this for clinical decision-making?

ZaroPGx is a research tool and should not be used directly for clinical decision-making without:
- **Clinical validation**: Verify results with clinical testing
- **Expert interpretation**: Review by qualified healthcare providers
- **Regulatory approval**: Ensure compliance with local regulations
- **Quality assurance**: Implement appropriate QA/QC procedures

## Deployment Questions

### Can I run this on the cloud?

Yes, ZaroPGx can be deployed on:
- **AWS**: EC2 instances with sufficient resources
- **Google Cloud**: Compute Engine instances
- **Azure**: Virtual machines
- **Other clouds**: Any cloud provider supporting Docker

### How do I secure the deployment?

**Network Security:**
- Use VPN or private networks
- Configure firewall rules
- Enable HTTPS/TLS encryption
- Implement access controls

**Data Security:**
- Encrypt data at rest
- Use secure authentication
- Regular security updates
- Audit logging

### Can I scale this for multiple users?

Yes, ZaroPGx can be scaled by:
- **Horizontal scaling**: Multiple app instances
- **Load balancing**: Distribute requests across instances
- **Database scaling**: Use external PostgreSQL cluster
- **Storage scaling**: Use shared storage systems

## Data Questions

### Where is my data stored?

Data is stored locally in Docker volumes:
- **Uploaded files**: `/data/uploads/`
- **Analysis results**: `/data/reports/`
- **Reference data**: `/reference/`
- **Database**: PostgreSQL container

### Is my data sent anywhere?

No, ZaroPGx is designed for complete data privacy:
- **No external transmission**: All processing happens locally
- **Self-contained**: All tools run in local containers
- **Offline capable**: Works without internet after setup
- **Data control**: You maintain complete control over your data

### Can I export my data?

Yes, ZaroPGx supports multiple export formats:
- **PDF reports**: Clinical reports
- **HTML reports**: Interactive web reports
- **JSON data**: Machine-readable results
- **FHIR format**: Healthcare interoperability standard
- **Raw data**: Original tool outputs

### How long is data retained?

Data retention is configurable:
- **Default**: Indefinite retention
- **Configurable**: Set retention periods in environment variables
- **Manual cleanup**: Remove data manually when needed
- **Backup**: Create backups before cleanup

## Troubleshooting Questions

### Why is my analysis taking so long?

Common causes:
- **Large files**: Whole genome data takes longer
- **Low resources**: Insufficient CPU/RAM
- **Network issues**: Slow reference genome downloads
- **Disk I/O**: Slow storage performance

**Solutions:**
- Use SSD storage
- Increase system resources
- Process smaller file subsets
- Check for background processes

### Why are some genes showing "No Call"?

Possible causes:
- **Low coverage**: Insufficient sequencing depth
- **Poor quality**: Low-quality input data
- **Missing variants**: Variants not in reference
- **Tool limitations**: Some genes are harder to call

**Solutions:**
- Check coverage depth
- Verify data quality
- Use different tools
- Review analysis parameters

### Why is my PDF report empty?

Common causes:
- **Analysis failed**: Check processing logs
- **Missing data**: Verify analysis completed
- **Template issues**: Check report templates
- **PDF engine problems**: Try different engine

**Solutions:**
- Review analysis logs
- Check report files
- Switch PDF engine
- Verify data availability

## Development Questions

### Can I contribute to ZaroPGx?

Yes! ZaroPGx is open-source and welcomes contributions:
- **Code contributions**: Bug fixes, new features
- **Documentation**: Improve guides and examples
- **Testing**: Report bugs, test new features
- **Community**: Help other users, share experiences

### How do I report bugs?

**Before reporting:**
- Check existing issues on GitHub
- Review troubleshooting guide
- Gather relevant logs and information

**When reporting:**
- Use the GitHub issue template
- Include system information
- Provide reproducible steps
- Attach relevant logs

### Can I customize the analysis pipeline?

Yes, ZaroPGx is designed for extensibility:
- **Environment variables**: Configure tool behavior
- **Custom references**: Add your own reference data
- **Tool integration**: Add new analysis tools
- **Report customization**: Modify report templates

## Next Steps

- **Get started**: {doc}`quick-start`
- **Learn more**: {doc}`usage`
- **Troubleshoot issues**: {doc}`troubleshooting`
- **Configure system**: {doc}`../advanced-configuration`
