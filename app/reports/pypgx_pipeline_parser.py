import csv
import io
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import zipfile


def _try_parse_table_from_zip(zip_path: Path) -> List[Dict[str, Any]]:
    """
    Return the first parseable TSV/CSV table found within the zip file.
    If multiple tables are present, prefer ones likely to contain gene-level summary
    by scoring filename keywords.
    """
    if not os.path.exists(zip_path):
        return []

    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            members = zf.namelist()

            def score(name: str) -> int:
                ln = name.lower()
                s = 0
                if 'summary' in ln:
                    s += 3
                if 'genotype' in ln or 'diplotype' in ln or 'phenotype' in ln:
                    s += 2
                if ln.endswith('.tsv') or ln.endswith('.csv'):
                    s += 1
                return s

            # Sort members by heuristic score descending
            members_sorted = sorted(members, key=score, reverse=True)
            for name in members_sorted:
                lower = name.lower()
                if not (lower.endswith('.tsv') or lower.endswith('.csv')):
                    continue
                try:
                    with zf.open(name, 'r') as fh:
                        raw = fh.read()
                    text = raw.decode('utf-8', errors='replace')
                    lines = [ln for ln in text.splitlines() if ln.strip() != '' and not ln.lstrip().startswith('#')]
                    if not lines:
                        continue
                    delimiter = '\t' if ('\t' in lines[0]) else ','
                    reader = csv.DictReader(io.StringIO('\n'.join(lines)), delimiter=delimiter)
                    return [dict(row) for row in reader]
                except Exception:
                    continue
    except Exception:
        return []

    return []


def _coalesce(*values: Optional[str]) -> Optional[str]:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s != '':
            return s
    return None


def _parse_diplotype_phenotype_activity(rows: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Extract diplotype, phenotype, activity_score, confidence from a list of dict rows.
    Returns tuple: (diplotype, phenotype, activity_score, confidence)
    """
    if not rows:
        return None, None, None, None

    # Take the first row that contains any of the target fields
    for row in rows:
        dipl = _coalesce(row.get('diplotype'), row.get('Diplotype'), row.get('DIPLOTYPE'),
                         row.get('genotype'), row.get('Genotype'))
        pheno = _coalesce(row.get('phenotype'), row.get('Phenotype'), row.get('PHENOTYPE'),
                          row.get('Predicted_Phenotype'), row.get('predicted_phenotype'))
        act = _coalesce(row.get('activity_score'), row.get('Activity_Score'), row.get('activityScore'), row.get('ActivityScore'))
        conf = _coalesce(row.get('confidence'), row.get('probability'), row.get('likelihood'))
        if dipl or pheno or act or conf:
            return dipl, pheno, act, conf

    return None, None, None, None


def _parse_variants(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Map variant table rows into a normalized evidence structure.
    Expected columns (best-effort): chrom, pos, rsid, ref, alt, zygosity, used_for_call.
    """
    variants: List[Dict[str, Any]] = []
    if not rows:
        return variants
    for row in rows:
        # Normalize keys case-insensitively
        def g(*keys: str) -> str:
            return _coalesce(*[row.get(k) for k in keys]) or ''

        variants.append({
            'chrom': g('chrom', 'CHROM', 'chr', 'Chr', 'chromosome'),
            'pos': g('pos', 'POS', 'position', 'Position'),
            'rsid': g('rsid', 'RSID', 'id', 'ID', 'rs'),
            'ref': g('ref', 'REF', 'reference', 'Reference'),
            'alt': g('alt', 'ALT', 'alternate', 'Alternate'),
            'zygosity': g('zygosity', 'Zygosity'),
            'used_for_call': (g('used_for_call', 'Used_For_Call', 'usedforcall').lower() in {'true', '1', 'yes'})
        })
    return variants


def _parse_alleles(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Map allele composition table rows into a normalized structure if present.
    Expected columns (best-effort): allele, haplotype, component, gene.
    """
    alleles: List[Dict[str, Any]] = []
    if not rows:
        return alleles
    for row in rows:
        def g(*keys: str) -> str:
            return _coalesce(*[row.get(k) for k in keys]) or ''
        alleles.append({
            'allele': g('allele', 'Allele'),
            'haplotype': g('haplotype', 'Haplotype'),
            'component': g('component', 'Component', 'SNP', 'Variant'),
            'gene': g('gene', 'Gene')
        })
    return alleles


def parse_gene_pipeline(pipeline_dir: str, gene_name: str) -> Dict[str, Any]:
    """
    Parse a PyPGx per-gene pipeline directory to extract:
    - diplotype, phenotype, activity_score, confidence
    - evidence: alleles[], variants[]
    - phased flag (if phased-variants.zip present)
    - copy_number when available (best-effort)
    """
    pdir = Path(pipeline_dir)
    result: Dict[str, Any] = {
        'gene': gene_name,
        'diplotype': None,
        'phenotype': None,
        'activity_score': None,
        'call_confidence': None,
        'evidence': {
            'alleles': [],
            'variants': []
        },
        'phased': False,
        'copy_number': None,
        'tool_source': 'PyPGx',
        'pyPgxOnly': True
    }

    # Diplotype/phenotype/activity/confidence: prefer genotypes.zip, then phenotypes.zip, then results.zip
    genotypes_rows = _try_parse_table_from_zip(pdir / 'genotypes.zip')
    phenotypes_rows = _try_parse_table_from_zip(pdir / 'phenotypes.zip')
    results_rows = _try_parse_table_from_zip(pdir / 'results.zip')

    dipl, pheno, act, conf = _parse_diplotype_phenotype_activity(genotypes_rows)
    if not (dipl or pheno or act or conf):
        d2, p2, a2, c2 = _parse_diplotype_phenotype_activity(phenotypes_rows)
        dipl, pheno, act, conf = d2, p2, a2, c2
    if not (dipl or pheno or act or conf):
        d3, p3, a3, c3 = _parse_diplotype_phenotype_activity(results_rows)
        dipl, pheno, act, conf = d3, p3, a3, c3

    if dipl:
        result['diplotype'] = dipl
    if pheno:
        result['phenotype'] = pheno
    if act:
        result['activity_score'] = act
    if conf:
        result['call_confidence'] = conf

    # Evidence: variants
    variants_rows = _try_parse_table_from_zip(pdir / 'consolidated-variants.zip')
    if not variants_rows:
        variants_rows = _try_parse_table_from_zip(pdir / 'imported-variants.zip')
    if variants_rows:
        result['evidence']['variants'] = _parse_variants(variants_rows)

    # Evidence: alleles
    alleles_rows = _try_parse_table_from_zip(pdir / 'alleles.zip')
    if alleles_rows:
        result['evidence']['alleles'] = _parse_alleles(alleles_rows)

    # Phasing flag
    if os.path.exists(pdir / 'phased-variants.zip'):
        result['phased'] = True

    # Copy number best-effort (look in any parsed rows)
    def find_cn(rows: List[Dict[str, Any]]) -> Optional[str]:
        for row in rows:
            cn = _coalesce(row.get('copy_number'), row.get('Copy_Number'), row.get('cn'), row.get('CN'))
            if cn:
                return cn
        return None

    cn = find_cn(genotypes_rows) or find_cn(phenotypes_rows) or find_cn(results_rows)
    if cn:
        result['copy_number'] = cn

    return result


