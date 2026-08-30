nextflow.enable.dsl=2

/*
  Comprehensive PGx Nextflow pipeline
  
  Optimal workflow (FASTQ input - per workflow_logic.md):
    FASTQ -> OptiType/HLA calling (parallel) + GATK alignment -> BAM -> PyPGx -> PharmCAT
  
  Alternative workflows:
    CRAM/SAM -> GATK conversion -> BAM -> OptiType/HLA + PyPGx -> PharmCAT
    BAM -> OptiType/HLA + PyPGx -> PharmCAT  
    VCF -> PyPGx -> PharmCAT (quick pipeline, no HLA)
    GRCh37/hg19 VCF -> GATK LiftoverVcf (via gatk-api) -> GRCh38 VCF -> PyPGx -> PharmCAT

  Inputs/Outputs are file-path based; integration with FastAPI will pass params.

  HTTP error handling: service calls use `curl -sS --fail-with-body` so that an HTTP
  error is an error. Plain curl exits 0 on 4xx/5xx, writes the error document where the
  result belongs, and lets the process carry on - the failure then surfaces much later
  as a bare "exit status (1)" from some downstream step, pointing nowhere near the call
  that actually failed. --fail-with-body (curl >= 7.76; the image ships 8.5.0) fails the
  call while keeping the server's message, which the caller echoes to stderr. -sS
  replaces the old `2>service.log` redirect, which was swallowing curl's own diagnostic
  into a work-dir file nobody reads; on stderr it reaches .command.err and therefore
  Nextflow's error report. Two call sites are deliberately exempt and say so inline:
  PyPGxGenotypeAll and PharmCATRun.
*/

params.input          = params.input ?: ''
params.input_type     = params.input_type ?: ''  // vcf|bam|cram|sam|fastq
params.patient_id     = params.patient_id ?: ''
params.report_id      = params.report_id ?: ''
params.reference      = params.reference ?: 'hg38'
params.outdir         = params.outdir ?: "data/reports/${params.patient_id}"
params.skip_hla       = params.skip_hla != null ? params.skip_hla : false
params.skip_pypgx     = params.skip_pypgx != null ? params.skip_pypgx : false
// skip_gatk covers the three GATK-container conversions (FastqToBAM/CramToBAM/SamToBAM).
// It is a no-op for vcf/bam input (no GATK process is invoked) and rejected for
// fastq/cram/sam, where there is no non-GATK route to a BAM - see the guard below.
params.skip_gatk      = params.skip_gatk != null ? params.skip_gatk : false
// skip_report is the ZaroPGx custom-report toggle. It is honoured, but app-side,
// not here: no process in this pipeline reads the param below. It is carried this
// far so the app stops silently dropping it - declared on NextflowRunRequest,
// emitted on the argv - and declared here so it shows up in the run's resolved
// params. There is no report process in this pipeline to gate. The gate that
// actually suppresses the report is upload_router.py's final-stages handler
// (_handle_final_stages_progression_sync), which skips generate_report() when
// needs_report is false; needs_report also drops the step template in
// app/services/workflow_registry.py. That app-side gate is the only thing acting
// on the toggle - declaring the param here is not a second line of defence. A
// report process added to this pipeline later must gate on it itself.
params.skip_report    = params.skip_report != null ? params.skip_report : false
// sample_identifier is a free-text, user-supplied field. It is DELIBERATELY not a
// pipeline param: a `val`/`params` string is interpolated verbatim into the shell
// block (Nextflow escapes only `path` inputs), so a value containing a double quote
// breaks out of the surrounding quoting and the rest runs as shell - unauthenticated
// RCE, since this container holds the Docker socket. The value now travels to
// PharmCATRun as the SAMPLE_IDENTIFIER environment variable (set by the runner,
// exactly as JOB_ID already is) and is referenced there as "$SAMPLE_IDENTIFIER".
// A bash variable expansion is data, never re-parsed as code, whatever it contains -
// which is the structural fix, not a smarter escape. Do not reintroduce a
// params.sample_identifier that any shell block interpolates.
params.pharmcat_absent_to_ref = params.pharmcat_absent_to_ref ?: 'false'
params.pharmcat_unspecified_to_ref = params.pharmcat_unspecified_to_ref ?: 'false'
// source_build: the build the *input file* is expressed against, as detected by the
// app's header inspector (empty when it is already GRCh38 or unknown). When it names
// GRCh37/hg19 and the input is a VCF, the LiftoverVCF process converts the file to
// GRCh38 coordinates before anything else touches it. This is deliberately separate
// from params.reference, which names the TARGET build the pipeline analyses against
// (hg38): conflating the two is how a GRCh37 file used to sail through on its
// original coordinates. The value is interpolated into a shell block below, so the
// runner (docker/nextflow/runner.py) allowlist-validates it exactly as it does
// params.reference; do not widen that alphabet.
params.source_build   = params.source_build ?: ''
// mtDNA calling via the mtdna sidecar (mutserve + haplogrep3 + haplocheck, from
// mtDNA-Server 2 v2.1.16). DEFAULT TRUE (skipped) -- deliberately the odd one out
// among the skip_* flags above, which all default to false. The UI toggle
// (needs_mtdna, WorkflowOptions) and upload_router's own skip_mtdna computation
// (app/api/routes/upload_router.py) now exist and default the same way: off
// unless the user opts in. This default is the fallback for any caller that
// posts to the runner without setting the field at all -- e.g. a direct
// nextflow /run call that bypasses upload_router -- which must land on the
// same safe "skipped" state a real job gets when its toggle is unchecked, not
// silently start producing mtDNA results. Keep this true in lockstep with
// upload_router's own default; do not "fix" this default in isolation.
params.skip_mtdna     = params.skip_mtdna != null ? params.skip_mtdna : true

