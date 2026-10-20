"""An actionable finding is a gene a guideline's prescribing change comes from.

The FHIR DiagnosticReport's conclusion ("N genes analyzed, M actionable findings
identified"), /api/pharmcat/actionable/{run_id} and the summaries' actionable
findings counted every stored diplotype whose phenotype was not "n/a", "Normal
Metabolizer" or "Uncertain Susceptibility". Normal Function, G6PD Normal, a negative
HLA-B screen, Indeterminate, No Result, and a poor metabolizer no guideline in the
run was even looked up for, all counted: 9 genes on NA12878 (PharmCAT 3.4), only
one of them (CFTR) a gene any guideline changes a prescription for. The FHIR export
of a report built from PharmCAT's JSON counted none, whatever it held.

The definition now (app.reports.actionable.actionable_genes) is the interactive
report's drug-gene network's: a gene some PharmCAT annotation's prescribing change
comes from (evidence.prescribing_changes, evidence.change_genes), judged by the
result the annotation was looked up by. The calls and annotations below are
synthetic, in PharmCAT 3.4's shape; the parity tests run PharmCAT's own example
output through both report lanes and the network's own script.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.pharmcat.pharmcat_client import normalize_pharmcat_results
from app.pharmcat.pharmcat_parser import (
    PharmCATDiplotype,
    PharmCATParser,
    get_pharmcat_summary,
)
from app.services.fhir_export_service import FHIRExportService
from app.services.pharmcat_data_service import PharmCATDataService

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "app" / "static" / "js" / "pgx-report.js"

NEGATIVE = ("*15:02 negative", "*57:01 negative", "*58:01 negative")
CARRIER = ("*15:02 negative", "*57:01 positive", "*58:01 negative")


def _diplotype(gene, label, phenotypes, activity=None):
    """A PharmCAT 3.4 diplotype; MT-RNR1's call is one allele."""
    alleles = label.split("/")
    return {
        "gene": gene,
        "label": label,
        "allele1": {"gene": gene, "name": alleles[0], "function": None},
        "allele2": (
            {"gene": gene, "name": alleles[1], "function": None}
            if len(alleles) > 1
            else None
        ),
        "phenotypes": list(phenotypes),
        "activityScore": activity,
        "lookupKey": list(phenotypes),
        "matchScore": 0,
        "inferred": False,
        "combination": False,
    }


CALLS = {
    "G6PD": _diplotype("G6PD", "B (reference)/B (reference)", ["Normal"]),
    "SLCO1B1": _diplotype("SLCO1B1", "*1/*1", ["Normal Function"]),
    "MT-RNR1": _diplotype(
        "MT-RNR1", "Reference", ["normal risk of aminoglycoside-induced hearing loss"]
    ),
    # *22's function is uncertain: no activity score, no phenotype.
    "CYP2D6": _diplotype("CYP2D6", "*1/*22", ["Indeterminate"], "n/a"),
    "HLA-B": _diplotype("HLA-B", "*08:01/*57:01", CARRIER),
    "CYP2C9": _diplotype("CYP2C9", "*1/*3", ["Intermediate Metabolizer"], "1.0"),
    "CYP2C19": _diplotype("CYP2C19", "*2/*2", ["Poor Metabolizer"]),
    "NUDT15": _diplotype("NUDT15", "*3/*3", ["Poor Metabolizer"]),
}


def _annotation(text, tags, lookup, *genes, calls=None):
    """A PharmCAT 3.4 annotation: looked up by ``lookup`` (gene -> result), its
    genotype the sample's calls for ``genes``. ``tags``: A(lternative drug),
    D(ose), O(ther guidance)."""
    calls = calls or CALLS
    return {
        "drugRecommendation": text,
        "classification": "Strong",
        "lookupKey": [lookup],
        "genotypes": [{"diplotypes": [calls[gene] for gene in genes]}],
        "alternateDrugAvailable": "A" in tags,
        "dosingInformation": "D" in tags,
        "otherPrescribingGuidance": "O" in tags,
    }


