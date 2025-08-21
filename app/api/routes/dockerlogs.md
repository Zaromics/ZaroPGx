Analysis Complete!
Your pharmacogenomic analysis has been completed successfully.

Report URLs not found in results data.

pgx_app                | INFO:app.reports.pdf_generators:✓ PDF generated successfully using WeasyPrint fallback: /data/reports/041e1f9a-bfa0-4fb1-81ba-6bf162139506/041e1f9a-bfa0-4fb1-81ba-6bf162139506_pgx_report.pdf
pgx_app                | INFO:app.api.routes.upload_router:✓ PDF report generated successfully using WeasyPrint: /data/reports/041e1f9a-bfa0-4fb1-81ba-6bf162139506/041e1f9a-bfa0-4fb1-81ba-6bf162139506_pgx_report.pdf
pgx_app                | INFO:app.api.routes.upload_router:Added PharmCAT HTML report URL (enabled via INCLUDE_PHARMCAT_HTML)
pgx_app                | INFO:app.api.routes.upload_router:Added PharmCAT JSON report URL (enabled via INCLUDE_PHARMCAT_JSON)
pgx_app                | INFO:app.api.routes.upload_router:Added PharmCAT TSV report URL (enabled via INCLUDE_PHARMCAT_TSV)
pgx_app                | INFO:app.services.job_status_service:Updated job f3774483-0206-4e79-aa17-b8ecb1158e6e: report - 95% - Reports generated successfully
pgx_app                | INFO:app.api.routes.upload_router:Updated job status with unified report URLs. Job directory: /data/reports/041e1f9a-bfa0-4fb1-81ba-6bf162139506
pgx_app                | INFO:app.services.job_status_service:Completed job f3774483-0206-4e79-aa17-b8ecb1158e6e: SUCCESS
pgx_app                | INFO:app.api.routes.upload_router:Job 2cad96c4-9e2e-468e-b419-9680c8bd87f6 completed successfully
pgx_app                | [REQUEST] GET /status/f3774483-0206-4e79-aa17-b8ecb1158e6e
pgx_app                | INFO:app:[REQUEST] GET /status/f3774483-0206-4e79-aa17-b8ecb1158e6e
pgx_app                | INFO:app.api.routes.upload_router:Returning status response for job f3774483-0206-4e79-aa17-b8ecb1158e6e: {'job_id': 'f3774483-0206-4e79-aa17-b8ecb1158e6e', 'status': 'completed', 'progress': 100, 'message': 'Analysis completed successfully', 'current_stage': 'report', 'data': {'workflow': {'warnings': ['Limited analysis may be available due to non-whole genome sequencing data for genes such as CYP2D6'], 'needs_gatk': False, 'needs_pypgx': False, 'unsupported': False, 'is_provisional': False, 'needs_alignment': False, 'recommendations': ['Your VCF file can be processed directly, but if you have the original sequencing file (BAM/CRAM), uploading that instead would provide more accurate variant calling with our latest GATK pipeline.', 'Creating index for VCF file for faster processing'], 'needs_conversion': False, 'unsupported_reason': None, 'go_directly_to_pharmcat': True}, 'final_status': 'success'}}
pgx_app                | [RESPONSE] GET /status/f3774483-0206-4e79-aa17-b8ecb1158e6e - Status: 200
pgx_app                | INFO:app:[RESPONSE] GET /status/f3774483-0206-4e79-aa17-b8ecb1158e6e - Status: 200
pgx_app                | INFO:     172.20.0.1:49120 - "GET /status/f3774483-0206-4e79-aa17-b8ecb1158e6e HTTP/1.1" 200 OK
pgx_pypgx              | INFO:     127.0.0.1:59604 - "GET /health HTTP/1.1" 200 OK