// FASTQ alignment
process FastqToBAM {
    tag "align_${patient_id}"
    publishDir { outdir }, mode: 'copy'

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
    if [ -n "${JOB_ID:-}" ]; then
      CURL_ARGS+=( -F job_id=${JOB_ID} -F step_name=gatk_alignment )
    fi
    if ! curl -sS --fail-with-body "${CURL_ARGS[@]}" http://gatk-api:5000/align-fastq > align_response.json; then
      echo "gatk-api /align-fastq returned an error:" >&2
      cat align_response.json >&2 || true
      exit 1
    fi
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
    publishDir { outdir }, mode: 'copy'

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
    if [ -n "${JOB_ID:-}" ]; then
      CURL_ARGS+=( -F job_id=${JOB_ID} -F step_name=gatk_cram_to_bam )
    fi
    if ! curl -sS --fail-with-body "${CURL_ARGS[@]}" http://gatk-api:5000/cram-to-bam > cram_response.json; then
      echo "gatk-api /cram-to-bam returned an error:" >&2
      cat cram_response.json >&2 || true
      exit 1
    fi
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
    publishDir { outdir }, mode: 'copy'

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
    if [ -n "${JOB_ID:-}" ]; then
      CURL_ARGS+=( -F job_id=${JOB_ID} -F step_name=gatk_sam_to_bam )
    fi
    if ! curl -sS --fail-with-body "${CURL_ARGS[@]}" http://gatk-api:5000/sam-to-bam > sam_response.json; then
      echo "gatk-api /sam-to-bam returned an error:" >&2
      cat sam_response.json >&2 || true
      exit 1
    fi
    BAM_PATH=$(python3 - <<'PY'
import json; import sys
data=json.load(open('sam_response.json'))
print(data.get('bam_path') or data.get('bam') or '')
PY
)
    test -n "$BAM_PATH" && cp "$BAM_PATH" .
    '''
}

// OptiType HLA calling on FASTQ
process OptiTypeHLAFromFastq {
    tag "hla_fastq_${patient_id}"
    publishDir { outdir }, mode: 'copy'

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
    if [ -n "${JOB_ID:-}" ]; then
      CURL_ARGS+=( -F job_id=${JOB_ID} -F step_name=hla_typing )
    fi
    # An HTTP error here used to be swallowed: the error JSON landed in hla_result.json,
    # the parser below found no HLA- keys, and the run completed reporting no HLA calls -
    # indistinguishable from OptiType legitimately finding none. Untick OptiType
    # (--skip_hla) to opt out of HLA typing; a failing service is not that.
    if ! curl -sS --fail-with-body "${CURL_ARGS[@]}" http://zarohla:5000/call-hla > hla_result.json; then
      echo "zarohla /call-hla returned an error:" >&2
      cat hla_result.json >&2 || true
      exit 1
    fi
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
    publishDir { outdir }, mode: 'copy'

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
    if [ -n "${JOB_ID:-}" ]; then
      CURL_ARGS+=( -F job_id=${JOB_ID} -F step_name=hla_typing )
    fi
    # An HTTP error here used to be swallowed: the error JSON landed in hla_result.json,
    # the parser below found no HLA- keys, and the run completed reporting no HLA calls -
    # indistinguishable from OptiType legitimately finding none. Untick OptiType
    # (--skip_hla) to opt out of HLA typing; a failing service is not that.
    if ! curl -sS --fail-with-body "${CURL_ARGS[@]}" http://zarohla:5000/call-hla > hla_result.json; then
      echo "zarohla /call-hla returned an error:" >&2
      cat hla_result.json >&2 || true
      exit 1
    fi
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

// Mitochondrial calling via the mtdna sidecar (mutserve + haplogrep3 +
// haplocheck, from mtDNA-Server 2 v2.1.16). Supplies MT-RNR1, which PharmCAT
// cannot call itself -- pharmcat_positions.vcf has no chrM position, which is
// why MT-RNR1 is configured as an outside call in config/genes.json.
process MtdnaCall {
    tag "mtdna_${patient_id}"
    publishDir { outdir }, mode: 'copy'

    input:
    path variants_file
    val patient_id
    val report_id
    val input_type
    val source_build
    val absent_to_ref
    val outdir

    output:
    path "pharmcat.mtdna.tsv", optional: true, emit: mtdna
    path "mtdna_result.json", emit: mtdna_json
    path "mtdna_report.html", optional: true, emit: mtdna_report
    path "mtdna_variants.vcf.gz", optional: true, emit: mtdna_vcf

    shell:
    '''
    set -euo pipefail
    CURL_ARGS=( -X POST -F patient_id=!{patient_id} -F report_id=!{report_id} \
                -F input_type=!{input_type} -F reference_genome=!{source_build} \
                -F absent_to_ref=!{absent_to_ref} -F file=@!{variants_file} )
    if [ -n "${JOB_ID:-}" ]; then
      CURL_ARGS+=( -F job_id=${JOB_ID} -F step_name=mtdna_analysis )
    fi
    # --fail-with-body for the same reason as the OptiType processes above: an
    # error JSON landing in the result file, parsed for keys that are not there,
    # is indistinguishable from a sample with no mitochondrial variants. Untick
    # mtDNA (--skip_mtdna) to opt out; a failing service is not that.
    if ! curl -sS --fail-with-body "${CURL_ARGS[@]}" http://mtdna:5000/call-mtdna > mtdna_result.json; then
      echo "mtdna /call-mtdna returned an error:" >&2
      cat mtdna_result.json >&2 || true
      exit 1
    fi
    python3 - <<'PY'
import json, shutil, os
data = json.load(open('mtdna_result.json'))
call = data.get('mt_rnr1')
if call:
    with open('pharmcat.mtdna.tsv', 'w', encoding='utf-8') as fh:
        fh.write(f"MT-RNR1\\t{call}\\n")
src = data.get('report_html')
if src and os.path.exists(src):
    shutil.copy(src, 'mtdna_report.html')
# The normalised chrM VCF (bcftools norm output), present on both the VCF and
# alignment paths -- unlike report_html, it does not depend on coverage or
# contamination metrics, so it is available regardless of input type.
src_vcf = data.get('chrm_vcf')
if src_vcf and os.path.exists(src_vcf):
    shutil.copy(src_vcf, 'mtdna_variants.vcf.gz')
PY
    '''
}

// GRCh37/hg19 VCF -> GRCh38 VCF, via gatk-api's Picard LiftoverVcf endpoint.
// Real coordinate conversion, not a contig rename - the sidecar normalises contig
// naming to match the UCSC chain, runs LiftoverVcf against the GRCh38 reference,
// and refuses (HTTP 500 -> --fail-with-body -> run failure) when an implausible
// share of variants fails to lift, so a chain/prefix mismatch can never quietly
// hand a near-empty VCF to PyPGx/PharmCAT. Variants that genuinely cannot be
// lifted are dropped; the response carries the counts.
process LiftoverVCF {
    tag "liftover_${patient_id}"
    publishDir { outdir }, mode: 'copy'

    input:
    path vcf
    val source_build
    val patient_id
    val report_id
    val reference
    val outdir

    output:
    // A fixed name, deliberately NOT a bare `*.vcf.gz` glob: the staged INPUT can
    // itself be a .vcf.gz, and a glob would emit input and output both - sending
    // the un-lifted GRCh37 file downstream alongside the lifted one. The staged
    // input can never collide with this name (uploads stage as `upload_*`).
    path "lifted.grch38.vcf.gz", emit: vcf
    path "lifted.grch38.vcf.gz.tbi", optional: true, emit: tbi

    shell:
    '''
    set -euo pipefail
    CURL_ARGS=( -X POST -F source_build=!{source_build} -F reference_genome=!{reference} -F patient_id=!{patient_id} -F report_id=!{report_id} -F file=@!{vcf} )
    if [ -n "${JOB_ID:-}" ]; then
      CURL_ARGS+=( -F job_id=${JOB_ID} -F step_name=liftover )
    fi
    if ! curl -sS --fail-with-body "${CURL_ARGS[@]}" http://gatk-api:5000/liftover-vcf > liftover_response.json; then
      echo "gatk-api /liftover-vcf returned an error:" >&2
      cat liftover_response.json >&2 || true
      exit 1
    fi
    VCF_PATH=$(python3 - <<'PY'
import json
data = json.load(open('liftover_response.json'))
print(data.get('vcf_path') or data.get('vcf') or '')
PY
)
    if [ -z "$VCF_PATH" ]; then
      echo "liftover response carried no vcf_path" >&2
      cat liftover_response.json >&2 || true
      exit 1
    fi
    cp "$VCF_PATH" lifted.grch38.vcf.gz
    # Bring the tabix index along if the sidecar made one (downstream re-indexes otherwise).
    [ -f "${VCF_PATH}.tbi" ] && cp "${VCF_PATH}.tbi" lifted.grch38.vcf.gz.tbi || true
    '''
}

process PyPGxBam2Vcf {
    tag "bam2vcf_${patient_id}"
    publishDir { outdir }, mode: 'copy'

    input:
    path bam
    val patient_id
    val report_id
    val reference
    val outdir

    output:
    // create-input-vcf emits a bgzipped VCF, so match .vcf.gz (a bare *.vcf never
    // matched and nextflow failed the process with "missing output" after a
    // successful conversion).
    path "*.vcf.gz", emit: vcf

    shell:
    '''
    set -euo pipefail
    CURL_ARGS=( -X POST -F reference_genome=!{reference} -F patient_id=!{patient_id} -F report_id=!{report_id} -F file=@!{bam} )
    if [ -n "${JOB_ID:-}" ]; then
      CURL_ARGS+=( -F job_id=${JOB_ID} -F step_name=pypgx_bam2vcf )
    fi
    if ! curl -sS --fail-with-body "${CURL_ARGS[@]}" http://pypgx:5000/create-input-vcf > response.json; then
      echo "pypgx /create-input-vcf returned an error:" >&2
      cat response.json >&2 || true
      exit 1
    fi
    VCF_PATH=$(python3 - <<'PY'
import json; import sys
data=json.load(open('response.json'))
print(data.get('vcf_path') or data.get('vcf') or '')
PY
)
    if [ -z "$VCF_PATH" ]; then
      echo "create-input-vcf response carried no vcf_path" >&2
      cat response.json >&2 || true
      exit 1
    fi
    cp "$VCF_PATH" .
    # Bring the tabix index along if the sidecar made one (downstream re-indexes otherwise).
    [ -f "${VCF_PATH}.tbi" ] && cp "${VCF_PATH}.tbi" . || true
    '''
}

process PyPGxGenotypeAll {
    tag "pypgx_${patient_id}"
    publishDir { outdir }, mode: 'copy'

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
    # DELIBERATELY EXEMPT from the --fail-with-body rule in the header: -f already makes
    # an HTTP error a non-zero exit, and the if/else below handles it on purpose by
    # degrading to PharmCAT-only. --fail-with-body would write the error body into
    # pypgx_result.json, which the else branch overwrites anyway - no gain, and the
    # explicit degradation path is the point.
    CURL_ARGS=( -f -X POST -F genes=ALL -F reference_genome=!{reference} -F patient_id=!{patient_id} -F report_id=!{report_id} -F file=@!{vcf} -F input_type=!{params.input_type} )
    if [ -n "${JOB_ID:-}" ]; then
      CURL_ARGS+=( -F job_id=${JOB_ID} -F step_name=pypgx_analysis )
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
    publishDir { outdir }, mode: 'copy'

    input:
    path vcf
    path outside_tsv, stageAs: 'pypgx_outside.tsv'
    path hla_tsv, stageAs: 'hla_outside.tsv'
    path mtdna_tsv, stageAs: 'mtdna_outside.tsv'
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
    
    # Combine PyPGx, HLA and mtDNA outside calls into a single file
    cat /dev/null > combined_outside.tsv
    [ -f "pypgx_outside.tsv" ] && cat pypgx_outside.tsv >> combined_outside.tsv
    [ -f "hla_outside.tsv" ] && cat hla_outside.tsv >> combined_outside.tsv
    [ -f "mtdna_outside.tsv" ] && cat mtdna_outside.tsv >> combined_outside.tsv

    CURL_ARGS=( -s -X POST -F patient_id=!{patient_id} -F report_id=!{report_id} -F file=@!{vcf} )
    # SAMPLE_IDENTIFIER is passed through the environment (see runner.py), NOT spliced
    # in by Nextflow interpolation. A shell variable expansion is inert data regardless
    # of its content, so a hostile value cannot break out of the quoting into shell.
    if [ -n "${SAMPLE_IDENTIFIER:-}" ]; then
      CURL_ARGS+=( -F "sample_identifier=${SAMPLE_IDENTIFIER}" )
    fi
    if [ -s combined_outside.tsv ]; then
      CURL_ARGS+=( -F outside_tsv=@combined_outside.tsv )
    fi
    # Add job_id if available
    if [ -n "${JOB_ID:-}" ]; then
      CURL_ARGS+=( -F job_id=${JOB_ID} -F step_name=pharmcat_analysis )
    fi
    CURL_ARGS+=( -F pharmcat_absent_to_ref=!{params.pharmcat_absent_to_ref} )
    CURL_ARGS+=( -F pharmcat_unspecified_to_ref=!{params.pharmcat_unspecified_to_ref} )
    # DELIBERATELY EXEMPT from the --fail-with-body rule in the header: the trailing
    # `|| true` already discards curl's exit status, so adding the flag would change
    # nothing, and removing `|| true` would turn a tolerated PharmCAT error into a hard
    # run failure - a behaviour change that cannot be validated without a live run.
    # Left as-is on purpose; making PharmCAT failures fail the run is its own change.
    curl "${CURL_ARGS[@]}" http://pharmcat:5000/genotype > pharmcat_result.json 2>pharmcat.log || true
    
    # Copy outputs from mounted volume
    for f in /data/reports/!{patient_id}/!{patient_id}_pgx_pharmcat.*; do
      [ -f "$f" ] && cp "$f" . || true
    done
    '''
}