ANNOTATIONS = {
    # CPIC's, as PharmCAT 3.4 gives them for these calls.
    "rasburicase": _annotation(
        "No reason to avoid based on G6PD status", "", {"G6PD": "Normal"}, "G6PD"
    ),
    "simvastatin": _annotation(
        "Prescribe desired starting dose and adjust doses based on "
        "disease-specific guidelines.",
        "",
        {"SLCO1B1": "Normal Function"},
        "SLCO1B1",
    ),
    # Tagged for its monitoring sentence; on a normal-risk result it is the
    # standard (evidence.py, 30x NA12878's 11 aminoglycosides).
    "gentamicin": _annotation(
        "Use aminoglycoside antibiotics at standard doses for the shortest feasible "
        "course with therapeutic dose monitoring.",
        "O",
        {"MT-RNR1": "normal risk of aminoglycoside-induced hearing loss"},
        "MT-RNR1",
    ),
    "codeine": _annotation("No recommendation", "", {"CYP2D6": "n/a"}, "CYP2D6"),
    "abacavir": _annotation(
        "Abacavir is not recommended", "A", {"HLA-B": "*57:01 positive"}, "HLA-B"
    ),
    # Looked up by the *15:02 result, negative; the dose change is CYP2C9's.
    "phenytoin": _annotation(
        "Consider 25% reduction of recommended starting maintenance dose.",
        "D",
        {"HLA-B": "*15:02 negative", "CYP2C9": "1.0"},
        "CYP2C9",
        "HLA-B",
    ),
    "omeprazole": _annotation(
        "Initiate standard starting daily dose. For chronic therapy (>12 weeks) and "
        "efficacy achieved, consider 50% reduction in daily dose.",
        "D",
        {"CYP2C19": "Poor Metabolizer"},
        "CYP2C19",
    ),
}


def _report(title, calls, annotations):
    """A PharmCAT 3.4 report.json holding ``calls`` and CPIC's ``annotations``."""
    return {
        "title": title,
        "pharmcatVersion": "3.4.0",
        "genes": {
            gene: {
                "geneSymbol": gene,
                "callSource": "MATCHER",
                "sourceDiplotypes": [call],
                "recommendationDiplotypes": [call],
            }
            for gene, call in calls.items()
        },
        "drugs": {
            "CPIC Guideline Annotation": {
                drug: {
                    "name": drug,
                    "guidelines": [
                        {"name": f"CPIC {drug}", "annotations": [annotation]}
                    ],
                }
                for drug, annotation in annotations.items()
            }
        },
    }


def _load(db_session, report):
    return PharmCATParser(db_session).parse_and_load(report, run_id=report["title"])


def _findings(db_session, run_id):
    return PharmCATParser(db_session).get_actionable_findings(run_id)


def _genes(findings):
    return [f["gene_symbol"] for f in findings]


def _conclusion(result):
    assert result["success"], result.get("error")
    return next(
        entry["resource"]["conclusion"]
        for entry in result["bundle"]["entry"]
        if entry["resource"]["resourceType"] == "DiagnosticReport"
    )


# ---------------------------------------------------------------------------
# What counts
# ---------------------------------------------------------------------------


def test_normal_negative_and_indeterminate_results_are_not_actionable(db_session):
    """None of these results changes a prescription: G6PD Normal and SLCO1B1 Normal
    Function keep the standard drug and dose, MT-RNR1's normal risk and HLA-B's
    negative screens are all-clears (their tags mark standard advice), and CPIC has
    no recommendation for an Indeterminate CYP2D6. Every one used to count."""
    calls = {gene: CALLS[gene] for gene in ("G6PD", "SLCO1B1", "MT-RNR1", "CYP2D6")}
    calls["HLA-B"] = _diplotype("HLA-B", "*08:01/*56:01", NEGATIVE)
    annotations = {
        drug: ANNOTATIONS[drug]
        for drug in ("rasburicase", "simvastatin", "gentamicin", "codeine")
    }
    annotations["abacavir"] = _annotation(
        "Use abacavir per standard dosing guidelines",
        "",
        {"HLA-B": "*57:01 negative"},
        "HLA-B",
        calls=calls,
    )
    # An FDA-style screening note, tagged, on a negative screen.
    annotations["carbamazepine"] = _annotation(
        "Patients with ancestry in populations in which HLA-B*15:02 may be present "
        "should be screened before starting carbamazepine.",
        "O",
        {"HLA-B": "*15:02 negative"},
        "HLA-B",
        calls=calls,
    )
    run_id = _load(db_session, _report("all-clear", calls, annotations))

    assert _findings(db_session, run_id) == []


def test_a_poor_metabolizer_with_a_dose_change_is_actionable(db_session):
    run_id = _load(
        db_session,
        _report(
            "cyp2c19-pm",
            {"CYP2C19": CALLS["CYP2C19"]},
            {"omeprazole": ANNOTATIONS["omeprazole"]},
        ),
    )

    assert _findings(db_session, run_id) == [
        {
            "gene_symbol": "CYP2C19",
            "diplotype_label": "*2/*2",
            "phenotype": "Poor Metabolizer",
            "activity_score": None,
            "allele1_name": "*2",
            "allele2_name": "*2",
            "drugs": ["omeprazole"],
            "prescribing_changes": ["dose change"],
        }
    ]


