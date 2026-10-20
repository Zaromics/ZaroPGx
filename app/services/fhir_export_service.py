"""
FHIR Export Service for ZaroPGx

This service generates FHIR R4-compliant exports of pharmacogenomic reports
following the HL7 Genomics Reporting Implementation Guide.

Reference: https://build.fhir.org/ig/HL7/genomics-reporting/pharmacogenomics.html
"""

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from sqlalchemy.orm import Session

from app.reports.actionable import actionable_genes
from app.services.fhir import terminology, xml_format
from app.services.pharmcat_data_service import PharmCATDataService

logger = logging.getLogger(__name__)

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def fhir_export_enabled() -> bool:
    """The single source of truth for FHIR_EXPORT_ENABLED.

    Whitespace is stripped: ``FHIR_EXPORT_ENABLED='true '`` is a trailing-space
    typo in a ``.env`` file, not a request to disable the export.

    Deliberately a function, not a module-level constant. This module is
    imported from ``app/main.py``'s import block, which runs *before*
    ``load_dotenv()`` further down that file. A constant evaluated here would
    therefore snapshot a pre-``.env`` environment, while ``app/main.py`` -- which
    decides whether to mount the ``/fhir/*`` router -- would read a post-``.env``
    one. Two readers, two answers, and the router's guard could contradict the
    mount that put it there.

    Resolving on demand removes the ordering question entirely: every caller
    reads the same variable through the same parser, and by the time anyone asks
    (router mounting at the bottom of ``app/main.py``, request handlers, or
    ``FHIRExportService.is_enabled``) ``load_dotenv()`` has already run.

    The parse is inline rather than a shared ``env_flag`` helper on purpose:
    this codebase already carries four near-copies of a private ``_env_flag``
    (app/main.py, app/reports/generator.py, app/api/routes/upload_router.py,
    app/visualizations/workflow_diagram.py) and a fifth generic parser living in
    a FHIR service is not the consolidation anyone wants.
    """
    raw = os.getenv("FHIR_EXPORT_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() in _TRUTHY


# Reports directory - same as other report outputs
REPORT_DIR = Path(os.getenv("REPORT_DIR", "/data/reports"))

# Namespace of the resources' ids. Each id is a name-based (v5) UUID of what the
# resource is -- the report's genotype for CYP2D6, its recommendation n for
# codeine -- so the bundle of one report names its resources the same way every
# time it is written, and pushing it again (app.services.fhir.push) updates them.
RESOURCE_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "urn:zaropgx:fhir-resource")


# The HLA-B alleles PharmCAT screens, each reported as its own result.
HLA_B_SCREENED_ALLELES = ("*15:02", "*57:01", "*58:01")


