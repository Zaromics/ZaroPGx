#!/usr/bin/env python3
"""
PharmCAT JSON Parser and Database Loader for ZaroPGx
Handles parsing and loading PharmCAT output into PostgreSQL using SQLAlchemy 2 and psycopg3
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Union
from datetime import datetime, timezone
import uuid

# SQLAlchemy 2.0 imports
from sqlalchemy import create_engine, text, String, Integer, DateTime, Text, JSON, Boolean, Column, DECIMAL, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError

# psycopg3 for modern PostgreSQL connections
import psycopg

# ZaroPGx imports
from app.api.db import get_db, DATABASE_URL

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create base class for declarative models
Base = declarative_base()

# ============================================================================
# SQLAlchemy Models for PharmCAT Data
# ============================================================================

class PharmCATResult(Base):
    """SQLAlchemy model for pharmcat.results table"""
    __tablename__ = "results"
    __table_args__ = {"schema": "pharmcat"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(String(255), unique=True, nullable=False)
    run_timestamp = Column(DateTime(timezone=True))
    pharmcat_version = Column(String(50))
    data_version = Column(String(50))
    genome_build = Column(String(20))
    raw_data = Column(JSONB, nullable=False)
    loaded_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PharmCATGeneSummary(Base):
    """SQLAlchemy model for pharmcat.gene_summary table"""
    __tablename__ = "gene_summary"
    __table_args__ = {"schema": "pharmcat"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(String(255), ForeignKey("pharmcat.results.run_id", ondelete="CASCADE"), nullable=False)
    gene_symbol = Column(String(20), nullable=False)
    call_source = Column(String(50))
    phenotype_source = Column(String(50))
    phenotype_version = Column(String(50))
    allele_definition_version = Column(String(50))
    allele_definition_source = Column(String(50))
    chromosome = Column(String(10))
    phased = Column(Boolean)
    effectively_phased = Column(Boolean)
    gene_full_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PharmCATDiplotype(Base):
    """SQLAlchemy model for pharmcat.diplotypes table"""
    __tablename__ = "diplotypes"
    __table_args__ = {"schema": "pharmcat"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(String(255), ForeignKey("pharmcat.results.run_id", ondelete="CASCADE"), nullable=False)
    gene_symbol = Column(String(20), nullable=False)
    diplotype_label = Column(String(255))
    allele1_name = Column(String(100))
    allele1_function = Column(String(100))
    allele2_name = Column(String(100))
    allele2_function = Column(String(100))
    activity_score = Column(DECIMAL(10, 4))
    phenotype = Column(String(255))
    match_score = Column(Integer)
    outside_phenotype = Column(Boolean)
    outside_activity_score = Column(Boolean)
    inferred = Column(Boolean)
    combination = Column(Boolean)
    phenotype_data_source = Column(String(50))
    diplotype_key = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PharmCATDrugGeneMap(Base):
    """SQLAlchemy model for pharmcat.drug_gene_map table"""
    __tablename__ = "drug_gene_map"
    __table_args__ = {"schema": "pharmcat"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(String(255), ForeignKey("pharmcat.results.run_id", ondelete="CASCADE"), nullable=False)
    gene_symbol = Column(String(20), nullable=False)
    drug_name = Column(String(255), nullable=False)
    drug_id = Column(String(100))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PharmCATMessage(Base):
    """SQLAlchemy model for pharmcat.messages table"""
    __tablename__ = "messages"
    __table_args__ = {"schema": "pharmcat"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(String(255), ForeignKey("pharmcat.results.run_id", ondelete="CASCADE"), nullable=False)
    gene_symbol = Column(String(20))
    rule_name = Column(String(100))
    version = Column(String(20))
    exception_type = Column(String(50))
    message = Column(Text)
    matches = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PharmCATVariant(Base):
    """SQLAlchemy model for pharmcat.variants table"""
    __tablename__ = "variants"
    __table_args__ = {"schema": "pharmcat"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(String(255), ForeignKey("pharmcat.results.run_id", ondelete="CASCADE"), nullable=False)
    gene_symbol = Column(String(20), nullable=False)
    chromosome = Column(String(10))
    position = Column(Integer)
    reference_allele = Column(String(60))  # Increased from 10 to accommodate complex indels (full data in variant_data JSONB)
    alternate_allele = Column(String(60))  # Increased from 10 to accommodate complex indels (full data in variant_data JSONB)
    genotype_call = Column(String(100))    # Increased from 20 to accommodate complex indels (full data in variant_data JSONB)
    dbsnp_id = Column(String(30))          # Increased from 20 for future dbSNP ID growth
    variant_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PharmCATDrugRecommendation(Base):
    """SQLAlchemy model for pharmcat.drug_recommendations table"""
    __tablename__ = "drug_recommendations"
    __table_args__ = {"schema": "pharmcat"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(String(255), ForeignKey("pharmcat.results.run_id", ondelete="CASCADE"), nullable=False)
    drug_name = Column(String(255), nullable=False)
    drug_id = Column(String(100))
    gene_symbol = Column(String(20))
    guideline_source = Column(String(50))
    guideline_id = Column(String(100))
    guideline_name = Column(String(255))
    guideline_url = Column(Text)
    recommendation_text = Column(Text)
    classification = Column(String(50))
    strength_of_evidence = Column(String(50))
    population = Column(Text)
    implications = Column(Text)
    drug_recommendation = Column(Text)
    citations = Column(JSONB)
    urls = Column(JSONB)
    recommendation_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PharmCATRecommendationCondition(Base):
    """SQLAlchemy model for pharmcat.recommendation_conditions table"""
    __tablename__ = "recommendation_conditions"
    __table_args__ = {"schema": "pharmcat"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recommendation_id = Column(UUID(as_uuid=True), ForeignKey("pharmcat.drug_recommendations.id", ondelete="CASCADE"), nullable=False)
    gene_symbol = Column(String(20), nullable=False)
    phenotype = Column(String(255), nullable=False)
    condition_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PharmCATUnannotatedGeneCall(Base):
    """SQLAlchemy model for pharmcat.unannotated_gene_calls table"""
    __tablename__ = "unannotated_gene_calls"
    __table_args__ = {"schema": "pharmcat"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(String(255), ForeignKey("pharmcat.results.run_id", ondelete="CASCADE"), nullable=False)
    gene_symbol = Column(String(20), nullable=False)
    allele_definition_version = Column(String(50))
    allele_definition_source = Column(String(50))
    phenotype_version = Column(String(50))
    phenotype_source = Column(String(50))
    chromosome = Column(String(10))
    phased = Column(Boolean)
    effectively_phased = Column(Boolean)
    call_source = Column(String(50))
    uncalled_haplotypes = Column(JSONB)
    messages = Column(JSONB)
    related_drugs = Column(JSONB)
    source_diplotypes = Column(JSONB)
    variants = Column(JSONB)
    variants_of_interest = Column(JSONB)
    has_undocumented_variations = Column(Boolean)
    treat_undocumented_variations_as_reference = Column(Boolean)
    gene_call_data = Column(JSONB)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ============================================================================
# PharmCAT Parser Class
# ============================================================================

class PharmCATParser:
    """Parse and load PharmCAT JSON results into PostgreSQL using SQLAlchemy 2"""
    
    def __init__(self, db_session: Optional[Session] = None):
        """
        Initialize with database session
        
        Args:
            db_session: SQLAlchemy session. If None, will create a new one.
        """
        self.db_session = db_session
        self._session_created = False
        
        if self.db_session is None:
            # Create engine and session
            self.engine = create_engine(DATABASE_URL, echo=False)
            SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
            self.db_session = SessionLocal()
            self._session_created = True
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._session_created and self.db_session:
            if exc_type:
                self.db_session.rollback()
            else:
                self.db_session.commit()
            self.db_session.close()
    
    def load_json_file(self, filepath: Union[str, Path]) -> Dict[str, Any]:
        """Load PharmCAT JSON from file"""
        filepath = Path(filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"PharmCAT file not found: {filepath}")
        
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def parse_and_load(self, data: Dict[str, Any]) -> str:
        """
        Parse PharmCAT JSON data and load into database
        
        Args:
            data: PharmCAT JSON data dictionary
            
        Returns:
            run_id: The unique identifier for this PharmCAT run
        """
        try:
            # Extract basic metadata
            run_id = data.get('title', str(uuid.uuid4()))
            run_timestamp = self._parse_timestamp(data.get('timestamp'))
            pharmcat_version = data.get('pharmcatVersion')
            data_version = data.get('dataVersion')
            genome_build = data.get('genomeBuild')
            
            # Check if this run already exists
            existing_result = self.db_session.query(PharmCATResult).filter(
                PharmCATResult.run_id == run_id
            ).first()
            
            if existing_result:
                logger.info(f"PharmCAT run {run_id} already exists, updating...")
                existing_result.raw_data = data
                existing_result.loaded_at = datetime.now(timezone.utc)
                self.db_session.commit()
                return run_id
            
            # Create main result record
            result = PharmCATResult(
                run_id=run_id,
                run_timestamp=run_timestamp,
                pharmcat_version=pharmcat_version,
                data_version=data_version,
                genome_build=genome_build,
                raw_data=data
            )
            self.db_session.add(result)
            self.db_session.flush()  # Get the ID
            
            # Parse and load gene data
            self._parse_genes(data.get('genes', {}), run_id)
            
            # Parse and load drug data
            self._parse_drugs(data.get('drugs', {}), run_id)
            
            # Parse and load unannotated gene calls
            self._parse_unannotated_gene_calls(data.get('unannotatedGeneCalls', []), run_id)
            
            # Parse and load matcher metadata
            self._parse_matcher_metadata(data.get('matcherMetadata', {}), run_id)
            
            self.db_session.commit()
            logger.info(f"Successfully loaded PharmCAT run {run_id}")
            return run_id
            
        except Exception as e:
            self.db_session.rollback()
            error_msg = str(e)
            logger.error(f"Error parsing PharmCAT data: {error_msg}")
            logger.error(f"Error type: {type(e).__name__}")
            
            # Check for specific database constraint errors
            if "value too long" in error_msg.lower() or "varchar" in error_msg.lower():
                logger.error("=" * 80)
                logger.error("DATABASE SCHEMA ISSUE DETECTED!")
                logger.error("=" * 80)
            
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            raise
    
    def _parse_timestamp(self, timestamp_str: Optional[str]) -> Optional[datetime]:
        """Parse timestamp string to datetime object"""
        if not timestamp_str:
            return None
        
        try:
            # Handle ISO format timestamps
            if 'T' in timestamp_str:
                return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            else:
                return datetime.fromisoformat(timestamp_str)
        except ValueError:
            logger.warning(f"Could not parse timestamp: {timestamp_str}")
            return None
    
    def _parse_genes(self, genes_data: Dict[str, Any], run_id: str) -> None:
        """Parse genes data and load into database.
        
        Handles two PharmCAT JSON formats:
        1. Nested: genes -> source (CPIC/DPWG) -> gene_symbol -> gene_data
        2. Flat: genes -> gene_symbol -> gene_data (gene_data contains 'geneSymbol' field)
        """
        gene_count = 0
        diplotype_count = 0
        
        logger.info(f"Parsing genes data for run {run_id}. Top-level keys: {list(genes_data.keys()) if genes_data else 'None'}")
        
        # Detect format: check if first value looks like gene data (has 'geneSymbol' or 'sourceDiplotypes')
        # or if it's a nested structure (contains gene symbols as keys)
        is_flat_format = False
        if genes_data:
            first_key = next(iter(genes_data.keys()))
            first_value = genes_data.get(first_key, {})
            if isinstance(first_value, dict):
                # If it has gene-specific fields, it's flat format
                if 'geneSymbol' in first_value or 'sourceDiplotypes' in first_value or 'alleleDefinitionVersion' in first_value:
                    is_flat_format = True
                    logger.info(f"Detected FLAT PharmCAT format (genes -> gene_symbol -> gene_data)")
                else:
                    logger.info(f"Detected NESTED PharmCAT format (genes -> source -> gene_symbol -> gene_data)")
        
        if is_flat_format:
            # Flat format: genes -> gene_symbol -> gene_data
            for gene_symbol, gene_data in genes_data.items():
                if not isinstance(gene_data, dict):
                    logger.warning(
                        "Skipping gene '%s' with unexpected type %s",
                        gene_symbol,
                        type(gene_data).__name__,
                    )
                    continue
                
                # Determine source from gene_data if available
                source = gene_data.get('phenotypeSource', 'CPIC')
                
                gene_count += 1
                self._store_gene_data(gene_symbol, gene_data, source, run_id)
                
                # Count diplotypes
                source_diplotypes = gene_data.get('sourceDiplotypes', [])
                diplotype_count += len(source_diplotypes)
                self._parse_diplotypes(source_diplotypes, run_id, gene_symbol)
                self._parse_related_drugs(gene_data.get('relatedDrugs', []), run_id, gene_symbol)
                self._parse_messages(gene_data.get('messages', []), run_id, gene_symbol)
                self._parse_variants(gene_data.get('variants', []), run_id, gene_symbol)
        else:
            # Nested format: genes -> source -> gene_symbol -> gene_data
            for source, genes in genes_data.items():
                if not isinstance(genes, dict):
                    logger.warning(
                        "Skipping genes block for source '%s' with unexpected type %s",
                        source,
                        type(genes).__name__,
                    )
                    continue
                
                logger.info(f"Processing source '{source}' with {len(genes)} genes")

                for gene_symbol, gene_data in genes.items():
                    if not isinstance(gene_data, dict):
                        logger.warning(
                            "Skipping gene '%s' in source '%s' with unexpected type %s",
                            gene_symbol,
                            source,
                            type(gene_data).__name__,
                        )
                        continue

                    gene_count += 1
                    self._store_gene_data(gene_symbol, gene_data, source, run_id)
                    
                    # Count diplotypes
                    source_diplotypes = gene_data.get('sourceDiplotypes', [])
                    diplotype_count += len(source_diplotypes)
                    self._parse_diplotypes(source_diplotypes, run_id, gene_symbol)
                    self._parse_related_drugs(gene_data.get('relatedDrugs', []), run_id, gene_symbol)
                    self._parse_messages(gene_data.get('messages', []), run_id, gene_symbol)
                    self._parse_variants(gene_data.get('variants', []), run_id, gene_symbol)
        
        logger.info(f"Completed parsing {gene_count} genes with {diplotype_count} diplotypes for run {run_id}")
    
    def _store_gene_data(self, gene_symbol: str, gene_data: Dict[str, Any], source: str, run_id: str) -> None:
        """Store gene summary data in the database."""
        gene_summary = PharmCATGeneSummary(
            run_id=run_id,
            gene_symbol=gene_symbol,
            call_source=source,
            phenotype_source=gene_data.get('phenotypeSource'),
            phenotype_version=gene_data.get('phenotypeVersion'),
            allele_definition_version=gene_data.get('alleleDefinitionVersion'),
            allele_definition_source=gene_data.get('alleleDefinitionSource'),
            chromosome=gene_data.get('chr'),
            phased=gene_data.get('phased'),
            effectively_phased=gene_data.get('effectivelyPhased'),
            gene_full_data=gene_data
        )
        self.db_session.add(gene_summary)
    
    def _parse_diplotypes(self, diplotypes: List[Dict[str, Any]], run_id: str, gene_symbol: str) -> None:
        """Parse diplotype data"""
        for diplotype in diplotypes:
            # Handle None alleles safely
            allele1 = diplotype.get('allele1') or {}
            allele2 = diplotype.get('allele2') or {}
            
            # Parse activity score
            activity_score = diplotype.get('activityScore')
            if activity_score is not None and activity_score != 'n/a':
                try:
                    activity_score = float(activity_score)
                except (ValueError, TypeError):
                    activity_score = None
            
            diplotype_record = PharmCATDiplotype(
                run_id=run_id,
                gene_symbol=gene_symbol,
                diplotype_label=diplotype.get('label'),
                allele1_name=allele1.get('name') if allele1 else None,
                allele1_function=allele1.get('function') if allele1 else None,
                allele2_name=allele2.get('name') if allele2 else None,
                allele2_function=allele2.get('function') if allele2 else None,
                activity_score=activity_score,
                phenotype=diplotype.get('phenotypes', [None])[0] if diplotype.get('phenotypes') else None,
                match_score=diplotype.get('matchScore'),
                outside_phenotype=diplotype.get('outsidePhenotype'),
                outside_activity_score=diplotype.get('outsideActivityScore'),
                inferred=diplotype.get('inferred'),
                combination=diplotype.get('combination'),
                phenotype_data_source=diplotype.get('phenotypeDataSource'),
                diplotype_key=diplotype.get('diplotypeKey')
            )
            self.db_session.add(diplotype_record)
    
    def _parse_related_drugs(self, drugs: List[Dict[str, Any]], run_id: str, gene_symbol: str) -> None:
        """Parse drug-gene relationships"""
        for drug in drugs:
            drug_record = PharmCATDrugGeneMap(
                run_id=run_id,
                gene_symbol=gene_symbol,
                drug_name=drug.get('name'),
                drug_id=drug.get('id')
            )
            self.db_session.add(drug_record)
    
    def _parse_messages(self, messages: List[Dict[str, Any]], run_id: str, gene_symbol: str) -> None:
        """Parse messages and warnings"""
        for message in messages:
            message_record = PharmCATMessage(
                run_id=run_id,
                gene_symbol=gene_symbol,
                rule_name=message.get('rule_name'),
                version=message.get('version'),
                exception_type=message.get('exception_type'),
                message=message.get('message'),
                matches=message.get('matches')
            )
            self.db_session.add(message_record)
    
    def _parse_variants(self, variants: List[Dict[str, Any]], run_id: str, gene_symbol: str) -> None:
        """Parse genetic variants from PharmCAT JSON format.
        
        PharmCAT JSON variant structure:
        - chromosome: e.g., "chr4"
        - position: e.g., 88131171
        - dbSnpId: e.g., "rs2231142" (note capital S)
        - call: e.g., "G/G" (the genotype call)
        - wildtypeAllele: e.g., "G" (reference allele)
        - alleles: array of allele names
        - phased: boolean
        - hasUndocumentedVariations: boolean
        - warnings: array
        """
        for variant in variants:
            # Get reference allele from wildtypeAllele (PharmCAT's field name)
            ref_allele = variant.get('wildtypeAllele', '') or ''
            
            # Get genotype call from 'call' field (PharmCAT uses 'call', not 'genotypeCall')
            genotype_call = variant.get('call', '')
            
            # Try to derive alternate allele from the genotype call
            # If call is "G/T" and ref is "G", alternate is "T"
            alt_allele = ''
            if genotype_call and ref_allele:
                # Split the call (e.g., "G/T" -> ["G", "T"])
                call_parts = genotype_call.replace('|', '/').split('/')
                for part in call_parts:
                    if part and part != ref_allele:
                        alt_allele = part
                        break
            
            # Truncate alleles if they exceed database limit (60 chars)
            # Full data is still stored in variant_data JSONB field
            if ref_allele and len(ref_allele) > 60:
                logger.warning(f"Truncating reference_allele from {len(ref_allele)} to 60 chars for variant at {variant.get('chromosome')}:{variant.get('position')}")
                ref_allele = ref_allele[:60]
            
            if alt_allele and len(alt_allele) > 60:
                logger.warning(f"Truncating alternate_allele from {len(alt_allele)} to 60 chars for variant at {variant.get('chromosome')}:{variant.get('position')}")
                alt_allele = alt_allele[:60]
            
            # Truncate genotype_call if it exceeds database limit (100 chars)
            # This can happen with complex indels like "ATCGATCG/ATCGATCGATCG"
            if genotype_call and len(genotype_call) > 100:
                logger.warning(f"Truncating genotype_call from {len(genotype_call)} to 100 chars for variant at {variant.get('chromosome')}:{variant.get('position')}")
                genotype_call = genotype_call[:100]
            
            # Truncate dbsnp_id if it exceeds database limit (30 chars)
            dbsnp_id = variant.get('dbSnpId', '') or ''
            if dbsnp_id and len(dbsnp_id) > 30:
                logger.warning(f"Truncating dbsnp_id from {len(dbsnp_id)} to 30 chars for variant at {variant.get('chromosome')}:{variant.get('position')}")
                dbsnp_id = dbsnp_id[:30]
            
            variant_record = PharmCATVariant(
                run_id=run_id,
                gene_symbol=gene_symbol,
                chromosome=variant.get('chromosome'),
                position=variant.get('position'),
                reference_allele=ref_allele,
                alternate_allele=alt_allele,
                genotype_call=genotype_call,
                dbsnp_id=dbsnp_id,
                variant_data=variant  # Full data preserved here
            )
            self.db_session.add(variant_record)
    
    def _parse_drugs(self, drugs_data: Dict[str, Any], run_id: str) -> None:
        """Parse drug recommendations data from nested structure"""
        # drugs_data structure: {"CPIC Guideline Annotation": {drug_name: drug_data}, ...}
        drug_count = 0
        recommendation_count = 0
        
        logger.info(f"Parsing drugs data for run {run_id}. Top-level keys: {list(drugs_data.keys()) if drugs_data else 'None'}")
        
        for guideline_source, drugs_in_source in drugs_data.items():
            if not isinstance(drugs_in_source, dict):
                logger.warning(f"Skipping drugs for source '{guideline_source}' with unexpected type {type(drugs_in_source).__name__}")
                continue
            
            logger.info(f"Processing drug source '{guideline_source}' with {len(drugs_in_source)} drugs")
                
            for drug_name, drug_data in drugs_in_source.items():
                if not isinstance(drug_data, dict):
                    continue
                
                # Extract basic drug info
                drug_id = drug_data.get('id')
                source = drug_data.get('source', guideline_source)
                
                # Parse guidelines array to extract recommendations
                guidelines = drug_data.get('guidelines', [])
                for guideline in guidelines:
                    if not isinstance(guideline, dict):
                        continue
                    
                    # Extract guideline info
                    guideline_id = guideline.get('id')
                    guideline_name = guideline.get('name')
                    guideline_url = guideline.get('url')
                    
                    # Parse annotations for recommendations
                    annotations = guideline.get('annotations', [])
                    for annotation in annotations:
                        if not isinstance(annotation, dict):
                            continue
                        
                        # Extract gene symbol from lookupKey or phenotypes
                        gene_symbol = self._extract_gene_symbol_from_annotation(annotation)
                        
                        # Create drug recommendation
                        recommendation = PharmCATDrugRecommendation(
                            run_id=run_id,
                            drug_name=drug_name,
                            drug_id=drug_id,
                            gene_symbol=gene_symbol,
                            guideline_source=source,
                            guideline_id=guideline_id,
                            guideline_name=guideline_name,
                            guideline_url=guideline_url,
                            recommendation_text=annotation.get('drugRecommendation'),
                            classification=annotation.get('classification'),
                            strength_of_evidence=annotation.get('strengthOfEvidence'),
                            population=annotation.get('population'),
                            implications=self._format_implications(annotation.get('implications')),
                            drug_recommendation=annotation.get('drugRecommendation'),
                            citations=drug_data.get('citations'),
                            urls=drug_data.get('urls'),
                            recommendation_data=annotation
                        )
                        self.db_session.add(recommendation)
                        self.db_session.flush()  # Get the ID
                        recommendation_count += 1
                        
                        # Parse recommendation conditions from genotypes
                        self._parse_recommendation_conditions_from_annotation(
                            annotation.get('genotypes', []), 
                            recommendation.id, 
                            run_id
                        )
                
                drug_count += 1
        
        logger.info(f"Completed parsing {drug_count} drugs with {recommendation_count} recommendations for run {run_id}")
    
    def _extract_gene_symbol_from_annotation(self, annotation: Dict[str, Any]) -> Optional[str]:
        """Extract gene symbol from annotation lookupKey or phenotypes"""
        # Try lookupKey first (e.g., {'HLA-B': '*57:01 positive'})
        lookup_key = annotation.get('lookupKey', {})
        if isinstance(lookup_key, dict) and lookup_key:
            return list(lookup_key.keys())[0]
        
        # Try phenotypes (e.g., {'HLA-B': '*57:01 positive'})
        phenotypes = annotation.get('phenotypes', {})
        if isinstance(phenotypes, dict) and phenotypes:
            return list(phenotypes.keys())[0]
        
        # Try genotypes array
        genotypes = annotation.get('genotypes', [])
        if genotypes and isinstance(genotypes[0], dict):
            diplotypes = genotypes[0].get('diplotypes', [])
            if diplotypes and isinstance(diplotypes[0], dict):
                gene = diplotypes[0].get('gene')
                if gene:
                    return gene
        
        return None
    
    def _format_implications(self, implications: Any) -> Optional[str]:
        """Format implications as a string"""
        if not implications:
            return None
        
        if isinstance(implications, list):
            return '; '.join(str(imp) for imp in implications)
        elif isinstance(implications, str):
            return implications
        else:
            return str(implications)
    
    def _parse_recommendation_conditions_from_annotation(self, genotypes: List[Dict[str, Any]], 
                                                        recommendation_id: str, run_id: str) -> None:
        """Parse recommendation conditions from genotypes in annotation"""
        for genotype in genotypes:
            if not isinstance(genotype, dict):
                continue
            
            diplotypes = genotype.get('diplotypes', [])
            for diplotype in diplotypes:
                if not isinstance(diplotype, dict):
                    continue
                
                gene_symbol = diplotype.get('gene')
                phenotypes = diplotype.get('phenotypes', [])
                
                # Create condition for each phenotype
                for phenotype in phenotypes:
                    if phenotype and gene_symbol:
                        condition_record = PharmCATRecommendationCondition(
                            recommendation_id=recommendation_id,
                            gene_symbol=gene_symbol,
                            phenotype=str(phenotype),
                            condition_data=diplotype
                        )
                        self.db_session.add(condition_record)
    
    def _parse_recommendation_conditions(self, conditions: List[Dict[str, Any]], 
                                       recommendation_id: str, run_id: str) -> None:
        """Parse recommendation conditions (legacy method for backward compatibility)"""
        for condition in conditions:
            condition_record = PharmCATRecommendationCondition(
                recommendation_id=recommendation_id,
                gene_symbol=condition.get('gene'),
                phenotype=condition.get('phenotype'),
                condition_data=condition
            )
            self.db_session.add(condition_record)
    
    def _parse_unannotated_gene_calls(self, unannotated_calls: List[Dict[str, Any]], run_id: str) -> None:
        """Parse unannotated gene calls"""
        for call in unannotated_calls:
            call_record = PharmCATUnannotatedGeneCall(
                run_id=run_id,
                gene_symbol=call.get('geneSymbol'),
                allele_definition_version=call.get('alleleDefinitionVersion'),
                allele_definition_source=call.get('alleleDefinitionSource'),
                phenotype_version=call.get('phenotypeVersion'),
                phenotype_source=call.get('phenotypeSource'),
                chromosome=call.get('chr'),
                phased=call.get('phased'),
                effectively_phased=call.get('effectivelyPhased'),
                call_source=call.get('callSource'),
                uncalled_haplotypes=call.get('uncalledHaplotypes'),
                messages=call.get('messages'),
                related_drugs=call.get('relatedDrugs'),
                source_diplotypes=call.get('sourceDiplotypes'),
                variants=call.get('variants'),
                variants_of_interest=call.get('variantsOfInterest'),
                has_undocumented_variations=call.get('hasUndocumentedVariations'),
                treat_undocumented_variations_as_reference=call.get('treatUndocumentedVariationsAsReference'),
                gene_call_data=call
            )
            self.db_session.add(call_record)
    
    def _parse_matcher_metadata(self, metadata: Dict[str, Any], run_id: str) -> None:
        """Parse matcher metadata (if needed)"""
        # This can be extended to parse matcher-specific metadata
        pass
    
    # ============================================================================
    # Query Methods
    # ============================================================================
    
    def get_gene_summary(self, run_id: str) -> List[Dict[str, Any]]:
        """Get summary of all genes for a run"""
        results = self.db_session.query(PharmCATGeneSummary).filter(
            PharmCATGeneSummary.run_id == run_id
        ).all()
        
        return [
            {
                'gene_symbol': r.gene_symbol,
                'call_source': r.call_source,
                'phenotype_source': r.phenotype_source,
                'chromosome': r.chromosome,
                'phased': r.phased
            }
            for r in results
        ]
    
    def get_diplotypes(self, run_id: str, gene_symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get diplotype information for a run, optionally filtered by gene"""
        query = self.db_session.query(PharmCATDiplotype).filter(
            PharmCATDiplotype.run_id == run_id
        )
        
        if gene_symbol:
            query = query.filter(PharmCATDiplotype.gene_symbol == gene_symbol)
        
        results = query.all()
        
        return [
            {
                'gene_symbol': r.gene_symbol,
                'diplotype_label': r.diplotype_label,
                'allele1_name': r.allele1_name,
                'allele1_function': r.allele1_function,
                'allele2_name': r.allele2_name,
                'allele2_function': r.allele2_function,
                'activity_score': float(r.activity_score) if r.activity_score else None,
                'phenotype': r.phenotype,
                'match_score': r.match_score,
                'inferred': r.inferred,
                'combination': r.combination
            }
            for r in results
        ]
    
    def get_drugs_by_gene(self, run_id: str, gene_symbol: str) -> List[Dict[str, Any]]:
        """Get all drugs related to a specific gene"""
        results = self.db_session.query(PharmCATDrugGeneMap).filter(
            PharmCATDrugGeneMap.run_id == run_id,
            PharmCATDrugGeneMap.gene_symbol == gene_symbol
        ).all()
        
        return [
            {
                'drug_name': r.drug_name,
                'drug_id': r.drug_id
            }
            for r in results
        ]
    
    def get_drug_recommendations(self, run_id: str) -> List[Dict[str, Any]]:
        """Get all drug recommendations for a run"""
        results = self.db_session.query(PharmCATDrugRecommendation).filter(
            PharmCATDrugRecommendation.run_id == run_id
        ).all()
        
        return [
            {
                'drug_name': r.drug_name,
                'drug_id': r.drug_id,
                'gene_symbol': r.gene_symbol,
                'guideline_source': r.guideline_source,
                'guideline_id': r.guideline_id,
                'guideline_name': r.guideline_name,
                'guideline_url': r.guideline_url,
                'recommendation_text': r.recommendation_text,
                'classification': r.classification,
                'strength_of_evidence': r.strength_of_evidence,
                'population': r.population,
                'implications': r.implications,
                'drug_recommendation': r.drug_recommendation,
                'citations': r.citations,
                'urls': r.urls,
                'recommendation_data': r.recommendation_data
            }
            for r in results
        ]
    
    def get_messages(self, run_id: str, gene_symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get messages/warnings for a run"""
        query = self.db_session.query(PharmCATMessage).filter(
            PharmCATMessage.run_id == run_id
        )
        
        if gene_symbol:
            query = query.filter(PharmCATMessage.gene_symbol == gene_symbol)
        
        results = query.all()
        
        return [
            {
                'gene_symbol': r.gene_symbol,
                'rule_name': r.rule_name,
                'exception_type': r.exception_type,
                'message': r.message
            }
            for r in results
        ]
    
    def get_actionable_findings(self, run_id: str) -> List[Dict[str, Any]]:
        """Get actionable findings (non-normal phenotypes) for a run"""
        results = self.db_session.query(PharmCATDiplotype).filter(
            PharmCATDiplotype.run_id == run_id,
            PharmCATDiplotype.phenotype.notin_(['n/a', 'Normal Metabolizer', 'Uncertain Susceptibility'])
        ).all()
        
        return [
            {
                'gene_symbol': r.gene_symbol,
                'diplotype_label': r.diplotype_label,
                'phenotype': r.phenotype,
                'activity_score': float(r.activity_score) if r.activity_score else None,
                'allele1_name': r.allele1_name,
                'allele2_name': r.allele2_name
            }
            for r in results
        ]
    
    def get_all_runs(self) -> List[Dict[str, Any]]:
        """Get all PharmCAT runs from the database"""
        results = self.db_session.query(PharmCATResult).order_by(
            PharmCATResult.created_at.desc()
        ).all()
        
        return [
            {
                'id': str(r.id),
                'run_id': r.run_id,
                'title': r.run_id,  # run_id is typically the title
                'run_timestamp': r.run_timestamp,
                'pharmcat_version': r.pharmcat_version,
                'data_version': r.data_version,
                'genome_build': r.genome_build,
                'created_at': r.created_at,
                'loaded_at': r.loaded_at
            }
            for r in results
        ]
    
    def explore_structure(self, data: Dict[str, Any], path: str = "", max_depth: int = 5) -> None:
        """Recursively explore and print JSON structure"""
        if max_depth == 0:
            return
        
        if isinstance(data, dict):
            for key, value in data.items():
                current_path = f"{path}.{key}" if path else key
                value_type = type(value).__name__
                
                if isinstance(value, (dict, list)):
                    if isinstance(value, list):
                        length = len(value)
                        print(f"{current_path}: {value_type}[{length}]")
                        if length > 0:
                            self.explore_structure(value[0], f"{current_path}[0]", max_depth - 1)
                    else:
                        print(f"{current_path}: {value_type}")
                        self.explore_structure(value, current_path, max_depth - 1)
                else:
                    sample = str(value)[:50]
                    print(f"{current_path}: {value_type} = {sample}")
        elif isinstance(data, list):
            if len(data) > 0:
                print(f"{path}: list[{len(data)}]")
                self.explore_structure(data[0], f"{path}[0]", max_depth - 1)


# ============================================================================
# Convenience Functions
# ============================================================================

def load_pharmcat_file(filepath: Union[str, Path], db_session: Optional[Session] = None) -> str:
    """
    Convenience function to load a PharmCAT file into the database
    
    Args:
        filepath: Path to PharmCAT JSON file
        db_session: Optional database session
        
    Returns:
        run_id: The unique identifier for this PharmCAT run
    """
    with PharmCATParser(db_session) as parser:
        data = parser.load_json_file(filepath)
        return parser.parse_and_load(data)


def get_pharmcat_summary(run_id: str, db_session: Optional[Session] = None) -> Dict[str, Any]:
    """
    Get a comprehensive summary of a PharmCAT run
    
    Args:
        run_id: PharmCAT run identifier
        db_session: Optional database session
        
    Returns:
        Dictionary containing summary information
    """
    with PharmCATParser(db_session) as parser:
        # Get run metadata from the results table
        result = parser.db_session.query(PharmCATResult).filter(
            PharmCATResult.run_id == run_id
        ).first()
        
        genes = parser.get_gene_summary(run_id)
        diplotypes = parser.get_diplotypes(run_id)
        actionable = parser.get_actionable_findings(run_id)
        messages = parser.get_messages(run_id)
        
        return {
            'run_id': run_id,
            'pharmcat_version': result.pharmcat_version if result else None,
            'data_version': result.data_version if result else None,
            'created_at': result.created_at if result else None,
            'sample_identifier': run_id,  # run_id is typically the sample identifier/title
            'total_genes': len(genes),
            'total_diplotypes': len(diplotypes),
            'actionable_findings_count': len(actionable),
            'total_messages': len(messages),
            'genes': genes,
            'actionable_findings': actionable,
            'warning_messages': [m for m in messages if m['exception_type'] in ['warning', 'error']]
        }


if __name__ == "__main__":
    """Example usage"""
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python pharmcat_parser.py <pharmcat_json_file>")
        print("\nExample:")
        print("  python pharmcat_parser.py example_pgx_pharmcat.json")
        sys.exit(1)
    
    filepath = sys.argv[1]
    print(f"Loading PharmCAT file: {filepath}")
    
    try:
        # Load the file
        run_id = load_pharmcat_file(filepath)
        print(f"✓ Successfully loaded as run ID: {run_id}")
        
        # Get summary
        summary = get_pharmcat_summary(run_id)
        print(f"\n=== PharmCAT Summary ===")
        print(f"Total genes analyzed: {summary['total_genes']}")
        print(f"Total diplotypes: {summary['total_diplotypes']}")
        print(f"Actionable findings: {summary['actionable_findings']}")
        print(f"Warning messages: {len(summary['warning_messages'])}")
        
        # Show actionable findings
        if summary['actionable_findings']:
            print(f"\n=== Actionable Findings ===")
            for finding in summary['actionable_findings'][:5]:  # Show first 5
                print(f"  ⚠ {finding['gene_symbol']} - {finding['diplotype_label']}")
                print(f"    Phenotype: {finding['phenotype']}")
                if finding['activity_score']:
                    print(f"    Activity Score: {finding['activity_score']}")
        
        print(f"\n✓ Analysis complete!")
        
    except Exception as e:
        print(f"✗ Error: {e}")
        sys.exit(1)