workflow {
    main:
    assert params.input : 'Missing --input path'
    assert params.input_type : 'Missing --input_type (vcf|bam|cram|sam|fastq)'

    // GATK gate. fastq/cram/sam only reach a BAM through the gatk-api container, so
    // gating FastqToBAM/CramToBAM/SamToBAM off would leave bam_ch empty and starve
    // every downstream channel - the pipeline would "succeed" having produced
    // nothing, which is worse than the silent-override it replaces. Refuse the
    // combination instead. For vcf/bam input no GATK process runs at all, so
    // skip_gatk is correctly a no-op there.
    if (params.skip_gatk && ['fastq', 'cram', 'sam'].contains(params.input_type)) {
        error(
            "--skip_gatk is not compatible with --input_type ${params.input_type}: " +
            "converting ${params.input_type} to BAM requires the GATK service. " +
            "Re-enable GATK, or upload a BAM/VCF instead."
        )
    }

    // Create input channels. .first() turns the single-item queue channel
    // Channel.fromPath emits into a reusable value channel: input_ch is read once
    // by whichever per-input-type branch below consumes it, and a second time by
    // the mtDNA wiring further down (see that block's comment) -- a queue channel
    // would be drained by the first reader and the second would see nothing, the
    // same class of problem the empty_file_ch comment just below records for
    // optional outside-call inputs.
    input_ch = Channel.fromPath(params.input).first()

    // Create parameter channels  
    patient_id_ch = Channel.value(params.patient_id)
    report_id_ch = Channel.value(params.report_id)
    reference_ch = Channel.value(params.reference)
    outdir_ch = Channel.value(params.outdir)
    
    // Static empty fallback for optional outside-call inputs. A plain path value
    // (not a channel): valid as a process input AND as an .ifEmpty() default, and
    // reusable across branches — avoids the queue-consumed-twice / DataflowVariable issues.
    empty_file_ch = file("${projectDir}/assets/empty.tsv")

    // Handle different input types with optimal HLA calling strategy
    
    // For FASTQ: HLA first (if enabled), then BAM conversion, then PyPGx sequentially
    if (params.input_type == 'fastq') {
        // Convert FASTQ to BAM (needed for PyPGx regardless of HLA). .first() makes
        // this reusable by the mtDNA wiring below too -- see input_ch's comment above
        // for why a plain queue channel can't feed two consumers.
        bam_ch = FastqToBAM(input_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).bam.first()

        if (params.skip_hla) {
            // HLA typing opted out: no OptiType, PyPGx runs on the converted BAM.
            hla_ch = empty_file_ch
            vcf_ch = PyPGxBam2Vcf(bam_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).vcf
        } else {
            // HLA typing on FASTQ (optimal - no conversion needed for OptiType).
            hla_result = OptiTypeHLAFromFastq(input_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch)
            hla_ch = hla_result.hla
            // PyPGx waits for HLA to complete
            hla_complete_ch = hla_result.hla_json.combine(bam_ch).map { hla_json, bam_file -> bam_file }
            vcf_ch = PyPGxBam2Vcf(hla_complete_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).vcf
        }
    }
    // For CRAM: convert to BAM, then HLA (unless skipped) + PyPGx sequentially
    else if (params.input_type == 'cram') {
        // .first(): see input_ch's comment above -- reused by the mtDNA wiring below.
        bam_ch = CramToBAM(input_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).bam.first()

        if (params.skip_hla) {
            hla_ch = empty_file_ch
            vcf_ch = PyPGxBam2Vcf(bam_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).vcf
        } else {
            hla_result = OptiTypeHLAFromBAM(bam_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch)
            hla_ch = hla_result.hla
            // Create a dependency: PyPGx waits for HLA to complete
            hla_complete_ch = hla_result.hla_json.combine(bam_ch).map { hla_json, bam_file -> bam_file }
            vcf_ch = PyPGxBam2Vcf(hla_complete_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).vcf
        }
    }
    // For SAM: convert to BAM, then HLA (unless skipped) + PyPGx sequentially
    else if (params.input_type == 'sam') {
        // .first(): see input_ch's comment above -- reused by the mtDNA wiring below.
        bam_ch = SamToBAM(input_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).bam.first()

        if (params.skip_hla) {
            hla_ch = empty_file_ch
            vcf_ch = PyPGxBam2Vcf(bam_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).vcf
        } else {
            hla_result = OptiTypeHLAFromBAM(bam_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch)
            hla_ch = hla_result.hla
            // Create a dependency: PyPGx waits for HLA to complete
            hla_complete_ch = hla_result.hla_json.combine(bam_ch).map { hla_json, bam_file -> bam_file }
            vcf_ch = PyPGxBam2Vcf(hla_complete_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).vcf
        }
    }
    // For BAM: HLA first (unless skipped), then PyPGx sequentially
    else if (params.input_type == 'bam') {
        if (params.skip_hla) {
            // HLA typing opted out: no OptiType, PyPGx runs directly on the BAM.
            hla_ch = empty_file_ch
            vcf_ch = PyPGxBam2Vcf(input_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).vcf
        } else {
            hla_result = OptiTypeHLAFromBAM(input_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch)
            hla_ch = hla_result.hla
            // Create a dependency: PyPGx waits for HLA to complete
            // Use the hla_json output as a trigger to start PyPGx with the original BAM
            hla_complete_ch = hla_result.hla_json.combine(input_ch).map { hla_json, bam_file -> bam_file }
            vcf_ch = PyPGxBam2Vcf(hla_complete_ch, patient_id_ch, report_id_ch, reference_ch, outdir_ch).vcf
        }
    }
    // For VCF: quick pipeline, no HLA. A GRCh37/hg19 VCF is lifted over to GRCh38
    // first; everything downstream (PyPGx, PharmCAT) is GRCh38-only, so skipping
    // this step for such a file would analyse it on the wrong coordinates.
    else if (params.input_type == 'vcf') {
        def source_build_norm = (params.source_build ?: '').toString().toLowerCase()
        if (['grch37', 'hg19', 'b37'].contains(source_build_norm)) {
            vcf_ch = LiftoverVCF(
                input_ch,
                Channel.value(params.source_build),
                patient_id_ch,
                report_id_ch,
                reference_ch,
                outdir_ch
            ).vcf
        } else {
            vcf_ch = input_ch
        }
        hla_ch = empty_file_ch
    }
    else {
        error "Unsupported input type: ${params.input_type}. Supported: vcf, bam, cram, sam, fastq"
    }

    // mtDNA calling. Which channel feeds it depends on input type:
    //   - vcf: the ORIGINAL upload (input_ch), never vcf_ch. On a b37/GRCh37 file
    //     vcf_ch may have been through LiftoverVCF above, whose chain's source is
    //     hg19's chrM (NC_001807, 16571bp) -- but b37's MT is already rCRS, so
    //     pushing it through that chain shifts positions by 2 inside MT-RNR1 and
    //     would report a pathogenic m.1555A>G at 1553. The sidecar does its own
    //     build-appropriate normalisation from the untouched input instead.
    //   - bam/cram/sam/fastq: the already-CONVERTED BAM (bam_ch, or input_ch
    //     itself for 'bam' input, which needs no conversion) -- never the raw
    //     cram/sam/fastq upload. There is no liftover concern on these paths, the
    //     pipeline already produced a BAM above, and reusing it avoids re-decoding
    //     CRAM inside the sidecar. FASTQ cannot be called by the sidecar directly
    //     at all, which is why every alignment-input branch sends "bam" as the
    //     input_type it tells the sidecar, regardless of what was actually
    //     uploaded.
    // input_ch and bam_ch were both made reusable with .first() above precisely so
    // they can feed this second consumer without being drained by the per-type
    // branch's own use of them -- see input_ch's comment near its declaration.
    if (params.skip_mtdna) {
        mtdna_outside = empty_file_ch
    } else {
        mtdna_variants_ch = (params.input_type in ['fastq', 'cram', 'sam']) ? bam_ch : input_ch
        mtdna_input_type = (params.input_type == 'vcf') ? 'vcf' : 'bam'
        mtdna_result = MtdnaCall(
            mtdna_variants_ch,
            patient_id_ch,
            report_id_ch,
            Channel.value(mtdna_input_type),
            // The DETECTED source build, never params.reference alone (which
            // defaults to hg38 regardless of what the file actually is) -- falls
            // back to it only when source_build was never populated, e.g. no
            // liftover was needed/detected for this input.
            Channel.value(params.source_build ?: params.reference),
            Channel.value(params.pharmcat_absent_to_ref),
            outdir_ch
        )
        mtdna_outside = mtdna_result.mtdna.ifEmpty(empty_file_ch)
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
            mtdna_outside,
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
            mtdna_outside,
            patient_id_ch,
            report_id_ch,
            outdir_ch
        )
    }
}


