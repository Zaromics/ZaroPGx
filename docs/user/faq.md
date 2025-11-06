---
title: Frequently Asked Questions
---

### What is ZaroPGx?

ZaroPGx is a containerized pharmacogenomics platform with that processes genomic data and generates clinical reports. It integrates multiple bioinformatics tools (PharmCAT, PyPGx, GATK, OptiType) to provide comprehensive pharmacogene analysis.

### Who is ZaroPGx for?

ZaroPGx is designed for general use. The simple user interface is meant to minimize user friction, while under-the-hood subroutines intelligently guide the submitted sample through the pipeline.

### How much does ZaroPGx cost?

Gratis; ZaroPGx is free and open-source software licensed under AGPLv3. You can *use*, modify, and distribute it freely, subject to the license terms.

### What makes ZaroPGx different from other PGx tools?

- **Comprehensive Coverage, Laser Focus**: Analyzes up to 23+ core pharmacogenes to identify clinically actionable guidelines, with total coverage for 90+ genes pharmacogenes. No additional information is provided that is tangential or not directly related to pharmacogenetic relationships. For expanded modules with nutrigenomics and more, see the Zaromics umbrella project.
- **Exceptionally Easy to Use**: The super simple user interface and "click and forget" functionality belie the comprehensiveness of the pipeline. ZaroPGx makes individual pharmacogenomics accessible to an interdisciplinary and lay audience.
- **Easy Self-Hosted Privacy**: The docker compose based containerized architecture enables anyone with a power enough computer to process pharmacogenomic analyses in the complete privacy of their LAN, after the required references have been fetched on first run. ZaroPGx does not transmit any data without your directive; this refers to usage via API and exporting via HAPI FHIR server. Of course, being open source, any function, can be added, modified, or removed, so long as it complies with the software license.
- **Dual Clinical + Research Focus**: Unlike tools which focus on either clinical, consumer, and/or research reporting, ZaroPGx differentiates between certainty levels, so reports contain clearly delineated clinically actionable guidelines and investigational insights. 
- **Just Works, Like a Swiss Army Knife**: Unlike tools with a specific requirement of input file type, sequencing/genotyping platform, or assembly reference alignment, ZaroPGx is designed with ease of use in mind. Although at this time only GRCh38 VCFs are working, most commonly encountered formats will be implemented over the coming months.
- **Export Function and Robust Schemas**: Unlike plain PDFs, emails, faxes, and printouts, ZaroPGx will soon support structured FHIR exports, first as XML/JSON, and eventually directly to an EHR/PHR, with HAPI FHIR. Moreover, with Phenopackets schema implemented in the bundled Postgres DB, analysis data will be normalized to both of the industry standard formats (HL7 FHIR, and GA4GH Phenopackets).

## Technical Questions

### Which file formats are supported?

ZaroPGx supports:
- **VCF**: Variant Call Format (now)
- **BAM**: Binary Alignment Map (soon)
- **CRAM**: Compressed BAM (soon)
- **SAM**: Sequence Alignment Map (soon)
- **FASTQ**: Raw sequencing data. Paired reads preferred. (soon)

### What reference genomes are supported?

- **GRCh38**: Fully supported (now)
- **GRCh37**: Supported with bcftools liftover to GRCh38 (soon)

### What are the computing hardware requirements?

**Minimum Requirements (VCF input):**:
- 4 CPU cores
- 16 GB RAM
- 30 GB storage

**Recommended (FASTQ/BAM/SAM/CRAM input):**
- 8+ CPU cores
- 64+ GB RAM
- 1+ TB SSD storage

### How long does analysis take?

Analysis time depends on:
- **File size**: Larger files take longer
- **File type**: VCF is fastest, FASTQ is slowest
- **System resources**: More CPU+RAM = faster processing
- **Reference genome**: GRCh37 adds a liftover re-alignment delay

**Typical times (ballpark estimate):**
- **VCF (exome)**: 5-15 minutes
- **VCF (whole genome)**: 30-60 minutes
- **BAM (exome)**: from 15-30 minutes, up to several hours or more
- **FASTQ (exome)**: from 30-60 minutes, up to several hours or more

## Clinical Considerations

### Which pharmacogenes are analyzed?

**Core 23 Pharmacogenes  (reports available):**
- ABCG2, CACNA1S, CFTR, CYP2B6, CYP2C19, CYP2C9,
- CYP3A4, CYP3A5, CYP4F2, CYP2D6, DPYD, G6PD,
- HLA-A, HLA-B, IFNL3, MT-RNR1, NAT2, NUDT15, RYR1,
- SLCO1B1, TPMT, UGT1A1, VKORC1

