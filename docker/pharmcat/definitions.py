"""PharmCAT's allele definitions, in the small shape the report needs.

PharmCAT's jar carries, per gene, ``definition/alleles/<GENE>_translation.json`` -- every
named allele with the base each of the gene's positions must carry for that allele to fit
-- and ``phenotype/<GENE>.json`` -- every diplotype's phenotype. Together they are the
only thing that can answer what else the same genotypes could have been called, which is
what ``app/reports/pharmcat_phase.py`` asks for a call resting on an arrangement nothing
measured.

Those two sets are 6.5 MB and 36 MB of JSON, nearly all of it provenance and prose. This
writes the part the question needs -- positions, each allele's required bases and score,
and each diplotype's phenotype -- as one gzipped file of about 430 kB, next to the run
data both containers share.

It lives here, and the file is written at runtime rather than committed, because the
definitions are ClinPGx's (CC-BY-SA-4.0, as the Credits page records), carried by the
PharmCAT image this project already distributes. Deriving them into the repository would
put adapted CC-BY-SA data under this repository's own license, which is not a decision
for a report feature to take.

Standalone by requirement: the pharmcat image has no ``app`` package
(tests/test_sidecar_standalone_modules.py polices the class), so this imports nothing
from it and nothing outside the standard library.
"""

from __future__ import annotations

import datetime
import gzip
import json
import logging
import os
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

#: The jar start.sh puts in place; every definition read here comes out of it.
JAR = Path(os.getenv("PHARMCAT_JAR", "/pharmcat/pharmcat.jar"))

#: Where the app reads it from. Both containers mount the same ``./data``.
DIGEST = Path(os.getenv("PHARMCAT_DEFINITIONS", "/data/pharmcat/allele-definitions.json.gz"))

#: Inside the jar.
DEFINITION_ENTRY = "org/pharmgkb/pharmcat/definition/alleles/{gene}_translation.json"
PHENOTYPE_ENTRY = "org/pharmgkb/pharmcat/phenotype/{gene}.json"
DEFINITION_PREFIX = "org/pharmgkb/pharmcat/definition/alleles/"
DEFINITION_SUFFIX = "_translation.json"

#: Bumped when the shape below changes, so an app reading an older file can refuse it
#: rather than mis-read it.
FORMAT = 1


def genes_in(jar: zipfile.ZipFile) -> Iterable[str]:
    """Every gene the jar carries allele definitions for."""
    for name in jar.namelist():
        if name.startswith(DEFINITION_PREFIX) and name.endswith(DEFINITION_SUFFIX):
            gene = name[len(DEFINITION_PREFIX) : -len(DEFINITION_SUFFIX)]
            if gene and "/" not in gene:
                yield gene


def _entry(jar: zipfile.ZipFile, name: str) -> Optional[dict]:
    try:
        with jar.open(name) as fh:
            data = json.load(fh)
    except KeyError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("Could not read %s from the jar: %s", name, exc)
        return None
    return data if isinstance(data, dict) else None


def definition_of(data: dict) -> Dict[str, Any]:
    """One gene's positions and named alleles, as the report reads them.

    ``w`` holds only the positions an allele is specified at, keyed by their index in
    ``loci``: ``*28`` asks one thing of UGT1A1's four positions, and writing the other
    three as nulls on every allele of every gene is most of the file.
    """
    loci = [
        {"c": variant.get("chromosome"), "p": variant.get("position"), "r": variant.get("rsid")}
        for variant in data.get("variants") or []
        if isinstance(variant, dict)
    ]
    alleles = []
    for allele in data.get("namedAlleles") or []:
        if not isinstance(allele, dict) or not allele.get("name"):
            continue
        bases = allele.get("alleles")
        if not isinstance(bases, list):
            continue
        entry: Dict[str, Any] = {
            "n": str(allele["name"]),
            "s": int(allele.get("score") or 0),
            "w": {
                str(index): base
                for index, base in enumerate(bases)
                if base is not None
            },
        }
        if allele.get("reference"):
            entry["ref"] = True
        alleles.append(entry)
    return {"version": data.get("version"), "loci": loci, "alleles": alleles}


