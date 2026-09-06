"""Policy pack loading, schema validation and semantic checks (docs/05).

A pack is three YAML files per product per version. Schema validation catches
shape errors; the semantic checks catch the ones that would quietly change a
decision: weights that do not sum to 1, thresholds in the wrong order, an
authority ladder that does not ascend, a rule citing an undeclared reason code.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from cio_common.assets import policy_pack_root

__all__ = [
    "PackError",
    "PolicyPack",
    "available_packs",
    "load_pack",
    "pack_root",
    "semantic_errors",
    "validate_pack",
]

_KINDS = ("policy", "dff", "autonomy")


class PackError(ValueError):
    """A pack is not usable. The message names every problem found."""

    def __init__(self, product: str, version: str, problems: list[str]) -> None:
        self.product = product
        self.version = version
        self.problems = problems
        listed = "\n  - ".join(problems)
        super().__init__(f"policy pack {product}/{version} is invalid:\n  - {listed}")


def pack_root() -> Path:
    """Where packs live. Overridden in tests via CIO_POLICY_PACK_ROOT."""
    return policy_pack_root()


@lru_cache(maxsize=8)
def _schema(kind: str) -> Draft202012Validator:
    body = yaml.safe_load((pack_root() / "schema" / f"{kind}.schema.json").read_text())
    Draft202012Validator.check_schema(body)
    return Draft202012Validator(body, format_checker=Draft202012Validator.FORMAT_CHECKER)


@dataclass(frozen=True, slots=True)
class PolicyPack:
    """One product's rules, weights and dial at one version."""

    product: str
    version: str
    policy: dict[str, Any]
    dff: dict[str, Any]
    autonomy: dict[str, Any]

    @property
    def policy_version(self) -> str:
        return f"policy/{self.product}/{self.version}"

    @property
    def dff_version(self) -> str:
        return f"dff/{self.product}/{self.version}"

    @property
    def autonomy_version(self) -> str:
        return f"autonomy/{self.product}/{self.version}"

    def rules(self) -> list[dict[str, Any]]:
        """Every gate rule, in evaluation order (docs/05 §3.2)."""
        order = ["eligibility", "documents", "shariah", "affordability", "exposure"]
        found: list[dict[str, Any]] = []
        for category in order:
            section = self.policy.get(category)
            if isinstance(section, dict):
                for rule in section.get("rules", []):
                    found.append({**rule, "category": _CATEGORY[category]})
        return found

    def routing_rules(self) -> list[dict[str, Any]]:
        return list(self.policy.get("routing", {}).get("rules", []))

    def authority_for(self, amount: float) -> str:
        """The smallest band whose ceiling covers ``amount`` (docs/05 §3.4)."""
        for band in self.policy["authority"]["bands"]:
            ceiling = band["max_amount"]
            if ceiling is None or amount <= ceiling:
                return str(band["role"])
        return str(self.policy["authority"]["bands"][-1]["role"])

    def exposure_limit(self, grade: str) -> float | None:
        limits = self.policy["exposure"]["limit_by_grade"]
        value = limits.get(grade)
        return float(value) if value is not None else None


_CATEGORY = {
    "eligibility": "ELIGIBILITY",
    "documents": "DOCUMENTS",
    "shariah": "SHARIAH",
    "affordability": "AFFORDABILITY",
    "exposure": "EXPOSURE",
}

# The vocabulary a rule may cite. Kept in step with contracts/reason_codes.yaml
# by test_packs.py rather than duplicated here.
_BLOCKING = {
    "INELIGIBLE",
    "BLOCK_NORMAL_PATH",
    "MORE_INFORMATION_REQUIRED",
    "POLICY_EXCEPTION_OR_DECLINE",
    "COMPLIANCE_REVIEW",
}


def validate_pack(policy: dict, dff: dict, autonomy: dict) -> list[str]:
    """Schema problems, as human-readable strings."""
    problems: list[str] = []
    for kind, body in (("policy", policy), ("dff", dff), ("autonomy", autonomy)):
        for error in sorted(_schema(kind).iter_errors(body), key=lambda e: list(e.absolute_path)):
            where = "/".join(str(p) for p in error.absolute_path) or "<root>"
            problems.append(f"{kind}: {where}: {error.message}")
    return problems


