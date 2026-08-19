"""
Compare a generated OpenAPI document against a reference one.

A single "how many leaves match" score is misleading, because it weighs a
hand-written sentence the same as a type: descriptions that say the right thing
in different words drag the number down while nothing is actually wrong. So the
leaves are split into three groups and reported apart:

    contract — paths, methods, schemas, types, $ref, enum, required. What the
               API promises. A miss here is a real defect.
    metadata — info, servers, externalDocs, openapi, tags, security. Describes
               the document rather than the API. Usually absent because no rule
               in the bank covers those blocks.
    prose    — description, summary, title, operationId. Only has to *mean* the
               same thing, so exact-match is reported but reads as a similarity
               hint, not as correctness.

Each group is counted three ways — exact, absent (the reference has the leaf
and the generated document does not), and differing (both have it, values
disagree) — plus, for the contract, the extra leaves the generated document
adds. Extra is not automatically wrong: a rules bank sometimes states something
the reference YAML leaves implicit (a `required` list, say), so those are listed
rather than scored.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

# Leaf name → prose. Compared for information, never counted as a defect.
PROSE_KEYS = ("description", "summary", "title", "operationId")

# Top-level block → metadata about the document rather than the API contract.
METADATA_ROOTS = ("info", "servers", "externalDocs", "openapi", "tags", "security")

Leaves = Dict[str, Any]


def flatten(node: Any, prefix: str = "") -> Leaves:
    """Flatten a parsed document into {dotted.path: scalar}.

    List position is part of the path (`allOf[1].type`), so a composition whose
    members moved is reported as a difference rather than silently matching.
    """
    out: Leaves = {}
    if isinstance(node, dict):
        for key, value in node.items():
            out.update(flatten(value, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            out.update(flatten(value, f"{prefix}[{i}]"))
    else:
        out[prefix] = node
    return out


def classify(leaf_path: str) -> str:
    """Bucket a leaf path into 'contract', 'metadata' or 'prose'."""
    leaf_name = leaf_path.split(".")[-1].split("[")[0]
    if leaf_name in PROSE_KEYS:
        return "prose"
    root = leaf_path.split(".")[0].split("[")[0]
    return "metadata" if root in METADATA_ROOTS else "contract"


@dataclass
class GroupScore:
    """How one group of leaves fared against the reference."""

    total: int = 0
    exact: int = 0
    absent: List[str] = field(default_factory=list)
    differing: List[Tuple[str, Any, Any]] = field(default_factory=list)
    extra: List[str] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        return self.exact / self.total if self.total else 1.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "exact": self.exact,
            "total": self.total,
            "ratio": round(self.ratio, 3),
            "absent": self.absent,
            "differing": [
                {"path": p, "generated": g, "reference": r}
                for p, g, r in self.differing
            ],
            "extra": self.extra,
        }


@dataclass
class Comparison:
    """Result of comparing a generated document against a reference."""

    groups: Dict[str, GroupScore]
    paths_missing: List[str] = field(default_factory=list)
    paths_extra: List[str] = field(default_factory=list)
    schemas_missing: List[str] = field(default_factory=list)
    schemas_extra: List[str] = field(default_factory=list)
    refs_total: int = 0
    refs_with_siblings: List[str] = field(default_factory=list)

    @property
    def contract(self) -> GroupScore:
        return self.groups["contract"]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "contract": self.groups["contract"].as_dict(),
            "metadata": self.groups["metadata"].as_dict(),
            "prose": self.groups["prose"].as_dict(),
            "paths_missing": self.paths_missing,
            "paths_extra": self.paths_extra,
            "schemas_missing": self.schemas_missing,
            "schemas_extra": self.schemas_extra,
            "refs_clean": f"{self.refs_total - len(self.refs_with_siblings)}/{self.refs_total}",
            "refs_with_siblings": self.refs_with_siblings,
        }


def _find_refs(node: Any, at: str = "") -> List[Tuple[str, List[str]]]:
    """Every $ref in the document, with the keys sitting beside it.

    OpenAPI 3.0 defines a Reference Object as holding `$ref` alone: "any
    properties added SHALL be ignored". Siblings are therefore dead text, worth
    reporting even though they do not change how a parser reads the document.
    """
    found: List[Tuple[str, List[str]]] = []
    if isinstance(node, dict):
        if "$ref" in node:
            found.append((at, [k for k in node if k != "$ref"]))
        for key, value in node.items():
            found.extend(_find_refs(value, f"{at}.{key}" if at else str(key)))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(_find_refs(value, f"{at}[{i}]"))
    return found


def _names(doc: Dict[str, Any], *path: str) -> set:
    node: Any = doc
    for part in path:
        node = (node or {}).get(part) if isinstance(node, dict) else None
    return set(node) if isinstance(node, dict) else set()


def compare(generated: Dict[str, Any], reference: Dict[str, Any]) -> Comparison:
    """Compare two parsed OpenAPI documents."""
    gen_leaves, ref_leaves = flatten(generated), flatten(reference)

    groups = {name: GroupScore() for name in ("contract", "metadata", "prose")}
    for path, ref_value in ref_leaves.items():
        score = groups[classify(path)]
        score.total += 1
        if path not in gen_leaves:
            score.absent.append(path)
        elif gen_leaves[path] == ref_value:
            score.exact += 1
        else:
            score.differing.append((path, gen_leaves[path], ref_value))

    for path in gen_leaves:
        if path not in ref_leaves:
            groups[classify(path)].extra.append(path)
    for score in groups.values():
        score.absent.sort()
        score.extra.sort()
        score.differing.sort()

    gen_paths, ref_paths = _names(generated, "paths"), _names(reference, "paths")
    gen_schemas = _names(generated, "components", "schemas")
    ref_schemas = _names(reference, "components", "schemas")
    refs = _find_refs(generated)

    return Comparison(
        groups=groups,
        paths_missing=sorted(ref_paths - gen_paths),
        paths_extra=sorted(gen_paths - ref_paths),
        schemas_missing=sorted(ref_schemas - gen_schemas),
        schemas_extra=sorted(gen_schemas - ref_schemas),
        refs_total=len(refs),
        refs_with_siblings=sorted(at for at, siblings in refs if siblings),
    )


def format_report(result: Comparison, max_items: int = 15) -> str:
    """Render a Comparison as a readable block."""
    labels = {
        "contract": "CONTRACT  paths, methods, schemas, types, $ref",
        "metadata": "METADATA  info, servers, externalDocs, openapi",
        "prose":    "PROSE     descriptions, summaries (wording may differ)",
    }
    lines = ["=" * 66, "COMPARISON AGAINST REFERENCE", "=" * 66, ""]
    for name in ("contract", "metadata", "prose"):
        score = result.groups[name]
        lines.append(
            f"{labels[name]}\n"
            f"    {score.exact}/{score.total} exact ({score.ratio:.0%})"
            f"  |  {len(score.absent)} absent"
            f"  |  {len(score.differing)} differing"
            f"  |  {len(score.extra)} extra"
        )
    lines.append("")

    lines.append(
        f"paths   missing={result.paths_missing or 'none'}  "
        f"extra={result.paths_extra or 'none'}"
    )
    lines.append(
        f"schemas missing={result.schemas_missing or 'none'}  "
        f"extra={result.schemas_extra or 'none'}"
    )
    clean = result.refs_total - len(result.refs_with_siblings)
    lines.append(f"$ref    {clean}/{result.refs_total} hold $ref alone")
    for at in result.refs_with_siblings[:max_items]:
        lines.append(f"          sibling keys at {at}")

    contract = result.contract
    if contract.absent:
        lines.append(f"\ncontract leaves NOT generated ({len(contract.absent)}):")
        lines += [f"  - {p}" for p in contract.absent[:max_items]]
    if contract.differing:
        lines.append(f"\ncontract leaves with a WRONG value ({len(contract.differing)}):")
        for p, g, r in contract.differing[:max_items]:
            lines.append(f"  ! {p}\n      generated: {str(g)[:88]}\n      reference: {str(r)[:88]}")
    if contract.extra:
        lines.append(
            f"\ncontract leaves the reference does not have ({len(contract.extra)}) "
            "— may be legitimate, a rule can state what the reference leaves implicit:"
        )
        lines += [f"  + {p}" for p in contract.extra[:max_items]]

    return "\n".join(lines)