**Additional Pharmacogenes (no reports):**
- "ABCB1", "ACYP2", "ADRA2A", "ADRB2", "ANKK1", "APOE", "ATM", "BCHE", "BDNF", "COMT",
- "CYP1A1", "CYP1A2", "CYP1B1", "CYP2A6", "CYP2A13", "CYP2C8", "CYP2D6", "CYP2E1", "CYP2F1", "CYP2J2",
- "CYP2R1", "CYP2S1", "CYP2W1", "CYP3A7", "CYP3A43", "CYP4A11", "CYP4A22", "CYP4B1", "CYP17A1",
- "CYP19A1", "CYP26A1", "DBH", "DRD2", "F2", "F5", "GRIK1", "GRIK4", "GRIN2B", "GSTM1", "GSTP1",
- "GSTT1", "HTR1A", "HTR2A", "IFNL4", "ITGB3", "ITPA", "MTHFR", "NAT1", "OPRK1", "OPRM1", "POR",
- "PTGIS", "RARG", "SLC6A4", "SLC15A2", "SLC22A2", "SLC28A3", "SLC47A2", "SLCO1B3", "SLCO2B1",
- "SULT1A1", "TBXAS1", "UGT1A4", "UGT1A6", "UGT2B7", "UGT2B15", "UGT2B17", "XPC"

### What clinical guidelines does ZaroPGx use?

**CPIC (Clinical Pharmacogenomics Implementation Consortium)**
**DPWG (Dutch Pharmacogenetics Working Group)**
**FDA (U.S. Food and Drug Administration)**
- Evidence-based recommendations
- Regularly updated with new evidence
- Graded by strength of evidence
- Specific to gene-drug pairs

### How accurate are the results?

Accuracy depends on:
- **Input data quality**: Higher coverage = better accuracy
- **Gene complexity**: Some genes are harder to call accurately
- **Tool limitations**: Each tool has specific strengths/weaknesses
- **Reference genome**: GRCh38 generally more accurate than GRCh37

### Can I use this for clinical decision-making?

Please do not use the reports in an authoritative context such as a clinical setting. ZaroPGx is a hobby research tool and should not be used directly for clinical decision-making without:
- **Laboratory validation**: Verify results with clinical grade lab testing
- **Expert interpretation**: Review by a qualified practitioner

## Deployment Questions

### Can I run this in the cloud?

Yes, ZaroPGx can be deployed on any cloud provider supporting Docker.
Make sure you understand the differnce between a local and public/production deployment.
ZaroPGx does not include a reverse proxy; you will have to set one up yourself. 
NPM, Apache, Traefik, or Caddy are all good options for securing your deployment.

### How do I secure my ZaroPGx deployment?

**Network Security:**
- Use a reverse proxy
- Configure firewall rules
- Enable HTTPS/TLS encryption
- Implement access controls
- Configure security mechanisms via the docker compose file and .env file

**Data Security:**
- Encrypt data at rest
- Use secure authentication
- Regular security updates
- Audit logging

### Can I scale this for multiple users?

In theory, it is possible, however, a large-scale persistent deployment is not being developed at this time.

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
- **Offline capable**: Works without internet after initial setup
- **Data control**: You maintain complete control over your data

### Can I export my data?

Yes, ZaroPGx supports multiple export formats:
- **PDF reports**: Clinical reports
- **HTML reports**: Interactive web reports
- **JSON data**: Machine-readable results
- **FHIR format**: Healthcare interoperability standard (soon; coming in 0.3)
- **Raw data**: Original tool outputs

### How long is data retained?

Data retention is configurable:
- **Default**: Indefinite retention
- **Configurable**: Set retention periods in environment variables
- **Manual cleanup**: Remove data manually when needed
- **Backup**: Create backups before cleanup

## Troubleshooting

### Why is my analysis taking so long?

Common causes:
- **Large files**: Whole genome raw data especially takes longer
- **Low resources**: Insufficient CPU/RAM
- **Network issues**: Slow reference genome downloads, slow sample uploads
- **Storage drive I/O**: Slow storage bandwidth

**Solutions:**
1. Wait for completion despite long processing time, or
2. Try using a more powerful device, or
3. Disable computationally intensive services and try again

### Why are some genes showing "No Call"?

Possible causes:
- **Low coverage**: Insufficient sequencing depth
- **Poor quality**: Low-quality input data
- **Missing variants**: Variants not in reference
- **Tool limitations**: Some genes are harder to call

### Why is my PDF report empty?

Common causes:
- **Analysis failed**: Check docker compose logs and processing logs
- **Missing data**: Verify analysis completed
- **PDF engine problems**: Try different engine

## Development

### Can I contribute to ZaroPGx?

Yes! Your contribution to the open-source ZaroPGx development is welcomes:
- **Code contributions**: Bug fixes, new features
- **Documentation**: Improve guides and examples
- **Testing**: Report bugs, test new features
- **Community**: Help other users, share experiences (on GitHub)

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

## See Next

- **Get started**: {doc}`quick-start`
- **Learn more**: {doc}`usage`
- **Troubleshoot issues**: {doc}`troubleshooting`
- **Configure system**: {doc}`../advanced-configuration`
