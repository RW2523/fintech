"""The metrics a manager copilot may read (T-072, docs/06 §4).

Aggregates and nothing else. The copilot that holds these tools answers
questions about a book, and a book is not a list of members: every row here is
a count, a share or a rate, and no path through this module can return a member
id, a case id or an account.

That is a property of the endpoint rather than of the prompt. A copilot told
not to name members will eventually name one; a copilot whose tools cannot
return a member cannot.
"""

from __future__ import annotations

from typing import Any

from ai.tools.client import services
from ai.tools.schemas import ARRAY_OF_OBJECTS, OBJECT, inputs, optional
from cio_tools.registry import tool
from cio_tools.spec import EvidenceSpec, PermittedUse, SideEffect

READ = SideEffect.READ
ANALYTICS = {PermittedUse.ANALYTICS}

METRIC_EVIDENCE = EvidenceSpec(
    type="ANALYTIC_RESULT",
    source_system="governance",
    source_record_path="metric",
    locator_from={"calc_id": "metric"},
)


@tool(
    "metrics.query",
    version="1.0",
    input_schema=inputs(
        metric={"type": "string", "minLength": 2, "maxLength": 64},
        days=optional({"type": "integer", "minimum": 1, "maximum": 3650}),
        product=optional({"type": "string", "maxLength": 32}),
    ),
    output_schema=OBJECT,
    purpose_tags=ANALYTICS,
    side_effects=READ,
    backing_service="governance",
    evidence=METRIC_EVIDENCE,
)
async def metrics_query(metric: str, days: int | None = None, product: str | None = None) -> dict[str, Any]:
    """One governed metric, with the sentence that says what it means.

    The same endpoint a cockpit tile reads. That is deliberate: a copilot with
    its own calculation would eventually quote a number the screen beside it
    disagrees with, and the manager would be right not to trust either.
    """
    return await services().get(
        "governance",
        f"/governance/metrics/{metric}",
        days=days or 90,
        product=product,
    )


@tool(
    "metrics.catalogue",
    version="1.0",
    input_schema=inputs(),
    output_schema=ARRAY_OF_OBJECTS,
    purpose_tags=ANALYTICS,
    side_effects=READ,
    backing_service="governance",
)
async def metrics_catalogue() -> list[dict[str, Any]]:
    """What can be asked for, and what each one means.

    Given to the copilot so it can pick a metric rather than invent one. A
    model asked "why did approvals fall" with no catalogue will guess at a
    metric name, get a 404, and answer from the question instead of the book.
    """
    body = await services().get("governance", "/governance/metrics")
    return list(body.get("metrics") or []) if isinstance(body, dict) else list(body or [])


@tool(
    "governance.overrides",
    version="1.0",
    input_schema=inputs(
        days=optional({"type": "integer", "minimum": 1, "maximum": 3650}),
        product_code=optional({"type": "string", "maxLength": 32}),
    ),
    output_schema=OBJECT,
    purpose_tags=ANALYTICS,
    side_effects=READ,
    backing_service="governance",
)
async def governance_overrides(days: int | None = None, product_code: str | None = None) -> dict[str, Any]:
    """Where people departed from the recommendation, as rates and reasons.

    The `recent` list the endpoint returns is dropped here. It carries case
    ids, and this tool is granted to an agent that must stay above the member
    level; a rate is what a manager is asking about anyway.
    """
    body = await services().get(
        "governance", "/governance/overrides", days=days or 90, product_code=product_code
    )
    return {key: value for key, value in body.items() if key != "recent"}


@tool(
    "governance.models",
    version="1.0",
    input_schema=inputs(),
    output_schema=OBJECT,
    purpose_tags=ANALYTICS,
    side_effects=READ,
    backing_service="governance",
)
async def governance_models() -> dict[str, Any]:
    """Which model serves each family, and what its card says about it."""
    return await services().get("governance", "/governance/models")