def phenotypes_of(data: dict) -> Dict[str, str]:
    """``"*1/*3A" -> "Intermediate Metabolizer"``, keyed on the two names sorted.

    Sorted as strings, so ``*10`` sorts before ``*2``. That is only a key: the reader
    sorts the same way, and nothing reads these keys as an ordering.
    """
    out: Dict[str, str] = {}
    for row in data.get("diplotypes") or []:
        if not isinstance(row, dict):
            continue
        key = row.get("diplotypekey")
        if not isinstance(key, dict):
            continue
        names = []
        for name, count in sorted(key.items()):
            try:
                names.extend([str(name)] * int(count))
            except (TypeError, ValueError):
                names = []
                break
        if len(names) != 2:
            continue
        phenotype = row.get("phenotype") or row.get("generesult")
        if phenotype:
            out["/".join(sorted(names))] = str(phenotype)
    return out


def build(jar_path: Path = JAR, version: Optional[str] = None) -> Dict[str, Any]:
    """The whole digest, read out of the jar."""
    genes: Dict[str, Any] = {}
    with zipfile.ZipFile(jar_path) as jar:
        for gene in sorted(genes_in(jar)):
            definition = _entry(jar, DEFINITION_ENTRY.format(gene=gene))
            if not definition:
                continue
            entry = definition_of(definition)
            if not entry["loci"] or not entry["alleles"]:
                continue
            phenotype = _entry(jar, PHENOTYPE_ENTRY.format(gene=gene))
            if phenotype:
                entry["phenotypes"] = phenotypes_of(phenotype)
            genes[gene] = entry
    return {
        "format": FORMAT,
        "source": "PharmCAT",
        "license": "Allele definitions and phenotypes from ClinPGx (PharmGKB), CC-BY-SA-4.0",
        "pharmcat_version": version or os.getenv("PHARMCAT_VERSION") or "",
        "built": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "genes": genes,
    }


def stamp_of(path: Path) -> Optional[str]:
    """The PharmCAT version a digest already on disk was built from, or None."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            head = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(head, dict) or head.get("format") != FORMAT:
        return None
    return str(head.get("pharmcat_version") or "")


def write(digest: Dict[str, Any], path: Path = DIGEST) -> None:
    """Written beside the run data, atomically: the app may be reading the old one."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=9) as fh:
        json.dump(digest, fh, separators=(",", ":"), sort_keys=True)
    os.replace(temporary, path)


def publish(jar_path: Path = JAR, path: Path = DIGEST) -> Optional[Path]:
    """Write the digest unless one from this PharmCAT is already there.

    Called at startup. Never raises: a report without the digest says nothing about
    arrangements, which is the state every release before this one shipped in.
    """
    try:
        jar_path, path = Path(jar_path), Path(path)
        if not jar_path.exists():
            logger.info("No PharmCAT jar at %s; allele definitions not published", jar_path)
            return None
        wanted = os.getenv("PHARMCAT_VERSION") or ""
        if path.exists():
            have = stamp_of(path)
            if have is not None and have == wanted:
                logger.info("Allele definitions already published for %s", have or "this jar")
                return path
        digest = build(jar_path, wanted)
        if not digest["genes"]:
            logger.warning("The PharmCAT jar carried no allele definitions")
            return None
        write(digest, path)
        logger.info(
            "Published allele definitions for %d genes to %s", len(digest["genes"]), path
        )
        return path
    except Exception as exc:  # the report is worth more than the note it loses
        logger.warning("Could not publish PharmCAT's allele definitions: %s", exc)
        return None


if __name__ == "__main__":  # what start.sh runs, and how to regenerate it by hand
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import sys

    jar = Path(sys.argv[1]) if len(sys.argv) > 1 else JAR
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else DIGEST
    written = publish(jar, out)
    if written is None:
        raise SystemExit(1)
    print(f"allele definitions at {written} ({written.stat().st_size / 1e3:.0f} kB)")
