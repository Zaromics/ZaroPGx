#!/usr/bin/env python3
"""Build config/rxnorm_drugs.json: every drug PharmCAT can name, with its RxNorm code.

The FHIR export names each drug as text, because PharmCAT identifies drugs by ClinPGx
accession (``PA448320``), and a receiving EHR matches medications by RxNorm. This
table supplies the code. Re-run it when PharmCAT is bumped:

    docker run --rm --entrypoint cat zaromicsresearch/zaropgx-pharmcat:<tag> \\
        /pharmcat/pipeline/pharmcat.jar > pharmcat.jar
    python3 scripts/build_rxnorm_drugs.py pharmcat.jar config/rxnorm_drugs.json

**The drugs** come from the jar's own ``reporter/prescribing_guidance.json``: every
CPIC and DPWG guideline and FDA label and PGx-association annotation PharmCAT
reports from, so every drug it can put in a report.json, by its accession and name.

**The codes** come from NLM's RxNav (https://rxnav.nlm.nih.gov), looked up by the
drug's name: the RxNorm concept whose name *is* that name (``search=0``, exact), of
type IN (ingredient), MIN (multiple ingredients), PIN (precise ingredient) or BN
(brand name, for the few PharmCAT names as brands). Where no concept has that exact
name and the name is a combination ("a / b"), RxNav's normalized match is taken if it
returns exactly one MIN, and the row says ``"match": "normalized"``. Anything else is
left without a code: a wrong code would be worse than none -- except the few in
``REVIEWED``, each looked up by hand and confirmed as the same ingredients by
RxNorm's own name for the code.

Not taken from ClinPGx's drugs.tsv, which has an RxNorm column: its download carries
terms beyond CC BY-SA 4.0 (recipients must agree to them first; no commercial use),
and RxNorm's own lookup is the authority for RxNorm codes anyway. Compared once, on
2026-11-18, against ClinPGx's column for PharmCAT 3.4.0's 197 drugs: 180 identical, 8
coded here that ClinPGx left blank, and 2 different -- dronabinol, which ClinPGx coded
as the brand Marinol (224925) where the ingredient is 10402, and sacituzumab
govitecan, coded here as the concept of that exact name.

Sends RxNav only the drug names. Standard library only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import urllib.parse
import urllib.request
import zipfile

RXNAV = "https://rxnav.nlm.nih.gov/REST"
GUIDANCE = "org/pharmgkb/pharmcat/reporter/prescribing_guidance.json"
ACCEPTED = ("IN", "MIN", "PIN", "BN")  # in order of preference
PAUSE = 0.07  # RxNav asks for at most 20 requests a second

#: Names RxNav matches neither exactly nor by its normalized search, mapped by hand.
#: Each is the MIN whose RxNorm name lists the same ingredients without their salt
#: forms or "and"; the build fetches RxNorm's name and type for it, so the table shows
#: what was chosen. Checked 2026-11-18.
REVIEWED = {
    # "dextromethorphan hydrobromide / bupropion hydrochloride"
    "PA166278341": "2611256",  # MIN "bupropion / dextromethorphan"
    # "oxymetazoline and tetracaine"
    "PA166182885": "1809049",  # MIN "oxymetazoline / tetracaine"
}


def _get(path: str, **params) -> dict:
    url = f"{RXNAV}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "pgx-drug-map-build"})
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.load(response)
    time.sleep(PAUSE)
    return body


def _concepts(name: str, search: int) -> list:
    ids = (_get("rxcui.json", name=name, search=search).get("idGroup") or {}).get(
        "rxnormId"
    ) or []
    found = []
    for rxcui in ids:
        props = _get(f"rxcui/{rxcui}/properties.json").get("properties") or {}
        found.append(
            {
                "rxcui": props.get("rxcui"),
                "name": props.get("name"),
                "tty": props.get("tty"),
            }
        )
    return found


def pharmcat_drugs(jar_path: str) -> tuple:
    """(PharmCAT's version, {accession: name}) from the jar."""
    with zipfile.ZipFile(jar_path) as jar:
        manifest = jar.read("META-INF/MANIFEST.MF").decode("utf-8")
        guidance = json.loads(jar.read(GUIDANCE))
    version = next(
        line.split(":", 1)[1].strip()
        for line in manifest.splitlines()
        if line.startswith("Implementation-Version:")
    )
    drugs = {}
    for entry in guidance["guidelines"]:
        chemicals = list(entry["guideline"].get("relatedChemicals") or [])
        for rec in entry.get("recommendations") or []:
            chemicals += rec.get("relatedChemicals") or []
        for chemical in chemicals:
            drugs[chemical["id"]] = chemical["name"]
    return version, drugs


def code_for(name: str) -> dict:
    exact = [c for c in _concepts(name, 0) if c["tty"] in ACCEPTED]
    if exact:
        best = min(exact, key=lambda c: ACCEPTED.index(c["tty"]))
        return dict(best, match="exact")
    if " / " in name:
        normalized = _concepts(name, 2)
        if len(normalized) == 1 and normalized[0]["tty"] == "MIN":
            return dict(normalized[0], match="normalized")
    return {}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("jar", help="pharmcat.jar from the pharmcat image")
    parser.add_argument(
        "out", help="where to write the table (config/rxnorm_drugs.json)"
    )
    args = parser.parse_args(argv)

    version, drugs = pharmcat_drugs(args.jar)
    rows = {}
    for accession, name in sorted(drugs.items(), key=lambda kv: kv[1].lower()):
        if accession in REVIEWED:
            props = _get(f"rxcui/{REVIEWED[accession]}/properties.json")["properties"]
            found = {
                "rxcui": props["rxcui"],
                "name": props["name"],
                "tty": props["tty"],
                "match": "reviewed",
            }
        else:
            found = code_for(name)
        rows[accession] = {
            "name": name,
            "rxcui": found.get("rxcui"),
            "rxnorm_name": found.get("name"),
            "tty": found.get("tty"),
            "match": found.get("match"),
        }
    coded = sum(1 for row in rows.values() if row["rxcui"])
    table = {
        "pharmcat_version": version,
        "built": dt.date.today().isoformat(),
        "source": (
            "Drugs: PharmCAT's prescribing_guidance.json. Codes: NLM RxNav "
            "(RxNorm), by exact name; see scripts/build_rxnorm_drugs.py."
        ),
        "drugs": rows,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(table, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    print(f"PharmCAT {version}: {len(rows)} drugs, {coded} with an RxNorm code")
    for accession, row in rows.items():
        if not row["rxcui"] or row["match"] != "exact":
            print(
                f"  {accession} {row['name']!r}: {row['rxcui'] or 'no code'} {row['match'] or ''}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