def test_an_hla_b_57_01_carrier_on_abacavir_is_an_hla_b_finding(db_session):
    """Stated with every HLA-B result, as the report states it."""
    run_id = _load(
        db_session,
        _report(
            "carrier-abacavir",
            {"HLA-B": CALLS["HLA-B"]},
            {"abacavir": ANNOTATIONS["abacavir"]},
        ),
    )

    [finding] = _findings(db_session, run_id)
    assert finding["gene_symbol"] == "HLA-B"
    assert finding["diplotype_label"] == "*08:01/*57:01"
    assert finding["phenotype"] == ", ".join(CARRIER)
    assert finding["drugs"] == ["abacavir"]
    assert finding["prescribing_changes"] == ["alternative drug"]


def test_phenytoin_for_a_carrier_is_a_cyp2c9_finding_not_an_hla_b_one(db_session):
    """Phenytoin's guideline is looked up by HLA-B's *15:02 result, negative here;
    its dose change comes from CYP2C9. The *57:01 result has nothing to do with it,
    so with no other drug HLA-B is not an actionable finding."""
    run_id = _load(
        db_session,
        _report(
            "carrier-phenytoin",
            {"HLA-B": CALLS["HLA-B"], "CYP2C9": CALLS["CYP2C9"]},
            {"phenytoin": ANNOTATIONS["phenytoin"]},
        ),
    )

    findings = _findings(db_session, run_id)
    assert _genes(findings) == ["CYP2C9"]
    assert findings[0]["drugs"] == ["phenytoin"]
    assert findings[0]["prescribing_changes"] == ["dose change"]


def test_a_gene_no_guideline_was_looked_up_for_is_not_actionable(db_session):
    """A NUDT15 poor metabolizer in a run with no annotation for it: nothing in
    this report says to prescribe differently."""
    run_id = _load(
        db_session, _report("no-annotations", {"NUDT15": CALLS["NUDT15"]}, {})
    )

    assert _findings(db_session, run_id) == []


def test_a_run_stored_with_hla_b_s_first_result_alone_states_every_one(db_session):
    """Runs stored before HLA-B kept every result hold "*15:02 negative" in its
    row. The findings are read from the stored annotations and the report's gene
    rows, so such a run still finds the carrier, with every result."""
    run_id = _load(
        db_session,
        _report(
            "carrier-stale",
            {"HLA-B": CALLS["HLA-B"]},
            {"abacavir": ANNOTATIONS["abacavir"]},
        ),
    )
    db_session.query(PharmCATDiplotype).filter_by(
        run_id=run_id, gene_symbol="HLA-B"
    ).update({"phenotype": "*15:02 negative"})
    db_session.commit()

    [finding] = _findings(db_session, run_id)
    assert finding["phenotype"] == ", ".join(CARRIER)
    assert finding["drugs"] == ["abacavir"]


# ---------------------------------------------------------------------------
# Everything that counts them
# ---------------------------------------------------------------------------

MIXED = _report("mixed", CALLS, ANNOTATIONS)
# Each actionable gene, with what makes it one.
EXPECTED = [
    ("CYP2C19", ["omeprazole"], ["dose change"]),
    ("CYP2C9", ["phenytoin"], ["dose change"]),
    ("HLA-B", ["abacavir"], ["alternative drug"]),
]


def test_the_api_routes_count_genes_a_prescribing_change_comes_from(client, db_session):
    run_id = _load(db_session, MIXED)

    actionable = client.get(f"/api/pharmcat/actionable/{run_id}")
    assert actionable.status_code == 200, actionable.text
    assert (
        sorted(
            (f["gene_symbol"], f["drugs"], f["prescribing_changes"])
            for f in actionable.json()
        )
        == EXPECTED
    )

    summary = client.get(f"/api/pharmcat/summary/{run_id}")
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body["actionable_findings"] == len(EXPECTED)
    assert sorted(_genes(body["actionable_findings_list"])) == [
        gene for gene, _, _ in EXPECTED
    ]
    assert get_pharmcat_summary(run_id, db_session)["actionable_findings_count"] == 3


def test_the_fhir_conclusion_counts_them_whichever_lane_built_the_data(db_session):
    """The database lane's data and the JSON lane's: the JSON lane's carries no
    actionable_findings, and its bundle said 0 whatever the run held."""
    run_id = _load(db_session, MIXED)
    service = FHIRExportService(db_session)

    database_lane = service.export_pgx_report(run_id=run_id)
    assert _conclusion(database_lane) == (
        "Pharmacogenomic analysis completed. 8 genes analyzed, "
        "3 actionable findings identified."
    )
    data = PharmCATDataService(db_session).get_pharmcat_data_by_run_id(run_id)
    assert sorted(_genes(data["actionable_findings"])) == [
        gene for gene, _, _ in EXPECTED
    ]

    json_lane = normalize_pharmcat_results(MIXED)["data"]
    assert "3 actionable findings identified." in _conclusion(
        service.export_pgx_report(run_id=run_id, pharmcat_data=json_lane)
    )


