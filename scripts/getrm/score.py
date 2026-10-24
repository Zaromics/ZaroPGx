"""Score ZaroPGx's calls for GeT-RM's reference samples against GeT-RM's consensus.

    python scripts/getrm/score.py RESULTS_DIR [--out DIR]

RESULTS_DIR holds one directory per sample, as run.py writes it: the PharmCAT JSON
(pharmcat.json), PyPGx's result (pypgx_result.json) and OptiType's (hla_result.json).
Writes calls.tsv, one row per sample, gene and caller with its verdict, and summary.tsv,
the counts per gene and caller. A row's role is "report" for the call the report makes,
"second" for PyPGx's own call of a gene the report takes from PharmCAT.

A GeT-RM genotype is not a complete one, so the comparison is made only where both
sides can speak:

- "*1" in GeT-RM means no allele the study tested was found (CDC's own note). An
  allele ZaroPGx calls that the study never reported in any sample (its vocabulary,
  per gene and study) is read as that gene's reference allele before comparing; one
  GeT-RM reports that the caller cannot name (PharmCAT 3.4 has no UGT1A1 *60 and no
  NUDT15 *2) is read as the reference allele too. Either way the verdict says so.
- Alleles in parentheses (confirmed by a single assay) are compared like the rest
  and marked tentative.
- "A or B" and "A;B" are alternatives: any one agreeing is agreement.
- DPYD is compared variant by variant: GeT-RM lists HGVS variants (or, from its 2016
  study, star alleles, mapped to their variant), and PharmCAT's JSON gives the genotype
  at each of its DPYD positions. SLCO1B1 is compared at c.521T>C (rs4149056), the
  variant its function turns on, because GeT-RM's names predate PharmVar's 2021
  renaming of the gene. VKORC1 is compared at rs9923231.
- NAT2 is compared by legacy group. GeT-RM's names predate PharmVar's: a haplotype is
  named by the slow-acetylator variant it carries (341T>C *5, 590G>A *6, 857G>A *7,
  191G>A *14) or, with none, *4, the group *11, *12 and *13 belong to. PharmCAT 3.4 names
  NAT2 by PharmVar, where 341T>C on 803A is *16 and the reference haplotype is *1, so its
  names are read as their group (pharmcat_alleles.tsv's legacy column) before comparing.
- HLA is compared at two fields; "B*40:06/11" is 40:06 or 40:11.
- A copy-number state GeT-RM never reported for a gene and study, a one-copy deletion
  or extra copies, was not tested there: GSTM1's assays saw a deletion only when both
  copies were gone, CYP2A6's no duplications. A call with such a state is compared as if
  the copies were there (GSTM1 *0/*B as *B/*B, CYP2A6 *1x2/*1 as *1/*1), and said.

Verdicts: match; match_projected (after the reading above; the note says what was
set aside); discordant; caller_cannot_name (discordant, and GeT-RM's call has an allele
the caller has no definition of); no_call; not_scored (a GeT-RM value this script
cannot read, or a gene no caller here calls).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

# Legacy names to today's, where only the name changed.
RENAME = {
    ("CYP2C19", "*38"): "*1",  # PharmCAT's reference haplotype; GeT-RM's *1 covers it
    ("CYP1A2", "*1"): "*1A",  # GeT-RM's "nothing tested found"; PyPGx's reference is *1A
    ("NAT2", "*11"): "*4",  # no slow variant: the *4 group (see the docstring)
    ("NAT2", "*12"): "*4",
    ("NAT2", "*13"): "*4",
}
# GeT-RM's 2016 study named DPYD alleles by star; PharmCAT names them by variant.
DPYD_STARS = {
    "*2A": "c.1905+1G>A", "*4": "c.1601G>A", "*5": "c.1627A>G", "*6": "c.2194G>A",
    "*9": "c.85T>C", "*9A": "c.85T>C", "*13": "c.1679T>G",
}
# rs4149056 (c.521T>C) in each SLCO1B1 allele GeT-RM reports, by its pre-2021 names.
SLCO1B1_521C = {"*1": 0, "*1A": 0, "*1B": 0, "*5": 1, "*14": 0, "*15": 1, "*17": 1}
SINGLE_SNVS = {"VKORC1_rs9923231": ("VKORC1", "rs9923231")}
COPY_GENES = {"GSTM1", "GSTT1"}


def load_tsv(path: Path) -> list[dict]:
    with open(path) as fh:
        lines = [line for line in fh if not line.startswith("#")]
    return list(csv.DictReader(lines, delimiter="\t"))


def pharmcat_nat2_groups() -> dict:
    """PharmCAT's NAT2 names as the legacy group each falls in, where it falls in one."""
    return {
        row["allele"]: row["legacy"]
        for row in load_tsv(HERE / "pharmcat_alleles.tsv")
        if row["gene"] == "NAT2" and row["legacy"]
    }


