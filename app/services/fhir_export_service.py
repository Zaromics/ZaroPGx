"""
FHIR Export Service for ZaroPGx

This service generates FHIR R4-compliant exports of pharmacogenomic reports
following the HL7 Genomics Reporting Implementation Guide.

Reference: https://build.fhir.org/ig/HL7/genomics-reporting/pharmacogenomics.html
"""

import json
import logging
import os
import uuid
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from sqlalchemy.orm import Session

from app.services.pharmcat_data_service import PharmCATDataService

logger = logging.getLogger(__name__)

# Environment flag to enable/disable FHIR export functionality
FHIR_EXPORT_ENABLED = os.getenv("FHIR_EXPORT_ENABLED", "true").lower() in {"1", "true", "yes", "on"}

# Reports directory - same as other report outputs
REPORT_DIR = Path(os.getenv("REPORT_DIR", "/data/reports"))

# FHIR namespace for XML export
FHIR_NAMESPACE = "http://hl7.org/fhir"


class FHIRExportService:
    """
    Service for generating FHIR R4-compliant pharmacogenomics exports.
    
    Follows the HL7 Genomics Reporting Implementation Guide for PGx reporting.
    Generates standalone FHIR Bundle exports in JSON or XML format.
    """
    
    # LOINC codes for PGx observations
    LOINC_CODES = {
        "genotype": "84413-4",  # Genotype display name
        "therapeutic_implication": "83009-1",  # Genetic variation clinical significance
        "medication_assessed": "51963-7",  # Medication assessed
        "pgx_report": "51969-4",  # Genetic analysis report
        "haplotype": "84414-2",  # Haplotype Name
        "gene_studied": "48018-6",  # Gene studied
        "phenotype": "79716-7",  # Molecular consequence
    }
    
    # Gene-specific LOINC codes (common PGx genes)
    GENE_LOINC_CODES = {
        "CYP2D6": "79714-2",
        "CYP2C19": "79713-4",
        "CYP2C9": "79712-6",
        "CYP3A4": "94040-2",
        "CYP3A5": "94041-0",
        "CYP1A2": "79711-8",
        "SLCO1B1": "79717-5",
        "VKORC1": "50720-0",
        "DPYD": "98059-8",
        "TPMT": "79715-9",
        "NUDT15": "98060-6",
        "UGT1A1": "79718-3",
        "HLA-B": "81247-9",
        "HLA-A": "81248-7",
        "G6PD": "79719-1",
    }
    
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
        return FHIR_EXPORT_ENABLED
    
    def export_pgx_report(
        self,
        run_id: Optional[str] = None,
        patient_info: Optional[Dict[str, Any]] = None,
        output_format: str = "json",
        include_recommendations: bool = True,
        pharmcat_data: Optional[Dict[str, Any]] = None,
        workflow_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Export a PharmCAT run as a FHIR Bundle.
        
        Args:
            run_id: PharmCAT run ID to export
            patient_info: Optional patient information dict
            output_format: "json" or "xml"
            include_recommendations: Whether to include therapeutic implications
            
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
            data = pharmcat_data or self.pharmcat_service.get_pharmcat_data_for_workflow(workflow_id)
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
        
        Files are saved alongside other report outputs (PDF, HTML, etc.) in the
        same patient/run subdirectory.
        
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
            
            # Create reports subdirectory if it doesn't exist
            report_subdir = REPORT_DIR / subdir_id
            report_subdir.mkdir(parents=True, exist_ok=True)
            
            files_saved = []
            formats_to_export = ["json", "xml"] if output_format.lower() == "both" else [output_format.lower()]
            
            for fmt in formats_to_export:
                # Generate the export
                result = self.export_pgx_report(
                    run_id=run_id,
                    patient_info=patient_info,
                    output_format=fmt,
                    include_recommendations=include_recommendations,
                    pharmcat_data=pharmcat_data,
                    workflow_id=workflow_id,
                )
                
                if not result.get("success"):
                    return {
                        "success": False,
                        "error": result.get("error", f"Failed to generate {fmt} export"),
                        "files_saved": files_saved,
                    }
                
                # Determine filename
                extension = "xml" if fmt == "xml" else "json"
                filename = f"pgx_fhir_report.{extension}"
                filepath = report_subdir / filename
                
                # Write the file
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(result["content"])
                
                logger.info(f"Saved FHIR {fmt.upper()} export to: {filepath}")
                files_saved.append({
                    "format": fmt,
                    "path": str(filepath),
                    "filename": filename,
                    "url": f"/reports/{subdir_id}/{filename}",
                })
            
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
            pharmcat_data = self.pharmcat_service.get_pharmcat_data_for_workflow(workflow_id)
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
    ) -> Dict[str, Any]:
        """
        Build a FHIR Bundle containing all PGx report resources.
        
        Following the HL7 Genomics Reporting IG structure:
        - DiagnosticReport (Genomic Report)
        - Observation (Genotype) for each gene
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
        implication_references = []
        task_references = []
        
        # Create Patient resource
        patient_resource, patient_ref = self._create_patient_resource(
            patient_info or {},
            reference_id,
        )
        entries.append({
            "fullUrl": f"urn:uuid:{patient_resource['id']}",
            "resource": patient_resource,
        })
        
        # Create Genotype Observations for each gene
        genes = pharmcat_data.get("genes", [])
        for gene_data in genes:
            genotype_obs = self._create_genotype_observation(
                gene_data=gene_data,
                patient_ref=patient_ref,
                run_id=run_id,
            )
            entries.append({
                "fullUrl": f"urn:uuid:{genotype_obs['id']}",
                "resource": genotype_obs,
            })
            observation_references.append({
                "reference": f"urn:uuid:{genotype_obs['id']}",
                "display": f"{gene_data.get('gene', 'Unknown')} {gene_data.get('diplotype', '')}",
            })
        
        # Create Therapeutic Implications and Medication Recommendations
        if include_recommendations:
            drug_recommendations = pharmcat_data.get("drugRecommendations", [])
            for drug_rec in drug_recommendations:
                # Therapeutic Implication observation
                implication_obs = self._create_therapeutic_implication(
                    drug_data=drug_rec,
                    patient_ref=patient_ref,
                    observation_refs=observation_references,
                    run_id=run_id,
                )
                entries.append({
                    "fullUrl": f"urn:uuid:{implication_obs['id']}",
                    "resource": implication_obs,
                })
                implication_references.append({
                    "reference": f"urn:uuid:{implication_obs['id']}",
                    "display": f"Therapeutic implication for {drug_rec.get('drug', 'Unknown')}",
                })
                
                # Medication Recommendation Task
                recommendations = drug_rec.get("recommendations", [])
                for rec in recommendations:
                    med_task = self._create_medication_recommendation_task(
                        drug_name=drug_rec.get("drug", "Unknown"),
                        recommendation=rec,
                        patient_ref=patient_ref,
                        implication_ref=f"urn:uuid:{implication_obs['id']}",
                        run_id=run_id,
                    )
                    entries.append({
                        "fullUrl": f"urn:uuid:{med_task['id']}",
                        "resource": med_task,
                    })
                    task_references.append({
                        "reference": f"urn:uuid:{med_task['id']}",
                    })
        
        # Create Genomic Study resource
        genomic_study = self._create_genomic_study(
            pharmcat_data=pharmcat_data,
            patient_ref=patient_ref,
            run_id=run_id,
        )
        entries.append({
            "fullUrl": f"urn:uuid:{genomic_study['id']}",
            "resource": genomic_study,
        })
        
        # Create DiagnosticReport (main Genomic Report)
        all_result_refs = observation_references + implication_references
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
                diagnostic_report["extension"].append({
                    "url": "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/recommended-action",
                    "valueReference": task_ref,
                })
        
        entries.insert(0, {
            "fullUrl": f"urn:uuid:{diagnostic_report['id']}",
            "resource": diagnostic_report,
        })
        
        # Build the Bundle
        bundle = {
            "resourceType": "Bundle",
            "id": bundle_id,
            "meta": {
                "profile": [
                    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/genomics-bundle"
                ],
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
    ) -> tuple:
        """Create a FHIR Patient resource."""
        patient_id = str(uuid.uuid4())
        
        resource = {
            "resourceType": "Patient",
            "id": patient_id,
            "meta": {
                "profile": ["http://hl7.org/fhir/StructureDefinition/Patient"],
            },
            "identifier": [
                {
                    "system": "urn:zaropgx:patient-id",
                    "value": patient_info.get("id", run_id),
                }
            ],
        }
        
        # Add name if provided
        name = patient_info.get("name", {})
        if name:
            resource["name"] = [{
                "family": name.get("family", ""),
                "given": name.get("given", []) if isinstance(name.get("given"), list) else [name.get("given", "")],
            }]
        
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
    ) -> Dict[str, Any]:
        """
        Create a FHIR Observation for genotype/diplotype.
        
        Following the Genotype profile from HL7 Genomics Reporting IG.
        """
        obs_id = str(uuid.uuid4())
        gene_symbol = gene_data.get("gene", "Unknown")
        diplotype = gene_data.get("diplotype", "Unknown")
        phenotype = gene_data.get("phenotype", "Unknown")
        activity_score = gene_data.get("activity_score")
        activity_value = None
        if activity_score not in (None, "", "n/a"):
            try:
                activity_value = float(activity_score)
            except (ValueError, TypeError):
                activity_value = None
        
        # Get gene-specific LOINC code or use generic
        gene_loinc = self.GENE_LOINC_CODES.get(gene_symbol, self.LOINC_CODES["genotype"])
        
        observation = {
            "resourceType": "Observation",
            "id": obs_id,
            "meta": {
                "profile": [
                    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/genotype"
                ],
            },
            "status": "final",
            "category": [
                {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                            "code": "laboratory",
                            "display": "Laboratory",
                        }
                    ]
                }
            ],
            "code": {
                "coding": [
                    {
                        "system": "http://loinc.org",
                        "code": gene_loinc,
                        "display": f"{gene_symbol} gene product metabolic activity interpretation",
                    }
                ],
                "text": f"{gene_symbol} Genotype",
            },
            "subject": {
                "reference": patient_ref,
            },
            "effectiveDateTime": datetime.now(timezone.utc).isoformat(),
            "valueCodeableConcept": {
                "coding": [
                    {
                        "system": "http://www.pharmvar.org",
                        "code": diplotype,
                        "display": diplotype,
                    }
                ],
                "text": diplotype,
            },
            "component": [],
        }
        
        # Add gene studied component
        observation["component"].append({
            "code": {
                "coding": [
                    {
                        "system": "http://loinc.org",
                        "code": self.LOINC_CODES["gene_studied"],
                        "display": "Gene studied [ID]",
                    }
                ],
            },
            "valueCodeableConcept": {
                "coding": [
                    {
                        "system": "http://www.genenames.org/geneId",
                        "code": gene_symbol,
                        "display": gene_symbol,
                    }
                ],
                "text": gene_symbol,
            },
        })
        
        # Add phenotype component
        if phenotype and phenotype != "Unknown":
            observation["component"].append({
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": self.LOINC_CODES["phenotype"],
                            "display": "Phenotype display name",
                        }
                    ],
                },
                "valueCodeableConcept": {
                    "text": phenotype,
                },
            })
        
        # Add activity score if present
        if activity_value is not None:
            observation["component"].append({
                "code": {
                    "coding": [
                        {
                            "system": "http://hl7.org/fhir/uv/genomics-reporting/CodeSystem/tbd-codes-cs",
                            "code": "activity-score",
                            "display": "Activity Score",
                        }
                    ],
                },
                "valueQuantity": {
                    "value": activity_value,
                    "system": "http://unitsofmeasure.org",
                    "code": "1",
                },
            })
        
        # Add allele information if available
        allele1 = gene_data.get("allele1")
        allele2 = gene_data.get("allele2")
        if allele1:
            observation["component"].append({
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": self.LOINC_CODES["haplotype"],
                            "display": "Haplotype Name",
                        }
                    ],
                },
                "valueCodeableConcept": {
                    "text": allele1,
                },
            })
        if allele2:
            observation["component"].append({
                "code": {
                    "coding": [
                        {
                            "system": "http://loinc.org",
                            "code": self.LOINC_CODES["haplotype"],
                            "display": "Haplotype Name",
                        }
                    ],
                },
                "valueCodeableConcept": {
                    "text": allele2,
                },
            })
        
        return observation
    
    def _create_therapeutic_implication(
        self,
        drug_data: Dict[str, Any],
        patient_ref: str,
        observation_refs: List[Dict[str, str]],
        run_id: str,
    ) -> Dict[str, Any]:
        """
        Create a FHIR Observation for therapeutic implication.
        
        Following the Therapeutic Implication profile from HL7 Genomics Reporting IG.
        """
        obs_id = str(uuid.uuid4())
        drug_name = drug_data.get("drug", "Unknown")
        genes = drug_data.get("genes", [])
        recommendations = drug_data.get("recommendations", [])
        
        # Get the first recommendation for the main implication
        primary_rec = recommendations[0] if recommendations else {}
        recommendation_text = primary_rec.get("recommendation", "See report for details")
        classification = primary_rec.get("classification", "")
        guideline_source = primary_rec.get("guideline_source", "")
        
        observation = {
            "resourceType": "Observation",
            "id": obs_id,
            "meta": {
                "profile": [
                    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/therapeutic-implication"
                ],
            },
            "status": "final",
            "category": [
                {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                            "code": "laboratory",
                            "display": "Laboratory",
                        }
                    ]
                }
            ],
            "code": {
                "coding": [
                    {
                        "system": "http://hl7.org/fhir/uv/genomics-reporting/CodeSystem/tbd-codes-cs",
                        "code": "therapeutic-implication",
                        "display": "Therapeutic Implication",
                    }
                ],
            },
            "subject": {
                "reference": patient_ref,
            },
            "effectiveDateTime": datetime.now(timezone.utc).isoformat(),
            "component": [],
            "derivedFrom": [],
        }
        
        # Add medication assessed component
        observation["component"].append({
            "code": {
                "coding": [
                    {
                        "system": "http://loinc.org",
                        "code": self.LOINC_CODES["medication_assessed"],
                        "display": "Medication assessed [ID]",
                    }
                ],
            },
            "valueCodeableConcept": {
                "coding": [
                    {
                        "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                        "display": drug_name,
                    }
                ],
                "text": drug_name,
            },
        })
        
        # Add conclusion string component
        if recommendation_text:
            observation["component"].append({
                "code": {
                    "coding": [
                        {
                            "system": "http://hl7.org/fhir/uv/genomics-reporting/CodeSystem/tbd-codes-cs",
                            "code": "conclusion-string",
                            "display": "Conclusion String",
                        }
                    ],
                },
                "valueString": recommendation_text,
            })
        
        # Add related artifacts (CPIC guidelines, etc.)
        if guideline_source:
            observation["extension"] = observation.get("extension", [])
            observation["extension"].append({
                "url": "http://hl7.org/fhir/StructureDefinition/workflow-relatedArtifact",
                "valueRelatedArtifact": {
                    "type": "citation",
                    "display": f"{guideline_source} Guideline",
                    "url": self._get_guideline_url(guideline_source, drug_name),
                },
            })
        
        # Link to derived genotype observations
        for gene in genes:
            # Find matching observation reference
            for obs_ref in observation_refs:
                if gene in obs_ref.get("display", ""):
                    observation["derivedFrom"].append({
                        "reference": obs_ref["reference"],
                        "display": obs_ref.get("display", ""),
                    })
                    break
        
        return observation
    
    def _create_medication_recommendation_task(
        self,
        drug_name: str,
        recommendation: Dict[str, Any],
        patient_ref: str,
        implication_ref: str,
        run_id: str,
    ) -> Dict[str, Any]:
        """
        Create a FHIR Task for medication recommendation.
        
        Following the Medication Recommendation profile from HL7 Genomics Reporting IG.
        """
        task_id = str(uuid.uuid4())
        rec_text = recommendation.get("recommendation", "See report for details")
        classification = recommendation.get("classification", "")
        
        task = {
            "resourceType": "Task",
            "id": task_id,
            "meta": {
                "profile": [
                    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/medication-recommendation"
                ],
            },
            "status": "requested",
            "intent": "proposal",
            "code": {
                "coding": [
                    {
                        "system": "http://hl7.org/fhir/uv/genomics-reporting/CodeSystem/tbd-codes-cs",
                        "code": "medication-recommendation",
                        "display": "Medication Recommendation",
                    }
                ],
                "text": f"Recommendation for {drug_name}",
            },
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
                        "coding": [
                            {
                                "system": "http://hl7.org/fhir/uv/genomics-reporting/CodeSystem/tbd-codes-cs",
                                "code": "medication-assessed",
                                "display": "Medication Assessed",
                            }
                        ],
                    },
                    "valueCodeableConcept": {
                        "text": drug_name,
                    },
                }
            ],
        }
        
        # Add evidence level if available
        if classification:
            task["input"].append({
                "type": {
                    "coding": [
                        {
                            "system": "http://hl7.org/fhir/uv/genomics-reporting/CodeSystem/tbd-codes-cs",
                            "code": "evidence-level",
                            "display": "Evidence Level",
                        }
                    ],
                },
                "valueCodeableConcept": {
                    "text": classification,
                },
            })
        
        return task
    
    def _create_genomic_study(
        self,
        pharmcat_data: Dict[str, Any],
        patient_ref: str,
        run_id: str,
    ) -> Dict[str, Any]:
        """Create a FHIR Procedure resource for the genomic study."""
        study_id = str(uuid.uuid4())
        pharmcat_version = pharmcat_data.get("pharmcat_version", "Unknown")
        
        study = {
            "resourceType": "Procedure",
            "id": study_id,
            "meta": {
                "profile": [
                    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/genomic-study"
                ],
            },
            "status": "completed",
            "category": {
                "coding": [
                    {
                        "system": "http://snomed.info/sct",
                        "code": "405824009",
                        "display": "Genetic analysis",
                    }
                ],
            },
            "code": {
                "coding": [
                    {
                        "system": "http://loinc.org",
                        "code": self.LOINC_CODES["pgx_report"],
                        "display": "Pharmacogenomic analysis panel",
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
        report_id = str(uuid.uuid4())
        total_genes = pharmcat_data.get("total_genes", 0)
        actionable_count = len(pharmcat_data.get("actionable_findings", []))
        
        report = {
            "resourceType": "DiagnosticReport",
            "id": report_id,
            "meta": {
                "profile": [
                    "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/genomic-report"
                ],
            },
            "identifier": [
                {
                    "system": "urn:zaropgx:report-id",
                    "value": run_id,
                }
            ],
            "status": "final",
            "category": [
                {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/v2-0074",
                            "code": "GE",
                            "display": "Genetics",
                        }
                    ]
                }
            ],
            "code": {
                "coding": [
                    {
                        "system": "http://loinc.org",
                        "code": self.LOINC_CODES["pgx_report"],
                        "display": "Pharmacogenomic analysis report",
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
            "conclusion": f"Pharmacogenomic analysis completed. {total_genes} genes analyzed, {actionable_count} actionable findings identified.",
            "conclusionCode": [
                {
                    "coding": [
                        {
                            "system": "urn:zaropgx:conclusion-codes",
                            "code": "PGX-COMPLETE",
                            "display": "Pharmacogenomic Analysis Complete",
                        }
                    ],
                }
            ],
        }
        
        # Add reference to genomic study
        report["extension"] = report.get("extension", [])
        report["extension"].append({
            "url": "http://hl7.org/fhir/uv/genomics-reporting/StructureDefinition/genomic-study-reference",
            "valueReference": {
                "reference": study_ref,
            },
        })
        
        return report
    
    def _get_guideline_url(self, source: str, drug_name: str) -> str:
        """Get the guideline URL based on source."""
        source_upper = source.upper() if source else ""
        drug_lower = drug_name.lower().replace(" ", "-") if drug_name else ""
        
        if source_upper == "CPIC" or source_upper == "C":
            return f"https://cpicpgx.org/guidelines/"
        elif source_upper == "DPWG" or source_upper == "D":
            return "https://www.pharmgkb.org/page/dpwg"
        elif source_upper == "FDA" or source_upper == "F":
            return "https://www.fda.gov/drugs/science-and-research-drugs/table-pharmacogenomic-biomarkers-drug-labeling"
        else:
            return f"https://www.pharmgkb.org/drug/{drug_lower}"
    
    def _bundle_to_xml(self, bundle: Dict[str, Any]) -> str:
        """
        Convert a FHIR Bundle dict to XML string.
        
        Args:
            bundle: FHIR Bundle as dictionary
            
        Returns:
            XML string representation
        """
        root = ET.Element("Bundle")
        root.set("xmlns", FHIR_NAMESPACE)
        
        self._dict_to_xml(bundle, root)
        
        # Pretty print
        xml_str = ET.tostring(root, encoding="unicode")
        parsed = minidom.parseString(xml_str)
        return parsed.toprettyxml(indent="  ")
    
    def _dict_to_xml(self, data: Any, parent: ET.Element) -> None:
        """Recursively convert dict to XML elements."""
        if isinstance(data, dict):
            for key, value in data.items():
                if key == "resourceType":
                    # resourceType is the element name in FHIR XML
                    continue
                elif key == "extension":
                    # Handle extensions specially
                    if isinstance(value, list):
                        for ext in value:
                            ext_elem = ET.SubElement(parent, "extension")
                            if isinstance(ext, dict) and "url" in ext:
                                ext_elem.set("url", ext["url"])
                                for k, v in ext.items():
                                    if k != "url":
                                        self._add_element(ext_elem, k, v)
                elif isinstance(value, list):
                    for item in value:
                        elem = ET.SubElement(parent, key)
                        if isinstance(item, dict):
                            self._dict_to_xml(item, elem)
                        else:
                            elem.set("value", str(item))
                elif isinstance(value, dict):
                    elem = ET.SubElement(parent, key)
                    self._dict_to_xml(value, elem)
                elif value is not None:
                    elem = ET.SubElement(parent, key)
                    elem.set("value", str(value))
    
    def _add_element(self, parent: ET.Element, key: str, value: Any) -> None:
        """Add an element to parent."""
        if isinstance(value, dict):
            elem = ET.SubElement(parent, key)
            self._dict_to_xml(value, elem)
        elif isinstance(value, list):
            for item in value:
                elem = ET.SubElement(parent, key)
                if isinstance(item, dict):
                    self._dict_to_xml(item, elem)
                else:
                    elem.set("value", str(item))
        elif value is not None:
            elem = ET.SubElement(parent, key)
            elem.set("value", str(value))


def get_fhir_export_service(db: Session) -> FHIRExportService:
    """Factory function to get FHIR export service instance."""
    return FHIRExportService(db)

