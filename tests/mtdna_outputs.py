"""mtDNA-Server 2's files in a job's report directory, as two real runs left them.

Both runs are from the app image built from the 2026-10-24 rehearsal (2026-10-20), as
MtdnaCall (pipelines/pgx/main.nf) published them:

- ``write_no_chrm_vcf_run``: PharmCAT's example VCF (test_data/pharmcat.example.vcf),
  which has no chrM record. mtdna_result.json is a no-call with reason no_chrm_data
  and no haplogroup; mtdna_variants.vcf.gz is the VCF's header and no record; there is
  no mtdna_report.html (a VCF never gets one).
- ``write_na12878_run``: the public NA12878 demo CRAM. Haplogroup H13a1a1a, MT-RNR1
  Reference, a chrM VCF with records and mtDNA-Server 2's report (8.9 MB there; a
  stand-in here).
"""

import gzip
import json
from pathlib import Path

NO_CHRM_RESULT = {
    "success": True,
    "patient_id": "272af295-247d-4d46-9638-4a4f79e28af7",
    "haplogroup": None,
    "haplogroup_quality": None,
    "haplogroup_range": None,
    "mt_rnr1": None,
    "mt_rnr1_no_call_reason": "no_chrm_data",
    "evidence_basis": None,
    "mt_rnr1_all_matches": [],
    "variants": [],
    "chrm_vcf": "/data/temp/mtdna/e9958c90-3571-4c5a-a0fe-ac257368f8ac/chrM.norm.vcf.gz",
    "report_html": None,
    "report_unavailable_reason": (
        "mtDNA-Server 2's report needs coverage and contamination metrics, which are "
        "only produced from an alignment (BAM/CRAM/FASTQ)."
    ),
}

NO_CHRM_VCF = """\
##fileformat=VCFv4.2
##FILTER=<ID=PASS,Description="All filters passed">
##source=PharmCAT allele definitions
##contig=<ID=chr1,assembly=GRCh38.p13,species="Homo sapiens">
##contig=<ID=chrX,assembly=GRCh38.p13,species="Homo sapiens">
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
##bcftools_normVersion=1.19+htslib-1.19.1
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSample_1
"""

NA12878_RESULT = {
    "success": True,
    "patient_id": "f39cb25a-3f5b-49e2-bc8a-e7a280bccc0d",
    "haplogroup": "H13a1a1a",
    "haplogroup_quality": "0.9957",
    "haplogroup_range": "1-16569",
    "mt_rnr1": "Reference",
    "mt_rnr1_no_call_reason": None,
    "evidence_basis": "measured",
    "mt_rnr1_all_matches": [],
    "mean_coverage": 1314.6058700209644,
    "variants": [
        {"pos": 750, "ref": "A", "alt": "G"},
        {"pos": 1438, "ref": "A", "alt": "G"},
    ],
    "chrm_vcf": "/data/temp/mtdna/d2101253-141b-4d07-ac0b-3b9f807f84c8/chrM.norm.vcf.gz",
    "report_html": "/data/temp/mtdna/d2101253-141b-4d07-ac0b-3b9f807f84c8/report.html",
    "report_unavailable_reason": None,
}

NA12878_VCF = """\
##fileformat=VCFv4.2
##FILTER=<ID=PASS,Description="All filters passed">
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
##Mutserve=v2.0.3
##contig=<ID=chrM,length=16569>
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tchrM.bam
chrM\t152\t.\tT\tC\t.\tPASS\t.\tGT:AF:BQ:DP\t1:0.999:29.97:1169
chrM\t263\t.\tA\tG\t.\tPASS\t.\tGT:AF:BQ:DP\t1:1.00:29.65:1053
chrM\t750\t.\tA\tG\t.\tPASS\t.\tGT:AF:BQ:DP\t1:1.00:29.94:1234
chrM\t1438\t.\tA\tG\t.\tPASS\t.\tGT:AF:BQ:DP\t1:1.00:29.99:1314
"""


def write_vcf_gz(path: Path, text: str) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(text)


def write_no_chrm_vcf_run(job_dir: Path) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "mtdna_result.json").write_text(json.dumps(NO_CHRM_RESULT))
    write_vcf_gz(job_dir / "mtdna_variants.vcf.gz", NO_CHRM_VCF)


def write_na12878_run(job_dir: Path) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "mtdna_result.json").write_text(json.dumps(NA12878_RESULT))
    write_vcf_gz(job_dir / "mtdna_variants.vcf.gz", NA12878_VCF)
    (job_dir / "mtdna_report.html").write_text(
        "<!DOCTYPE html><html><head><title>mtDNA-Server 2</title></head></html>"
    )
