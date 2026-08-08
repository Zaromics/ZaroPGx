"""
PharmCAT Data Service

This service provides a bridge between the PharmCAT database schema and the report generation system.
It handles data retrieval, transformation, and linking between workflows and PharmCAT results.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from sqlalchemy import and_, desc, or_
from sqlalchemy.orm import Session

from app.api.db import Job
from app.pharmcat.pharmcat_parser import PharmCATParser, get_pharmcat_summary
from app.reports.evidence import classify_evidence
from app.reports.provenance import resolve_called_by, resolve_guideline_source

logger = logging.getLogger(__name__)


class PharmCATDataService:
    """
    Service for retrieving and transforming PharmCAT data from the database
    for use in report generation and API responses.
    """

    def __init__(self, db: Session):
        self.db = db

    def get_pharmcat_data_for_workflow(
        self, workflow_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get PharmCAT data for a specific workflow.

        Args:
            workflow_id: Workflow ID to get PharmCAT data for

        Returns:
            Dict containing normalized PharmCAT data for report generation, or None if not found
        """
        try:
            # Convert workflow_id to UUID format for database query
            workflow_uuid = uuid.UUID(str(workflow_id))

            # Get workflow to find associated PharmCAT run. populate_existing() for
            # the same reason as JobService.get_job: SessionLocal sets
            # expire_on_commit=False, so a plain .first() fetches the row and then
            # discards it in favour of whatever instance this session already has in
            # its identity map. pharmcat_run_id is written by whichever session
            # finished the PharmCAT stage -- never this one -- so without this the
            # report route reads job_metadata as it stood when the session first
            # loaded the job and reports "no PharmCAT data" for a completed run.
            job = (
                self.db.query(Job)
                .filter(Job.id == workflow_uuid)
                .populate_existing()
                .first()
            )
            if not job:
                logger.warning(f"Workflow {workflow_id} not found")
                return None

            # Look for PharmCAT run_id in workflow metadata
            metadata = job.job_metadata or {}
            pharmcat_run_id = metadata.get("pharmcat_run_id")

            logger.info(
                f"Workflow {workflow_id} metadata keys: {list(metadata.keys())}"
            )
            logger.info(f"PharmCAT run_id in metadata: {pharmcat_run_id}")

            if not pharmcat_run_id:
                logger.warning(
                    f"No PharmCAT run_id found in workflow {workflow_id} metadata"
                )
                logger.warning(f"Available metadata keys: {list(metadata.keys())}")
                return None

            # Get PharmCAT data using the parser
            with PharmCATParser(self.db) as parser:
                return self._get_normalized_pharmcat_data(parser, pharmcat_run_id)

        except Exception as e:
            logger.error(f"Error getting PharmCAT data for workflow {workflow_id}: {e}")
            return None

    def get_pharmcat_data_for_patient(
        self, patient_id: str
    ) -> Optional[Dict[str, Any]]:
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
                    if patient_id in str(run.get("run_id", "")) or patient_id in str(
                        run.get("title", "")
                    ):
                        patient_runs.append(run)

                if not patient_runs:
                    logger.warning(f"No PharmCAT runs found for patient {patient_id}")
                    return None

                # Get the most recent run
                latest_run = max(
                    patient_runs, key=lambda x: x.get("created_at", datetime.min)
                )
                run_id = latest_run["run_id"]

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

    def _get_normalized_pharmcat_data(
        self, parser: PharmCATParser, run_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get normalized PharmCAT data using the parser.

        Args:
            parser: PharmCATParser instance
            run_id: PharmCAT run ID

        Returns:
            Dict containing normalized PharmCAT data in the format expected by report generation
        """
        try:
            logger.info(f"Retrieving normalized PharmCAT data for run_id: {run_id}")

            # Get comprehensive summary
            summary = get_pharmcat_summary(run_id, self.db)
            if not summary:
                logger.warning(f"No summary data found for run {run_id}")
                return None

            logger.info(
                f"Summary retrieved: total_genes={summary.get('total_genes')}, actionable={summary.get('actionable_findings_count')}"
            )

            # Get detailed gene data
            genes = parser.get_gene_summary(run_id)
            diplotypes = parser.get_diplotypes(run_id)
            drug_recommendations = parser.get_drug_recommendations(run_id)
            messages = parser.get_messages(run_id)

            logger.info(
                f"Raw data counts - genes: {len(genes)}, diplotypes: {len(diplotypes)}, drug_recs: {len(drug_recommendations)}, messages: {len(messages)}"
            )

            # Transform data to match report generation expectations
            normalized_data = {
                "genes": self._transform_genes_for_reports(genes, diplotypes),
                "drugRecommendations": self._transform_drug_recommendations_for_reports(
                    drug_recommendations
                ),
                "file_type": "vcf",  # Default assumption
                "workflow": {"used_pharmcat": True, "pharmcat_run_id": run_id},
                "pharmcat_version": summary.get("pharmcat_version", "Unknown"),
                "total_genes": summary.get("total_genes", 0),
                "actionable_findings": summary.get("actionable_findings", []),
                "warning_messages": summary.get("warning_messages", []),
                "run_id": run_id,
                "created_at": summary.get("created_at"),
                "sample_identifier": summary.get("sample_identifier"),
            }

            # Add messages if available
            if messages:
                normalized_data["messages"] = messages

            logger.info(
                f"Successfully normalized PharmCAT data for run {run_id}: {len(normalized_data['genes'])} genes, {len(normalized_data['drugRecommendations'])} recommendations"
            )
            return normalized_data

        except Exception as e:
            logger.error(f"Error normalizing PharmCAT data for run {run_id}: {e}")
            return None

    def _transform_genes_for_reports(
        self, genes: List[Dict[str, Any]], diplotypes: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
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
            gene_symbol = diplotype.get("gene_symbol")
            if gene_symbol:
                if gene_symbol not in diplotype_map:
                    diplotype_map[gene_symbol] = []
                diplotype_map[gene_symbol].append(diplotype)

        # Group genes by gene_symbol to handle duplicates
        gene_groups = {}

        for gene in genes:
            gene_symbol = gene.get("gene_symbol")
            if not gene_symbol:
                continue

            # If we haven't seen this gene before, or if this entry has better data
            if gene_symbol not in gene_groups:
                gene_groups[gene_symbol] = gene
            else:
                # Choose the better entry based on guideline-bucket preference
                existing = gene_groups[gene_symbol]
                if self._is_better_gene_entry(gene, existing):
                    gene_groups[gene_symbol] = gene

        # Transform grouped genes to report format
        report_genes = []
        for gene_symbol, gene in gene_groups.items():
            diplotype_data = diplotype_map.get(gene_symbol, [])

            # Find the primary diplotype (usually the first one)
            primary_diplotype = diplotype_data[0] if diplotype_data else {}

            # Report what the run recorded -- never a gene-name guess, never a
            # constant. "?"/"-" are legitimate values (BACKLOG 28 + 216).
            # The gene_summary row carries no diplotype, so hand the resolver
            # the one about to be rendered: that is what separates "not
            # recorded" (?) from "no call made" (-) for rows written before
            # call_source held PharmCAT's callSource.
            provenance = resolve_called_by(
                {
                    **gene,
                    "diplotype": primary_diplotype.get("diplotype_label") or "",
                }
            )
            guideline_source = resolve_guideline_source(gene)

            report_gene = {
                "gene": gene_symbol,
                "diplotype": primary_diplotype.get("diplotype_label", "Unknown"),
                "phenotype": primary_diplotype.get("phenotype", "Unknown"),
                "activity_score": primary_diplotype.get("activity_score"),
                "call_source": gene.get(
                    "call_source", "PharmCAT"
                ),  # Keep for backward compatibility
                "confidence": primary_diplotype.get("match_score"),
                "allele1": primary_diplotype.get("allele1_name"),
                "allele2": primary_diplotype.get("allele2_name"),
                "function1": primary_diplotype.get("allele1_function"),
                "function2": primary_diplotype.get("allele2_function"),
                "inferred": primary_diplotype.get("inferred", False),
                "combination": primary_diplotype.get("combination", False),
            }

            report_gene["called_by"] = provenance.letter
            report_gene["called_by_label"] = provenance.label

            if guideline_source:
                report_gene["guideline_source"] = guideline_source

            # Add all diplotypes for this gene
            if len(diplotype_data) > 1:
                report_gene["all_diplotypes"] = [
                    {
                        "diplotype": d.get("diplotype_label"),
                        "phenotype": d.get("phenotype"),
                        "activity_score": d.get("activity_score"),
                        "allele1": d.get("allele1_name"),
                        "allele2": d.get("allele2_name"),
                    }
                    for d in diplotype_data
                ]

            report_genes.append(report_gene)

        return report_genes

    def _is_better_gene_entry(
        self, candidate: Dict[str, Any], existing: Dict[str, Any]
    ) -> bool:
        """
        Determine if a candidate gene entry is better than the existing one.

        Nested PharmCAT output yields one ``gene_summary`` row per guideline
        bucket (CPIC and DPWG), so this picks between duplicates of the same
        gene. It is a display-precedence tiebreak, not a provenance claim.

        Args:
            candidate: New gene entry to evaluate
            existing: Existing gene entry to compare against

        Returns:
            True if candidate is better, False otherwise
        """
        # The CPIC > DPWG preference is about the guideline bucket, which lives
        # on phenotype_source. call_source now carries MATCHER/OUTSIDE/NONE, so
        # keying on it would compare identical values for the nested duplicate
        # rows this exists to separate (BACKLOG 28 + 216).
        candidate_source = candidate.get("phenotype_source", "")
        existing_source = existing.get("phenotype_source", "")

        if candidate_source and not existing_source:
            return True
        if existing_source and not candidate_source:
            return False

        # If both have sources, prefer CPIC over DPWG over others
        if candidate_source == "CPIC" and existing_source != "CPIC":
            return True
        if existing_source == "CPIC" and candidate_source != "CPIC":
            return False

        if candidate_source == "DPWG" and existing_source not in ["CPIC", "DPWG"]:
            return True
        if existing_source == "DPWG" and candidate_source not in ["CPIC", "DPWG"]:
            return False

        # If sources are equivalent, prefer the one with more complete data
        candidate_completeness = sum(
            1 for v in candidate.values() if v is not None and v != ""
        )
        existing_completeness = sum(
            1 for v in existing.values() if v is not None and v != ""
        )

        return candidate_completeness > existing_completeness

    def _transform_drug_recommendations_for_reports(
        self, drug_recommendations: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
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
            drug_name = rec.get("drug_name", "Unknown")
            gene_symbol = rec.get("gene_symbol", "Unknown")

            if drug_name not in drug_groups:
                drug_groups[drug_name] = {
                    "drug": drug_name,
                    "genes": [],
                    "recommendations": [],
                    "pharmgkb_id": rec.get("drug_id"),
                }

            # Add gene to genes list if not already present
            if gene_symbol not in drug_groups[drug_name]["genes"]:
                drug_groups[drug_name]["genes"].append(gene_symbol)

            # Get recommendation text - prefer drugRecommendation over recommendation_text
            recommendation_text = rec.get("drug_recommendation") or rec.get(
                "recommendation_text", ""
            )
            guideline_source = self._determine_guideline_source_letter(
                rec.get("guideline_source")
            )

            # Format citations if available
            citations = rec.get("citations", [])
            literature_references = []
            if citations:
                if isinstance(citations, list):
                    literature_references = [str(c) for c in citations]
                else:
                    literature_references = [str(citations)]

            # Create individual recommendation entry
            # Use strength_of_evidence (CPIC levels A/B/C) if available, fallback to classification
            evidence_level = rec.get("strength_of_evidence") or rec.get(
                "classification", "Unknown"
            )

            tier = classify_evidence(evidence_level)

            recommendation_entry = {
                "gene": gene_symbol,
                "recommendation": (
                    recommendation_text
                    if recommendation_text
                    else "See report for details"
                ),
                "classification": evidence_level,  # This is what the templates check
                "evidence_rank": tier.rank,
                "evidence_class": tier.css_class,
                "strength_of_evidence": rec.get(
                    "strength_of_evidence"
                ),  # Keep original for reference
                "implications": rec.get("implications"),
                "literature_references": literature_references,
            }

            if guideline_source:
                recommendation_entry["guideline_source"] = guideline_source

            # Clean up empty values
            recommendation_entry = {
                k: v
                for k, v in recommendation_entry.items()
                if v is not None and v != ""
            }

            drug_groups[drug_name]["recommendations"].append(recommendation_entry)

        # Convert grouped data to list format
        report_recommendations = []
        for drug_name, drug_data in drug_groups.items():
            # Clean up empty values from the main drug entry
            clean_drug_data = {
                k: v for k, v in drug_data.items() if v is not None and v != ""
            }
            report_recommendations.append(clean_drug_data)

        return report_recommendations

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

        if guideline_source == "FDA":
            return "F"
        elif guideline_source == "DPWG":
            return "D"
        elif guideline_source == "CPIC":
            return "C"
        elif guideline_source == "PHARMGKB":
            return "P"
        else:
            # Default mapping for PharmCAT sources
            if guideline_source in ["CPIC", "DPWG"]:
                return guideline_source[0]  # C for CPIC, D for DPWG
            return ""

    def link_pharmcat_run_to_workflow(
        self, workflow_id: str, pharmcat_run_id: str
    ) -> bool:
        """
        Link a PharmCAT run to a workflow by storing the run_id in workflow metadata.

        Args:
            workflow_id: Workflow ID
            pharmcat_run_id: PharmCAT run ID

        Returns:
            True if successful, False otherwise
        """
        try:
            workflow_uuid = uuid.UUID(str(workflow_id))
            # populate_existing(): this is a read-modify-write of job_metadata, so it
            # has to start from the row as it stands now. Reading a stale copy and
            # writing the merged dict back would drop every key another session has
            # added since -- including the "cancelled" flag the cancel endpoint
            # writes -- because the whole dict is replaced, not patched.
            job = (
                self.db.query(Job)
                .filter(Job.id == workflow_uuid)
                .populate_existing()
                .first()
            )
            if not job:
                logger.error(f"Workflow {workflow_id} not found")
                return False

            # Update workflow metadata.
            # dict() is load-bearing: jobs.job_metadata is a plain Column(JSON) with
            # no MutableDict, so mutating the attached dict in place and assigning the
            # *same object* back leaves the attribute history empty -- SQLAlchemy
            # emits no UPDATE, commit() succeeds, this method returns True, and
            # nothing persists. expire_on_commit=False then hides it: the caller's own
            # session keeps showing the in-memory mutation, so the loss only surfaces
            # on the next request. create_job always seeds job_metadata, so this is
            # never the accidentally-safe empty-dict case. Same pattern as
            # JobService.link_pharmcat_run and job_router.py.
            metadata = dict(job.job_metadata or {})
            metadata["pharmcat_run_id"] = pharmcat_run_id
            metadata["pharmcat_linked_at"] = datetime.now(timezone.utc).isoformat()

            job.job_metadata = metadata
            self.db.commit()

            logger.info(
                f"Successfully linked PharmCAT run {pharmcat_run_id} to workflow {workflow_id}"
            )
            return True

        except Exception as e:
            logger.error(f"Error linking PharmCAT run to workflow: {e}")
            self.db.rollback()
            return False

    def get_workflow_pharmcat_summary(
        self, workflow_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get a summary of PharmCAT data for a workflow.

        Args:
            workflow_id: Workflow ID

        Returns:
            Dict containing PharmCAT summary data, or None if not found
        """
        try:
            workflow_uuid = uuid.UUID(str(workflow_id))
            # populate_existing(): the link is written by whichever session finished
            # the PharmCAT stage, which is not the session asking here. Same reason as
            # get_pharmcat_data_for_workflow.
            job = (
                self.db.query(Job)
                .filter(Job.id == workflow_uuid)
                .populate_existing()
                .first()
            )
            if not job:
                return None

            metadata = job.job_metadata or {}
            pharmcat_run_id = metadata.get("pharmcat_run_id")

            if not pharmcat_run_id:
                return None

            # Pass the request session: without it PharmCATParser opens a second
            # engine/connection per request, outside the request transaction,
            # and commits+closes it (matches _get_normalized_pharmcat_data).
            return get_pharmcat_summary(pharmcat_run_id, self.db)

        except Exception as e:
            logger.error(
                f"Error getting PharmCAT summary for workflow {workflow_id}: {e}"
            )
            return None
