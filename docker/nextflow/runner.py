import os
import subprocess
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok"})

@app.route('/run', methods=['POST'])
def run():
    data = request.form or request.json or {}
    input_path = data.get('input')
    input_type = data.get('input_type')
    patient_id = data.get('patient_id')
    report_id = data.get('report_id', patient_id)
    reference = data.get('reference', 'hg38')
    outdir = data.get('outdir', f"/data/reports/{patient_id}")

    if not input_path or not input_type or not patient_id:
        return jsonify({"success": False, "error": "Missing required params: input, input_type, patient_id"}), 400

    cmd = [
        'nextflow', 'run', 'pipelines/pgx/main.nf', '-profile', 'docker',
        '--input', input_path,
        '--input_type', input_type,
        '--patient_id', str(patient_id),
        '--report_id', str(report_id),
        '--reference', reference,
        '--outdir', outdir,
        '-with-report', f"{outdir}/nextflow_report.html",
        '-with-trace', f"{outdir}/nextflow_trace.txt",
        '-with-timeline', f"{outdir}/nextflow_timeline.html",
        '-ansi-log', 'false'
    ]

    try:
        os.makedirs(outdir, exist_ok=True)
        # Stream output in real-time to docker compose logs
        proc = subprocess.run(cmd, text=True)
        return jsonify({
            "success": proc.returncode == 0,
            "returncode": proc.returncode,
            "outdir": outdir
        }), (200 if proc.returncode == 0 else 500)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5055)


