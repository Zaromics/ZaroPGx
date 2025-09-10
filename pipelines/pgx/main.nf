nextflow.enable.dsl=2

/*
  Comprehensive PGx Nextflow pipeline
  
  Optimal workflow (FASTQ input - per workflow_logic.md):
    FASTQ -> OptiType/HLA calling -> GATK alignment -> BAM -> PyPGx -> PharmCAT
  
  Alternative workflows:
    CRAM/SAM -> GATK conversion -> BAM -> OptiType/HLA + PyPGx -> PharmCAT
    BAM -> OptiType/HLA + PyPGx -> PharmCAT  
    VCF -> PyPGx -> PharmCAT (quick pipeline, no HLA)

  Inputs/Outputs are file-path based; integration with FastAPI will pass params.
*/

params.input          = params.input ?: ''
params.input_type     = params.input_type ?: ''  // vcf|bam|cram|sam|fastq
params.patient_id     = params.patient_id ?: ''
params.report_id      = params.report_id ?: ''
params.reference      = params.reference ?: 'hg38'
params.outdir         = params.outdir ?: "data/reports/${params.patient_id}"
params.skip_hla       = params.skip_hla != null ? params.skip_hla : false

// FASTQ alignment process (OPTIMAL STARTING FORMAT per workflow_logic.md)
process FastqToBAM {
    tag "align_${patient_id}"
    publishDir "${outdir}", mode: 'copy'

    input:
    path fastq
    val patient_id
    val report_id
    val reference
    val outdir

    output:
    path "*.bam", emit: bam

    shell:
    '''
    set -euo pipefail
    curl -s -X POST -F reference_genome=!{reference} -F patient_id=!{patient_id} -F report_id=!{report_id} \
         -F file=@!{fastq} http://gatk-api:5000/align-fastq > align_response.json
    BAM_PATH=$(python3 - <<'PY'
import json; import sys
data=json.load(open('align_response.json'))
print(data.get('bam_path') or data.get('bam') or '')
PY
)
    test -n "$BAM_PATH" && cp "$BAM_PATH" .
    '''
}

// CRAM to BAM conversion
process CramToBAM {
    tag "cram2bam_${patient_id}"
    publishDir "${outdir}", mode: 'copy'

    input:
    path cram
    val patient_id
    val report_id
    val reference
    val outdir

    output:
    path "*.bam", emit: bam

    shell:
    '''
    set -euo pipefail
    curl -s -X POST -F reference_genome=!{reference} -F patient_id=!{patient_id} -F report_id=!{report_id} \
         -F file=@!{cram} http://gatk-api:5000/cram-to-bam > cram_response.json
    BAM_PATH=$(python3 - <<'PY'
import json; import sys
data=json.load(open('cram_response.json'))
print(data.get('bam_path') or data.get('bam') or '')
PY
)
    test -n "$BAM_PATH" && cp "$BAM_PATH" .
    '''
}

// SAM to BAM conversion
process SamToBAM {
    tag "sam2bam_${patient_id}"
    publishDir "${outdir}", mode: 'copy'

    input:
    path sam
    val patient_id
    val report_id
    val reference
    val outdir

    output:
    path "*.bam", emit: bam

    shell:
    '''
    set -euo pipefail
    curl -s -X POST -F reference_genome=!{reference} -F patient_id=!{patient_id} -F report_id=!{report_id} \
         -F file=@!{sam} http://gatk-api:5000/sam-to-bam > sam_response.json
    BAM_PATH=$(python3 - <<'PY'
import json; import sys
data=json.load(open('sam_response.json'))
print(data.get('bam_path') or data.get('bam') or '')
PY
)
    test -n "$BAM_PATH" && cp "$BAM_PATH" .
    '''
}

// OptiType HLA calling on FASTQ (OPTIMAL per workflow_logic.md)
process OptiTypeHLAFromFastq {
    tag "hla_fastq_${patient_id}"
    publishDir "${outdir}", mode: 'copy'

    input:
    path fastq
    val patient_id
    val report_id
    val reference
    val outdir

    output:
    path "*.hla_calls.tsv", optional: true, emit: hla
    path "hla_result.json", emit: hla_json

    shell:
    '''
    set -euo pipefail
    curl -s -X POST -F reference_genome=!{reference} -F patient_id=!{patient_id} -F report_id=!{report_id} \
         -F file=@!{fastq} http://hlatyping:5000/call-hla > hla_result.json
    python3 - <<'PY'
import json,sys
data=json.load(open('hla_result.json'))
results=data.get('results') or {}
lines=[]
for gene,call in results.items():
    if call and gene.startswith('HLA-'):
        lines.append(f"{gene}\t{call}")
if lines:
    open('pharmcat.hla_calls.tsv','w',encoding='utf-8').write('\n'.join(lines)+'\n')
PY
    '''
}

