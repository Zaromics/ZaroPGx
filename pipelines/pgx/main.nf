nextflow.enable.dsl=2

/*
  Comprehensive PGx Nextflow pipeline
  
  Optimal workflow (FASTQ input - per workflow_logic.md):
    FASTQ -> OptiType/HLA calling (parallel) + GATK alignment -> BAM -> PyPGx -> PharmCAT
  
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
params.skip_pypgx     = params.skip_pypgx != null ? params.skip_pypgx : false
params.sample_identifier = params.sample_identifier ?: ''

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
    CURL_ARGS=( -X POST -F reference_genome=!{reference} -F patient_id=!{patient_id} -F report_id=!{report_id} -F file=@!{fastq} )
    if [ -n "${WORKFLOW_ID:-}" ]; then
      CURL_ARGS+=( -F workflow_id=${WORKFLOW_ID} -F step_name=gatk_alignment )
    fi
    curl "${CURL_ARGS[@]}" http://gatk-api:5000/align-fastq > align_response.json 2>gatk.log
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
    CURL_ARGS=( -X POST -F reference_genome=!{reference} -F patient_id=!{patient_id} -F report_id=!{report_id} -F file=@!{cram} )
    if [ -n "${WORKFLOW_ID:-}" ]; then
      CURL_ARGS+=( -F workflow_id=${WORKFLOW_ID} -F step_name=gatk_cram_to_bam )
    fi
    curl "${CURL_ARGS[@]}" http://gatk-api:5000/cram-to-bam > cram_response.json 2>gatk.log
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
    CURL_ARGS=( -X POST -F reference_genome=!{reference} -F patient_id=!{patient_id} -F report_id=!{report_id} -F file=@!{sam} )
    if [ -n "${WORKFLOW_ID:-}" ]; then
      CURL_ARGS+=( -F workflow_id=${WORKFLOW_ID} -F step_name=gatk_sam_to_bam )
    fi
    curl "${CURL_ARGS[@]}" http://gatk-api:5000/sam-to-bam > sam_response.json 2>gatk.log
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
    CURL_ARGS=( -X POST -F reference_genome=!{reference} -F patient_id=!{patient_id} -F report_id=!{report_id} -F file=@!{fastq} )
    if [ -n "${WORKFLOW_ID:-}" ]; then
      CURL_ARGS+=( -F workflow_id=${WORKFLOW_ID} -F step_name=hlatyping_fastq )
    fi
    curl "${CURL_ARGS[@]}" http://hlatyping:5000/call-hla > hla_result.json 2>hla.log
    python3 - <<'PY'
import json,sys
data=json.load(open('hla_result.json'))
results=data.get('results') or {}
lines=[]
for gene,call in results.items():
    if call and gene.startswith('HLA-'):
        lines.append(f"{gene}\t{call}")
if lines:
    open('pharmcat.hla_calls.tsv','w',encoding='utf-8').write('\\n'.join(lines)+'\\n')
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
    CURL_ARGS=( -X POST -F reference_genome=!{reference} -F patient_id=!{patient_id} -F report_id=!{report_id} -F file=@!{bam} )
    if [ -n "${WORKFLOW_ID:-}" ]; then
      CURL_ARGS+=( -F workflow_id=${WORKFLOW_ID} -F step_name=hlatyping_bam )
    fi
    curl "${CURL_ARGS[@]}" http://hlatyping:5000/call-hla > hla_result.json 2>hla.log
    python3 - <<'PY'
import json,sys
data=json.load(open('hla_result.json'))
results=data.get('results') or {}
lines=[]
for gene,call in results.items():
    if call and gene.startswith('HLA-'):
        lines.append(f"{gene}\t{call}")
if lines:
    open('pharmcat.hla_calls.tsv','w',encoding='utf-8').write('\\n'.join(lines)+'\\n')
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
    CURL_ARGS=( -X POST -F reference_genome=!{reference} -F patient_id=!{patient_id} -F report_id=!{report_id} -F file=@!{bam} )
    if [ -n "${WORKFLOW_ID:-}" ]; then
      CURL_ARGS+=( -F workflow_id=${WORKFLOW_ID} -F step_name=pypgx_bam2vcf )
    fi
    curl "${CURL_ARGS[@]}" http://pypgx:5000/create-input-vcf > response.json 2>pypgx_bam2vcf.log
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
    # Capture both stdout and stderr from PyPGx container
    CURL_ARGS=( -f -X POST -F genes=ALL -F reference_genome=!{reference} -F patient_id=!{patient_id} -F report_id=!{report_id} -F file=@!{vcf} -F input_type=!{params.input_type} )
    if [ -n "${WORKFLOW_ID:-}" ]; then
      CURL_ARGS+=( -F workflow_id=${WORKFLOW_ID} -F step_name=pypgx_analysis )
    fi
    if curl "${CURL_ARGS[@]}" http://pypgx:5000/genotype > pypgx_result.json 2>pypgx_stderr.log; then
      echo "PyPGx API call succeeded" >&2
      export PYPGX_SUCCESS=true
    else
      echo "PyPGx API completely failed - bypassing PyPGx and going direct to PharmCAT" >&2
      # Create error JSON but don't create outside.tsv file
      echo '{"success": false, "error": "PyPGx service unavailable", "results": {}}' > pypgx_result.json
      export PYPGX_SUCCESS=false
    fi
    python3 - <<PY
import json,sys,os
pypgx_success = os.environ.get('PYPGX_SUCCESS', 'false').lower() == 'true'

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
    
    CURL_ARGS=( -s -X POST -F patient_id=!{patient_id} -F report_id=!{report_id} -F file=@!{vcf} )
    if [ -n "!{params.sample_identifier}" ]; then
      CURL_ARGS+=( -F sample_identifier=!{params.sample_identifier} )
    fi
    if [ -s combined_outside.tsv ]; then
      CURL_ARGS+=( -F outside_tsv=@combined_outside.tsv )
    fi
    # Add workflow_id if available
    if [ -n "${WORKFLOW_ID:-}" ]; then
      CURL_ARGS+=( -F workflow_id=${WORKFLOW_ID} -F step_name=pharmcat_analysis )
    fi
    curl "${CURL_ARGS[@]}" http://pharmcat:5000/genotype > pharmcat_result.json 2>pharmcat.log || true
    
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
    
    // For FASTQ: HLA first (if enabled), then BAM conversion, then PyPGx sequentially
    if (params.input_type == 'fastq') {
        // Step 1: HLA typing on FASTQ (optimal - no conversion needed)
        hla_result = OptiTypeHLAFromFastq(input_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch)
        hla_ch = hla_result.hla
        
        // Step 2: Convert FASTQ to BAM
        bam_ch = FastqToBAM(input_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).bam
        
        // Step 3: PyPGx waits for HLA to complete
        hla_complete_ch = hla_result.hla_json.combine(bam_ch).map { hla_json, bam_file -> bam_file }
        vcf_ch = PyPGxBam2Vcf(hla_complete_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).vcf
    }
    // For CRAM: convert to BAM, then HLA + PyPGx sequentially
    else if (params.input_type == 'cram') {
        bam_ch = CramToBAM(input_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).bam
        
        hla_result = OptiTypeHLAFromBAM(bam_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch)
        hla_ch = hla_result.hla
        
        // Create a dependency: PyPGx waits for HLA to complete
        hla_complete_ch = hla_result.hla_json.combine(bam_ch).map { hla_json, bam_file -> bam_file }
        vcf_ch = PyPGxBam2Vcf(hla_complete_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).vcf
    }
    // For SAM: convert to BAM, then HLA + PyPGx sequentially
    else if (params.input_type == 'sam') {
        bam_ch = SamToBAM(input_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).bam
        
        hla_result = OptiTypeHLAFromBAM(bam_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch)
        hla_ch = hla_result.hla
        
        // Create a dependency: PyPGx waits for HLA to complete
        hla_complete_ch = hla_result.hla_json.combine(bam_ch).map { hla_json, bam_file -> bam_file }
        vcf_ch = PyPGxBam2Vcf(hla_complete_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).vcf
    }
    // For BAM: HLA first, then PyPGx sequentially
    else if (params.input_type == 'bam') {
        hla_result = OptiTypeHLAFromBAM(input_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch)
        hla_ch = hla_result.hla
        
        // Create a dependency: PyPGx waits for HLA to complete
        // Use the hla_json output as a trigger to start PyPGx with the original BAM
        hla_complete_ch = hla_result.hla_json.combine(input_ch).map { hla_json, bam_file -> bam_file }
        vcf_ch = PyPGxBam2Vcf(hla_complete_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).vcf
    }
    // For VCF: quick pipeline, no HLA
    else if (params.input_type == 'vcf') {
        vcf_ch = input_ch
        hla_ch = empty_file_ch
    }
    else {
        error "Unsupported input type: ${params.input_type}. Supported: vcf, bam, cram, sam, fastq"
    }

    // Run PyPGx genotyping on VCF (if enabled)
    if (params.skip_pypgx) {
        pypgx_outside = empty_file_ch
        // When PyPGx is skipped, run PharmCAT directly on VCF
        hla_outside = (params.skip_hla || params.input_type == 'vcf') ? empty_file_ch : hla_ch.ifEmpty(empty_file_ch)
        PharmCATRun(
            vcf_ch,
            pypgx_outside,
            hla_outside,
            patient_id_ch,
            report_id_ch,
            outdir_ch
        )
    } else {
        // PyPGx is enabled - run it first, then PharmCAT
        pypgx_result = PyPGxGenotypeAll(vcf_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch)
        // Handle PyPGx results: if PyPGx completely failed (service unavailable),
        // it won't emit an outside.tsv file, so Nextflow uses empty_file_ch.
        // If PyPGx succeeded but produced no valid results, it may emit an empty file.
        // In both cases, PharmCAT will skip outside calls if the file is empty.
        pypgx_outside = pypgx_result.outside.ifEmpty(empty_file_ch)
        
        hla_outside = (params.skip_hla || params.input_type == 'vcf') ? empty_file_ch : hla_ch.ifEmpty(empty_file_ch)
        
        // Create dependency: PharmCAT waits for PyPGx to complete
        // This ensures sequential execution: PyPGx -> PharmCAT
        pypgx_complete_ch = pypgx_result.pypgx_json.combine(vcf_ch).map { pypgx_json, vcf_file -> vcf_file }
        
        PharmCATRun(
            pypgx_complete_ch,
            pypgx_outside,
            hla_outside,
            patient_id_ch,
            report_id_ch,
            outdir_ch
        )
    }
}