# ---------------------------------------------------------------------------
# The report's drug network draws the same genes
# ---------------------------------------------------------------------------

NETWORK = r"""
const fs = require('fs');
const vm = require('vm');
const context = { document: { addEventListener() {} }, console };
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), context);
const [recs, dips] = JSON.parse(fs.readFileSync(0, 'utf8'));
context.recs = recs; context.dips = dips;
const net = vm.runInContext('actionableNetwork(recs, dips)', context);
process.stdout.write(JSON.stringify(net.genes.map(g => g.label)));
"""


def _network_genes(data):
    """The genes the interactive report's network draws for ``data``, fed to it as
    the template feeds it (interactive_report.html's recommendation-data)."""
    from app.reports.generator import map_recommendations_for_template

    recs = [
        {
            "drug": rec["drug"],
            "gene": rec["gene"],
            "genes": rec.get("recommendation_genes") or [rec["gene"]],
            "prescribingChanges": rec.get("prescribing_changes") or [],
            "changeGenes": rec.get("change_genes") or [],
        }
        for rec in map_recommendations_for_template(data["drugRecommendations"])
    ]
    dips = [
        {"gene": g["gene"], "phenotype": g.get("phenotype") or ""}
        for g in data["genes"]
    ]
    proc = subprocess.run(
        [shutil.which("node"), "-e", NETWORK, str(SCRIPT)],
        input=json.dumps([recs, dips]),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return sorted(json.loads(proc.stdout))


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize(
    "fixture",
    [
        # PharmCAT 2.15's example: 167 annotations, CPIC and DPWG gene blocks.
        "pharmcat.example.report.json",
        # PharmCAT 3.4, unphased: candidates' annotations.
        "pharmcat.unphased.example.report.json",
    ],
)
def test_the_findings_are_the_genes_the_drug_network_draws(db_session, fixture):
    report = json.loads((REPO / "test_data" / fixture).read_text(encoding="utf-8"))
    run_id = PharmCATParser(db_session).parse_and_load(report, run_id="parity")
    data = PharmCATDataService(db_session).get_pharmcat_data_by_run_id(run_id)
    drawn = _network_genes(data)
    assert drawn, "the fixture should hold prescribing changes"

    assert sorted(_genes(_findings(db_session, run_id))) == drawn
    assert sorted(_genes(data["actionable_findings"])) == drawn
    assert f"{len(drawn)} actionable findings identified." in _conclusion(
        FHIRExportService(db_session).export_pgx_report(run_id=run_id)
    )

    # The JSON lane's network, and its FHIR conclusion.
    json_lane = normalize_pharmcat_results(report)["data"]
    assert _network_genes(json_lane) == drawn
    assert f"{len(drawn)} actionable findings identified." in _conclusion(
        FHIRExportService(db_session).export_pgx_report(
            run_id=run_id, pharmcat_data=json_lane
        )
    )


@pytest.mark.parametrize(
    "data, analyzed",
    [
        # generate_report's data: PharmCAT counted 2 genes, the bundle states 4.
        (
            {
                "total_genes": 2,
                "genes": [
                    {"gene": g, "diplotype": "*1/*1"}
                    for g in ("CYP2C19", "CYP2D6", "NUDT15", "TPMT")
                ],
            },
            "4 genes analyzed",
        ),
        # PharmCAT 2.x: a CPIC and a DPWG row for the same gene.
        (
            {
                "total_genes": 3,
                "genes": [
                    {"gene": "CYP2C19", "diplotype": "*1/*1"},
                    {"gene": "CYP2C19", "diplotype": "*1/*1"},
                    {"gene": "DPYD", "diplotype": "Reference/Reference"},
                ],
            },
            "2 genes analyzed",
        ),
        # The JSON lane's data: no total_genes at all.
        ({"genes": [{"gene": "CYP2C9", "diplotype": "*1/*1"}]}, "1 gene analyzed"),
    ],
)
def test_the_conclusion_counts_the_genes_the_bundle_states(data, analyzed):
    """It counted PharmCAT's rows (total_genes): 23 for a 30x NA12878 bundle
    carrying 91 genotypes, a gene twice on PharmCAT 2.x, and 0 in the JSON lane."""
    from unittest.mock import MagicMock

    result = FHIRExportService(MagicMock()).export_pgx_report(
        run_id="run-1",
        patient_info={"id": "p"},
        pharmcat_data={"drugRecommendations": [], **data},
    )
    assert analyzed in _conclusion(result), _conclusion(result)
