import os
import json
import logging
import requests
from typing import Dict, Any, List, Optional
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class FhirClient:
    """
    Client for interacting with FHIR servers to export pharmacogenomic reports
    """
    
    def __init__(self, fhir_server_url: str = None):
        """
        Initialize the FHIR client
        
        Args:
            fhir_server_url: URL of the FHIR server, defaults to environment variable
        """
        self.fhir_server_url = fhir_server_url or os.environ.get("FHIR_SERVER_URL", "http://fhir-server:8080/fhir")
        logger.info(f"Initialized FHIR client with server URL: {self.fhir_server_url}")
    
    def check_server_connection(self) -> bool:
        """
        Check if the FHIR server is accessible
        
        Returns:
            True if server is accessible, False otherwise
        """
        try:
            response = requests.get(f"{self.fhir_server_url}/metadata", timeout=5)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Error connecting to FHIR server: {str(e)}")
            return False
    
    def export_pgx_report_to_fhir(
        self,
        patient_info: Dict[str, Any],
        report_id: str,
        diplotypes: List[Dict[str, Any]],
        recommendations: List[Dict[str, Any]],
        report_pdf_url: str,
        report_html_url: str,
        target_fhir_server: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Export pharmacogenomic report to FHIR server as DiagnosticReport
        
        Args:
            patient_info: Patient information
            report_id: Unique report identifier
            diplotypes: List of gene diplotypes
            recommendations: List of drug recommendations
            report_pdf_url: URL to the PDF report
            report_html_url: URL to the HTML report
            target_fhir_server: Optional target FHIR server URL
            
        Returns:
            Response from FHIR server with created resources
        """
        try:
            # Use specified target FHIR server or default
            server_url = target_fhir_server or self.fhir_server_url
            
            # Create Patient resource
            patient_resource = self._create_patient_resource(patient_info)
            patient_response = self._post_resource(server_url, "Patient", patient_resource)
            patient_id = patient_response.get("id", "unknown")
            
            # Create Observation resources for each genotype
            observation_references = []
            for diplotype in diplotypes:
                observation = self._create_observation_resource(
                    patient_id,
                    diplotype["gene"],
                    diplotype["diplotype"],
                    diplotype["phenotype"]
                )
                obs_response = self._post_resource(server_url, "Observation", observation)
                observation_references.append({
                    "reference": f"Observation/{obs_response.get('id', 'unknown')}"
                })
            
            # Create DiagnosticReport resource
            diagnostic_report = self._create_diagnostic_report_resource(
                patient_id,
                report_id,
                observation_references,
                report_pdf_url,
                report_html_url,
                recommendations
            )
            
            report_response = self._post_resource(server_url, "DiagnosticReport", diagnostic_report)
            
            return {
                "status": "success",
                "patient_id": patient_id,
                "report_id": report_response.get("id", "unknown"),
                "fhir_server": server_url,
                "resources_created": 2 + len(observation_references)  # Patient + DiagnosticReport + Observations
            }
            
        except Exception as e:
            logger.error(f"Error exporting PGx report to FHIR: {str(e)}")
            return {
                "status": "error",
                "error": str(e)
            }
    
    def _post_resource(self, server_url: str, resource_type: str, resource: Dict[str, Any]) -> Dict[str, Any]:
        """
        Post a FHIR resource to the server
        
        Args:
            server_url: URL of the FHIR server
            resource_type: Type of FHIR resource
            resource: Resource data
            
        Returns:
            Response from FHIR server
        """
        try:
            headers = {
                "Content-Type": "application/fhir+json",
                "Accept": "application/fhir+json"
            }
            
            response = requests.post(
                f"{server_url}/{resource_type}",
                json=resource,
                headers=headers
            )
            
            if response.status_code >= 400:
                logger.error(f"Error posting {resource_type}: {response.text}")
                raise Exception(f"Error posting {resource_type}: {response.status_code}")
                
            return response.json()
        except Exception as e:
            logger.error(f"Error in _post_resource: {str(e)}")
            raise
    
    def _create_patient_resource(self, patient_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a FHIR Patient resource
        
        Args:
            patient_info: Patient information
            
        Returns:
            FHIR Patient resource
        """
        # Extract patient info or use defaults
        patient_id = patient_info.get("id", "unknown")
        name = patient_info.get("name", {})
        
        return {
            "resourceType": "Patient",
            "identifier": [
                {
                    "system": "urn:zaroPGx:patient-ids",
                    "value": patient_id
                }
            ],
            "name": [
                {
                    "family": name.get("family", ""),
                    "given": name.get("given", [])
                }
            ],
            "gender": patient_info.get("gender", "unknown"),
            "birthDate": patient_info.get("birthDate", ""),
            "meta": {
                "tag": [
                    {
                        "system": "urn:zaroPGx:tags",
                        "code": "pgx-patient"
                    }
                ]
            }
        }
    
    def _create_observation_resource(
        self, 
        patient_id: str, 
        gene: str, 
        diplotype: str, 
        phenotype: str
    ) -> Dict[str, Any]:
        """
        Create a FHIR Observation resource for a gene diplotype
        
        Args:
            patient_id: FHIR Patient resource ID
            gene: Gene symbol
            diplotype: Gene diplotype value
            phenotype: Metabolizer status
            
        Returns:
            FHIR Observation resource
        """
        return {
            "resourceType": "Observation",
            "status": "final",
            "category": [
                {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                            "code": "laboratory",
                            "display": "Laboratory"
                        }
                    ]
                }
            ],
            "code": {
                "coding": [
                    {
                        "system": "http://loinc.org",
                        "code": "83034-8",  # This is a placeholder - actual LOINC codes would vary by gene
                        "display": f"Pharmacogenetic result for {gene}"
                    }
                ],
                "text": f"{gene} Genotype"
            },
            "subject": {
                "reference": f"Patient/{patient_id}"
            },
            "effectiveDateTime": datetime.utcnow().isoformat(),
            "issued": datetime.utcnow().isoformat(),
            "valueString": diplotype,
            "interpretation": [
                {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
                            "code": "POS",  # Placeholder - would map phenotypes to appropriate codes
                            "display": phenotype
                        }
                    ],
                    "text": phenotype
                }
            ],
            "note": [
                {
                    "text": f"Gene: {gene}, Diplotype: {diplotype}, Phenotype: {phenotype}"
                }
            ],
            "meta": {
                "tag": [
                    {
                        "system": "urn:zaroPGx:tags",
                        "code": "pgx-observation"
                    }
                ]
            }
        }
    
    def _create_diagnostic_report_resource(
        self,
        patient_id: str,
        report_id: str,
        observation_references: List[Dict[str, str]],
        pdf_url: str,
        html_url: str,
        recommendations: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Create a FHIR DiagnosticReport resource for the PGx report
        
        Args:
            patient_id: FHIR Patient resource ID
            report_id: Report identifier
            observation_references: List of references to Observation resources
            pdf_url: URL to the PDF report
            html_url: URL to the HTML report
            recommendations: List of drug recommendations
            
        Returns:
            FHIR DiagnosticReport resource
        """
        # Format recommendations as a text block
        recommendation_text = ""
        for rec in recommendations:
            recommendation_text += f"- {rec.get('drug', 'Unknown drug')}: {rec.get('recommendation', 'See report')}\n"
        
        return {
            "resourceType": "DiagnosticReport",
            "identifier": [
                {
                    "system": "urn:zaroPGx:report-ids",
                    "value": report_id
                }
            ],
            "status": "final",
            "category": [
                {
                    "coding": [
                        {
                            "system": "http://terminology.hl7.org/CodeSystem/v2-0074",
                            "code": "GE",
                            "display": "Genetics"
                        }
                    ]
                }
            ],
            "code": {
                "coding": [
                    {
                        "system": "http://loinc.org",
                        "code": "51969-4",  # Genetic analysis report
                        "display": "Pharmacogenomic Analysis Report"
                    }
                ],
                "text": "Pharmacogenomic Analysis Report"
            },
            "subject": {
                "reference": f"Patient/{patient_id}"
            },
            "effectiveDateTime": datetime.utcnow().isoformat(),
            "issued": datetime.utcnow().isoformat(),
            "result": observation_references,
            "presentedForm": [
                {
                    "contentType": "application/pdf",
                    "language": "en-US",
                    "title": f"PGx Report {report_id} (PDF)",
                    "url": pdf_url
                },
                {
                    "contentType": "text/html",
                    "language": "en-US",
                    "title": f"PGx Report {report_id} (HTML)",
                    "url": html_url
                }
            ],
            "conclusion": f"Pharmacogenomic analysis completed. See attached report for detailed recommendations.",
            "conclusionCode": [
                {
                    "coding": [
                        {
                            "system": "urn:zaroPGx:conclusion-codes",
                            "code": "PGX-COMPLETE",
                            "display": "Pharmacogenomic Analysis Complete"
                        }
                    ]
                }
            ],
            "meta": {
                "tag": [
                    {
                        "system": "urn:zaroPGx:tags",
                        "code": "pgx-report"
                    }
                ]
            }
        } 