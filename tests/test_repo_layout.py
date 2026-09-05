"""Repository layout invariants (T-001).

CLAUDE.md §3 fixes the tree; these tests fail loudly if it drifts.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SERVICES = [
    "gateway",
    "application",
    "document",
    "member_intelligence",
    "policy",
    "feature",
    "risk",
    "fraud",
    "lmi",
    "committee",
    "agent_runtime",
    "decision",
    "execution",
    "notification",
    "audit",
    "governance",
    "llm_gateway",
    "core_stub",
]

LIBS = ["cio_contracts", "cio_common", "cio_tools"]

TOP_LEVEL = [
    "contracts/schemas",
    "contracts/codegen",
    "docker/images",
    "libs",
    "services",
    "workflows",
    "ai/agents",
    "ai/tools",
    "ai/guardrails",
    "ai/rag",
    "ai/evals",
    "ml",
    "policy_packs",
    "synthetic",
    "apps/web",
    "infra/observability",
    "scripts",
    "docs",
]


def test_top_level_tree_exists() -> None:
    missing = [p for p in TOP_LEVEL if not (ROOT / p).is_dir()]
    assert not missing, f"missing directories from CLAUDE.md §3: {missing}"


def test_every_service_has_app_tests_and_alembic() -> None:
    missing: list[str] = []
    for svc in SERVICES:
        base = ROOT / "services" / svc
        for part in ("app", "tests", "alembic/versions", "pyproject.toml", "conftest.py"):
            if not (base / part).exists():
                missing.append(f"services/{svc}/{part}")
    assert not missing, f"incomplete service scaffolding: {missing}"


def test_every_lib_is_an_installable_package() -> None:
    for lib in LIBS:
        base = ROOT / "libs" / lib
        assert (base / "pyproject.toml").is_file(), f"{lib} has no pyproject.toml"
        assert (base / lib / "__init__.py").is_file(), f"{lib} has no package module"


def test_workspace_members_cover_the_tree() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    members = data["tool"]["uv"]["workspace"]["members"]
    assert set(members) >= {"libs/*", "services/*", "workflows", "ai", "ml", "synthetic"}


def test_python_is_pinned_to_312() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert data["project"]["requires-python"] == ">=3.12,<3.13"