class FHIRExportService:
    """
    Service for generating FHIR R4-compliant pharmacogenomics exports.

    Follows the HL7 Genomics Reporting Implementation Guide for PGx reporting.
    Generates standalone FHIR Bundle exports in JSON or XML format.
    """

    # Terminology is single-sourced in app.services.fhir.terminology; these
    # aliases keep the historical `self.LOINC_CODES[...]` call sites working.
    LOINC_CODES = terminology.LOINC_CODES

    def __init__(self, db: Session):
        """
        Initialize the FHIR export service.

        Args:
            db: SQLAlchemy database session
        """
        self.db = db
        self.pharmcat_service = PharmCATDataService(db)

    def is_enabled(self) -> bool:
        """Check if FHIR export is enabled via environment flag."""
        return fhir_export_enabled()

    def export_pgx_report(
        self,
        run_id: Optional[str] = None,
        patient_info: Optional[Dict[str, Any]] = None,
        output_format: str = "json",
        include_recommendations: bool = True,
        pharmcat_data: Optional[Dict[str, Any]] = None,
        workflow_id: Optional[str] = None,
        patient_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Export a PharmCAT run as a FHIR Bundle.

        Args:
            run_id: PharmCAT run ID to export
            patient_info: Optional patient information dict
            output_format: "json" or "xml"
            include_recommendations: Whether to include therapeutic implications
            patient_id: The ZaroPGx patient the run belongs to, the Patient's
                identifier when patient_info names none (else the job's
                patient, else the run id)

        Returns:
            Dict containing:
                - success: bool
                - format: str ("json" or "xml")
                - content: str (the FHIR Bundle as string)
                - filename: str (suggested filename)
                - error: str (if success is False)
        """
        if not self.is_enabled():
            return {
                "success": False,
                "error": "FHIR export is disabled. Set FHIR_EXPORT_ENABLED=true to enable.",
            }

        try:
            # Determine PharmCAT data source (database or provided)
            data = pharmcat_data
            if data is None:
                if not run_id:
                    return {
                        "success": False,
                        "error": "No PharmCAT data provided and run_id is missing",
                    }
                data = self.pharmcat_service.get_pharmcat_data_by_run_id(run_id)
                if not data:
                    return {
                        "success": False,
                        "error": f"No PharmCAT data found for run_id: {run_id}",
                    }
            else:
                # Ensure run_id is populated if available in provided data
                run_id = run_id or data.get("run_id")

            # Build FHIR Bundle
            bundle = self._build_fhir_bundle(
                pharmcat_data=data,
                run_id=run_id,
                patient_info=patient_info,
                include_recommendations=include_recommendations,
                workflow_id=workflow_id,
                patient_id=patient_id or self._job_patient_id(workflow_id),
            )

            # Convert to requested format
            file_stub = run_id or workflow_id or data.get("run_id") or "workflow"
            if output_format.lower() == "xml":
                content = self._bundle_to_xml(bundle)
                filename = f"pgx_report_{file_stub}.xml"
            else:
                content = json.dumps(bundle, indent=2, default=str)
                filename = f"pgx_report_{file_stub}.json"

            return {
                "success": True,
                "format": output_format.lower(),
                "content": content,
                "filename": filename,
                "bundle": bundle,
            }

        except Exception as e:
            logger.error(f"Error exporting FHIR report for run {run_id}: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    def export_workflow_to_fhir(
        self,
        workflow_id: str,
        patient_info: Optional[Dict[str, Any]] = None,
        output_format: str = "json",
        pharmcat_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Export a workflow's PharmCAT results as a FHIR Bundle.

        Args:
            workflow_id: Workflow ID to export
            patient_info: Optional patient information
            output_format: "json" or "xml"

        Returns:
            Export result dict
        """
        if not self.is_enabled():
            return {
                "success": False,
                "error": "FHIR export is disabled. Set FHIR_EXPORT_ENABLED=true to enable.",
            }

        try:
            # Get PharmCAT data via workflow (or use provided data)
            data = (
                pharmcat_data
                or self.pharmcat_service.get_pharmcat_data_for_workflow(workflow_id)
            )
            if not data:
                return {
                    "success": False,
                    "error": f"No PharmCAT data found for workflow: {workflow_id}",
                }

            run_id = data.get("run_id", workflow_id)

            # Build and export
            bundle = self._build_fhir_bundle(
                pharmcat_data=data,
                run_id=run_id,
                patient_info=patient_info,
                workflow_id=workflow_id,
                patient_id=self._job_patient_id(workflow_id),
            )

            file_stub = workflow_id or run_id or "workflow"
            if output_format.lower() == "xml":
                content = self._bundle_to_xml(bundle)
                filename = f"pgx_report_{file_stub}.xml"
            else:
                content = json.dumps(bundle, indent=2, default=str)
                filename = f"pgx_report_{file_stub}.json"

            return {
                "success": True,
                "format": output_format.lower(),
                "content": content,
                "filename": filename,
                "bundle": bundle,
            }

        except Exception as e:
            logger.error(f"Error exporting FHIR report for workflow {workflow_id}: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    def save_fhir_export(
        self,
        run_id: Optional[str] = None,
        patient_id: Optional[str] = None,
        patient_info: Optional[Dict[str, Any]] = None,
        output_format: str = "json",
        include_recommendations: bool = True,
        pharmcat_data: Optional[Dict[str, Any]] = None,
        workflow_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Export a PharmCAT run as a FHIR Bundle and save it to the reports directory.

        Files are saved alongside other report outputs (PDF, HTML, etc.): with a
        workflow_id, in that job's directory under the patient, named after the job.

        Args:
            run_id: PharmCAT run ID to export
            patient_id: Patient ID for the subdirectory (defaults to run_id)
            patient_info: Optional patient information dict
            output_format: "json" or "xml" (or "both" for both formats)
            include_recommendations: Whether to include therapeutic implications

        Returns:
            Dict containing:
                - success: bool
                - files_saved: list of saved file paths
                - error: str (if success is False)
        """
        if not self.is_enabled():
            return {
                "success": False,
                "error": "FHIR export is disabled. Set FHIR_EXPORT_ENABLED=true to enable.",
                "files_saved": [],
            }

        try:
            # Use patient_id or run_id for the subdirectory
            subdir_id = patient_id or run_id or workflow_id
            url_dir = subdir_id
            stem = "pgx_fhir_report"
            # A job's bundle goes in that job's directory, named like its other
            # reports. Written to the patient directory under one fixed name, each
            # job of a patient overwrote the last, and every earlier report's FHIR
            # link served the newest job's bundle.
            if workflow_id and str(workflow_id) != str(subdir_id):
                url_dir = f"{subdir_id}/{workflow_id}"
                stem = f"{workflow_id}_pgx_fhir_report"

            # Create reports subdirectory if it doesn't exist
            report_subdir = REPORT_DIR / url_dir
            report_subdir.mkdir(parents=True, exist_ok=True)

            files_saved = []
            formats_to_export = (
                ["json", "xml"]
                if output_format.lower() == "both"
                else [output_format.lower()]
            )

            # The patient directory is the patient's id, unless it is the job's
            # own directory (no patient given; save_fhir_export_for_workflow).
            bundle_patient_id = (
                patient_id
                if patient_id and str(patient_id) != str(workflow_id)
                else None
            )

            for fmt in formats_to_export:
                # Generate the export
                result = self.export_pgx_report(
                    run_id=run_id,
                    patient_info=patient_info,
                    output_format=fmt,
                    include_recommendations=include_recommendations,
                    pharmcat_data=pharmcat_data,
                    workflow_id=workflow_id,
                    patient_id=bundle_patient_id,
                )

                if not result.get("success"):
                    return {
                        "success": False,
                        "error": result.get(
                            "error", f"Failed to generate {fmt} export"
                        ),
                        "files_saved": files_saved,
                    }

                # Determine filename
                extension = "xml" if fmt == "xml" else "json"
                filename = f"{stem}.{extension}"
                filepath = report_subdir / filename

                # Write the file
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(result["content"])

                logger.info(f"Saved FHIR {fmt.upper()} export to: {filepath}")
                files_saved.append(
                    {
                        "format": fmt,
                        "path": str(filepath),
                        "filename": filename,
                        "url": f"/reports/{url_dir}/{filename}",
                    }
                )

            return {
                "success": True,
                "files_saved": files_saved,
                "report_directory": str(report_subdir),
            }

        except Exception as e:
            logger.error(f"Error saving FHIR export for run {run_id}: {e}")
            return {
                "success": False,
                "error": str(e),
                "files_saved": [],
            }

    def save_fhir_export_for_workflow(
        self,
        workflow_id: str,
        patient_id: Optional[str] = None,
        patient_info: Optional[Dict[str, Any]] = None,
        output_format: str = "json",
    ) -> Dict[str, Any]:
        """
        Export a workflow's PharmCAT results as FHIR and save to reports directory.

        Args:
            workflow_id: Workflow ID to export
            patient_id: Patient ID for the subdirectory (defaults to workflow_id)
            patient_info: Optional patient information
            output_format: "json", "xml", or "both"

        Returns:
            Export result dict with saved file paths
        """
        if not self.is_enabled():
            return {
                "success": False,
                "error": "FHIR export is disabled. Set FHIR_EXPORT_ENABLED=true to enable.",
                "files_saved": [],
            }

        try:
            # Get PharmCAT data via workflow to find run_id
            pharmcat_data = self.pharmcat_service.get_pharmcat_data_for_workflow(
                workflow_id
            )
            if not pharmcat_data:
                return {
                    "success": False,
                    "error": f"No PharmCAT data found for workflow: {workflow_id}",
                    "files_saved": [],
                }

            # Use patient_id, workflow_id, or run_id for subdirectory
            subdir_id = patient_id or workflow_id

            return self.save_fhir_export(
                run_id=pharmcat_data.get("run_id"),
                patient_id=subdir_id,
                patient_info=patient_info,
                output_format=output_format,
                pharmcat_data=pharmcat_data,
                workflow_id=workflow_id,
            )

        except Exception as e:
            logger.error(f"Error saving FHIR export for workflow {workflow_id}: {e}")
            return {
                "success": False,
                "error": str(e),
                "files_saved": [],
            }

    def _build_fhir_bundle(
        self,
        pharmcat_data: Dict[str, Any],
        run_id: Optional[str],
        patient_info: Optional[Dict[str, Any]] = None,
        include_recommendations: bool = True,
        workflow_id: Optional[str] = None,
        patient_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Build a FHIR Bundle containing all PGx report resources.

        Following the HL7 Genomics Reporting IG structure:
        - DiagnosticReport (Genomic Report)
        - Observation (Genotype) for each gene
        - Observation (Therapeutic Implication) for each gene's phenotype
        - Observation (Therapeutic Implication) for each drug
        - Task (Medication Recommendation) for each recommendation
        - Patient

        Args:
            pharmcat_data: Normalized PharmCAT data
            run_id: Run identifier
            patient_info: Optional patient information
            include_recommendations: Whether to include drug recommendations

        Returns:
            FHIR Bundle dict
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        bundle_id = str(uuid.uuid4())
        reference_id = (
            run_id
            or workflow_id
            or pharmcat_data.get("workflow", {}).get("pharmcat_run_id")
            or pharmcat_data.get("run_id")
            or str(uuid.uuid4())
        )

        entries = []
        observation_references = []
        phenotype_references = []
        # The same phenotype observations as a drug's implication cites them: by
        # gene, without the phenotype (the observation states it).
        phenotype_citations = []
        implication_references = []
        task_references = []
        # How often each name has come up, so a gene or drug listed twice still
        # gets an id of its own.
        seen: Dict[tuple, int] = {}

        def stable_id(kind: str, name: Any = "") -> str:
            key = (kind, str(name))
            seen[key] = seen.get(key, 0) + 1
            suffix = f"/{seen[key]}" if seen[key] > 1 else ""
            return self._stable_id(kind, reference_id, f"{name}{suffix}")

        # Create Patient resource
        patient_resource, patient_ref = self._create_patient_resource(
            patient_info or {},
            reference_id,
            patient_id=patient_id,
        )
        entries.append(
            {
                "fullUrl": f"urn:uuid:{patient_resource['id']}",
                "resource": patient_resource,
            }
        )

        # Create Genotype Observations for each gene
        genes = pharmcat_data.get("genes", [])
        for gene_data in genes:
            genotype_obs = self._create_genotype_observation(
                gene_data=gene_data,
                patient_ref=patient_ref,
                run_id=run_id,
                resource_id=stable_id("genotype", gene_data.get("gene")),
            )
            entries.append(
                {
                    "fullUrl": f"urn:uuid:{genotype_obs['id']}",
                    "resource": genotype_obs,
                }
            )
            genotype_ref = {
                "reference": f"urn:uuid:{genotype_obs['id']}",
                "display": f"{gene_data.get('gene', 'Unknown')} {gene_data.get('diplotype', '')}",
            }
            observation_references.append(genotype_ref)

            # The phenotype, as the IG states one: a therapeutic implication of
            # the genotype (see _create_phenotype_implication).
            phenotype = gene_data.get("phenotype")
            if self._states_a_phenotype(phenotype):
                phenotype_obs = self._create_phenotype_implication(
                    gene_data=gene_data,
                    patient_ref=patient_ref,
                    genotype_ref=genotype_ref,
                    resource_id=stable_id("phenotype", gene_data.get("gene")),
                )
                entries.append(
                    {
                        "fullUrl": f"urn:uuid:{phenotype_obs['id']}",
                        "resource": phenotype_obs,
                    }
                )
                phenotype_references.append(
                    {
                        "reference": f"urn:uuid:{phenotype_obs['id']}",
                        "display": f"{gene_data.get('gene', 'Unknown')} {phenotype}",
                    }
                )
                phenotype_citations.append(
                    {
                        "reference": f"urn:uuid:{phenotype_obs['id']}",
                        "display": f"{gene_data.get('gene', 'Unknown')} phenotype",
                    }
                )

        # Create Therapeutic Implications and Medication Recommendations
        if include_recommendations:
            drug_recommendations = pharmcat_data.get("drugRecommendations", [])
            for drug_rec in drug_recommendations:
                # Therapeutic Implication observation
                implication_id = stable_id(
                    "therapeutic-implication", drug_rec.get("drug")
                )
                implication_obs = self._create_therapeutic_implication(
                    drug_data=drug_rec,
                    patient_ref=patient_ref,
                    observation_refs=observation_references,
                    phenotype_refs=phenotype_citations,
                    run_id=run_id,
                    resource_id=implication_id,
                )
                entries.append(
                    {
                        "fullUrl": f"urn:uuid:{implication_obs['id']}",
                        "resource": implication_obs,
                    }
                )
                implication_references.append(
                    {
                        "reference": f"urn:uuid:{implication_obs['id']}",
                        "display": f"Therapeutic implication for {drug_rec.get('drug', 'Unknown')}",
                    }
                )

                # Medication Recommendation Task
                recommendations = drug_rec.get("recommendations", [])
                for index, rec in enumerate(recommendations, start=1):
                    med_task = self._create_medication_recommendation_task(
                        drug_name=drug_rec.get("drug", "Unknown"),
                        recommendation=rec,
                        patient_ref=patient_ref,
                        implication_ref=f"urn:uuid:{implication_obs['id']}",
                        run_id=run_id,
                        resource_id=self._stable_id(
                            "medication-recommendation", implication_id, index
                        ),
                    )
                    entries.append(
                        {
                            "fullUrl": f"urn:uuid:{med_task['id']}",
                            "resource": med_task,
                        }
                    )
                    task_references.append(
                        {
                            "reference": f"urn:uuid:{med_task['id']}",
                        }
                    )

        # Create Genomic Study resource
        genomic_study = self._create_genomic_study(
            pharmcat_data=pharmcat_data,
            patient_ref=patient_ref,
            run_id=run_id,
            resource_id=stable_id("genomic-study"),
        )
        entries.append(
            {
                "fullUrl": f"urn:uuid:{genomic_study['id']}",
                "resource": genomic_study,
            }
        )

        # Create DiagnosticReport (main Genomic Report)
        all_result_refs = (
            observation_references + phenotype_references + implication_references
        )
        diagnostic_report = self._create_diagnostic_report(
            pharmcat_data=pharmcat_data,
            patient_ref=patient_ref,
            result_refs=all_result_refs,
            study_ref=f"urn:uuid:{genomic_study['id']}",
            run_id=reference_id,
            workflow_id=workflow_id,
        )

        # Add extension for recommended actions (Tasks)
        if task_references:
            diagnostic_report["extension"] = diagnostic_report.get("extension", [])
            for task_ref in task_references:
                diagnostic_report["extension"].append(
                    {
                        "url": terminology.EXT_RECOMMENDED_ACTION,
                        "valueReference": task_ref,
                    }
                )

        entries.insert(
            0,
            {
                "fullUrl": f"urn:uuid:{diagnostic_report['id']}",
                "resource": diagnostic_report,
            },
        )

        # Build the Bundle
        bundle = {
            "resourceType": "Bundle",
            "id": bundle_id,
            # No meta.profile: the Genomics Reporting IG defines no Bundle profile
            # ("genomics-bundle" was cited here and exists in no version of it).
            "meta": {
                "lastUpdated": timestamp,
            },
            "type": "collection",
            "timestamp": timestamp,
            "entry": entries,
        }

        return bundle

    def _create_patient_resource(
        self,
        patient_info: Dict[str, Any],
        run_id: str,
        patient_id: Optional[str] = None,
    ) -> tuple:
        """Create a FHIR Patient resource.

        Its identifier is the id patient_info gives, else the ZaroPGx patient's
        (patient_id), and only failing both the run's. It used to fall straight
        to the run: a bundle saved without patient details (POST
        /fhir/save/workflow/{job}) named a Patient after the job, and pushing it
        then made a second Patient and moved the report onto it.
        """
        patient_identifier = patient_info.get("id") or patient_id or run_id
        patient_id = self._stable_id("Patient", patient_identifier)

        resource = {
            "resourceType": "Patient",
            "id": patient_id,
            "meta": {
                "profile": [terminology.PROFILE_PATIENT],
            },
            "identifier": [
                {
                    "system": terminology.ZAROPGX_PATIENT_ID,
                    "value": patient_identifier,
                }
            ],
        }

        # Add name if provided
        name = patient_info.get("name", {})
        if name:
            resource["name"] = [
                {
                    "family": name.get("family", ""),
                    "given": (
                        name.get("given", [])
                        if isinstance(name.get("given"), list)
                        else [name.get("given", "")]
                    ),
                }
            ]

        # Add gender if provided
        if patient_info.get("gender"):
            resource["gender"] = patient_info["gender"]

        # Add birthDate if provided
        if patient_info.get("birthDate"):
            resource["birthDate"] = patient_info["birthDate"]

        patient_ref = f"urn:uuid:{patient_id}"
        return resource, patient_ref

    def _create_genotype_observation(
        self,
        gene_data: Dict[str, Any],
        patient_ref: str,
        run_id: str,
        resource_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a FHIR Observation for genotype/diplotype.

        Following the Genotype profile from HL7 Genomics Reporting IG.
        """
        obs_id = resource_id or str(uuid.uuid4())
        gene_symbol = gene_data.get("gene", "Unknown")
        diplotype = gene_data.get("diplotype", "Unknown")
        activity_score = gene_data.get("activity_score")
        activity_value = None
        if activity_score not in (None, "", "n/a"):
            try:
                activity_value = float(activity_score)
            except (ValueError, TypeError):
                activity_value = None

        observation = {
            "resourceType": "Observation",
            "id": obs_id,
            "meta": {
                "profile": [terminology.PROFILE_GENOTYPE],
            },
            "identifier": [self._resource_identifier(obs_id)],
            "status": "final",
            "category": self._genomic_observation_category(),
            "code": {
                "coding": [
                    {
                        "system": terminology.LOINC,
                        "code": self.LOINC_CODES["genotype"],
                        "display": terminology.LOINC_DISPLAYS[
                            self.LOINC_CODES["genotype"]
                        ],
                    }
                ],
                "text": f"{gene_symbol} Genotype",
            },
            "subject": {
                "reference": patient_ref,
            },
            "effectiveDateTime": datetime.now(timezone.utc).isoformat(),
            # Text, as the IG's PharmVar genotype example has it. The diplotype
            # was sent as a code of http://www.pharmvar.org, whose codes are
            # PharmVar's allele ids (PV00155), never a diplotype -- and genes
            # PharmVar does not cover (ABCG2's rs2231142) went under it too.
            "valueCodeableConcept": {
                "text": diplotype,
            },
            "component": [],
        }

        # Add gene studied component
        observation["component"].append(
            {
                "code": {
                    "coding": [
                        {
                            "system": terminology.LOINC,
                            "code": self.LOINC_CODES["gene_studied"],
                            "display": terminology.LOINC_DISPLAYS[
                                self.LOINC_CODES["gene_studied"]
                            ],
                        }
                    ],
                },
                "valueCodeableConcept": self._gene_concept(gene_symbol),
            }
        )

        # No phenotype here: the genotype profile has no place for one, and the
        # IG states it as a therapeutic implication of the genotype
        # (_create_phenotype_implication). It was a text component.

        # Add activity score if present
        if activity_value is not None:
            observation["component"].append(
                {
                    "code": self._activity_score_code(gene_symbol),
                    "valueQuantity": {
                        "value": activity_value,
                        "system": terminology.UCUM,
                        "code": "1",
                    },
                }
            )

        # Add allele information if available
        allele1 = gene_data.get("allele1")
        allele2 = gene_data.get("allele2")
        if allele1:
            observation["component"].append(
                {
                    "code": {
                        "coding": [
                            {
                                "system": terminology.LOINC,
                                "code": self.LOINC_CODES["haplotype"],
                                "display": terminology.LOINC_DISPLAYS[
                                    self.LOINC_CODES["haplotype"]
                                ],
                            }
                        ],
                    },
                    "valueCodeableConcept": {
                        "text": allele1,
                    },
                }
            )
        if allele2:
            observation["component"].append(
                {
                    "code": {
                        "coding": [
                            {
                                "system": terminology.LOINC,
                                "code": self.LOINC_CODES["haplotype"],
                                "display": terminology.LOINC_DISPLAYS[
                                    self.LOINC_CODES["haplotype"]
                                ],
                            }
                        ],
                    },
                    "valueCodeableConcept": {
                        "text": allele2,
                    },
                }
            )

        return observation

    @staticmethod
    def _states_a_phenotype(phenotype: Any) -> bool:
        """Whether the row has a phenotype to state. "N/A" (app/reports/
        generator.py) says the calling tool defines none for the gene; "Unknown"
        that none was reported."""
        return bool(phenotype) and phenotype not in ("Unknown", "N/A")

    def _create_phenotype_implication(
        self,
        gene_data: Dict[str, Any],
        patient_ref: str,
        genotype_ref: Dict[str, str],
        resource_id: str,
    ) -> Dict[str, Any]:
        """The gene's phenotype as a therapeutic implication of its genotype.

        The Genomics Reporting IG (3.0.0) states a pharmacogenomic phenotype --
        metabolizer status, function, a risk -- in the "therapeutic-implication"
        component of a therapeutic-implication observation derived from the
        genotype, coded from its genetic-therapeutic-implications value set
        (LOINC answers such as LA9657-3 "Poor metabolizer"). This one names no
        medication: the phenotype is the genotype's, whatever the drug, and every
        gene with one gets it, drugs or not. A phenotype the value set has no code
        for travels as text (_phenotype_concept); a risk coded after CPIC's EHR
        priority carries a note that says so.
        """
        gene_symbol = gene_data.get("gene", "Unknown")
        concept = self._phenotype_concept(gene_data.get("phenotype"))
        observation = {
            "resourceType": "Observation",
            "id": resource_id,
            "meta": {
                "profile": [terminology.PROFILE_THERAPEUTIC_IMPLICATION],
            },
            "identifier": [self._resource_identifier(resource_id)],
            "status": "final",
            "category": self._genomic_observation_category(),
            "code": {
                "coding": [self._tbd_coding("therapeutic-implication")],
                "text": f"{gene_symbol} phenotype",
            },
            "subject": {
                "reference": patient_ref,
            },
            "effectiveDateTime": datetime.now(timezone.utc).isoformat(),
            "derivedFrom": [dict(genotype_ref)],
            "component": [
                {
                    "code": {
                        "coding": [self._tbd_coding("therapeutic-implication")],
                    },
                    "valueCodeableConcept": concept,
                }
            ],
        }
        # High or Low risk is CPIC's EHR priority for the call, not the call
        # itself (terminology.CPIC_PRIORITY_TERMS). A reader of the code alone
        # would take it for the result, so the observation says what it stands for.
        coding = concept.get("coding", [{}])[0]
        priority = terminology.CPIC_PRIORITY_TERMS.get(coding.get("code"))
        if priority:
            # One result, one term. HLA-B states a result per risk allele, joined
            # with ", " (_hla_results_code; no other risk phenotype has a comma):
            # High risk is CPIC's term for a positive one among them, Low risk its
            # term for negative results, every one of them.
            if "," not in concept["text"]:
                basis = "that result"
            elif coding["code"] == terminology.HLA_ALLELE_RESULT_ANSWERS["positive"]:
                basis = "a positive result"
            else:
                basis = "negative results"
            observation["note"] = [
                {
                    "text": (
                        f'ZaroPGx called this phenotype "{concept["text"]}". It is '
                        f'coded LOINC {coding["code"]} "{coding["display"]}" after '
                        f'CPIC\'s EHR priority term for {basis}, "{priority}", as '
                        "the IG's value set has no code for the call itself."
                    )
                }
            ]
        return observation

    @staticmethod
    def _phenotype_concept(phenotype: str) -> Dict[str, Any]:
        """The phenotype as the IG codes it, with ZaroPGx's words as the text, or
        the words alone when the value set has no code that says the same thing
        (terminology.PHENOTYPE_ANSWERS)."""
        text = str(phenotype).strip()
        key = " ".join(text.lower().split())
        code = terminology.PHENOTYPE_ANSWERS.get(key)
        if code is None:
            code = FHIRExportService._hla_results_code(key)
        if code is None:
            return {"text": text}
        return {
            "coding": [
                {
                    "system": terminology.LOINC,
                    "code": code,
                    "display": terminology.LOINC_DISPLAYS[code],
                }
            ],
            "text": text,
        }

    @staticmethod
    def _hla_results_code(key: str) -> Optional[str]:
        """High or Low risk for an HLA phenotype: one result per risk allele
        screened, joined with ", " ("*31:01 negative"; HLA-B's "*15:02 negative,
        *57:01 positive, *58:01 negative"). High risk if any result is positive,
        Low risk only if every one is negative; None, so the phenotype stays
        text, if any part is not an allele result. The results hold at once: by
        the first alone, a *57:01 or *58:01 carrier would be Low risk."""
        results = {}
        for part in key.split(","):
            allele_result = re.fullmatch(
                r"(\*\d+:\d+) (positive|negative)", part.strip()
            )
            if allele_result is None:
                return None
            results[allele_result.group(1)] = allele_result.group(2)
        # HLA-B is screened for three alleles. A phenotype naming only some of
        # them is HLA-B cut short (main kept only the first, "*15:02 negative",
        # before fix/hla-b-every-allele-result): coding it would call a *57:01 or
        # *58:01 carrier Low risk, so it stays text.
        screened = set(HLA_B_SCREENED_ALLELES)
        if screened & set(results) and not screened <= set(results):
            return None
        result = "positive" if "positive" in results.values() else "negative"
        return terminology.HLA_ALLELE_RESULT_ANSWERS[result]

    def _create_therapeutic_implication(
        self,
        drug_data: Dict[str, Any],
        patient_ref: str,
        observation_refs: List[Dict[str, str]],
        run_id: str,
        resource_id: Optional[str] = None,
        phenotype_refs: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Create a FHIR Observation for therapeutic implication.

        Following the Therapeutic Implication profile from HL7 Genomics Reporting IG.
        It derives from its genes' genotypes and cites their phenotypes, which
        their own implications state (_create_phenotype_implication), instead of
        saying them again.
        """
        obs_id = resource_id or str(uuid.uuid4())
        drug_name = drug_data.get("drug", "Unknown")
        genes = drug_data.get("genes", [])
        recommendations = drug_data.get("recommendations", [])

        # Get the first recommendation for the main implication
        primary_rec = recommendations[0] if recommendations else {}
        recommendation_text = primary_rec.get(
            "recommendation", "See report for details"
        )
        guideline_source = primary_rec.get("guideline_source", "")

        observation = {
            "resourceType": "Observation",
            "id": obs_id,
            "meta": {
                "profile": [terminology.PROFILE_THERAPEUTIC_IMPLICATION],
            },
            "identifier": [self._resource_identifier(obs_id)],
            "status": "final",
            "category": self._genomic_observation_category(),
            "code": {
                "coding": [self._tbd_coding("therapeutic-implication")],
            },
            "subject": {
                "reference": patient_ref,
            },
            "effectiveDateTime": datetime.now(timezone.utc).isoformat(),
            "component": [],
            "derivedFrom": [],
        }

        # Add medication assessed component
        observation["component"].append(
            {
                "code": {
                    "coding": [
                        {
                            "system": terminology.LOINC,
                            "code": self.LOINC_CODES["medication_assessed"],
                            "display": terminology.LOINC_DISPLAYS[
                                self.LOINC_CODES["medication_assessed"]
                            ],
                        }
                    ],
                },
                # The drug's name. An RxNorm coding with no code stood here:
                # PharmCAT names drugs by ClinPGx id, not RxNorm, so there is
                # no code to send.
                "valueCodeableConcept": {
                    "text": drug_name,
                },
            }
        )

        # Add conclusion string component
        if recommendation_text:
            observation["component"].append(
                {
                    "code": {
                        "coding": [self._tbd_coding("conclusion-string")],
                    },
                    "valueString": recommendation_text,
                }
            )

        # Add related artifacts (CPIC guidelines, etc.)
        if guideline_source:
            observation["extension"] = observation.get("extension", [])
            observation["extension"].append(
                {
                    "url": terminology.EXT_WORKFLOW_RELATED_ARTIFACT,
                    "valueRelatedArtifact": {
                        "type": "citation",
                        # PharmCAT's section name ("CPIC Guideline Annotation")
                        # as it stands; a short source ("CPIC") as "CPIC Guideline".
                        "display": (
                            guideline_source
                            if " " in guideline_source.strip()
                            else f"{guideline_source} Guideline"
                        ),
                        "url": self._get_guideline_url(guideline_source, drug_name),
                    },
                }
            )

        # Link to derived genotype observations. Each reference's display is
        # "<gene> <diplotype>": match its gene exactly. A substring match tied
        # CYP3A4's drugs to whichever of CYP3A4 and CYP3A43 came first, and F2's
        # to CYP4F2.
        for gene in genes:
            for obs_ref in observation_refs:
                if obs_ref.get("display", "").split(" ", 1)[0] == gene:
                    observation["derivedFrom"].append(
                        {
                            "reference": obs_ref["reference"],
                            "display": obs_ref.get("display", ""),
                        }
                    )
                    break
        # Then to the phenotype implications of the same genes, matched the same
        # way ("<gene> phenotype"). The IG's profile slices derivedFrom openly, so
        # another therapeutic implication is allowed there; the phenotype stays in
        # its own observation, not repeated here.
        for gene in genes:
            for phenotype_ref in phenotype_refs or []:
                if phenotype_ref.get("display", "").split(" ", 1)[0] == gene:
                    observation["derivedFrom"].append(dict(phenotype_ref))
                    break
        # No genotype to point at: no derivedFrom, not an empty list (FHIR JSON
        # has no empty arrays). The IG's profile wants one, so such a drug's
        # implication will not validate; it is still sent.
        if not observation["derivedFrom"]:
            del observation["derivedFrom"]

        return observation

    def _create_medication_recommendation_task(
        self,
        drug_name: str,
        recommendation: Dict[str, Any],
        patient_ref: str,
        implication_ref: str,
        run_id: str,
        resource_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a FHIR Task for medication recommendation.

        Following the Medication Recommendation profile from HL7 Genomics Reporting IG.
        """
        task_id = resource_id or str(uuid.uuid4())
        rec_text = recommendation.get("recommendation", "See report for details")
        classification = recommendation.get("classification", "")

        # No Task.code: the profile binds it (required) to LOINC's medication
        # usage suggestions (LL4049-4: consider alternative, decrease dose, ...).
        # Choosing one would be reading the guideline's text; it said
        # "Recommendation for <drug>" in a code system with no such code. The
        # drug is the medication-assessed input, the guidance the description.
        task = {
            "resourceType": "Task",
            "id": task_id,
            "meta": {
                "profile": [terminology.PROFILE_MEDICATION_RECOMMENDATION],
            },
            "identifier": [self._resource_identifier(task_id)],
            "status": "requested",
            "intent": "proposal",
            "description": rec_text,
            "for": {
                "reference": patient_ref,
            },
            "reasonReference": {
                "reference": implication_ref,
            },
            "input": [
                {
                    "type": {
                        "coding": [self._loinc_coding("medication_assessed")],
                    },
                    "valueCodeableConcept": {
                        "text": drug_name,
                    },
                }
            ],
        }

        # PharmCAT's classification is CPIC's strength of recommendation (Strong,
        # Moderate, Optional, No recommendation; "Unspecified" for a guideline
        # without one, see app/reports/evidence.py), not a level of evidence:
        # sent as text, under a text type, since no LOINC code names it. Sent as
        # LOINC 93044-6 "Level of evidence" before. "Unknown" is the readers'
        # default when PharmCAT gave nothing, so it is left out.
        if classification and classification.strip().lower() != "unknown":
            task["input"].append(
                {
                    "type": {"text": "Strength of recommendation"},
                    "valueCodeableConcept": {
                        "text": classification,
                    },
                }
            )

        return task

    def _create_genomic_study(
        self,
        pharmcat_data: Dict[str, Any],
        patient_ref: str,
        run_id: str,
        resource_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a FHIR Procedure resource for the genomic study."""
        study_id = resource_id or str(uuid.uuid4())
        pharmcat_version = pharmcat_data.get("pharmcat_version", "Unknown")

        study = {
            "resourceType": "Procedure",
            "id": study_id,
            "meta": {
                "profile": [terminology.PROFILE_GENOMIC_STUDY],
            },
            "identifier": [self._resource_identifier(study_id)],
            "status": "completed",
            # The genomic-study profile fixes the category to "laboratory".
            "category": {
                "coding": [
                    {
                        "system": terminology.OBSERVATION_CATEGORY,
                        "code": "laboratory",
                        "display": "Laboratory",
                    }
                ],
            },
            # What was done: a genetic test (a SNOMED CT procedure). It was coded
            # as LOINC's "Genetic analysis report", the DiagnosticReport's code.
            "code": {
                "coding": [
                    {
                        "system": terminology.SNOMED_CT,
                        "code": terminology.SNOMED_GENETIC_TEST[0],
                        "display": terminology.SNOMED_GENETIC_TEST[1],
                    }
                ],
                "text": "Pharmacogenomic Analysis",
            },
            "subject": {
                "reference": patient_ref,
            },
            "performedDateTime": datetime.now(timezone.utc).isoformat(),
            "note": [
                {
                    "text": f"PharmCAT Version: {pharmcat_version}",
                }
            ],
        }

        return study

    def _create_diagnostic_report(
        self,
        pharmcat_data: Dict[str, Any],
        patient_ref: str,
        result_refs: List[Dict[str, str]],
        study_ref: str,
        run_id: str,
        workflow_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a FHIR DiagnosticReport (Genomic Report).

        Following the Genomic Report profile from HL7 Genomics Reporting IG.
        """
        report_id = self._stable_id("DiagnosticReport", run_id)
        # The genes this bundle states a genotype for (one genotype observation
        # each), counted the same whichever lane built the data. total_genes was
        # PharmCAT's row count: 23 for a 30x NA12878 report whose bundle carries 91
        # genotypes (PharmCAT's and PyPGx's), CPIC and DPWG rows both for PharmCAT
        # 2.x (37 for 22 genes), and absent, so "0 genes", in the JSON lane's data.
        total_genes = len(
            {
                str(gene.get("gene")).strip()
                for gene in pharmcat_data.get("genes") or []
                if isinstance(gene, dict) and str(gene.get("gene") or "").strip()
            }
        )
        # Genes a guideline's prescribing change comes from (app.reports.actionable),
        # counted off the recommendations this bundle is built from, whichever lane
        # built them: the JSON lane's data has no actionable_findings and read 0.
        actionable_count = len(
            actionable_genes(pharmcat_data.get("drugRecommendations"))
        )

        report = {
            "resourceType": "DiagnosticReport",
            "id": report_id,
            "meta": {
                "profile": [terminology.PROFILE_GENOMIC_REPORT],
            },
            "identifier": [
                {
                    "system": terminology.ZAROPGX_REPORT_ID,
                    "value": run_id,
                }
            ],
            "status": "final",
            "category": [
                {
                    "coding": [
                        {
                            "system": terminology.DIAGNOSTIC_SERVICE_SECTION,
                            "code": "GE",
                            "display": "Genetics",
                        }
                    ]
                }
            ],
            "code": {
                "coding": [
                    {
                        "system": terminology.LOINC,
                        "code": self.LOINC_CODES["pgx_report"],
                        "display": terminology.LOINC_DISPLAYS[
                            self.LOINC_CODES["pgx_report"]
                        ],
                    }
                ],
                "text": "Pharmacogenomic Analysis Report",
            },
            "subject": {
                "reference": patient_ref,
            },
            "effectiveDateTime": datetime.now(timezone.utc).isoformat(),
            "issued": datetime.now(timezone.utc).isoformat(),
            "result": result_refs,
            # No conclusionCode: "PGX-COMPLETE" in urn:zaropgx:conclusion-codes, a
            # code system defined nowhere, said what status "final" says.
            "conclusion": (
                f"Pharmacogenomic analysis completed. {total_genes} "
                f"gene{'' if total_genes == 1 else 's'} analyzed, {actionable_count} "
                f"actionable finding{'' if actionable_count == 1 else 's'} identified."
            ),
        }

        # Add reference to genomic study
        report["extension"] = report.get("extension", [])
        report["extension"].append(
            {
                "url": terminology.EXT_GENOMIC_STUDY_REFERENCE,
                "valueReference": {
                    "reference": study_ref,
                },
            }
        )

        return report

    def _job_patient_id(self, workflow_id: Optional[str]) -> Optional[str]:
        """The patient a job belongs to (its metadata's patient_id, the name of
        its report directory's parent), or None."""
        if not workflow_id:
            return None
        try:
            # Lazy for the same circular-import reason as generator's imports.
            from app.services.job_service import JobService

            job = JobService(self.db).get_job(str(workflow_id))
        except Exception as e:
            logger.debug(f"No job metadata for {workflow_id}: {e}")
            return None
        metadata = getattr(job, "job_metadata", None)
        value = metadata.get("patient_id") if isinstance(metadata, dict) else None
        return str(value) if isinstance(value, (str, uuid.UUID)) and value else None

    @staticmethod
    def _stable_id(*parts: Any) -> str:
        """The v5 UUID of what a resource is (see RESOURCE_ID_NAMESPACE)."""
        name = "/".join(str(part) for part in parts)
        return str(uuid.uuid5(RESOURCE_ID_NAMESPACE, name))

    @staticmethod
    def _resource_identifier(resource_id: str) -> Dict[str, str]:
        """The resource's id as its business identifier: what a FHIR server
        matches a re-pushed resource on (``Observation?identifier=...``)."""
        return {
            "system": terminology.URI_IDENTIFIER,
            "value": f"urn:uuid:{resource_id}",
        }

    @staticmethod
    def _genomic_observation_category() -> List[Dict[str, Any]]:
        """Both categories the IG's genomic observations require: laboratory and
        Genetics (v2-0074 GE). The genotype and therapeutic-implication
        profiles reject an observation that has only one."""
        return [
            {
                "coding": [
                    {
                        "system": terminology.OBSERVATION_CATEGORY,
                        "code": "laboratory",
                        "display": "Laboratory",
                    }
                ]
            },
            {
                "coding": [
                    {
                        "system": terminology.DIAGNOSTIC_SERVICE_SECTION,
                        "code": "GE",
                        "display": "Genetics",
                    }
                ]
            },
        ]

    @staticmethod
    def _loinc_coding(key: str) -> Dict[str, str]:
        """The LOINC coding for one of LOINC_CODES, with its official display."""
        code = terminology.LOINC_CODES[key]
        return {
            "system": terminology.LOINC,
            "code": code,
            "display": terminology.LOINC_DISPLAYS[code],
        }

    @staticmethod
    def _tbd_coding(code: str) -> Dict[str, str]:
        """A code of the IG's tbd-codes-cs, with the IG's display."""
        return {
            "system": terminology.GENOMICS_TBD_CODES,
            "code": code,
            "display": terminology.GENOMICS_TBD_DISPLAYS[code],
        }

    @staticmethod
    def _gene_concept(gene_symbol: str) -> Dict[str, Any]:
        """A gene as the IG's hgnc-vs codes it: its HGNC id, the symbol as the
        display. A gene missing from HGNC_IDS is sent by its symbol alone."""
        hgnc_id = terminology.HGNC_IDS.get(gene_symbol)
        if not hgnc_id:
            return {"text": gene_symbol}
        return {
            "coding": [
                {
                    "system": terminology.HGNC,
                    "code": hgnc_id,
                    "display": gene_symbol,
                }
            ],
            "text": gene_symbol,
        }

    @staticmethod
    def _activity_score_code(gene_symbol: str) -> Dict[str, Any]:
        """LOINC's activity-score code for the gene, or a text code for a gene
        LOINC has none for."""
        code = terminology.ACTIVITY_SCORE_LOINC.get(gene_symbol)
        if not code:
            return {"text": "Activity score"}
        return {
            "coding": [
                {
                    "system": terminology.LOINC,
                    "code": code,
                    "display": terminology.LOINC_DISPLAYS[code],
                }
            ],
        }

    def _get_guideline_url(self, source: str, drug_name: str) -> str:
        """Get the guideline URL based on source."""
        # A letter (C, D, F), a short name, PharmCAT's code (CPIC_GUIDELINE) or its
        # section name ("CPIC Guideline Annotation"): the first word decides.
        source_upper = (
            (source.upper().replace("_", " ").split() or [""])[0] if source else ""
        )
        drug_lower = drug_name.lower().replace(" ", "-") if drug_name else ""

        if source_upper == "CPIC" or source_upper == "C":
            return f"https://www.clinpgx.org/guidelineAnnotations"
        elif source_upper == "DPWG" or source_upper == "D":
            return "https://www.pharmgkb.org/page/dpwg"
        elif source_upper == "FDA" or source_upper == "F":
            return "https://www.fda.gov/drugs/science-and-research-drugs/table-pharmacogenomic-biomarkers-drug-labeling"
        else:
            return f"https://www.pharmgkb.org/drug/{drug_lower}"

    def _bundle_to_xml(self, bundle: Dict[str, Any]) -> str:
        """The bundle as FHIR XML (app.services.fhir.xml_format)."""
        return xml_format.bundle_to_xml(bundle)


def get_fhir_export_service(db: Session) -> FHIRExportService:
    """Factory function to get FHIR export service instance."""
    return FHIRExportService(db)