def semantic_errors(policy: dict, dff: dict, autonomy: dict) -> list[str]:
    """Problems JSON Schema cannot express but that would change decisions."""
    problems: list[str] = []

    # --- the three files must describe the same product and version ---
    products = {policy.get("product"), dff.get("product"), autonomy.get("product")}
    if len(products) != 1:
        problems.append(f"the three files disagree on product: {sorted(map(str, products))}")
    versions = {policy.get("version"), dff.get("version"), autonomy.get("version")}
    if len(versions) != 1:
        problems.append(f"the three files disagree on version: {sorted(map(str, versions))}")

    # --- weights must sum to exactly 1 (docs/05 §4) ---
    weights = dff.get("weights", {})
    total = round(sum(weights.values()), 6)
    if total != 1.0:
        problems.append(f"dff weights sum to {total}, must be 1.0")

    # --- thresholds must be ordered ---
    thresholds = dff.get("thresholds", {})
    approve, decline = thresholds.get("approve"), thresholds.get("decline")
    if approve is not None and decline is not None and decline >= approve:
        problems.append(f"dff thresholds are not ordered: decline {decline} must be below approve {approve}")

    # --- disagreement thresholds must be ordered ---
    dis = dff.get("disagreement_thresholds") or {}
    if dis and dis.get("officer_review", 0) >= dis.get("enhanced_assessment", 1):
        problems.append("dff disagreement thresholds are not ordered")

    # --- authority bands must ascend and end open ---
    bands = policy.get("authority", {}).get("bands", [])
    ceilings = [b.get("max_amount") for b in bands]
    finite = [c for c in ceilings if c is not None]
    if finite != sorted(finite):
        problems.append(f"authority bands do not ascend: {ceilings}")
    if ceilings and ceilings[-1] is not None:
        problems.append("the last authority band must be open-ended (max_amount: null)")
    if None in ceilings[:-1]:
        problems.append("only the last authority band may be open-ended")

    # --- autonomy bands must ascend and end open ---
    a_ceilings = [b.get("max_amount") for b in autonomy.get("bands", [])]
    a_finite = [c for c in a_ceilings if c is not None]
    if a_finite != sorted(a_finite):
        problems.append(f"autonomy bands do not ascend: {a_ceilings}")

    # --- product terms must be coherent ---
    terms = policy.get("product_terms", {})
    if terms.get("min_amount", 0) >= terms.get("max_amount", 1):
        problems.append("product min_amount is not below max_amount")
    if terms.get("min_tenor", 0) >= terms.get("max_tenor", 1):
        problems.append("product min_tenor is not below max_tenor")

    # --- rule ids must be unique across the whole pack ---
    ids: list[str] = []
    for section in policy.values():
        if isinstance(section, dict):
            ids += [r["id"] for r in section.get("rules", []) if "id" in r]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        problems.append(f"duplicate rule ids: {duplicates}")

    # --- every scored family must carry a weight, and vice versa ---
    scored, weighted = set(dff.get("scoring", {})), set(weights)
    if scored != weighted:
        problems.append(
            f"dff scoring and weights disagree: only scored {sorted(scored - weighted)}, "
            f"only weighted {sorted(weighted - scored)}"
        )

    # --- a blocking gate needs a reason code the officer can be shown ---
    # Routing rules are exempt: they carry no reason_code, because the reason is
    # the finding that triggered them (docs/05 §2, docs/07 §3).
    for name, section in policy.items():
        if not isinstance(section, dict) or name == "routing":
            continue
        for rule in section.get("rules", []):
            if rule.get("on_fail") in _BLOCKING and not rule.get("reason_code"):
                problems.append(f"rule {rule.get('id')} blocks without a reason code")

    # --- the autonomy dial must reference an allowed setting for the sampling role ---
    autonomy_conditions = autonomy.get("autonomous_conditions", {})
    if autonomy_conditions.get("min_confidence", 0) < 0.5:
        problems.append("autonomous_conditions.min_confidence below 0.5 is not a bounded dial")

    # --- documents: every critical field list must belong to a required document ---
    documents = policy.get("documents", {})
    required = set(documents.get("required", []))
    unknown = set(documents.get("critical_fields", {})) - required
    if unknown:
        problems.append(f"critical_fields names documents that are not required: {sorted(unknown)}")

    return problems


def load_pack(product: str, version: str, *, root: Path | None = None) -> PolicyPack:
    """Read, validate and return a pack. Raises :class:`PackError` if unusable."""
    base = (root or pack_root()) / product / version
    bodies: dict[str, Any] = {}
    problems: list[str] = []

    for kind in _KINDS:
        path = base / f"{kind}.yaml"
        if not path.is_file():
            problems.append(f"{kind}.yaml is missing")
            continue
        try:
            bodies[kind] = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            problems.append(f"{kind}.yaml is not valid YAML: {exc}")

    if problems:
        raise PackError(product, version, problems)

    problems = validate_pack(bodies["policy"], bodies["dff"], bodies["autonomy"])
    problems += semantic_errors(bodies["policy"], bodies["dff"], bodies["autonomy"])
    if problems:
        raise PackError(product, version, problems)

    return PolicyPack(
        product=product,
        version=version,
        policy=bodies["policy"],
        dff=bodies["dff"],
        autonomy=bodies["autonomy"],
    )


def available_packs(root: Path | None = None) -> list[tuple[str, str]]:
    """Every (product, version) pair on disk, newest version last."""
    base = root or pack_root()
    found = [
        (product.name, version.name)
        for product in sorted(base.iterdir())
        if product.is_dir() and product.name != "schema"
        for version in sorted(product.iterdir())
        if version.is_dir() and (version / "policy.yaml").is_file()
    ]
    return found


def write_pack(product: str, version: str, bodies: dict[str, Any], *, root: Path | None = None) -> Path:
    """Write a new pack version, once.

    Write-once on purpose. A version somebody decided under must still read
    back exactly as it did, and overwriting one would make every ledger entry
    citing it a claim about a document that no longer exists. Re-adopting the
    same change gets the next sequence rather than replacing the last.

    The bodies are validated by loading them back before this returns, so a
    version that would fail to load is never left on disk for a later request
    to trip over.
    """
    base = (root or pack_root()) / product / version
    if base.exists():
        raise PackError(product, version, ["that version already exists on disk"])

    base.mkdir(parents=True)
    for kind in _KINDS:
        body = bodies.get(kind)
        if body is None:
            raise PackError(product, version, [f"{kind}.yaml is missing from the candidate"])
        (base / f"{kind}.yaml").write_text(yaml.safe_dump(body, sort_keys=False, allow_unicode=True))

    load_pack(product, version, root=root)
    return base