def legacy_nat2(label: str, groups: dict) -> str:
    """A NAT2 diplotype in PharmVar's names as one in the legacy groups: "*6/*16" -> "*6/*5"."""
    return re.sub(r"\*\d+", lambda m: groups.get(m.group(0), m.group(0)), label)


def vocabularies() -> tuple[dict, dict, dict]:
    """(PharmCAT alleles, PyPGx alleles, PyPGx reference allele), per gene."""
    pharmcat, pypgx, reference = defaultdict(set), defaultdict(set), {}
    for row in load_tsv(HERE / "pharmcat_alleles.tsv"):
        pharmcat[row["gene"]].add(row["allele"])
    for row in csv.DictReader(open(ROOT / "lexicon" / "allele-table.csv")):
        pypgx[row["Gene"]].add(row["StarAllele"])
    for row in csv.DictReader(open(ROOT / "lexicon" / "gene-table.csv")):
        reference[row["Gene"]] = row["RefAllele"]
    return pharmcat, pypgx, reference


# --- diplotype parsing -----------------------------------------------------------


def alternatives(value: str) -> list[str]:
    """The plain diplotypes a GeT-RM value allows, its parentheses resolved.

    "A or B", "A;B" and "A,B" (outside parentheses) are alternatives; "X(Y)" or "X (Y)"
    in a haplotype is X or Y, "(X;Y)" X or Y, "(X)" X; a group after a whole diplotype,
    "*1/*1 (*27)", is that diplotype or either haplotype replaced by the group's allele.
    """
    out = []
    for alt in _split_outside_parens(value, (" or ", ";", ",")):
        tail = re.fullmatch(r"(.+/[^()]+?)\s+\(([^()]+)\)", alt)
        if tail:
            left, right = tail.group(1).split("/", 1)
            alt_hap = tail.group(2)
            out += [f"{left}/{right}", f"{left}/{alt_hap}", f"{alt_hap}/{right}"]
            continue
        halves = alt.split("/") if alt.count("/") == 1 else None
        if not halves:
            out.append(alt.replace("(", "").replace(")", ""))
            continue
        options = [_haplotype_options(h) for h in halves]
        out += [f"{x}/{y}" for x in options[0] for y in options[1]]
    return [o.strip() for o in out if o.strip()]


def _split_outside_parens(text: str, seps: tuple) -> list[str]:
    parts, depth, cur, i = [], 0, "", 0
    while i < len(text):
        sep = next((s for s in seps if depth == 0 and text.startswith(s, i)), None)
        if sep:
            parts.append(cur)
            cur, i = "", i + len(sep)
            continue
        depth += {"(": 1, ")": -1}.get(text[i], 0)
        cur, i = cur + text[i], i + 1
    return [p.strip() for p in parts + [cur] if p.strip()]


def _haplotype_options(text: str) -> list[str]:
    text = text.strip()
    m = re.fullmatch(r"([^()]+?)\s*\(([^()]+)\)", text)  # X(Y): X or Y
    if m:
        return [m.group(1).strip()] + [o.strip() for o in re.split(r"[;,]", m.group(2))]
    m = re.fullmatch(r"\(([^()]+)\)", text)  # (X;Y): X or Y; (X): X
    if m and not "+" in text:
        return [o.strip() for o in re.split(r"[;,]", m.group(1))]
    return [text.replace("(", "").replace(")", "")]