// OptiType HLA calling on BAM (will internally convert to FASTQ - less optimal)
process OptiTypeHLAFromBAM {
    tag "hla_bam_${patient_id}"
    publishDir "${outdir}", mode: 'copy'

    input:
    path bam
    val patient_id
    val report_id
    val reference
    val outdir

    output:
    path "*.hla_calls.tsv", optional: true, emit: hla
    path "hla_result.json", emit: hla_json

    shell:
    '''
    set -euo pipefail
    curl -s -X POST -F reference_genome=!{reference} -F patient_id=!{patient_id} -F report_id=!{report_id} \
         -F file=@!{bam} http://hlatyping:5000/call-hla > hla_result.json
    python3 - <<'PY'
import json,sys
data=json.load(open('hla_result.json'))
results=data.get('results') or {}
lines=[]
for gene,call in results.items():
    if call and gene.startswith('HLA-'):
        lines.append(f"{gene}\t{call}")
if lines:
    open('pharmcat.hla_calls.tsv','w',encoding='utf-8').write('\n'.join(lines)+'\n')
PY
    '''
}

process PyPGxBam2Vcf {
    tag "bam2vcf_${patient_id}"
    publishDir "${outdir}", mode: 'copy'

    input:
    path bam
    val patient_id
    val report_id
    val reference
    val outdir

    output:
    path "*.vcf", emit: vcf

    shell:
    '''
    set -euo pipefail
    curl -s -X POST -F reference_genome=!{reference} -F patient_id=!{patient_id} -F report_id=!{report_id} \
         -F file=@!{bam} http://pypgx:5000/create-input-vcf > response.json
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
    tag "pypgx_${patient_id}"
    publishDir "${outdir}", mode: 'copy'

    input:
    path vcf
    val patient_id
    val report_id
    val reference
    val outdir

    output:
    path "pypgx_result.json", emit: pypgx_json
    path "*.outside.tsv", optional: true, emit: outside

    shell:
    '''
    # Don't use set -e here to allow graceful error handling
    set -uo pipefail
    # Try curl, but don't fail if it returns HTTP errors
    if curl -s -f -X POST -F genes=ALL -F reference_genome=!{reference} -F patient_id=!{patient_id} -F report_id=!{report_id} \
         -F file=@!{vcf} http://pypgx:5000/genotype > pypgx_result.json 2>/dev/null; then
      echo "PyPGx API call succeeded" >&2
      export PYPX_SUCCESS=true
    else
      echo "PyPGx API completely failed - bypassing PyPGx and going direct to PharmCAT" >&2
      # Create error JSON but don't create outside.tsv file
      echo '{"success": false, "error": "PyPGx service unavailable", "results": {}}' > pypgx_result.json
      export PYPX_SUCCESS=false
    fi
    python3 - <<PY
import json,sys,os
pypgx_success = os.environ.get('PYPX_SUCCESS', 'false').lower() == 'true'

try:
    if os.path.exists('pypgx_result.json'):
        with open('pypgx_result.json', 'r') as f:
            data = json.load(f)
        res = data.get('results') or {}
    else:
        print("PyPGx result file not found, creating empty results", file=sys.stderr)
        res = {}
except (json.JSONDecodeError, IOError) as e:
    print(f"Error reading PyPGx results: {e}, creating empty results", file=sys.stderr)
    res = {}

# Only create outside.tsv if PyPGx service was actually available
if pypgx_success:
    lines = []
    for gene, resu in res.items():
        if not isinstance(resu, dict) or not resu.get('success'):
            continue
        dip = resu.get('diplotype') or ''
        det = resu.get('details') or {}
        ph = det.get('phenotype') or det.get('Phenotype') or ''
        act = det.get('activity_score') or det.get('activityScore') or ''
        if any([dip, ph, act]):
            lines.append(f"{gene}\t{dip}\t{ph}\t{act}")

    if lines:
        with open('pharmcat.outside.tsv', 'w', encoding='utf-8') as f:
            f.write('\\n'.join(lines) + '\\n')
        print(f"Created outside.tsv with {len(lines)} gene results", file=sys.stderr)
    else:
        print("PyPGx succeeded but no valid gene results found", file=sys.stderr)
else:
    print("PyPGx service unavailable - skipping outside.tsv creation", file=sys.stderr)
PY
    '''
}

process PharmCATRun {
    tag "pharmcat_${patient_id}"
    publishDir "${outdir}", mode: 'copy'

    input:
    path vcf
    path outside_tsv, stageAs: 'pypgx_outside.tsv'
    path hla_tsv, stageAs: 'hla_outside.tsv'
    val patient_id
    val report_id
    val outdir

    output:
    path "${patient_id}_pgx_pharmcat.html", optional: true
    path "${patient_id}_pgx_pharmcat.json", optional: true
    path "${patient_id}_pgx_pharmcat.tsv", optional: true

    shell:
    '''
    set -euo pipefail
    
    # Combine PyPGx and HLA outside calls into single file
    cat /dev/null > combined_outside.tsv
    [ -f "pypgx_outside.tsv" ] && cat pypgx_outside.tsv >> combined_outside.tsv
    [ -f "hla_outside.tsv" ] && cat hla_outside.tsv >> combined_outside.tsv
    
    CURL_ARGS=( -s -X POST -F patientId=!{patient_id} -F reportId=!{report_id} -F file=@!{vcf} )
    if [ -s combined_outside.tsv ]; then
      CURL_ARGS+=( -F outside_tsv=@combined_outside.tsv )
    fi
    curl "${CURL_ARGS[@]}" http://pharmcat:5000/process > pharmcat_result.json || true
    
    # Copy outputs from mounted volume
    for f in /data/reports/!{patient_id}/!{patient_id}_pgx_pharmcat.*; do
      [ -f "$f" ] && cp "$f" . || true
    done
    '''
}

// Helper process to create empty files for optional inputs
process CreateEmptyFile {
    output:
    path 'empty.tsv', emit: empty_tsv
    
    script:
    '''
    touch empty.tsv
    '''
}

workflow {
    main:
    assert params.input, 'Missing --input path'
    assert params.input_type, 'Missing --input_type (vcf|bam|cram|sam|fastq)'
    
    // Create input channels
    input_ch = Channel.fromPath(params.input)
    
    // Create parameter channels  
    patient_id_ch = Channel.value(params.patient_id)
    report_id_ch = Channel.value(params.report_id)
    reference_ch = Channel.value(params.reference)
    outdir_ch = Channel.value(params.outdir)
    
    // Create an actual empty file for optional inputs
    empty_file_ch = CreateEmptyFile().empty_tsv

    // Handle different input types with optimal HLA calling strategy
    
    // For FASTQ: HLA first (optimal), then alignment
    if (params.input_type == 'fastq') {
        hla_result = OptiTypeHLAFromFastq(input_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch)
        hla_ch = hla_result.hla
        
        bam_ch = FastqToBAM(input_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).bam
        vcf_ch = PyPGxBam2Vcf(bam_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).vcf
    }
    // For CRAM: convert to BAM, then HLA + PyPGx
    else if (params.input_type == 'cram') {
        bam_ch = CramToBAM(input_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).bam
        
        hla_result = OptiTypeHLAFromBAM(bam_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch)
        hla_ch = hla_result.hla
        
        vcf_ch = PyPGxBam2Vcf(bam_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).vcf
    }
    // For SAM: convert to BAM, then HLA + PyPGx  
    else if (params.input_type == 'sam') {
        bam_ch = SamToBAM(input_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).bam
        
        hla_result = OptiTypeHLAFromBAM(bam_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch)
        hla_ch = hla_result.hla
        
        vcf_ch = PyPGxBam2Vcf(bam_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).vcf
    }
    // For BAM: HLA + PyPGx directly
    else if (params.input_type == 'bam') {
        hla_result = OptiTypeHLAFromBAM(input_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch)
        hla_ch = hla_result.hla
        
        vcf_ch = PyPGxBam2Vcf(input_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).vcf
    }
    // For VCF: quick pipeline, no HLA
    else if (params.input_type == 'vcf') {
        vcf_ch = input_ch
        hla_ch = empty_file_ch
    }
    else {
        error "Unsupported input type: ${params.input_type}. Supported: vcf, bam, cram, sam, fastq"
    }

    // Run PyPGx genotyping on VCF
    pypgx_result = PyPGxGenotypeAll(vcf_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch)

    // Handle PyPGx results: if PyPGx completely failed (service unavailable),
    // it won't emit an outside.tsv file, so Nextflow uses empty_file_ch.
    // If PyPGx succeeded but produced no valid results, it may emit an empty file.
    // In both cases, PharmCAT will skip outside calls if the file is empty.
    pypgx_outside = pypgx_result.outside.ifEmpty(empty_file_ch)

    hla_outside = (params.skip_hla || params.input_type == 'vcf') ? empty_file_ch : hla_ch.ifEmpty(empty_file_ch)

    // Run PharmCAT - will automatically skip outside calls if files are empty
    PharmCATRun(
        vcf_ch,
        pypgx_outside,
        hla_outside,
        patient_id_ch,
        report_id_ch,
        outdir_ch
    )
}


