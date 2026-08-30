"""The alignment path produces upstream's own report, or fails loudly."""

import ast
from pathlib import Path

from app.mtdna.builds import (
    MitoBuild,
    classify_build,
    classify_from_mito_contig,
    plan_for,
)

APP_PY = Path(__file__).resolve().parent.parent / "docker/mtdna-server-2/app.py"


def _alignment_branch() -> str:
    source = APP_PY.read_text(encoding="utf-8")
    return source[source.index("async def _call_from_alignment") :]


def _classify_as_call_from_alignment_would(name, length, build_label):
    """Mirrors _call_from_alignment's own classification block: header
    ground truth first, falling back to the label only when the header
    carries no usable mito @SQ line at all (build == UNSUPPORTED)."""
    build = classify_from_mito_contig(name, length, build_label)
    if build == MitoBuild.UNSUPPORTED:
        build = classify_build(build_label)
    return build


def test_it_runs_mutserve_against_the_vendored_rcrs():
    branch = _alignment_branch()
    assert "MUTSERVE_JAR" in branch
    assert "RCRS_FASTA" in branch


def test_it_runs_haplocheck_for_contamination():
    assert "HAPLOCHECK_JAR" in _alignment_branch()


def test_it_renders_upstreams_rmd():
    branch = _alignment_branch()
    assert "report.Rmd" in branch
    assert "Rscript" in branch


def test_hg19_alignment_input_is_refused():
    """No alignment-level liftover exists; a wrong haplogroup is worse than none."""
    branch = _alignment_branch()
    assert "hg19" in branch.lower()
    assert "422" in branch


def test_reference_is_coverage_gated_on_this_path():
    """Scoped to the alignment branch's actual gating expression, not a bare
    substring anywhere in the file -- MIN_MEAN_COVERAGE is also just a
    constant *definition*, which would stay green even if every use of it
    were deleted. The real behaviour (a real match always wins; without
    positive coverage evidence the call stays a no-call; an unresolved
    delins at 961 blocks the promotion even at high coverage) is exercised
    directly against real VcfRecord inputs in test_mt_rnr1_vocabulary.py's
    resolve_mt_rnr1_call tests -- see review round 1, finding 4 (2026-08-30).
    """
    branch = _alignment_branch()
    assert "coverage >= MIN_MEAN_COVERAGE" in branch
    # `records` (not just `matched`) has to reach resolve_mt_rnr1_call: it is
    # what lets that function's own has_unresolved_961_deletion check block
    # promotion for an unmatched delins overlapping 961, even at high
    # coverage -- see test_resolve_withholds_reference_for_an_unresolved_961_delins.
    assert "resolve_mt_rnr1_call(" in branch
    assert "matched, records, evidence_reason=evidence_reason" in branch


def test_it_reads_the_bam_header_for_ground_truth():
    """`reference_genome` alone cannot tell hg19 from b37/GRCh38 -- the same
    reason _call_from_vcf reads the VCF's own ##contig line instead of
    trusting the label."""
    branch = _alignment_branch()
    assert "_read_mito_sq_header" in branch
    assert "classify_from_mito_contig" in branch


def test_header_classification_runs_before_the_hg19_refusal():
    """Ordering matters: a mislabelled hg19 BAM must be caught on the
    header's own evidence before the label-driven refusal check runs."""
    branch = _alignment_branch()
    header_call = branch.index("_read_mito_sq_header(")
    hg19_check = branch.index("MitoBuild.HG19")
    assert header_call < hg19_check


def test_b37_header_classifies_as_b37_even_though_the_label_could_lie():
    build = _classify_as_call_from_alignment_would("MT", 16569, "GRCh37")
    assert build == MitoBuild.B37
    assert plan_for(build).supported


def test_mislabelled_hg19_bam_is_caught_by_the_header_not_the_label():
    """Label says GRCh37; header (LN:16571) says hg19. Ground truth wins."""
    build = _classify_as_call_from_alignment_would("chrM", 16571, "GRCh37")
    assert build == MitoBuild.HG19


def test_no_mito_sq_line_falls_back_to_the_label():
    build = _classify_as_call_from_alignment_would(None, None, "GRCh38")
    assert build == MitoBuild.GRCH38


def test_cram_without_a_staged_reference_is_refused():
    """CRAM stores no sequence of its own; decoding it needs the exact
    reference it was compressed against, which this image does not fetch on
    its own (no REF_CACHE/REF_PATH baked in). The FASTA-path constant sits
    just above the function (like MIN_MEAN_COVERAGE), so read the whole file
    rather than _alignment_branch(), which starts at the `def` line."""
    source = APP_PY.read_text(encoding="utf-8")
    branch = _alignment_branch()
    assert "cram" in branch.lower()
    assert "--reference" in branch
    assert "human_g1k_v37.fasta" in source
    assert "Homo_sapiens_assembly38.fasta" in source
    assert "422" in branch


def test_the_alignment_path_labels_its_reference_measured():
    assert "BASIS_MEASURED" in APP_PY.read_text(encoding="utf-8")