def haplotype(text: str, gene: str) -> Counter | None:
    """A haplotype as a count of its components: "*36x2+*10" -> {*36: 2, *10: 1}; None
    for one this cannot read (CYP2B6's "*1 *27", "*1*5")."""
    out = Counter()
    for part in text.split("+"):
        part = part.strip().replace("×", "x")
        if not part:
            continue
        m = re.fullmatch(r"(.+?)x(\d+|N)", part, re.IGNORECASE)
        name, n = (m.group(1), m.group(2)) if m else (part, "1")
        if name == "WT":
            name = "*1"
        elif re.fullmatch(r"[A-Z]\d+[A-Z]", name):  # a protein change: PyPGx's "*K432Q"
            name = "*" + name
        if not re.fullmatch(r"\*?[A-Za-z0-9.]+", name) or name.count("*") > 1:
            return None
        name = RENAME.get((gene, name), name)
        out[name] += 2 if n.upper() == "N" else int(n)
    return out


def diplotype(text: str, gene: str) -> tuple[Counter, Counter] | None:
    """Two haplotypes, or None for a value that is not a diplotype."""
    text = text.replace("(", "").replace(")", "").strip()
    if gene in COPY_GENES:
        if text == "0":
            text = "*0/*0"
        text = "/".join("*0" if h.strip() in ("0", "DEL") else h for h in text.split("/"))
        text = re.sub(r"(\*[A-Z])XN", r"\1x2", text)
    halves = text.split("/")
    if len(halves) != 2 or not all(h.strip() for h in halves):
        return None
    pair = haplotype(halves[0], gene), haplotype(halves[1], gene)
    return None if None in pair else pair


def project(hap: Counter, keep: set, ref: str) -> tuple[Counter, set]:
    """The haplotype with every component outside `keep` dropped, and what was dropped;
    a haplotype left empty is the reference allele."""
    dropped = {c for c in hap if c not in keep}
    kept = Counter({c: n for c, n in hap.items() if c in keep})
    return (kept or Counter({ref: 1})), dropped


def components(haps, ref: str) -> Counter:
    """The alleles a diplotype carries besides the reference, with their copies."""
    out = Counter()
    for h in haps:
        out.update(h)
    del out[ref]
    return out


def same(a: tuple[Counter, Counter], b: tuple[Counter, Counter]) -> bool:
    key = lambda h: tuple(sorted(h.items()))  # noqa: E731
    return sorted(map(key, a)) == sorted(map(key, b))


# --- ZaroPGx's calls -------------------------------------------------------------


def pharmcat_genes(result: dict) -> dict:
    genes = result.get("genes") or {}
    if genes and all(isinstance(v, dict) and "callSource" not in v for v in genes.values()):
        merged = {}
        for per_source in genes.values():
            merged.update(per_source)
        return merged
    return genes


def calls_for(sample_dir: Path) -> dict:
    """{gene: [(caller, [diplotype strings], gene entry or None)]}."""
    out = defaultdict(list)
    pc = sample_dir / "pharmcat.json"
    if pc.exists():
        for gene, entry in pharmcat_genes(json.loads(pc.read_text())).items():
            labels = [d.get("label") for d in entry.get("sourceDiplotypes") or [] if d.get("label")]
            source = "pharmcat"
            if entry.get("callSource") == "OUTSIDE":
                source = "optitype" if gene.startswith("HLA-") else "pypgx"
            out[gene].append(("report", source, labels, entry))
    py = sample_dir / "pypgx_result.json"
    if py.exists():
        for gene, entry in (json.loads(py.read_text()).get("results") or {}).items():
            dip = entry.get("diplotype")
            labels = [d for d in (dip or "").split(";") if d and d not in ("Indeterminate", "None")]
            out[gene].append(("pypgx", "pypgx", labels, entry))
    hla = sample_dir / "hla_result.json"
    if hla.exists():
        for locus, pair in (json.loads(hla.read_text()).get("results") or {}).items():
            out[locus].append(("report", "optitype", [pair.replace(",", "/")], None))
    return out


