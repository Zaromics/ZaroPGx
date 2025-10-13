"""
PharmCAT Data Service

This service provides a bridge between the PharmCAT database schema and the report generation system.
It handles data retrieval, transformation, and linking between workflows and PharmCAT results.
"""

import logging
from typing import Dict, List, Any, Optional, Union
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, desc
from datetime import datetime, timezone
import uuid

from app.pharmcat.pharmcat_parser import PharmCATParser, get_pharmcat_summary
from app.api.db import Workflow

logger = logging.getLogger(__name__)


class PharmCATDataService:
    """
    Service for retrieving and transforming PharmCAT data from the database
    for use in report generation and API responses.
    """
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_pharmcat_data_for_workflow(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """
        Get PharmCAT data for a specific workflow.
        
        Args:
            workflow_id: Workflow ID to get PharmCAT data for
            
        Returns:
            Dict containing normalized PharmCAT data for report generation, or None if not found
        """
        try:
            # Convert workflow_id to UUID format for database query
            import uuid
            workflow_uuid = uuid.UUID(str(workflow_id))
            
            # Get workflow to find associated PharmCAT run
            workflow = self.db.query(Workflow).filter(Workflow.id == workflow_uuid).first()
            if not workflow:
                logger.warning(f"Workflow {workflow_id} not found")
                return None
            
            # Look for PharmCAT run_id in workflow metadata
            metadata = workflow.workflow_metadata or {}
            pharmcat_run_id = metadata.get('pharmcat_run_id')
            
            logger.info(f"Workflow {workflow_id} metadata keys: {list(metadata.keys())}")
            logger.info(f"PharmCAT run_id in metadata: {pharmcat_run_id}")
            
            if not pharmcat_run_id:
                logger.warning(f"No PharmCAT run_id found in workflow {workflow_id} metadata")
                logger.warning(f"Available metadata keys: {list(metadata.keys())}")
                return None
            
            # Get PharmCAT data using the parser
            with PharmCATParser(self.db) as parser:
                return self._get_normalized_pharmcat_data(parser, pharmcat_run_id)
                
        except Exception as e:
            logger.error(f"Error getting PharmCAT data for workflow {workflow_id}: {e}")
            return None
    
    def get_pharmcat_data_for_patient(self, patient_id: str) -> Optional[Dict[str, Any]]:
        """
        Get PharmCAT data for a specific patient.
        
        Args:
            patient_id: Patient ID to get PharmCAT data for
            
        Returns:
            Dict containing normalized PharmCAT data for report generation, or None if not found
        """
        try:
            # Find the most recent PharmCAT run for this patient
            with PharmCATParser(self.db) as parser:
                # Get all runs for this patient (assuming patient_id is stored in run_id or metadata)
                runs = parser.get_all_runs()
                
                # Filter runs by patient_id (this might need adjustment based on how patient_id is stored)
                patient_runs = []
                for run in runs:
                    if patient_id in str(run.get('run_id', '')) or patient_id in str(run.get('title', '')):
                        patient_runs.append(run)
                
                if not patient_runs:
                    logger.warning(f"No PharmCAT runs found for patient {patient_id}")
                    return None
                
                # Get the most recent run
                latest_run = max(patient_runs, key=lambda x: x.get('created_at', datetime.min))
                run_id = latest_run['run_id']
                
                return self._get_normalized_pharmcat_data(parser, run_id)
                
        except Exception as e:
            logger.error(f"Error getting PharmCAT data for patient {patient_id}: {e}")
            return None
    
    def get_pharmcat_data_by_run_id(self, run_id: str) -> Optional[Dict[str, Any]]:
        """
        Get PharmCAT data for a specific run ID.
        
        Args:
            run_id: PharmCAT run ID to get data for
            
        Returns:
            Dict containing normalized PharmCAT data for report generation, or None if not found
        """
        try:
            with PharmCATParser(self.db) as parser:
                return self._get_normalized_pharmcat_data(parser, run_id)
        except Exception as e:
            logger.error(f"Error getting PharmCAT data for run {run_id}: {e}")
            return None
    
    def _get_normalized_pharmcat_data(self, parser: PharmCATParser, run_id: str) -> Optional[Dict[str, Any]]:
        """
        Get normalized PharmCAT data using the parser.
        
        Args:
            parser: PharmCATParser instance
            run_id: PharmCAT run ID
            
        Returns:
            Dict containing normalized PharmCAT data in the format expected by report generation
        """
        try:
            # Get comprehensive summary
            summary = get_pharmcat_summary(run_id, self.db)
            if not summary:
                logger.warning(f"No summary data found for run {run_id}")
                return None
            
            # Get detailed gene data
            genes = parser.get_gene_summary(run_id)
            diplotypes = parser.get_diplotypes(run_id)
            drug_recommendations = parser.get_drug_recommendations(run_id)
            messages = parser.get_messages(run_id)
            
            # Transform data to match report generation expectations
            normalized_data = {
                "genes": self._transform_genes_for_reports(genes, diplotypes),
                "drugRecommendations": self._transform_drug_recommendations_for_reports(drug_recommendations),
                "file_type": "vcf",  # Default assumption
                "workflow": {
                    "used_pharmcat": True,
                    "pharmcat_run_id": run_id
                },
                "pharmcat_version": summary.get('pharmcat_version', 'Unknown'),
                "total_genes": summary.get('total_genes', 0),
                "actionable_findings": summary.get('actionable_findings', []),
                "warning_messages": summary.get('warning_messages', []),
                "run_id": run_id,
                "created_at": summary.get('created_at'),
                "sample_identifier": summary.get('sample_identifier')
            }
            
            # Add messages if available
            if messages:
                normalized_data["messages"] = messages
            
            logger.info(f"Successfully normalized PharmCAT data for run {run_id}: {len(normalized_data['genes'])} genes, {len(normalized_data['drugRecommendations'])} recommendations")
            return normalized_data
            
        except Exception as e:
            logger.error(f"Error normalizing PharmCAT data for run {run_id}: {e}")
            return None
    
    def _transform_genes_for_reports(self, genes: List[Dict[str, Any]], diplotypes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Transform gene data from database format to report format.
        Ensures only one entry per gene symbol to avoid duplicates.
        
        Args:
            genes: List of gene summary data
            diplotypes: List of diplotype data
            
        Returns:
            List of gene data in report format, deduplicated by gene symbol
        """
        # Create a mapping of gene_symbol to diplotype data
        diplotype_map = {}
        for diplotype in diplotypes:
            gene_symbol = diplotype.get('gene_symbol')
            if gene_symbol:
                if gene_symbol not in diplotype_map:
                    diplotype_map[gene_symbol] = []
                diplotype_map[gene_symbol].append(diplotype)
        
        # Group genes by gene_symbol to handle duplicates
        gene_groups = {}
        
        for gene in genes:
            gene_symbol = gene.get('gene_symbol')
            if not gene_symbol:
                continue
                
            # If we haven't seen this gene before, or if this entry has better data
            if gene_symbol not in gene_groups:
                gene_groups[gene_symbol] = gene
            else:
                # Choose the better entry based on call_source preference
                existing = gene_groups[gene_symbol]
                if self._is_better_gene_entry(gene, existing):
                    gene_groups[gene_symbol] = gene
        
        # Transform grouped genes to report format
        report_genes = []
        for gene_symbol, gene in gene_groups.items():
            diplotype_data = diplotype_map.get(gene_symbol, [])
            
            # Find the primary diplotype (usually the first one)
            primary_diplotype = diplotype_data[0] if diplotype_data else {}
            
            # Only set source fields if PharmCAT actually processed this gene
            called_by = self._determine_called_by_letter(gene.get('call_source'))
            guideline_source = self._determine_guideline_source_letter(gene.get('call_source'))
            
            report_gene = {
                "gene": gene_symbol,
                "diplotype": primary_diplotype.get('diplotype_label', 'Unknown'),
                "phenotype": primary_diplotype.get('phenotype', 'Unknown'),
                "activity_score": primary_diplotype.get('activity_score'),
                "call_source": gene.get('call_source', 'PharmCAT'),  # Keep for backward compatibility
                "confidence": primary_diplotype.get('match_score'),
                "allele1": primary_diplotype.get('allele1_name'),
                "allele2": primary_diplotype.get('allele2_name'),
                "function1": primary_diplotype.get('allele1_function'),
                "function2": primary_diplotype.get('allele2_function'),
                "inferred": primary_diplotype.get('inferred', False),
                "combination": primary_diplotype.get('combination', False)
            }
            
            # Only add source fields if PharmCAT actually called this gene
            if called_by:
                report_gene["called_by"] = called_by
                report_gene["report_data_from"] = "C"  # C for PharmCAT
            
            if guideline_source:
                report_gene["guideline_source"] = guideline_source
            
            # Add all diplotypes for this gene
            if len(diplotype_data) > 1:
                report_gene["all_diplotypes"] = [
                    {
                        "diplotype": d.get('diplotype_label'),
                        "phenotype": d.get('phenotype'),
                        "activity_score": d.get('activity_score'),
                        "allele1": d.get('allele1_name'),
                        "allele2": d.get('allele2_name')
                    }
                    for d in diplotype_data
                ]
            
            report_genes.append(report_gene)
        
        return report_genes
    
    def _is_better_gene_entry(self, candidate: Dict[str, Any], existing: Dict[str, Any]) -> bool:
        """
        Determine if a candidate gene entry is better than the existing one.
        
        Args:
            candidate: New gene entry to evaluate
            existing: Existing gene entry to compare against
            
        Returns:
            True if candidate is better, False otherwise
        """
        # Prefer entries with actual call_source over None/empty
        candidate_source = candidate.get('call_source', '')
        existing_source = existing.get('call_source', '')
        
        if candidate_source and not existing_source:
            return True
        if existing_source and not candidate_source:
            return False
        
        # If both have sources, prefer CPIC over DPWG over others
        if candidate_source == 'CPIC' and existing_source != 'CPIC':
            return True
        if existing_source == 'CPIC' and candidate_source != 'CPIC':
            return False
        
        if candidate_source == 'DPWG' and existing_source not in ['CPIC', 'DPWG']:
            return True
        if existing_source == 'DPWG' and candidate_source not in ['CPIC', 'DPWG']:
            return False
        
        # If sources are equivalent, prefer the one with more complete data
        candidate_completeness = sum(1 for v in candidate.values() if v is not None and v != '')
        existing_completeness = sum(1 for v in existing.values() if v is not None and v != '')
        
        return candidate_completeness > existing_completeness
    
    def _transform_drug_recommendations_for_reports(self, drug_recommendations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Transform drug recommendation data from database format to report format.
        Groups recommendations by drug name to avoid duplicates.
        
        Args:
            drug_recommendations: List of drug recommendation data
            
        Returns:
            List of drug recommendation data in report format, grouped by drug
        """
        # Group recommendations by drug name
        drug_groups = {}
        
        for rec in drug_recommendations:
            drug_name = rec.get('drug_name', 'Unknown')
            gene_symbol = rec.get('gene_symbol', 'Unknown')
            
            if drug_name not in drug_groups:
                drug_groups[drug_name] = {
                    "drug": drug_name,
                    "genes": [],
                    "recommendations": [],
                    "pharmgkb_id": rec.get('drug_id'),
                    "called_by": "C",  # C for PharmCAT
                    "report_data_from": "C"  # C for PharmCAT
                }
            
            # Add gene to genes list if not already present
            if gene_symbol not in drug_groups[drug_name]["genes"]:
                drug_groups[drug_name]["genes"].append(gene_symbol)
            
            # Get recommendation text - prefer drugRecommendation over recommendation_text
            recommendation_text = rec.get('drug_recommendation') or rec.get('recommendation_text', '')
            guideline_source = self._determine_guideline_source_letter(rec.get('guideline_source'))
            
            # Format citations if available
            citations = rec.get('citations', [])
            literature_references = []
            if citations:
                if isinstance(citations, list):
                    literature_references = [str(c) for c in citations]
                else:
                    literature_references = [str(citations)]
            
            # Create individual recommendation entry
            # Use strength_of_evidence (CPIC levels A/B/C) if available, fallback to classification
            evidence_level = rec.get('strength_of_evidence') or rec.get('classification', 'Unknown')
            
            # DEBUG: Log what we're receiving
            logger.info(f"DEBUG - Drug: {drug_name}, Gene: {gene_symbol}")
            logger.info(f"  strength_of_evidence: '{rec.get('strength_of_evidence')}'")
            logger.info(f"  classification: '{rec.get('classification')}'")
            logger.info(f"  evidence_level (final): '{evidence_level}'")
            
            recommendation_entry = {
                "gene": gene_symbol,
                "recommendation": recommendation_text if recommendation_text else 'See report for details',
                "classification": evidence_level,  # This is what the templates check
                "strength_of_evidence": rec.get('strength_of_evidence'),  # Keep original for reference
                "implications": rec.get('implications'),
                "literature_references": literature_references,
                "cpic_level": rec.get('strength_of_evidence') if rec.get('guideline_source') == 'CPIC' else None,
                "dpwg_level": rec.get('strength_of_evidence') if rec.get('guideline_source') == 'DPWG' else None,
                "fda_level": rec.get('strength_of_evidence') if rec.get('guideline_source') == 'FDA' else None,
            }
            
            if guideline_source:
                recommendation_entry["guideline_source"] = guideline_source
            
            # Clean up empty values
            recommendation_entry = {k: v for k, v in recommendation_entry.items() if v is not None and v != ''}
            
            drug_groups[drug_name]["recommendations"].append(recommendation_entry)
        
        # Convert grouped data to list format
        report_recommendations = []
        for drug_name, drug_data in drug_groups.items():
            # Clean up empty values from the main drug entry
            clean_drug_data = {k: v for k, v in drug_data.items() if v is not None and v != ''}
            report_recommendations.append(clean_drug_data)
        
        return report_recommendations
    
    def _determine_called_by_letter(self, call_source: str) -> str:
        """
        Determine which tool actually made the genetic call based on call_source.
        
        Args:
            call_source: The call_source from PharmCAT (CPIC, DPWG, OUTSIDE, NONE, etc.)
            
        Returns:
            Single letter code: P (PyPGx), C (PharmCAT), O (OptiType), M (mtDNA-server-2), G (GATK)
        """
        if not call_source:
            return ""
        
        call_source = call_source.upper()
        
        # PharmCAT guideline sources (CPIC, DPWG) - these are called by PharmCAT
        if call_source in ['CPIC', 'DPWG', 'PHARMCAT']:
            return "C"
        
        # Legacy values that indicate PharmCAT called these genes
        elif call_source in ['OUTSIDE', 'NONE']:
            return "C"
        
        # If it's something else, it might be from an external tool
        else:
            return "C"  # Default to PharmCAT for PharmCAT data
    
    def _determine_guideline_source_letter(self, guideline_source: str) -> str:
        """
        Determine guideline source letter code.
        
        Args:
            guideline_source: The guideline source (CPIC, DPWG, FDA, etc.)
            
        Returns:
            Single letter code: F (FDA), D (DPWG), C (CPIC), P (PharmGKB)
        """
        if not guideline_source:
            return ""
        
        guideline_source = guideline_source.upper()
        
        if guideline_source == 'FDA':
            return "F"
        elif guideline_source == 'DPWG':
            return "D"
        elif guideline_source == 'CPIC':
            return "C"
        elif guideline_source == 'PHARMGKB':
            return "P"
        else:
            # Default mapping for PharmCAT sources
            if guideline_source in ['CPIC', 'DPWG']:
                return guideline_source[0]  # C for CPIC, D for DPWG
            return ""
    
    def link_pharmcat_run_to_workflow(self, workflow_id: str, pharmcat_run_id: str) -> bool:
        """
        Link a PharmCAT run to a workflow by storing the run_id in workflow metadata.
        
        Args:
            workflow_id: Workflow ID
            pharmcat_run_id: PharmCAT run ID
            
        Returns:
            True if successful, False otherwise
        """
        try:
            workflow = self.db.query(Workflow).filter(Workflow.id == workflow_id).first()
            if not workflow:
                logger.error(f"Workflow {workflow_id} not found")
                return False
            
            # Update workflow metadata
            metadata = workflow.workflow_metadata or {}
            metadata['pharmcat_run_id'] = pharmcat_run_id
            metadata['pharmcat_linked_at'] = datetime.now(timezone.utc).isoformat()
            
            workflow.workflow_metadata = metadata
            self.db.commit()
            
            logger.info(f"Successfully linked PharmCAT run {pharmcat_run_id} to workflow {workflow_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error linking PharmCAT run to workflow: {e}")
            self.db.rollback()
            return False
    
    def get_workflow_pharmcat_summary(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a summary of PharmCAT data for a workflow.
        
        Args:
            workflow_id: Workflow ID
            
        Returns:
            Dict containing PharmCAT summary data, or None if not found
        """
        try:
            workflow = self.db.query(Workflow).filter(Workflow.id == workflow_id).first()
            if not workflow:
                return None
            
            metadata = workflow.workflow_metadata or {}
            pharmcat_run_id = metadata.get('pharmcat_run_id')
            
            if not pharmcat_run_id:
                return None
            
            return get_pharmcat_summary(pharmcat_run_id)
            
        except Exception as e:
            logger.error(f"Error getting PharmCAT summary for workflow {workflow_id}: {e}")
            return None
