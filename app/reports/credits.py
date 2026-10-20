"""Who and what made a report: every tool and data source a run used.

One list feeds every place ZaroPGx credits its parts: each report's Platform and
Citations section, which names only what that run used, and the docs' Credits page,
which names everything. Each entry says what the part does here, where its version
comes from, the papers its authors ask to be cited by (each project's own "how to
cite"; DOIs checked against Crossref on 2026-10-12) and any notice its terms require.

References are AMA style: up to six authors are listed, more become the first three
and "et al".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from markupsafe import Markup

# ---------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Paper:
    """One reference.

    ``source`` is a journal (set in italics) or, with ``publisher``, the publisher
    or repository of a book, thesis or data set; ``detail`` follows it:
    "2020;107(1):203–210" for an article, the year for the rest.
    """

    authors: Tuple[str, ...]
    title: str
    source: str = ""
    detail: str = ""
    doi: str = ""
    url: str = ""  # only without a DOI
    kind: str = (
        ""  # "Preprint", "dissertation", "Data set": in brackets after the title
    )
    publisher: bool = False
    edition: str = ""
    group: str = ""  # a consortium author
    group_first: bool = False  # the consortium leads the byline

    @property
    def key(self) -> str:
        return self.doi or self.url or self.title

    @property
    def byline(self) -> str:
        names = list(self.authors)
        listed = names if len(names) <= 6 else names[:3] + ["et al"]
        text = ", ".join(listed)
        if self.group and self.group_first:
            return f"{self.group}; {text}" if text else self.group
        if self.group and len(names) <= 6:
            return f"{text}; {self.group}" if text else self.group
        return text

    def _segments(self) -> Tuple[str, str, str]:
        """(text before the source, the source, text after it)."""
        before = self.title + (f" [{self.kind}]" if self.kind else "") + "."
        if self.byline:
            before = f"{self.byline}. {before}"
        if self.edition:
            before += f" {self.edition} ed."
        after = ""
        if self.source:
            after = (
                (f"; {self.detail}." if self.publisher else f". {self.detail}.")
                if self.detail
                else "."
            )
        elif self.detail:
            after = f" {self.detail}."
        return before, self.source, after

    def text(self) -> str:
        before, source, after = self._segments()
        out = f"{before} {source}{after}" if source else f"{before}{after}"
        if self.doi:
            out += f" doi:{self.doi}"
        elif self.url:
            out += f" {self.url}"
        return out

    def html(self) -> Markup:
        before, source, after = self._segments()
        if source and not self.publisher:
            out = Markup("{} <em>{}</em>{}").format(before, source, after)
        elif source:
            out = Markup("{} {}{}").format(before, source, after)
        else:
            out = Markup("{}{}").format(before, after)
        if self.doi:
            out += Markup(' doi:<a href="https://doi.org/{0}">{0}</a>').format(self.doi)
        elif self.url:
            out += Markup(' <a href="{0}">{0}</a>').format(self.url)
        return out


CPT = "Clinical Pharmacology & Therapeutics"
NAR = "Nucleic Acids Research"

# PharmCAT ------------------------------------------------------------------
SANGKUHL_2020 = Paper(
    (
        "Sangkuhl K",
        "Whirl-Carrillo M",
        "Whaley RM",
        "Woon M",
        "Lavertu A",
        "Altman RB",
        "Carter L",
        "Verma A",
        "Ritchie MD",
        "Klein TE",
    ),
    "Pharmacogenomics Clinical Annotation Tool (PharmCAT)",
    CPT,
    "2020;107(1):203–210",
    doi="10.1002/cpt.1568",
)
LI_2023 = Paper(
    (
        "Li B",
        "Sangkuhl K",
        "Keat K",
        "Whaley RM",
        "Woon M",
        "Verma S",
        "Dudek S",
        "Tuteja S",
        "Verma A",
        "Whirl-Carrillo M",
        "Ritchie MD",
        "Klein TE",
    ),
    "How to Run the Pharmacogenomics Clinical Annotation Tool (PharmCAT)",
    CPT,
    "2023;113(5):1036–1047",
    doi="10.1002/cpt.2790",
)
KLEIN_2018 = Paper(
    ("Klein TE", "Ritchie MD"),
    "PharmCAT: A Pharmacogenomics Clinical Annotation Tool",
    CPT,
    "2018;104(1):19–22",
    doi="10.1002/cpt.928",
)
# PyPGx and fuc ---------------------------------------------------------------
CLINPHARMSEQ_2022 = Paper(
    ("Lee SB", "Shin JY", "Kwon NJ", "Kim C", "Seo JS"),
    "ClinPharmSeq: A targeted sequencing panel for clinical pharmacogenetics implementation",
    "PLOS ONE",
    "2022;17(7):e0272129",
    doi="10.1371/journal.pone.0272129",
)
STARGAZER_2019_GIM = Paper(
    (
        "Lee SB",
        "Wheeler MM",
        "Patterson K",
        "McGee S",
        "Dalton R",
        "Woodahl EL",
        "Gaedigk A",
        "Thummel KE",
        "Nickerson DA",
    ),
    "Stargazer: a software tool for calling star alleles from next-generation sequencing "
    "data using CYP2D6 as a model",
    "Genetics in Medicine",
    "2019;21(2):361–372",
    doi="10.1038/s41436-018-0054-0",
)
STARGAZER_2019_CPT = Paper(
    ("Lee SB", "Wheeler MM", "Thummel KE", "Nickerson DA"),
    "Calling Star Alleles With Stargazer in 28 Pharmacogenes With Whole Genome Sequences",
    CPT,
    "2019;106(6):1328–1337",
    doi="10.1002/cpt.1552",
)
BEAGLE_2021 = Paper(
    ("Browning BL", "Tian X", "Zhou Y", "Browning SR"),
    "Fast two-stage phasing of large-scale sequence data",
    "American Journal of Human Genetics",
    "2021;108(10):1880–1890",
    doi="10.1016/j.ajhg.2021.08.005",
)
SKLEARN_2011 = Paper(
    (
        "Pedregosa F",
        "Varoquaux G",
        "Gramfort A",
        "Michel V",
        "Thirion B",
        "Grisel O",
        "Blondel M",
        "Prettenhofer P",
        "Weiss R",
        "Dubourg V",
        "Vanderplas J",
        "Passos A",
        "Cournapeau D",
        "Brucher M",
        "Perrot M",
        "Duchesnay É",
    ),
    "Scikit-learn: Machine Learning in Python",
    "Journal of Machine Learning Research",
    "2011;12(85):2825–2830",
    url="https://jmlr.org/papers/v12/pedregosa11a.html",
)
# GATK, Picard, BWA, htslib ---------------------------------------------------
GATK_BOOK_2020 = Paper(
    ("Van der Auwera GA", "O'Connor BD"),
    "Genomics in the Cloud: Using Docker, GATK, and WDL in Terra",
    "O'Reilly Media",
    "2020",
    edition="1st",
    publisher=True,
)
MCKENNA_2010 = Paper(
    (
        "McKenna A",
        "Hanna M",
        "Banks E",
        "Sivachenko A",
        "Cibulskis K",
        "Kernytsky A",
        "Garimella K",
        "Altshuler D",
        "Gabriel S",
        "Daly M",
        "DePristo MA",
    ),
    "The Genome Analysis Toolkit: A MapReduce framework for analyzing next-generation "
    "DNA sequencing data",
    "Genome Research",
    "2010;20(9):1297–1303",
    doi="10.1101/gr.107524.110",
)
POPLIN_2017 = Paper(
    (
        "Poplin R",
        "Ruano-Rubio V",
        "DePristo MA",
        "Fennell TJ",
        "Carneiro MO",
        "Van der Auwera GA",
        "Kling DE",
        "Gauthier LD",
        "Levy-Moonshine A",
        "Roazen D",
        "Shakir K",
        "Thibault J",
        "Chandran S",
        "Whelan C",
        "Lek M",
        "Gabriel S",
        "Daly MJ",
        "Neale B",
        "MacArthur DG",
        "Banks E",
    ),
    "Scaling accurate genetic variant discovery to tens of thousands of samples",
    "bioRxiv",
    "2017:201178",
    doi="10.1101/201178",
    kind="Preprint",
)
PICARD_2019 = Paper(
    ("Broad Institute",),
    "Picard Toolkit",
    "GitHub repository",
    "2019",
    url="https://broadinstitute.github.io/picard/",
    kind="Software",
    publisher=True,
)
BWA_MEM_2013 = Paper(
    ("Li H",),
    "Aligning sequence reads, clone sequences and assembly contigs with BWA-MEM",
    "arXiv",
    "2013:1303.3997v2",
    doi="10.48550/arXiv.1303.3997",
    kind="Preprint",
)
SAMTOOLS_2021 = Paper(
    (
        "Danecek P",
        "Bonfield JK",
        "Liddle J",
        "Marshall J",
        "Ohan V",
        "Pollard MO",
        "Whitwham A",
        "Keane T",
        "McCarthy SA",
        "Davies RM",
        "Li H",
    ),
    "Twelve years of SAMtools and BCFtools",
    "GigaScience",
    "2021;10(2):giab008",
    doi="10.1093/gigascience/giab008",
)
HTSLIB_2021 = Paper(
    (
        "Bonfield JK",
        "Marshall J",
        "Danecek P",
        "Li H",
        "Ohan V",
        "Whitwham A",
        "Keane T",
        "Davies RM",
    ),
    "HTSlib: C library for reading/writing high-throughput sequencing data",
    "GigaScience",
    "2021;10(2):giab007",
    doi="10.1093/gigascience/giab007",
)
NEXTFLOW_2017 = Paper(
    ("Di Tommaso P", "Chatzou M", "Floden EW", "Barja PP", "Palumbo E", "Notredame C"),
    "Nextflow enables reproducible computational workflows",
    "Nature Biotechnology",
    "2017;35(4):316–319",
    doi="10.1038/nbt.3820",
)
# HLA -----------------------------------------------------------------------------
OPTITYPE_2014 = Paper(
    ("Szolek A", "Schubert B", "Mohr C", "Sturm M", "Feldhahn M", "Kohlbacher O"),
    "OptiType: precision HLA typing from next-generation sequencing data",
    "Bioinformatics",
    "2014;30(23):3310–3316",
    doi="10.1093/bioinformatics/btu548",
)
YARA_2013 = Paper(
    ("Siragusa E", "Weese D", "Reinert K"),
    "Fast and accurate read mapping with approximate seeds and multiple backtracking",
    NAR,
    "2013;41(7):e78",
    doi="10.1093/nar/gkt005",
)
YARA_THESIS_2015 = Paper(
    ("Siragusa E",),
    "Approximate string matching for high-throughput sequencing",
    "Freie Universität Berlin",
    "2015",
    doi="10.17169/refubium-15562",
    kind="dissertation",
    publisher=True,
)
SEQAN_2017 = Paper(
    (
        "Reinert K",
        "Dadi TH",
        "Ehrhardt M",
        "Hauswedell H",
        "Mehringer S",
        "Rahn R",
        "Kim J",
        "Pockrandt C",
        "Winkler J",
        "Siragusa E",
        "Urgese G",
        "Weese D",
    ),
    "The SeqAn C++ template library for efficient sequence analysis: A resource for "
    "programmers",
    "Journal of Biotechnology",
    "2017;261:157–168",
    doi="10.1016/j.jbiotec.2017.07.017",
)
PYOMO_BOOK_2021 = Paper(
    (
        "Bynum ML",
        "Hackebeil GA",
        "Hart WE",
        "Laird CD",
        "Nicholson BL",
        "Siirola JD",
        "Watson JP",
        "Woodruff DL",
    ),
    "Pyomo — Optimization Modeling in Python",
    "Springer",
    "2021",
    doi="10.1007/978-3-030-68928-5",
    edition="3rd",
    publisher=True,
)
PYOMO_2011 = Paper(
    ("Hart WE", "Watson JP", "Woodruff DL"),
    "Pyomo: modeling and solving mathematical programs in Python",
    "Mathematical Programming Computation",
    "2011;3(3):219–260",
    doi="10.1007/s12532-011-0026-8",
)
IMGT_HLA_2026 = Paper(
    (
        "Barker DJ",
        "Natarajan RHL",
        "Cooper MA",
        "Hopper SJF",
        "Yates AD",
        "Parham P",
        "Marsh SGE",
        "Robinson J",
    ),
    "The IPD-IMGT/HLA database: recent developments in sequence submission",
    NAR,
    "2026;54(D1):D1152–D1158",
    doi="10.1093/nar/gkaf1218",
)
IMGT_HLA_2013 = Paper(
    ("Robinson J", "Halliwell JA", "McWilliam H", "Lopez R", "Parham P", "Marsh SGE"),
    "The IMGT/HLA database",
    NAR,
    "2013;41(D1):D1222–D1227",
    doi="10.1093/nar/gks949",
)
# mtDNA -------------------------------------------------------------------------
MTDNA_SERVER_2_2024 = Paper(
    ("Weissensteiner H", "Forer L", "Kronenberg F", "Schönherr S"),
    "mtDNA-Server 2: advancing mitochondrial DNA analysis through highly parallelized "
    "data processing and interactive analytics",
    NAR,
    "2024;52(W1):W102–W107",
    doi="10.1093/nar/gkae296",
)
MTDNA_SERVER_2016 = Paper(
    (
        "Weissensteiner H",
        "Forer L",
        "Fuchsberger C",
        "Schöpf B",
        "Kloss-Brandstätter A",
        "Specht G",
        "Kronenberg F",
        "Schönherr S",
    ),
    "mtDNA-Server: next-generation sequencing data analysis of human mitochondrial DNA "
    "in the cloud",
    NAR,
    "2016;44(W1):W64–W69",
    doi="10.1093/nar/gkw247",
)
HAPLOGREP_3_2023 = Paper(
    ("Schönherr S", "Weissensteiner H", "Kronenberg F", "Forer L"),
    "Haplogrep 3 - an interactive haplogroup classification and analysis platform",
    NAR,
    "2023;51(W1):W263–W268",
    doi="10.1093/nar/gkad284",
)
HAPLOCHECK_2021 = Paper(
    (
        "Weissensteiner H",
        "Forer L",
        "Fendt L",
        "Kheirkhah A",
        "Salas A",
        "Kronenberg F",
        "Schoenherr S",
    ),
    "Contamination detection in sequencing studies using the mitochondrial phylogeny",
    "Genome Research",
    "2021;31(2):309–316",
    doi="10.1101/gr.256545.119",
)
PHYLOTREE_2009 = Paper(
    ("van Oven M", "Kayser M"),
    "Updated comprehensive phylogenetic tree of global human mitochondrial DNA variation",
    "Human Mutation",
    "2009;30(2):E386–E394",
    doi="10.1002/humu.20921",
)
PHYLOTREE_FU_2021 = Paper(
    ("Dür A", "Huber N", "Parson W"),
    "Fine-Tuning Phylogenetic Alignment and Haplogrouping of mtDNA Sequences",
    "International Journal of Molecular Sciences",
    "2021;22(11):5747",
    doi="10.3390/ijms22115747",
)
RCRS_1999 = Paper(
    (
        "Andrews RM",
        "Kubacka I",
        "Chinnery PF",
        "Lightowlers RN",
        "Turnbull DM",
        "Howell N",
    ),
    "Reanalysis and revision of the Cambridge reference sequence for human mitochondrial DNA",
    "Nature Genetics",
    "1999;23(2):147",
    doi="10.1038/13779",
)
# Reference genomes and panels -----------------------------------------------------
GRCH38_2017 = Paper(
    (
        "Schneider VA",
        "Graves-Lindsay T",
        "Howe K",
        "Bouk N",
        "Chen HC",
        "Kitts PA",
        "Murphy TD",
        "Pruitt KD",
        "Thibaud-Nissen F",
        "Albracht D",
        "Fulton RS",
        "Kremitzki M",
        "Magrini V",
        "Markovic C",
        "McGrath S",
        "Steinberg KM",
        "Auger K",
        "Chow W",
        "Collins J",
        "Harden G",
        "Hubbard T",
        "Pelan S",
        "Simpson JT",
        "Threadgold G",
        "Torrance J",
        "Wood JM",
        "Clarke L",
        "Koren S",
        "Boitano M",
        "Peluso P",
        "Li H",
        "Chin CS",
        "Phillippy AM",
        "Durbin R",
        "Wilson RK",
        "Flicek P",
        "Eichler EE",
        "Church DM",
    ),
    "Evaluation of GRCh38 and de novo haploid genome assemblies demonstrates the "
    "enduring quality of the reference assembly",
    "Genome Research",
    "2017;27(5):849–864",
    doi="10.1101/gr.213611.116",
)
GRCH37_2011 = Paper(
    (
        "Church DM",
        "Schneider VA",
        "Graves T",
        "Auger K",
        "Cunningham F",
        "Bouk N",
        "Chen HC",
        "Agarwala R",
        "McLaren WM",
        "Ritchie GRS",
        "Albracht D",
        "Kremitzki M",
    ),
    "Modernizing Reference Genome Assemblies",
    "PLOS Biology",
    "2011;9(7):e1001091",
    doi="10.1371/journal.pbio.1001091",
)
PHARMCAT_REFERENCE_2022 = Paper(
    ("Woon M",),
    "GRCh38.p13 Reference FASTA (bgzip'd with faidx)",
    "Zenodo",
    "2022",
    doi="10.5281/zenodo.7288118",
    kind="Data set",
    publisher=True,
)
UCSC_2026 = Paper(
    (
        "Casper J",
        "Speir ML",
        "Raney BJ",
        "Perez G",
        "Nassar LR",
        "Lee CM",
        "Hinrichs AS",
        "Gonzalez JN",
        "Fischer C",
        "Diekhans M",
        "Clawson H",
        "Benet-Pages A",
        "Barber GP",
        "Vaske CJ",
        "van Baren MJ",
        "Wang K",
        "Rodriguez YJP",
        "Jenkins-Kiefer JA",
        "Chalamala M",
        "Haussler D",
        "Kent WJ",
        "Haeussler M",
    ),
    "The UCSC Genome Browser database: 2026 update",
    NAR,
    "2026;54(D1):D1331–D1335",
    doi="10.1093/nar/gkaf1250",
)
UCSC_LIFTOVER_2006 = Paper(
    (
        "Hinrichs AS",
        "Karolchik D",
        "Baertsch R",
        "Barber GP",
        "Bejerano G",
        "Clawson H",
        "Diekhans M",
        "Furey TS",
        "Harte RA",
        "Hsu F",
        "Hillman-Jackson J",
        "Kuhn RM",
        "Pedersen JS",
        "Pohl A",
        "Raney BJ",
        "Rosenbloom KR",
        "Siepel A",
        "Smith KE",
        "Sugnet CW",
        "Sultan-Qurraie A",
        "Thomas DJ",
        "Trumbower H",
        "Weber RJ",
        "Weirauch M",
        "Zweig AS",
        "Haussler D",
        "Kent WJ",
    ),
    "The UCSC Genome Browser Database: update 2006",
    NAR,
    "2006;34(Database issue):D590–D598",
    doi="10.1093/nar/gkj144",
)
KGP_PHASE3_2015 = Paper(
    (
        "Auton A",
        "Brooks LD",
        "Durbin RM",
        "Garrison EP",
        "Kang HM",
        "Korbel JO",
        "Marchini JL",
        "McCarthy S",
        "McVean GA",
        "Abecasis GR",
    ),
    "A global reference for human genetic variation",
    "Nature",
    "2015;526(7571):68–74",
    doi="10.1038/nature15393",
    group="1000 Genomes Project Consortium",
    group_first=True,
)
KGP_30X_2022 = Paper(
    (
        "Byrska-Bishop M",
        "Evani US",
        "Zhao X",
        "Basile AO",
        "Abel HJ",
        "Regier AA",
        "Corvelo A",
        "Clarke WE",
        "Musunuri R",
        "Nagulapalli K",
        "Fairley S",
        "Runnels A",
        "Winterkorn L",
        "Lowy E",
        "Flicek P",
        "Germer S",
        "Brand H",
        "Hall IM",
        "Talkowski ME",
        "Narzisi G",
        "Zody MC",
    ),
    "High-coverage whole-genome sequencing of the expanded 1000 Genomes Project cohort "
    "including 602 trios",
    "Cell",
    "2022;185(18):3426–3440.e19",
    doi="10.1016/j.cell.2022.08.004",
)
KGP_GRCH38_2017 = Paper(
    (
        "Zheng-Bradley X",
        "Streeter I",
        "Fairley S",
        "Richardson D",
        "Clarke L",
        "Flicek P",
    ),
    "Alignment of 1000 Genomes Project reads to reference assembly GRCh38",
    "GigaScience",
    "2017;6(7):1–8",
    doi="10.1093/gigascience/gix038",
    group="1000 Genomes Project Consortium",
)
# Knowledge -----------------------------------------------------------------------
CPIC_2025 = Paper(
    (
        "Caudle KE",
        "Whirl-Carrillo M",
        "Relling MV",
        "Hoffman JM",
        "Donnelly RS",
        "Haidar CE",
        "Bourque MS",
        "Frear S",
        "Gong L",
        "Sangkuhl K",
        "Whaley R",
        "Klein TE",
    ),
    "Advancing Clinical Pharmacogenomics Worldwide Through the Clinical "
    "Pharmacogenetics Implementation Consortium (CPIC)",
    CPT,
    "2025;118(6):1512–1522",
    doi="10.1002/cpt.70005",
)
DPWG_2008 = Paper(
    (
        "Swen JJ",
        "Wilting I",
        "de Goede AL",
        "Grandia L",
        "Mulder H",
        "Touw DJ",
        "de Boer A",
        "Conemans JMH",
        "Egberts TCG",
        "Klungel OH",
        "Koopmans R",
        "van der Weide J",
        "Wilffert B",
        "Guchelaar HJ",
        "Deneer VHM",
    ),
    "Pharmacogenetics: From Bench to Byte",
    CPT,
    "2008;83(5):781–787",
    doi="10.1038/sj.clpt.6100507",
)
DPWG_2011 = Paper(
    (
        "Swen JJ",
        "Nijenhuis M",
        "de Boer A",
        "Grandia L",
        "Maitland-van der Zee AH",
        "Mulder H",
        "Rongen GAPJM",
        "van Schaik RHN",
        "Schalekamp T",
        "Touw DJ",
        "van der Weide J",
        "Wilffert B",
        "Deneer VHM",
        "Guchelaar HJ",
    ),
    "Pharmacogenetics: From Bench to Byte—An Update of Guidelines",
    CPT,
    "2011;89(5):662–673",
    doi="10.1038/clpt.2011.34",
)
FDA_ASSOCIATIONS = Paper(
    ("U.S. Food and Drug Administration",),
    "Table of Pharmacogenetic Associations",
    url="https://www.fda.gov/medical-devices/precision-medicine/table-pharmacogenetic-associations",
)
FDA_BIOMARKERS = Paper(
    ("U.S. Food and Drug Administration",),
    "Table of Pharmacogenomic Biomarkers in Drug Labeling",
    url="https://www.fda.gov/drugs/science-and-research-drugs/table-pharmacogenomic-biomarkers-drug-labeling",
)
CLINPGX_2021 = Paper(
    (
        "Whirl-Carrillo M",
        "Huddart R",
        "Gong L",
        "Sangkuhl K",
        "Thorn CF",
        "Whaley R",
        "Klein TE",
    ),
    "An Evidence-Based Framework for Evaluating Pharmacogenomics Knowledge for "
    "Personalized Medicine",
    CPT,
    "2021;110(3):563–572",
    doi="10.1002/cpt.2350",
)
PHARMVAR_2021 = Paper(
    ("Gaedigk A", "Casey ST", "Whirl-Carrillo M", "Miller NA", "Klein TE"),
    "Pharmacogene Variation Consortium: A Global Resource and Repository for "
    "Pharmacogene Variation",
    CPT,
    "2021;110(3):542–545",
    doi="10.1002/cpt.2321",
)
PHARMVAR_2020 = Paper(
    ("Gaedigk A", "Whirl-Carrillo M", "Pratt VM", "Miller NA", "Klein TE"),
    "PharmVar and the Landscape of Pharmacogenetic Resources",
    CPT,
    "2020;107(1):43–46",
    doi="10.1002/cpt.1654",
)
PHARMVAR_2019 = Paper(
    (
        "Gaedigk A",
        "Sangkuhl K",
        "Whirl-Carrillo M",
        "Twist GP",
        "Klein TE",
        "Miller NA",
    ),
    "The Evolution of PharmVar",
    CPT,
    "2019;105(1):29–32",
    doi="10.1002/cpt.1275",
    group="PharmVar Steering Committee",
)
PHARMVAR_2018 = Paper(
    (
        "Gaedigk A",
        "Ingelman-Sundberg M",
        "Miller NA",
        "Leeder JS",
        "Whirl-Carrillo M",
        "Klein TE",
    ),
    "The Pharmacogene Variation (PharmVar) Consortium: Incorporation of the Human "
    "Cytochrome P450 (CYP) Allele Nomenclature Database",
    CPT,
    "2018;103(3):399–401",
    doi="10.1002/cpt.910",
    group="PharmVar Steering Committee",
)
DBSNP_2025 = Paper(
    (
        "Phan L",
        "Zhang H",
        "Wang Q",
        "Villamarin R",
        "Hefferon T",
        "Ramanathan A",
        "Kattman B",
    ),
    "The evolution of dbSNP: 25 years of impact in genomic research",
    NAR,
    "2025;53(D1):D925–D931",
    doi="10.1093/nar/gkae977",
)
# Report rendering ------------------------------------------------------------------
D3_2011 = Paper(
    ("Bostock M", "Ogievetsky V", "Heer J"),
    "D³ Data-Driven Documents",
    "IEEE Transactions on Visualization and Computer Graphics",
    "2011;17(12):2301–2309",
    doi="10.1109/TVCG.2011.185",
)
MERMAID = Paper(
    ("Sveidqvist K", "Contributors to Mermaid"),
    "Mermaid: Generate diagrams from markdown-like text",
    "GitHub repository",
    url="https://github.com/mermaid-js/mermaid",
    kind="Software",
    publisher=True,
)


# ---------------------------------------------------------------------------
# What a run used
# ---------------------------------------------------------------------------

ALIGNMENT_INPUTS = frozenset({"bam", "cram", "sam", "fastq"})


@dataclass(frozen=True)
class CreditRun:
    """The facts about a run that decide what its report credits.

    ``steps`` are the job steps that completed; None when they could not be read,
    and then the report credits everything that could have run. ``pypgx`` is the
    pypgx_analysis step's output, which counts the genes Beagle phased and the
    genes the copy-number models called.
    """

    steps: Optional[frozenset] = None
    file_type: Optional[str] = None
    pypgx: Optional[Mapping] = None
    pdf: bool = True
    interactive: bool = True
    kroki: bool = True
    pharmcat_data_version: Optional[str] = None

    def ran(self, *steps: str) -> bool:
        return self.steps is None or any(step in self.steps for step in steps)

    @property
    def alignment(self) -> bool:
        """Reads, not variants: None (unknown) counts as possibly."""
        return self.file_type is None or self.file_type.lower() in ALIGNMENT_INPUTS

    def pypgx_genes(self, key: str) -> Optional[int]:
        """How many genes used a PyPGx method; None when the run did not say."""
        value = (self.pypgx or {}).get(key)
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


def _beagle(run: CreditRun) -> bool:
    phased = run.pypgx_genes("beagle_genes")
    return run.ran("pypgx_analysis") and (phased is None or phased > 0)


def _cnv_models(run: CreditRun) -> bool:
    called = run.pypgx_genes("cnv_model_genes")
    if not run.ran("pypgx_analysis"):
        return False
    return called > 0 if called is not None else run.alignment


# ---------------------------------------------------------------------------
# The credits
# ---------------------------------------------------------------------------

Versions = Mapping[str, str]  # lowercased manifest name -> version
Components = Mapping[str, Mapping[str, str]]  # service -> tool -> version


@dataclass(frozen=True)
class Credit:
    """A tool or data source, what it does here, and how to credit it.

    ``service``/``component`` say where its version is recorded
    (data/versions/about.md); ``release`` is a fixed release for data that has
    no recorded version. ``when`` decides whether a run used it.
    """

    key: str
    name: str
    kind: str  # "software" or "data"
    used_for: str
    url: str
    licence: str
    when: object  # Callable[[CreditRun], bool]
    papers: Tuple[Paper, ...] = ()
    service: str = ""
    component: str = ""
    release: str = ""
    notice: str = ""
    # When a run uses it, in words, for the docs' Credits page.
    used_when: str = "Every run"
    # Papers that apply only when a step ran: GATK's joint-calling paper for
    # GenotypeGVCFs.
    papers_when: Tuple[Tuple[str, Paper], ...] = ()
    # A tool with several jobs: (steps, what it did when one of them ran), after
    # ``uses_lead``. A run that did some of them is told only those; ``used_for``
    # says all of them.
    uses: Tuple[Tuple[Tuple[str, ...], str], ...] = ()
    uses_lead: str = ""

    def papers_for(self, run: "CreditRun") -> Tuple[Paper, ...]:
        return self.papers + tuple(p for step, p in self.papers_when if run.ran(step))

    def used_for_in(self, run: "CreditRun") -> str:
        """What it did in this run: a FASTQ run's GATK aligned the reads, and its
        row should not also say liftover."""
        done = [phrase for steps, phrase in self.uses if run.ran(*steps)]
        if not done or len(done) == len(self.uses):
            return self.used_for
        text = self.uses_lead + _and_list(done)
        if text[1:2].isupper():  # gVCF
            return text
        return text[:1].upper() + text[1:]


def _and_list(items: Sequence[str]) -> str:
    """["a", "b", "c"] -> "a, b and c"."""
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _always(run: CreditRun) -> bool:
    return True


CREDITS: Tuple[Credit, ...] = (
    # Software, in the order a run meets it.
    Credit(
        "zaropgx",
        "ZaroPGx",
        "software",
        "Runs the pipeline and writes the reports",
        "https://github.com/Zaromics/ZaroPGx",
        "AGPL-3.0-only",
        _always,
        service="zaropgx",
    ),
    Credit(
        "nextflow",
        "Nextflow",
        "software",
        "Runs the pipeline's steps in order",
        "https://www.nextflow.io",
        "Apache-2.0",
        _always,
        (NEXTFLOW_2017,),
        service="nextflow",
    ),
    Credit(
        "htslib",
        "SAMtools, BCFtools and HTSlib",
        "software",
        "PharmCAT's VCF preprocessing; file conversion and variant calling where "
        "the input needs them",
        "https://www.htslib.org",
        "MIT; BSD-3-Clause (HTSlib's CRAM code)",
        _always,
        (SAMTOOLS_2021, HTSLIB_2021),
        service="pharmcat",
        component="bcftools",
    ),
    Credit(
        "pysam",
        "pysam",
        "software",
        "Reads the input files for ZaroPGx and PyPGx",
        "https://github.com/pysam-developers/pysam",
        "MIT",
        _always,
        service="app",
        component="pysam",
    ),
    Credit(
        "gatk",
        "GATK",
        "software",
        "gVCF genotyping, liftover, read alignment and re-alignment of the CYP2D6 "
        "and UGT2B loci",
        "https://gatk.broadinstitute.org",
        "Apache-2.0",
        lambda run: run.ran(
            "gvcf_to_vcf", "liftover", "gatk_alignment", "alt_realignment"
        ),
        (GATK_BOOK_2020, MCKENNA_2010),
        service="gatk",
        papers_when=(("gvcf_to_vcf", POPLIN_2017),),
        used_when=(
            "gVCF, GRCh37 VCF or BCF, FASTQ; an alignment with reads on the CYP2D6 "
            "or UGT2B ALT contigs"
        ),
        uses=(
            (("gvcf_to_vcf",), "gVCF genotyping"),
            (("liftover",), "liftover"),
            (("gatk_alignment",), "read alignment"),
            (("alt_realignment",), "re-alignment of the CYP2D6 and UGT2B loci"),
        ),
    ),
    Credit(
        "picard",
        "Picard",
        "software",
        "LiftoverVcf (GRCh37 to GRCh38) and FastqToSam",
        "https://broadinstitute.github.io/picard/",
        "MIT",
        lambda run: run.ran("liftover", "gatk_alignment"),
        (PICARD_2019,),
        service="gatk",
        component="picard",
        used_when="GRCh37 VCF or BCF, FASTQ",
        uses=(
            (("liftover",), "LiftoverVcf (GRCh37 to GRCh38)"),
            (("gatk_alignment",), "FastqToSam"),
        ),
    ),
    Credit(
        "bwa",
        "BWA-MEM",
        "software",
        "Aligns to GRCh38, with GATK's BwaSpark, a FASTQ's reads and the CYP2D6 and "
        "UGT2B loci's reads",
        "https://github.com/lh3/bwa",
        "Apache-2.0 (as built into GATK)",
        lambda run: run.ran("gatk_alignment", "alt_realignment"),
        (BWA_MEM_2013,),
        service="gatk",
        component="bwa",
        used_when="FASTQ; an alignment with reads on the CYP2D6 or UGT2B ALT contigs",
        uses=(
            (("gatk_alignment",), "a FASTQ's reads"),
            (("alt_realignment",), "the CYP2D6 and UGT2B loci's reads"),
        ),
        uses_lead="Aligns to GRCh38, with GATK's BwaSpark, ",
    ),
    Credit(
        "pypgx",
        "PyPGx",
        "software",
        "Star alleles beyond PharmCAT's genes, CYP2D6 and structural variants",
        "https://pypgx.readthedocs.io",
        "MIT",
        lambda run: run.ran("pypgx_analysis", "pypgx_bam2vcf"),
        (CLINPHARMSEQ_2022, STARGAZER_2019_GIM, STARGAZER_2019_CPT),
        service="pypgx",
        used_when="Every run, unless PyPGx is off",
    ),
    Credit(
        "fuc",
        "fuc",
        "software",
        "Calls variants from the reads for PyPGx",
        "https://github.com/sbslee/fuc",
        "MIT",
        lambda run: run.ran("pypgx_bam2vcf"),
        (CLINPHARMSEQ_2022,),
        service="pypgx",
        component="fuc",
        used_when="BAM, CRAM, SAM, FASTQ",
    ),
    Credit(
        "beagle",
        "Beagle",
        "software",
        "Phases variants for PyPGx",
        "https://faculty.washington.edu/browning/beagle/beagle.html",
        "GPL-3.0-or-later",
        _beagle,
        (BEAGLE_2021,),
        service="pypgx",
        component="beagle",
        used_when="When PyPGx phases an input that is not already phased",
    ),
    Credit(
        "sklearn",
        "scikit-learn",
        "software",
        "Runs PyPGx's copy-number models",
        "https://scikit-learn.org",
        "BSD-3-Clause",
        _cnv_models,
        (SKLEARN_2011,),
        service="pypgx",
        component="scikit-learn",
        used_when="Whole-genome alignments of 15x or more",
    ),
    Credit(
        "optitype",
        "OptiType",
        "software",
        "HLA-A, HLA-B and HLA-C typing",
        "https://github.com/FRED-2/OptiType",
        "BSD-3-Clause",
        lambda run: run.ran("hla_typing"),
        (OPTITYPE_2014,),
        service="optitype",
        used_when="BAM, CRAM, SAM, FASTQ, with HLA typing on",
    ),
    Credit(
        "yara",
        "YARA",
        "software",
        "Maps the HLA reads for OptiType",
        "https://github.com/seqan/seqan/tree/main/apps/yara",
        "BSD-3-Clause",
        lambda run: run.ran("hla_typing"),
        (YARA_2013, YARA_THESIS_2015),
        service="optitype",
        component="yara",
        used_when="BAM, CRAM, SAM, FASTQ, with HLA typing on",
    ),
    Credit(
        "seqan",
        "SeqAn",
        "software",
        "The library YARA is built on",
        "https://www.seqan.de",
        "BSD-3-Clause",
        lambda run: run.ran("hla_typing"),
        (SEQAN_2017,),
        service="optitype",
        component="seqan",
        used_when="BAM, CRAM, SAM, FASTQ, with HLA typing on",
    ),
    Credit(
        "glpk",
        "GLPK",
        "software",
        "Solves OptiType's integer linear program",
        "https://www.gnu.org/software/glpk/",
        "GPL-3.0-or-later",
        lambda run: run.ran("hla_typing"),
        service="optitype",
        component="glpk",
        used_when="BAM, CRAM, SAM, FASTQ, with HLA typing on",
    ),
    Credit(
        "pyomo",
        "Pyomo",
        "software",
        "Builds OptiType's integer linear program",
        "https://www.pyomo.org",
        "BSD-3-Clause",
        lambda run: run.ran("hla_typing"),
        (PYOMO_BOOK_2021, PYOMO_2011),
        service="optitype",
        component="pyomo",
        used_when="BAM, CRAM, SAM, FASTQ, with HLA typing on",
    ),
    Credit(
        "mtdna",
        "mtDNA-Server 2",
        "software",
        "Mitochondrial variants, haplogroup and the MT-RNR1 call",
        "https://github.com/genepi/mtdna-server-2",
        "MIT",
        lambda run: run.ran("mtdna_analysis"),
        (MTDNA_SERVER_2_2024,),
        service="mtdna-server-2",
        used_when="With mtDNA analysis on",
    ),
    Credit(
        "mutserve",
        "mutserve",
        "software",
        "Calls mitochondrial variants from the reads",
        "https://github.com/seppinho/mutserve",
        "MIT",
        lambda run: run.ran("mtdna_analysis") and run.alignment,
        (MTDNA_SERVER_2016, HAPLOCHECK_2021),
        service="mtdna-server-2",
        component="mutserve",
        used_when="BAM, CRAM, SAM, FASTQ, with mtDNA analysis on",
    ),
    Credit(
        "haplogrep",
        "Haplogrep 3",
        "software",
        "Assigns the mitochondrial haplogroup",
        "https://github.com/genepi/haplogrep3",
        "MIT",
        lambda run: run.ran("mtdna_analysis"),
        (HAPLOGREP_3_2023,),
        service="mtdna-server-2",
        component="haplogrep3",
        used_when="With mtDNA analysis on",
    ),
    Credit(
        "haplocheck",
        "haplocheck",
        "software",
        "Checks the mitochondrial reads for contamination",
        "https://github.com/genepi/haplocheck",
        "MIT",
        lambda run: run.ran("mtdna_analysis") and run.alignment,
        (HAPLOCHECK_2021,),
        service="mtdna-server-2",
        component="haplocheck",
        used_when="BAM, CRAM, SAM, FASTQ, with mtDNA analysis on",
    ),
    Credit(
        "pharmcat",
        "PharmCAT",
        "software",
        "Diplotypes, phenotypes and drug guidance",
        "https://pharmcat.org",
        "MPL-2.0",
        _always,
        (SANGKUHL_2020, LI_2023, KLEIN_2018),
        service="pharmcat",
    ),
    Credit(
        "weasyprint",
        "WeasyPrint",
        "software",
        "Draws the PDF",
        "https://weasyprint.org",
        "BSD-3-Clause",
        lambda run: run.pdf,
        service="app",
        component="weasyprint",
        used_when="The PDF report",
    ),
    Credit(
        "kroki",
        "Kroki",
        "software",
        "Draws the workflow diagram",
        "https://kroki.io",
        "MIT",
        lambda run: run.kroki,
        service="kroki",
        component="kroki",
        used_when="The workflow diagram, with Kroki on",
    ),
    Credit(
        "mermaid",
        "Mermaid",
        "software",
        "The workflow diagram's layout",
        "https://mermaid.js.org",
        "MIT",
        lambda run: run.kroki,
        (MERMAID,),
        service="kroki",
        component="mermaid",
        used_when="The workflow diagram, with Kroki on",
    ),
    Credit(
        "chartjs",
        "Chart.js",
        "software",
        "The interactive report's charts",
        "https://www.chartjs.org",
        "MIT",
        lambda run: run.interactive,
        service="vendor",
        component="chart.js",
        used_when="The interactive report",
    ),
    Credit(
        "d3",
        "D3",
        "software",
        "The interactive report's gene-drug network",
        "https://d3js.org",
        "ISC",
        lambda run: run.interactive,
        (D3_2011,),
        service="vendor",
        component="d3",
        used_when="The interactive report",
    ),
    # Data and knowledge.
    Credit(
        "grch38",
        "GRCh38 (Genome Reference Consortium)",
        "data",
        "The genome build every call is made on",
        "https://www.ncbi.nlm.nih.gov/grc/human",
        "No restrictions (NCBI)",
        _always,
        (GRCH38_2017,),
        release="GCA_000001405.15",
    ),
    Credit(
        "pharmcat_reference",
        "PharmCAT's GRCh38 reference FASTA",
        "data",
        "Normalises the input in PharmCAT's preprocessor",
        "https://zenodo.org/records/7288118",
        "CC-BY-4.0",
        _always,
        (PHARMCAT_REFERENCE_2022,),
        release="GRCh38.p13 analysis set, primary sequences",
    ),
    Credit(
        "ucsc_hg38",
        "UCSC hg38",
        "data",
        "The reference for reads, gVCFs and liftover",
        "https://hgdownload.soe.ucsc.edu/goldenPath/hg38/bigZips/",
        "Freely available (UCSC)",
        lambda run: run.ran(
            "gvcf_to_vcf",
            "liftover",
            "gatk_cram_sam_to_bam",
            "gatk_alignment",
            "pypgx_bam2vcf",
        ),
        (UCSC_2026,),
        release="hg38",
        used_when="BAM, CRAM, SAM, FASTQ, gVCF, GRCh37 VCF or BCF",
        uses=(
            (
                ("gatk_cram_sam_to_bam", "gatk_alignment", "pypgx_bam2vcf"),
                "reads",
            ),
            (("gvcf_to_vcf",), "gVCFs"),
            (("liftover",), "liftover"),
        ),
        uses_lead="The reference for ",
    ),
    Credit(
        "kgp_grch38",
        "1000 Genomes GRCh38 analysis set",
        "data",
        "Decodes the CRAM, with UCSC hg38, by sequence checksum",
        "https://ftp.1000genomes.ebi.ac.uk/vol1/ftp/technical/reference/GRCh38_reference_genome/",
        "EMBL-EBI terms of use",
        lambda run: run.ran("gatk_cram_sam_to_bam")
        and (run.file_type or "cram").lower() == "cram",
        (KGP_GRCH38_2017,),
        release="full analysis set, with decoys and HLA",
        used_when="CRAM",
    ),
    Credit(
        "grch37",
        "GRCh37",
        "data",
        "The build the input was on, before liftover",
        "https://www.ncbi.nlm.nih.gov/grc/human",
        "No restrictions (NCBI)",
        lambda run: run.ran("liftover"),
        (GRCH37_2011,),
        release="GCA_000001405.1",
        used_when="GRCh37 VCF or BCF",
    ),
    Credit(
        "ucsc_chain",
        "UCSC hg19ToHg38 chain",
        "data",
        "Lifts the input from GRCh37 to GRCh38",
        "https://hgdownload.soe.ucsc.edu/goldenPath/hg19/liftOver/",
        "UCSC Chain Files licence (non-commercial)",
        lambda run: run.ran("liftover"),
        (UCSC_LIFTOVER_2006,),
        release="hg19ToHg38.over.chain",
        notice=(
            "The UCSC Chain Files are property of The Regents of the University of "
            "California, and made available free for non-commercial use by "
            "Independent Researchers and Nonprofit Organizations. Any other use of "
            "UCSC Chain Files requires a commercial license "
            "(https://genome.ucsc.edu/license/)."
        ),
        used_when="GRCh37 VCF or BCF",
    ),
    Credit(
        "kgp_panel",
        "1000 Genomes Project 30x panel",
        "data",
        "The haplotypes Beagle phases against",
        "https://www.internationalgenome.org",
        "EMBL-EBI terms of use",
        _beagle,
        (KGP_30X_2022, KGP_PHASE3_2015),
        service="pypgx",
        component="pypgx-bundle",
        notice=(
            "The 1000 Genomes 30x data were generated at the New York Genome Center "
            "with funds provided by NHGRI Grants 3UM1HG008901-03S1 and "
            "3UM1HG008901-04S2, from cell lines and DNA samples obtained from the "
            "NIGMS Human Genetic Cell Repository at the Coriell Institute for "
            "Medical Research."
        ),
        used_when="When Beagle phases",
    ),
    Credit(
        "imgt_hla",
        "IPD-IMGT/HLA",
        "data",
        "The HLA allele sequences OptiType types against",
        "https://www.ebi.ac.uk/ipd/imgt/hla/",
        "CC-BY-ND",
        lambda run: run.ran("hla_typing"),
        (IMGT_HLA_2026, IMGT_HLA_2013),
        # OptiType 1.5.0's data/hla_reference_dna.fasta (unchanged since 2014) holds
        # 6,889 alleles; every ID and name is in IPD-IMGT/HLA's Allelelist.3130.txt.
        # 3.12.0 lacks 236 of them and 3.14.0 renames 7 (Allelelist_history.txt).
        release="3.13.0, in OptiType",
        notice=(
            "HLA allele sequences are from the IPD-IMGT/HLA Database, used under "
            "the Creative Commons Attribution-NoDerivs licence."
        ),
        used_when="BAM, CRAM, SAM, FASTQ, with HLA typing on",
    ),
    Credit(
        "phylotree",
        "PhyloTree",
        "data",
        "The mitochondrial phylogeny haplogroups are assigned on",
        "https://github.com/genepi/phylotree-fu-rcrs",
        "MIT (Forensic Update)",
        lambda run: run.ran("mtdna_analysis"),
        (PHYLOTREE_2009, PHYLOTREE_FU_2021),
        service="mtdna-server-2",
        component="phylotree",
        used_when="With mtDNA analysis on",
    ),
    Credit(
        "rcrs",
        "Revised Cambridge Reference Sequence (rCRS)",
        "data",
        "The mitochondrial reference",
        "https://www.ncbi.nlm.nih.gov/nuccore/NC_012920.1",
        "No restrictions (NCBI)",
        lambda run: run.ran("mtdna_analysis"),
        (RCRS_1999,),
        release="NC_012920.1",
        used_when="With mtDNA analysis on",
    ),
    Credit(
        "cpic",
        "CPIC",
        "data",
        "Guidelines: phenotypes and recommendations",
        "https://www.clinpgx.org/cpic",
        "CC0-1.0",
        _always,
        (CPIC_2025,),
        service="pharmcat",
        component="data",
        notice=(
            "CPIC® content is available under CC0 1.0. CPIC guidelines and "
            "content are subject to updates and modifications; refer to ClinPGx "
            "(https://www.clinpgx.org/cpic) for the most current content. CPIC® is a "
            "registered service mark of the U.S. Department of Health & Human "
            "Services (HHS)."
        ),
    ),
    Credit(
        "dpwg",
        "DPWG",
        "data",
        "Guidelines of the Dutch Pharmacogenetics Working Group (KNMP)",
        "https://www.knmp.nl/dossiers/farmacogenetica/pharmacogenetics",
        "Not stated",
        _always,
        (DPWG_2008, DPWG_2011),
        service="pharmcat",
        component="data",
    ),
    Credit(
        "fda",
        "FDA",
        "data",
        "Drug-label annotations and pharmacogenetic associations",
        "https://www.fda.gov/medical-devices/precision-medicine/table-pharmacogenetic-associations",
        "Public domain (U.S. Government)",
        _always,
        (FDA_ASSOCIATIONS, FDA_BIOMARKERS),
        service="pharmcat",
        component="data",
    ),
    Credit(
        "clinpgx",
        "ClinPGx (PharmGKB)",
        "data",
        "Allele definitions, guideline and label annotations, as PharmCAT carries them",
        "https://www.clinpgx.org",
        "CC-BY-SA-4.0",
        _always,
        (CLINPGX_2021,),
        service="pharmcat",
        component="data",
        notice=(
            "Content from ClinPGx/PharmGKB (https://www.clinpgx.org), as carried by "
            "PharmCAT, is used under the Creative Commons Attribution-ShareAlike 4.0 "
            "International License (https://creativecommons.org/licenses/by-sa/4.0/). "
            "ZaroPGx changes its presentation and may abridge it. ClinPGx does not "
            "endorse ZaroPGx. Under ClinPGx's terms its data are for research use, "
            "not for sale."
        ),
    ),
    Credit(
        "pharmvar",
        "PharmVar",
        "data",
        "Star-allele definitions and names",
        "https://www.pharmvar.org",
        "CC-BY-SA-4.0",
        _always,
        (PHARMVAR_2021, PHARMVAR_2020, PHARMVAR_2019, PHARMVAR_2018),
        service="pharmcat",
        component="data",
        notice=(
            "Star-allele definitions from the Pharmacogene Variation Consortium "
            "(PharmVar, https://www.pharmvar.org), through CPIC and ClinPGx, are "
            "used under the Creative Commons Attribution-ShareAlike 4.0 "
            "International License, for research use."
        ),
    ),
    Credit(
        "dbsnp",
        "dbSNP",
        "data",
        "Variant identifiers (rs numbers)",
        "https://www.ncbi.nlm.nih.gov/snp/",
        "No restrictions (NCBI)",
        _always,
        (DBSNP_2025,),
        service="pharmcat",
        component="data",
    ),
)

# Vendored into the interactive report (app/static/vendor; tests/test_no_cdn_assets.py
# pins the files).
VENDORED = {"chart.js": "4.5.1", "d3": "7.9.0"}

NOT_RECORDED = "not recorded"
_UNKNOWN = {"", "unknown", "n/a", "none", "latest", "not recorded"}


def _clean(value: Optional[str]) -> Optional[str]:
    """A recorded version, or None. "v2.1.16" reads as 2.1.16; GATK's banner
    ("The Genome Analysis Toolkit () v4.7.0.0") as its number."""
    if value is None or str(value).strip().lower() in _UNKNOWN:
        return None
    text = str(value).strip()
    banner = re.search(r"\bv?(\d+(?:\.\d+)+\S*)", text)
    if banner and not re.match(r"^v?\d", text):
        return banner.group(1)
    return text[1:] if re.match(r"^v\d", text) else text


def resolve_version(
    credit: Credit,
    run: CreditRun,
    versions: Versions,
    components: Components,
    zaropgx_version: Optional[str] = None,
) -> str:
    """The version or release to print for ``credit`` in this run."""

    def component(service: str, name: str) -> Optional[str]:
        return _clean((components.get(service) or {}).get(name))

    if credit.key == "zaropgx":
        return _clean(zaropgx_version) or NOT_RECORDED
    if credit.key == "bwa":
        gatk = _clean(versions.get("gatk"))
        return f"in GATK {gatk}" if gatk else "in GATK"
    if credit.key == "htslib":
        main = component("pharmcat", "bcftools") or component("gatk", "bcftools")
        text = main or NOT_RECORDED
        mtdna = component("mtdna-server-2", "bcftools")
        if run.ran("mtdna_analysis") and mtdna and mtdna != main:
            text += f"; {mtdna} in mtDNA-Server 2"
        return text
    if credit.key == "phylotree":
        # "phylotree-fu-rcrs@1.3": a tree name, not a version _clean can read.
        tree = (components.get("mtdna-server-2") or {}).get("phylotree")
        found = re.match(r"phylotree-fu-rcrs@(\S+)", tree or "")
        return f"17, Forensic Update {found.group(1)}" if found else (tree or "17")
    if credit.key == "kgp_panel":
        bundle = component("pypgx", "pypgx-bundle")
        return "30x, phased 2020-10-28" + (
            f" (pypgx-bundle {bundle})" if bundle else ""
        )
    if credit.service == "pharmcat" and credit.component == "data":
        if run.pharmcat_data_version:
            return f"PharmCAT data {run.pharmcat_data_version}"
        pharmcat = _clean(versions.get("pharmcat"))
        return f"in PharmCAT {pharmcat}" if pharmcat else "in PharmCAT"
    if credit.service == "vendor":
        return VENDORED.get(credit.component) or NOT_RECORDED
    if credit.release:
        return credit.release
    if credit.component:
        return component(credit.service, credit.component) or NOT_RECORDED
    return _clean(versions.get(credit.service)) or NOT_RECORDED


def _software_reference(credit: Credit, version: str) -> Paper:
    """For software without a paper: its name, version and home."""
    detail = f"Version {version}" if version and version != NOT_RECORDED else ""
    return Paper((), credit.name, detail=detail, url=credit.url, kind="Software")


def report_credits(
    run: CreditRun,
    versions: Versions,
    components: Components,
    zaropgx_version: Optional[str] = None,
) -> Dict[str, list]:
    """What this run's report credits: the software and data it used, each with
    its version and reference numbers, the numbered references, and the notices
    their terms require."""
    rows: Dict[str, list] = {"software": [], "data": []}
    references: List[Paper] = []
    numbers: Dict[str, int] = {}
    notices: List[str] = []

    for credit in CREDITS:
        if not credit.when(run):
            continue
        version = resolve_version(credit, run, versions, components, zaropgx_version)
        papers = credit.papers_for(run)
        if not papers and credit.key != "zaropgx":
            papers = (_software_reference(credit, version),)
        refs = []
        for paper in papers:
            if paper.key not in numbers:
                references.append(paper)
                numbers[paper.key] = len(references)
            refs.append(numbers[paper.key])
        rows[credit.kind].append(
            {
                "key": credit.key,
                "name": credit.name,
                "version": version,
                "used_for": credit.used_for_in(run),
                "url": credit.url,
                "refs": refs,
            }
        )
        if credit.notice:
            notices.append(credit.notice)

    return {
        "software": rows["software"],
        "data": rows["data"],
        "references": [
            {"n": i, "text": paper.text(), "html": paper.html()}
            for i, paper in enumerate(references, 1)
        ],
        "notices": notices,
        # PharmCAT asks a report built from its output to cite its methods paper.
        "pharmcat_ref": numbers.get(SANGKUHL_2020.key),
    }


def ref_numbers(refs: List[int]) -> str:
    """[1, 2, 3, 5] -> "1–3, 5"."""
    spans: List[str] = []
    start = prev = None
    for n in sorted(set(refs)):
        if prev is not None and n == prev + 1:
            prev = n
            continue
        if start is not None:
            spans.append(str(start) if start == prev else f"{start}–{prev}")
        start = prev = n
    if start is not None:
        spans.append(str(start) if start == prev else f"{start}–{prev}")
    return ", ".join(spans)


# ---------------------------------------------------------------------------
# The platform: what runs the application, not an analysis (docs and NOTICE)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Component:
    name: str
    used_for: str
    url: str
    licence: str


PLATFORM: Tuple[Component, ...] = (
    Component(
        "FastAPI",
        "The web application and its API",
        "https://fastapi.tiangolo.com",
        "MIT",
    ),
    Component(
        "Starlette", "FastAPI's web toolkit", "https://www.starlette.io", "BSD-3-Clause"
    ),
    Component(
        "Uvicorn", "Serves the application", "https://www.uvicorn.org", "BSD-3-Clause"
    ),
    Component(
        "Gunicorn", "Serves the HLA and mtDNA services", "https://gunicorn.org", "MIT"
    ),
    Component(
        "Pydantic", "Request and settings models", "https://docs.pydantic.dev", "MIT"
    ),
    Component("SQLAlchemy", "Database access", "https://www.sqlalchemy.org", "MIT"),
    Component(
        "psycopg", "PostgreSQL driver", "https://www.psycopg.org", "LGPL-3.0-only"
    ),
    Component(
        "PostgreSQL",
        "Jobs, results and FHIR storage",
        "https://www.postgresql.org",
        "PostgreSQL",
    ),
    Component(
        "HAPI FHIR JPA Server",
        "The bundled FHIR server",
        "https://hapifhir.io",
        "Apache-2.0",
    ),
    Component(
        "Jinja2",
        "Report templates",
        "https://jinja.palletsprojects.com",
        "BSD-3-Clause",
    ),
    Component(
        "ReportLab",
        "The fallback PDF engine",
        "https://www.reportlab.com/opensource/",
        "BSD-3-Clause",
    ),
    Component(
        "Pillow",
        "Images for WeasyPrint and ReportLab",
        "https://python-pillow.github.io",
        "MIT-CMU",
    ),
    Component(
        "Biopython",
        "Reads FASTQ and FASTA headers",
        "https://biopython.org",
        "Biopython License or BSD-3-Clause",
    ),
    Component(
        "Requests",
        "HTTP between the services",
        "https://requests.readthedocs.io",
        "Apache-2.0",
    ),
    Component(
        "HTTPX",
        "HTTP between the services",
        "https://www.python-httpx.org",
        "BSD-3-Clause",
    ),
    Component(
        "AIOHTTP", "HTTP between the services", "https://docs.aiohttp.org", "Apache-2.0"
    ),
    Component(
        "python-multipart",
        "File uploads",
        "https://github.com/Kludex/python-multipart",
        "Apache-2.0",
    ),
    Component(
        "python-jose",
        "Sign-in tokens, when sign-in is on",
        "https://github.com/mpdavis/python-jose",
        "MIT",
    ),
    Component(
        "python-dotenv",
        "Reads .env",
        "https://github.com/theskumar/python-dotenv",
        "BSD-3-Clause",
    ),
    Component(
        "Werkzeug",
        "Upload file names",
        "https://werkzeug.palletsprojects.com",
        "BSD-3-Clause",
    ),
    Component(
        "Bootstrap",
        "The web pages' styles and scripts",
        "https://getbootstrap.com",
        "MIT",
    ),
    Component(
        "Bootstrap Icons",
        "The web pages' icons",
        "https://icons.getbootstrap.com",
        "MIT",
    ),
    Component(
        "Swagger UI",
        "The API explorer at /api/docs",
        "https://swagger.io/tools/swagger-ui/",
        "Apache-2.0",
    ),
    Component(
        "ReDoc", "The API reference at /api/redoc", "https://redocly.com/redoc", "MIT"
    ),
    Component(
        "Sphinx",
        "Builds this documentation",
        "https://www.sphinx-doc.org",
        "BSD-2-Clause",
    ),
    Component(
        "MyST-Parser",
        "Markdown for Sphinx",
        "https://myst-parser.readthedocs.io",
        "MIT",
    ),
    Component(
        "Read the Docs Sphinx theme",
        "This documentation's theme",
        "https://sphinx-rtd-theme.readthedocs.io",
        "MIT",
    ),
    Component(
        "Liberation fonts",
        "The PDF's text",
        "https://github.com/liberationfonts",
        "GPL-2.0-only WITH Font-exception-2.0",
    ),
    Component(
        "DejaVu fonts",
        "Monospace and fallback text",
        "https://dejavu-fonts.github.io",
        "Bitstream-Vera",
    ),
    Component(
        "Noto fonts", "Fallback text", "https://fonts.google.com/noto", "OFL-1.1"
    ),
    Component(
        "Eclipse Temurin and OpenJDK",
        "Run the Java tools",
        "https://adoptium.net",
        "GPL-2.0-only WITH Classpath-exception-2.0",
    ),
    Component(
        "R, R Markdown and their packages",
        "Draw mtDNA-Server 2's own report",
        "https://www.r-project.org",
        "R: GPL-2.0-only OR GPL-3.0-only; packages: their own",
    ),
    Component(
        "Docker and Docker Compose",
        "Run the services",
        "https://www.docker.com",
        "Apache-2.0",
    ),
    Component(
        "uv",
        "Installs the Python packages",
        "https://docs.astral.sh/uv/",
        "MIT OR Apache-2.0",
    ),
)

# Notices for the terminologies the FHIR export codes with (docs and NOTICE).
TERMINOLOGY_NOTICES: Tuple[Tuple[str, str], ...] = (
    (
        "LOINC",
        "This material contains content from LOINC (http://loinc.org). LOINC is "
        "copyright © Regenstrief Institute, Inc. and the Logical Observation "
        "Identifiers Names and Codes (LOINC) Committee and is available at no cost "
        "under the license at http://loinc.org/license. LOINC® is a registered "
        "United States trademark of Regenstrief Institute, Inc.",
    ),
    (
        "SNOMED CT",
        "This material includes SNOMED Clinical Terms® (SNOMED CT®) which is used "
        "by permission of the International Health Terminology Standards "
        "Development Organisation (IHTSDO). All rights reserved. SNOMED CT®, was "
        'originally created by The College of American Pathologists. "SNOMED" and '
        '"SNOMED CT" are registered trademarks of the IHTSDO. Use of SNOMED CT is '
        "subject to its licence (https://www.snomed.org/get-snomed).",
    ),
    (
        "HGNC",
        "Gene identifiers from the HUGO Gene Nomenclature Committee (HGNC, "
        "https://www.genenames.org), released under CC0.",
    ),
    (
        "HL7 FHIR",
        "HL7®, FHIR® and the FHIR [FLAME DESIGN] are the registered trademarks of "
        "Health Level Seven International and their use does not constitute "
        "endorsement by HL7. FHIR R4 is published under CC0, and so are the "
        "three FHIR packages the bundled HAPI FHIR server loads: the Genomics "
        "Reporting Implementation Guide 3.0.0, HL7 Terminology (THO) 6.1.0 and "
        "the FHIR Extensions Pack 5.1.0.",
    ),
)