def report_callers(calls: dict, gene: str) -> list:
    """The report's call, and PyPGx's own where it is a different caller ("second").
    A gene PharmCAT does not report is PyPGx's alone, so its call is the report's."""
    rows = calls.get(gene, [])
    report = [r for r in rows if r[0] == "report"]
    pypgx = [r for r in rows if r[0] == "pypgx"]
    if not report:
        return [("report",) + r[1:] for r in pypgx]
    if report[0][1] == "pypgx":
        return report
    return report + [("second",) + r[1:] for r in pypgx]


# --- scoring ---------------------------------------------------------------------


def variant_counts(entry: dict) -> dict:
    """PharmCAT's genotype at each position of a gene: {allele name: alt allele count}."""
    out = {}
    for v in entry.get("variants") or []:
        call = (v.get("call") or "").replace("|", "/")
        if "/" not in call:
            continue
        alt = sum(1 for a in call.split("/") if a != v.get("referenceAllele"))
        for name in v.get("alleles") or []:
            for part in name.split(" (")[0].split(", "):
                out.setdefault(part.strip(), alt)
        if v.get("dbSnpId"):
            out[v["dbSnpId"]] = alt
    return out


def dpyd_truth(value: str) -> Counter | None:
    """GeT-RM's DPYD genotype as {HGVS variant: copies}."""
    value = value.replace("(", "").replace(")", "")
    if "/" in value and "het" not in value and "hom" not in value:
        out = Counter()
        for half in value.split("/"):
            half = half.strip()
            if half in ("*1", "no variants detected", "no variant detected"):
                continue
            for token in half.split(","):
                token = DPYD_STARS.get(token.strip(), token.strip())
                if not token.startswith("c."):
                    return None
                out[token] += 1
        return out
    out = Counter()
    for token in value.split(","):
        m = re.fullmatch(r"\s*(c\.\S+)\s+(het|hom)\s*", token)
        if not m:
            return None
        out[m.group(1)] += 1 if m.group(2) == "het" else 2
    return out


def score_dpyd(truth: str, entry: dict | None, tested: set) -> tuple[str, str, str]:
    want = dpyd_truth(truth)
    if want is None:
        return "not_scored", "", "GeT-RM value not read"
    if entry is None:
        return "no_call", "", ""
    have = variant_counts(entry)
    unknown = sorted(v for v in tested if v not in have)
    diffs = [
        f"{v}: GeT-RM {want.get(v, 0)}, ZaroPGx {have[v]}"
        for v in sorted(tested)
        if v in have and want.get(v, 0) != have[v]
    ]
    called = ", ".join(f"{v} x{have[v]}" for v in sorted(tested) if have.get(v))
    note = "; ".join(diffs) + (f" (no PharmCAT position for {', '.join(unknown)})" if unknown else "")
    return ("discordant" if diffs else "match"), called or "no listed variant", note


def score_count(want: int | None, have: int | None, label: str) -> tuple[str, str, str]:
    if want is None:
        return "not_scored", "", "GeT-RM allele with no known c.521T>C status"
    if have is None:
        return "no_call", "", ""
    verdict = "match" if want == have else "discordant"
    return verdict, f"{label} x{have}", "" if verdict == "match" else f"GeT-RM {label} x{want}"


