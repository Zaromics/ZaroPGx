nextflow.enable.dsl=2

/*
  Minimal PGx Nextflow pipeline scaffold
  Stages (initial):
    - input: pass-through (expects pre-detected file type from API)
    - optional: bam2vcf via PyPGx when BAM input
    - pyPgx: multi-gene genotype (ALL) -> produce outside.tsv
    - pharmcat: run analysis with outside.tsv

  Inputs/Outputs are file-path based; integration with FastAPI will pass params.
*/

params.input          = params.input ?: ''
params.input_type     = params.input_type ?: ''  // vcf|bam|cram|sam|fastq
params.patient_id     = params.patient_id ?: ''
params.report_id      = params.report_id ?: ''
params.reference      = params.reference ?: 'hg38'
params.outdir         = params.outdir ?: "data/reports/${params.patient_id}"

process PyPGxBam2Vcf {
    tag "bam2vcf_${params.patient_id}"
    publishDir params.outdir, mode: 'copy'

    input:
    path bam

    output:
    path "*.vcf", emit: vcf

    when:
    params.input_type == 'bam'

    shell:
    '''
    set -euo pipefail
    curl -s -X POST -F reference_genome=${params.reference} -F patient_id=${params.patient_id} -F report_id=${params.report_id} \
         -F file=@${bam} http://pypgx:5000/create-input-vcf > response.json
    VCF_PATH=$(python3 - <<'PY'
import json; import sys
data=json.load(open('response.json'))
print(data.get('vcf_path') or data.get('vcf') or '')
PY
)
    test -n "$VCF_PATH" && cp "$VCF_PATH" .
    '''
}

process PyPGxGenotypeAll {
    tag "pypgx_${params.patient_id}"
    publishDir params.outdir, mode: 'copy'

    input:
    path vcf

    output:
    path "*.outside.tsv", optional: true, emit: outside
    path "pypgx_result.json", emit: pypgx_json

    shell:
    '''
    set -euo pipefail
    curl -s -X POST -F genes=ALL -F reference_genome=${params.reference} -F patient_id=${params.patient_id} -F report_id=${params.report_id} \
         -F file=@${vcf} http://pypgx:5000/genotype > pypgx_result.json
    python3 - <<'PY'
import json,sys
data=json.load(open('pypgx_result.json'))
res=data.get('results') or {}
lines=[]
for gene,resu in res.items():
    if not isinstance(resu,dict) or not resu.get('success'):
        continue
    dip=resu.get('diplotype') or ''
    det=resu.get('details') or {}
    ph=det.get('phenotype') or det.get('Phenotype') or ''
    act=det.get('activity_score') or det.get('activityScore') or ''
    if any([dip,ph,act]):
        lines.append(f"{gene}\t{dip}\t{ph}\t{act}")
open('pharmcat.outside.tsv','w',encoding='utf-8').write('\n'.join(lines)+'\n') if lines else None
PY
    '''
}

process PharmCATRun {
    tag "pharmcat_${params.patient_id}"
    publishDir params.outdir, mode: 'copy'

    input:
    path vcf
    path outside_tsv optional true

    output:
    path "${params.patient_id}_pgx_pharmcat.html", optional: true
    path "${params.patient_id}_pgx_pharmcat.json", optional: true
    path "${params.patient_id}_pgx_pharmcat.tsv", optional: true

    shell:
    '''
    set -euo pipefail
    CURL_ARGS=( -s -X POST -F patientId=${params.patient_id} -F reportId=${params.report_id} -F file=@${vcf} )
    if [ -f "${outside_tsv}" ]; then
      CURL_ARGS+=( -F outside_tsv=@${outside_tsv} )
    fi
    curl "${CURL_ARGS[@]}" http://pharmcat:5000/process > pharmcat_result.json || true
    # PharmCAT container should emit outputs to mounted /data/reports/<patient_id>, but we copy any present here as well
    for f in /data/reports/${params.patient_id}/${params.patient_id}_pgx_pharmcat.*; do
      [ -f "$f" ] && cp "$f" . || true
    done
    '''
}

workflow {
    main:
    assert params.input, 'Missing --input path'
    file inputFile = file(params.input)

    Channel.fromPath(inputFile).set { input_ch }

    vcf_ch = (
        params.input_type == 'bam' ? PyPGxBam2Vcf(input_ch).vcf : input_ch
    )

    geno = PyPGxGenotypeAll(vcf_ch)
    PharmCATRun(vcf_ch, geno.outside)
}