def test_the_alignment_path_does_not_fall_back_to_tier_c():
    """Measured-and-below-floor is stronger evidence than Tier C's inference.

    Letting Tier C rescue a call that a depth measurement just refused would
    invert the ladder: a weaker signal overriding a stronger negative one.
    Tier C is for inputs where no measurement is possible, not a second
    chance for inputs where one failed.
    """
    branch = _alignment_branch()
    assert "has_variant_in_gene" not in branch
    assert "BASIS_INFERRED" not in branch


# --- AST-based arity guard --------------------------------------------------
#
# Exists because a real arity mismatch shipped green: _classify_haplogroup
# was changed from a 2-tuple to a 4-tuple return (haplogroup, quality,
# not_found_polys, range), but the alignment path's only call site kept
# unpacking two values -- `haplogroup, quality = ...` against a 4-tuple,
# which raises ValueError at runtime. Nothing in the suite caught it: the
# alignment tests above are all source-text pins that never construct a call,
# and the VCF path exercises a *different* call site that happened to already
# be correct. A whole calling path was non-functional under a fully green
# suite.
#
# This test parses app.py with `ast` (no subprocess, no Docker, no import of
# the sidecar -- see the VCF path's own fixture docstring for why app.py is
# not importable in this venv) and checks, structurally, that every call
# site's unpack target has exactly as many names as the function actually
# returns. It does not hardcode "4": it reads the arity from
# _classify_haplogroup's own `return` statements, so it keeps working the
# next time the tuple's shape changes and stays useful long after this task.


def _attach_parents(tree: ast.AST) -> None:
    """Give every node a `.parent` link so a Call can walk up to the
    assignment that consumes it -- the stdlib `ast` module builds no such
    link on its own."""
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child.parent = node


def _descendants_skipping_nested_defs(node: ast.AST):
    """Yield every descendant of `node`, except it does not look inside a
    nested function/lambda/class body -- so a `return` that belongs to some
    other, inner function is never mistaken for this one's."""
    for child in ast.iter_child_nodes(node):
        yield child
        if not isinstance(
            child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
        ):
            yield from _descendants_skipping_nested_defs(child)


def _find_function(tree: ast.AST, name: str) -> ast.AST:
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            return node
    raise AssertionError(f"no function named {name!r} in {APP_PY}")


def _return_arities(func_node: ast.AST):
    """The set of tuple-lengths every `return` inside `func_node` produces
    (bare `return` counts as 0; `return x` counts as 1)."""
    arities = set()
    for node in _descendants_skipping_nested_defs(func_node):
        if isinstance(node, ast.Return):
            value = node.value
            if value is None:
                arities.add(0)
            elif isinstance(value, ast.Tuple):
                arities.add(len(value.elts))
            else:
                arities.add(1)
    return arities


def _is_within(node, ancestor) -> bool:
    current = node
    while current is not None:
        if current is ancestor:
            return True
        current = getattr(current, "parent", None)
    return False


def _enclosing_assign_targets(call_node: ast.Call):
    """Walk up from a Call (through `await`, ternaries, etc.) to the nearest
    enclosing assignment, and return its target list -- or None if this call
    site is not the value of a plain assignment this guard knows how to
    check."""
    node = call_node
    while node is not None:
        parent = getattr(node, "parent", None)
        if isinstance(parent, ast.Assign):
            return parent.targets
        if isinstance(parent, ast.AnnAssign) and parent.value is not None:
            return [parent.target]
        node = parent
    return None


def _target_arity(target: ast.AST) -> int:
    if isinstance(target, (ast.Tuple, ast.List)):
        return len(target.elts)
    return 1


def test_classify_haplogroup_return_arity_matches_every_call_site():
    """Every place app.py unpacks _classify_haplogroup()'s result must ask
    for exactly as many values as it actually returns.

    This is a static, structural check -- it parses the module with `ast`
    rather than importing or running it, so it runs in any environment the
    rest of this suite runs in (no psutil, no /job-client, no Docker).
    """
    tree = ast.parse(APP_PY.read_text(encoding="utf-8"))
    _attach_parents(tree)

    func_node = _find_function(tree, "_classify_haplogroup")
    return_arities = _return_arities(func_node)
    assert return_arities, "_classify_haplogroup has no `return` statements to check"
    assert len(return_arities) == 1, (
        "_classify_haplogroup's own `return` statements disagree on how many "
        f"values they produce: {sorted(return_arities)}"
    )
    (arity,) = return_arities

    call_sites = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_classify_haplogroup"
        and not _is_within(node, func_node)
    ]
    assert call_sites, "found no call sites for _classify_haplogroup to check"

    for call in call_sites:
        targets = _enclosing_assign_targets(call)
        assert targets is not None, (
            f"_classify_haplogroup call at line {call.lineno} is not the "
            "direct value of an assignment this guard knows how to check -- "
            "extend it rather than silently skipping"
        )
        for target in targets:
            unpack_arity = _target_arity(target)
            assert unpack_arity == arity, (
                f"_classify_haplogroup returns a {arity}-tuple but the "
                f"assignment at line {target.lineno} unpacks {unpack_arity} "
                "value(s) -- this is exactly the mismatch that shipped green "
                "before this test existed"
            )