def score_star(truth: str, gene: str, labels: list, caller_vocab: set, tested: set, ref: str):
    if not labels:
        return "no_call", "", ""
    wants = [d for d in (diplotype(t, gene) for t in alternatives(truth))]
    if not wants or any(w is None for w in wants):
        return "not_scored", " | ".join(labels), "GeT-RM value not read"
    haves = [d for d in (diplotype(label.replace(" ", ""), gene) for label in labels) if d]
    if not haves:
        return "not_scored", " | ".join(labels), "call not read"
    called = " | ".join(labels)
    if any(same(w, h) for w in wants for h in haves):
        return "match", called, ""
    keep = (tested & caller_vocab) | {ref}
    unnamed = set()
    for w in wants:
        pw = tuple(project(h, keep, ref) for h in w)
        for h, untested in (untested_copies(h, tested) for h in haves):
            ph = tuple(project(x, keep, ref) for x in h)
            set_aside = set().union(*(d for _, d in ph)) - {ref}
            missing = set().union(*(d for _, d in pw)) - {ref}
            if same(tuple(p for p, _ in pw), tuple(p for p, _ in ph)):
                why = [untested] if untested else []
                if set_aside:
                    why.append("not tested by GeT-RM: " + ", ".join(sorted(set_aside)))
                if missing:
                    why.append("not defined by the caller: " + ", ".join(sorted(missing)))
                return "match_projected", called, "; ".join(why)
            # The caller may be naming an allele it has no definition of by one it has
            # (NUDT15 *2 as *3): only if GeT-RM's call, set aside, has nothing the caller
            # could have named and did not (UGT1A1 *27 lost beside *28+*60 is an error).
            lost = {c for c in missing if c not in caller_vocab}
            if lost and not components((p for p, _ in pw), ref) - components((p for p, _ in ph), ref):
                unnamed |= lost
    if unnamed:
        return "caller_cannot_name", called, "caller has no " + ", ".join(sorted(unnamed))
    return "discordant", called, ""


# Marks in a study's vocabulary (tested_alleles) for the copy-number states it reported.
HET_DELETION, EXTRA_COPIES = "<one copy deleted>", "<extra copies>"


def untested_copies(pair: tuple[Counter, Counter], tested: set) -> tuple[tuple, str]:
    """The call with the copy-number states GeT-RM did not test undone, and what was."""
    a, b = pair
    said = []
    if EXTRA_COPIES not in tested and any(n > 1 for h in pair for n in h.values()):
        a, b = (Counter({c: 1 for c in h}) for h in (a, b))
        said.append("extra copies")
    deleted = Counter({"*0": 1})
    if HET_DELETION not in tested and (a == deleted) != (b == deleted):
        a, b = (b, b) if a == deleted else (a, a)
        said.append("a one-copy deletion")
    return (a, b), (" and ".join(said) + " not tested by GeT-RM") if said else ""


def two_field(allele: str) -> str:
    """"HLA-A*02:01:01", "A*02:01" and "*02:01" all as "02:01"."""
    m = re.search(r"\*?(\d+):(\d+)", allele)
    return f"{m.group(1)}:{m.group(2)}" if m else allele.strip()


def hla_options(truth: str) -> list[set]:
    """GeT-RM's HLA pair as each allele's two-field readings: "B*40:06/11/B*51:06" ->
    [{40:06, 40:11}, {51:06}]."""
    out = []
    for token in truth.split("/"):
        token = token.strip()
        if re.fullmatch(r"\d+", token) and out:
            out[-1] |= {f"{next(iter(out[-1])).split(':')[0]}:{token}"}
        else:
            out.append({two_field(token)})
    return out


def score_hla(truth: str, labels: list) -> tuple[str, str, str]:
    if not labels:
        return "no_call", "", ""
    want = hla_options(truth)
    have = sorted(two_field(a) for a in labels[0].split("/"))
    agree = len(want) == 2 and len(have) == 2 and (
        (have[0] in want[0] and have[1] in want[1]) or (have[0] in want[1] and have[1] in want[0])
    )
    return ("match" if agree else "discordant"), "/".join(have), ""


