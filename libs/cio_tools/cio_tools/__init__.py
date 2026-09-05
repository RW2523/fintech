"""Tool registry: grants, masking, evidence and budgets for agent tool calls.

Agents have no database access and hold no credentials. Every capability they
have is a registered :class:`~cio_tools.spec.ToolSpec` reached through
:meth:`~cio_tools.registry.ToolRegistry.call` (CLAUDE.md §2.5).
"""

from cio_tools.evidence import EVIDENCE_KEY, build_evidence, extract_evidence_ids
from cio_tools.grants import BudgetExceeded, CallBudget, Grant, GrantRegistry, ToolDenied
from cio_tools.masking import MASK, mask_paths, masked_field_count
from cio_tools.registry import Invocation, ToolContext, ToolRegistry, registry, tool
from cio_tools.spec import EvidenceSpec, PermittedUse, SideEffect, ToolSpec

__version__ = "0.1.0"

__all__ = [
    "EVIDENCE_KEY",
    "MASK",
    "BudgetExceeded",
    "CallBudget",
    "EvidenceSpec",
    "Grant",
    "GrantRegistry",
    "Invocation",
    "PermittedUse",
    "SideEffect",
    "ToolContext",
    "ToolDenied",
    "ToolRegistry",
    "ToolSpec",
    "__version__",
    "build_evidence",
    "extract_evidence_ids",
    "mask_paths",
    "masked_field_count",
    "registry",
    "tool",
]