def tested_alleles(truth_rows: list) -> dict:
    """The alleles GeT-RM reported in any sample, per gene and study: its vocabulary."""
    vocab = defaultdict(set)
    for row in truth_rows:
        gene, key = row["gene"], (row["gene"], row["source"])
        if gene == "DPYD":
            parsed = dpyd_truth(row["truth"])
            vocab[key] |= set(parsed or ())
            continue
        for alt in alternatives(row["truth"]):
            d = diplotype(alt, gene)
            for h in d or ():
                vocab[key] |= set(h)
                if sum(h.values()) > 1:
                    vocab[key].add(EXTRA_COPIES)
            if d and (d[0] == Counter({"*0": 1})) != (d[1] == Counter({"*0": 1})):
                vocab[key].add(HET_DELETION)
    return vocab


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("results", type=Path)
    ap.add_argument("--truth", type=Path, default=HERE / "truth.tsv")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    out_dir = args.out or args.results

    pharmcat_vocab, pypgx_vocab, refs = vocabularies()
    nat2_groups = pharmcat_nat2_groups()
    truth = load_tsv(args.truth)
    tested = tested_alleles(truth)
    samples = {p.name for p in args.results.iterdir() if p.is_dir()}

    rows = []
    for t in truth:
        sample, gene, value = t["sample"], t["gene"], t["truth"]
        if sample not in samples:
            continue
        calls = calls_for(args.results / sample)
        tentative = "yes" if "(" in value else ""
        vocab_tested = tested[(gene, t["source"])]
        if gene.startswith("HLA-"):
            got = report_callers(calls, gene)
            scored = [("report", "optitype") + score_hla(value, got[0][2] if got else [])]
        elif gene in SINGLE_SNVS:
            g, rsid = SINGLE_SNVS[gene]
            entry = next((r[3] for r in calls.get(g, []) if r[0] == "report"), None)
            want = value.replace("/", "").upper().count("A") if re.fullmatch(r"[GA]/?[GA]", value) else None
            have = variant_counts(entry).get(rsid) if entry else None
            scored = [("report", "pharmcat") + score_count(want, have, rsid)]
        elif gene == "DPYD":
            entry = next((r[3] for r in calls.get(gene, []) if r[0] == "report"), None)
            scored = [("report", "pharmcat") + score_dpyd(value, entry, vocab_tested)]
        elif gene == "SLCO1B1":
            entry = next((r[3] for r in calls.get(gene, []) if r[0] == "report"), None)
            wants = set()
            for alt in alternatives(value):
                d = diplotype(alt, gene)
                parts = [c for h in (d or ()) for c in h]
                wants.add(sum(SLCO1B1_521C[c] for c in parts) if d and all(c in SLCO1B1_521C for c in parts) else None)
            want = wants.pop() if len(wants) == 1 else None
            have = variant_counts(entry).get("rs4149056") if entry else None
            scored = [("report", "pharmcat") + score_count(want, have, "c.521T>C")]
        elif gene in calls:
            scored = []
            for role, source, labels, _ in report_callers(calls, gene):
                vocab = pharmcat_vocab[gene] if source == "pharmcat" else pypgx_vocab[gene]
                legacy = labels
                if gene == "NAT2" and source == "pharmcat":
                    legacy = [legacy_nat2(label, nat2_groups) for label in labels]
                    vocab = vocab | set(nat2_groups.values())
                verdict, called, note = score_star(value, gene, legacy, vocab, vocab_tested, refs.get(gene, "*1"))
                if legacy != labels:
                    called = " | ".join(labels)
                    note = "; ".join(filter(None, [f"read as {' | '.join(legacy)}", note]))
                scored.append(("report" if role == "report" else "second", source, verdict, called, note))
        else:
            scored = [("", "", "not_scored", "", "no caller here calls this gene")]
        for role, caller, verdict, called, note in scored:
            rows.append((sample, gene, value, tentative, caller, called, verdict, note, role))

    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "calls.tsv", "w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(("sample", "gene", "getrm", "tentative", "caller", "zaropgx", "verdict", "note", "role"))
        w.writerows(rows)
    counts = defaultdict(Counter)
    for sample, gene, _, _, caller, _, verdict, _, role in rows:
        counts[(gene, caller, role)][verdict] += 1
    verdicts = ["match", "match_projected", "discordant", "caller_cannot_name", "no_call", "not_scored"]
    with open(out_dir / "summary.tsv", "w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n")
        w.writerow(("gene", "caller", "role", *verdicts, "concordance"))
        for (gene, caller, role), c in sorted(counts.items()):
            agree, scored = c["match"] + c["match_projected"], sum(c[v] for v in verdicts[:4])
            w.writerow((gene, caller, role, *(c[v] for v in verdicts), f"{agree / scored:.3f}" if scored else ""))
    print(f"{len(rows)} rows -> {out_dir / 'calls.tsv'}, {out_dir / 'summary.tsv'}")


if __name__ == "__main__":
    main()
