"""agent-runtime endpoints (docs/06 §1, docs/08 §5)."""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from ai.agents.bundle import list_bundles, load_bundle
from ai.tools import registry, tool_names
from app.copilot import CopilotQuestion, answer_question
from app.gateway_client import GatewayClient, HttpGatewayClient
from app.guided import prune_defs, simplify
from app.invoke import OPINION_SCHEMA_ID, Invocation, invoke
from cio_common.errors import Forbidden, NotFound, ValidationFailed
from cio_contracts import bundle as contract_bundle
from cio_tools.grants import Grant, GrantRegistry, ToolDenied
from cio_tools.registry import ToolContext
from cio_tools.spec import PermittedUse, SideEffect

router = APIRouter(tags=["agent_runtime"])

#: Swapped for a fake in tests.
_client: GatewayClient | None = None


def gateway_client() -> GatewayClient:
    global _client
    if _client is None:
        _client = HttpGatewayClient(os.environ.get("LLM_GATEWAY_URL"))
    return _client


def set_gateway_client(client: GatewayClient | None) -> None:
    global _client
    _client = client


@lru_cache(maxsize=1)
def opinion_schema() -> dict[str, Any]:
    """The AgentOpinion contract, as a schema the model is given.

    Taken from the published contract rather than restated here, so an agent
    is asked for exactly the shape the rest of the platform validates.
    """
    document = contract_bundle()
    return {"$schema": document["$schema"], "$ref": "#/$defs/AgentOpinion", "$defs": document["$defs"]}


#: The fields the runtime sets itself (docs/06 §1). An agent that could set its
#: own id or version could claim to be a different agent, so the model is asked
#: only for what it decides.
MODEL_FIELDS = (
    "stance",
    "confidence",
    "reason_codes",
    "claims",
    "contradictions",
    "unresolved",
    "proposed_actions",
    "factor_scores",
    "changed_from_prior",
)


@lru_cache(maxsize=1)
def model_output_schema() -> dict[str, Any]:
    """The part of an opinion the model is responsible for."""
    full = opinion_schema()
    opinion = full["$defs"]["AgentOpinion"]
    # Simplified for the grammar engine: a decoder cannot compile
    # `propertyNames`, and the runtime revalidates the real contract anyway.
    return prune_defs(
        simplify(
            {
                "$schema": full["$schema"],
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "stance",
                    "confidence",
                    "reason_codes",
                    "claims",
                    "contradictions",
                    "unresolved",
                    "proposed_actions",
                ],
                "properties": {
                    name: opinion["properties"][name]
                    for name in MODEL_FIELDS
                    if name in opinion["properties"]
                },
                "$defs": full["$defs"],
            }
        )
    )


class InvokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=2, max_length=64)
    committee_run_id: str = Field(min_length=3, max_length=64)
    snapshot: dict[str, Any]
    round: str = Field(default="ASSESS", pattern="^(ASSESS|CHALLENGE|REVISE|TEMPORAL)$")
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    clauses: list[dict[str, Any]] = Field(default_factory=list)
    prior_opinions: list[dict[str, Any]] = Field(default_factory=list)
    temporal_context: dict[str, Any] | None = None
    run_id: str = ""
    budget_tokens: int = Field(default=0, ge=0)


class ToolCallRequest(BaseModel):
    """A tool call made by the orchestrator, not by an agent (docs/06 §8).

    The Challenger names a gap and the tool that would fill it; the
    orchestrator calls that tool itself. An agent allowed to fill its own gap
    would be deciding what counts as evidence about its own case.
    """

    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=3, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)
    committee_run_id: str = Field(min_length=3, max_length=64)
    #: Whose behalf. Only the workflow may repair evidence; an agent id here
    #: would be an agent calling a tool outside its own invocation.
    principal: str = Field(default="workflow", pattern="^workflow$")
    purpose: str = Field(default="UNDERWRITING")
    case_id: str | None = None
    member_id: str | None = None
    max_calls: int = Field(default=2, ge=1, le=10)


@router.get("/tools", summary="Every registered tool")
async def tools() -> dict[str, Any]:
    return {"tools": list(tool_names()), "count": len(tool_names())}


@router.post("/tools/call", summary="Call one tool as the workflow")
async def call_tool(body: ToolCallRequest) -> dict[str, Any]:
    """Run a tool outside any agent's invocation, for evidence repair.

    The grant is minted for this call rather than read from an agent bundle:
    the caller is the orchestrator, which has no bundle, and the tool's own
    purpose tags and field purposes still decide what comes back.
    """
    try:
        purpose = PermittedUse[body.purpose.upper()]
    except KeyError as exc:
        raise ValidationFailed(
            f"unknown purpose {body.purpose!r}",
            purposes=[p.name for p in PermittedUse],
        ) from exc

    try:
        spec = registry.get(body.tool)
    except KeyError as exc:
        raise NotFound(str(exc)) from exc

    if spec.side_effects is SideEffect.WRITE_PROPOSAL and spec.name != "evidence.request":
        # Repair may read, and may register a request for something missing.
        # It may not act on the case: acting is a person's to do, and a
        # Challenger that could name an action tool could move the case by
        # asking for it.
        raise Forbidden(
            f"tool {body.tool!r} writes, and evidence repair may only read",
            tool=body.tool,
        )

    scoped = registry.with_grants(
        GrantRegistry([Grant(agent_id=body.principal, tool=body.tool, max_calls=body.max_calls)])
    )

    context = ToolContext(
        agent_id=body.principal,
        run_id=body.committee_run_id,
        purpose=purpose,
        principal=body.principal,
        case_id=body.case_id,
        member_id=body.member_id,
    )

    try:
        result = await scoped.call(body.tool, dict(body.args), context)
    except ToolDenied as denied:
        raise Forbidden(str(denied), tool=body.tool, reason=denied.reason) from denied

    invocation = scoped.invocations[-1] if scoped.invocations else None
    return {
        "tool": body.tool,
        "version": spec.version,
        "result": result,
        "call_id": invocation.call_id if invocation else None,
        "evidence_ids": list(invocation.evidence_ids) if invocation else [],
        "seconds": round(invocation.seconds, 4) if invocation else 0.0,
    }


@router.get("/agents", summary="Every agent bundle and its version")
async def agents() -> dict[str, Any]:
    entries = []
    for agent_id in list_bundles():
        try:
            entries.append(load_bundle(agent_id).as_dict())
        except (ValueError, FileNotFoundError) as exc:
            entries.append({"agent_id": agent_id, "error": str(exc)})
    return {"agents": entries, "count": len(entries), "output_schema": OPINION_SCHEMA_ID}


@router.post("/agents/invoke", summary="Run one agent and return its opinion")
async def invoke_agent(body: InvokeRequest) -> dict[str, Any]:
    try:
        agent = load_bundle(body.agent_id)
    except FileNotFoundError as exc:
        raise NotFound(f"no agent bundle {body.agent_id!r}") from exc
    except ValueError as exc:
        raise ValidationFailed(str(exc)) from exc

    if body.round == "REVISE" and not body.prior_opinions:
        # docs/06 §3 — a revision without the prior round is not a revision.
        raise ValidationFailed("a REVISE round needs prior_opinions", agent_id=body.agent_id)

    result = await invoke(
        Invocation(
            bundle=agent,
            snapshot=body.snapshot,
            committee_run_id=body.committee_run_id,
            round=body.round,
            tool_results=body.tool_results,
            clauses=body.clauses,
            prior_opinions=body.prior_opinions,
            temporal_context=body.temporal_context,
            run_id=body.run_id or body.committee_run_id,
            budget_tokens=body.budget_tokens,
        ),
        client=gateway_client(),
        output_schema=model_output_schema(),
    )
    return result.as_contract()


# ---------------------------------------------------------------------------
# copilots (docs/06 §2.3, docs/09 §3.5)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def answer_schema() -> dict[str, Any]:
    """The CopilotAnswer contract, as a schema the model is given."""
    document = contract_bundle()
    return {
        "$schema": document["$schema"],
        "$ref": "#/$defs/CopilotAnswer",
        "$defs": document["$defs"],
    }


class AskRequest(BaseModel):
    """A question about one case."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(default="officer_copilot", min_length=2, max_length=64)
    case_id: str = Field(min_length=3, max_length=64)
    question: str = Field(min_length=3, max_length=1000)
    decision_record_id: str | None = None
    run_id: str = ""
    budget_tokens: int = Field(default=0, ge=0)


#: What the copilot is given before it is asked anything. Gathered by the
#: service rather than called by the model: a copilot that chose its own reads
#: could read another case, and scoping the question is the one guarantee this
#: endpoint makes.
async def _gather(case_id: str, decision_record_id: str | None) -> list[dict[str, Any]]:
    from ai.tools import registry
    from cio_tools.grants import Grant, GrantRegistry
    from cio_tools.registry import ToolContext
    from cio_tools.spec import PermittedUse

    wanted: list[tuple[str, dict[str, Any]]] = [
        ("case.get", {"case_id": case_id}),
        ("evidence.search", {"case_id": case_id}),
    ]
    if decision_record_id is None:
        # Found from the case rather than required from the caller. Without the
        # record the copilot has no recommendation, no gates and no factor
        # scores, and it will refuse half the questions an officer asks while
        # looking as though it decided to.
        decision_record_id = await _record_for(case_id)
    if decision_record_id:
        wanted.append(("decision_record.get", {"decision_record_id": decision_record_id}))

    scoped = registry.with_grants(
        GrantRegistry([Grant(agent_id="copilot", tool=name, max_calls=2) for name, _ in wanted])
    )
    context = ToolContext(
        agent_id="copilot",
        run_id=case_id,
        purpose=PermittedUse.UNDERWRITING,
        principal="copilot",
        case_id=case_id,
    )

    gathered: list[dict[str, Any]] = []
    for name, args in wanted:
        try:
            result = await scoped.call(name, args, context)
        except Exception as exc:
            # Named rather than dropped: an answer that could not read the
            # decision record should be able to say so.
            gathered.append({"tool": name, "unavailable": str(exc)})
            continue
        gathered.append({"tool": name, "result": result, "evidence_refs": []})
    return gathered


async def _record_for(case_id: str) -> str | None:
    """The decision record on a case, if there is one."""
    import httpx

    url = os.environ.get("DECISION_URL", "http://decision:8012").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{url}/queue")
            response.raise_for_status()
    except httpx.HTTPError:
        return None
    for row in response.json().get("decisions") or []:
        if row.get("case_id") == case_id:
            return str(row.get("decision_record_id"))
    return None


@router.post("/copilot/ask", summary="Answer a question about one case")
async def ask(body: AskRequest, request: Request) -> dict[str, Any]:
    """docs/09 §3.5 — grounded question and answer over the case in front of
    an officer.

    The case is gathered here and handed to the model. The model does not
    choose what to read, because a copilot that could would eventually read
    somebody else's case, and being confined to this one is the guarantee that
    makes the panel safe to put in front of an officer.
    """
    # A member's token reaches this service like anybody else's: the gateway
    # authenticates and the service authorises. Until roles are enforced across
    # the API (T-084) this is the one place it matters, because the member
    # assistant is the first member-facing token in the platform and this
    # endpoint reads whole case files by id.
    if request.headers.get("X-Principal-Role") == "member":
        raise Forbidden("the case copilot is for staff; members use /assistant/ask")

    try:
        bundle = load_bundle(body.agent_id)
    except FileNotFoundError as exc:
        raise NotFound(f"no agent bundle {body.agent_id!r}") from exc

    if bundle.family != "copilot":
        raise ValidationFailed(
            f"{body.agent_id} is a {bundle.family} agent, not a copilot",
            agent_id=body.agent_id,
        )

    tool_results = await _gather(body.case_id, body.decision_record_id)
    result = await answer_question(
        CopilotQuestion(
            agent_id=body.agent_id,
            question=body.question,
            case_id=body.case_id,
            tool_results=tool_results,
            run_id=body.run_id,
            budget_tokens=body.budget_tokens,
        ),
        bundle=bundle,
        client=gateway_client(),
        output_schema=simplify(prune_defs(answer_schema())),
    )

    return {
        **result.as_contract(),
        "case_id": body.case_id,
        "agent_id": body.agent_id,
        "agent_version": bundle.agent_version,
        "tools_read": [entry["tool"] for entry in tool_results],
        "tools_unavailable": [entry["tool"] for entry in tool_results if "unavailable" in entry],
    }


# ---------------------------------------------------------------------------
# member assistant (T-071, docs/06 §2.3, docs/09 §5)
# ---------------------------------------------------------------------------
class AssistantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    agent_id: str = Field(default="member_assistant", min_length=1, max_length=64)
    conversation_id: str | None = None
    case_id: str | None = None
    run_id: str = ""
    budget_tokens: int = 0
    #: Only honoured when the request carries no member identity of its own.
    #: A token that names a member always wins, so a member cannot ask about
    #: anybody else by putting an id in the body.
    member_id: str | None = None


def _member_of(request: Request, body: AssistantRequest) -> str:
    """Whose records this turn may read.

    The gateway validates the token and forwards the member it names. That
    header is the identity: the body's `member_id` is accepted only when there
    is no header at all, which is the case for a service calling in directly,
    and is ignored the moment a real member is signed in. Identity that can be
    typed is not identity.
    """
    from_token = request.headers.get("X-Principal-Member")
    member_id = from_token or body.member_id
    if not member_id:
        raise Forbidden("the member assistant needs a member; sign in first")
    if from_token and body.member_id and body.member_id != from_token:
        raise Forbidden("you can only ask about your own records")
    return member_id


@router.post("/assistant/ask", summary="Answer a member's question about their own records")
async def assistant_ask(body: AssistantRequest, request: Request) -> dict[str, Any]:
    """docs/09 §5 — the member assistant.

    Distress is classified before the model is called, refusals are decided by
    rule, and both sides of every turn are written down. The model's only job
    is the questions a member may safely be answered.
    """
    from app.assistant import answer_member
    from app.conversations import log_turns

    member_id = _member_of(request, body)

    try:
        bundle = load_bundle(body.agent_id)
    except FileNotFoundError as exc:
        raise NotFound(f"no agent bundle {body.agent_id!r}") from exc
    if bundle.family != "copilot":
        raise ValidationFailed(
            f"{body.agent_id} is a {bundle.family} agent, not a copilot",
            agent_id=body.agent_id,
        )

    reply = await answer_member(
        member_id=member_id,
        question=body.question,
        bundle=bundle,
        client=gateway_client(),
        output_schema=simplify(prune_defs(answer_schema())),
        case_id=body.case_id,
        run_id=body.run_id,
        budget_tokens=body.budget_tokens,
    )

    conversation_id = await log_turns(
        member_id=member_id,
        conversation_id=body.conversation_id,
        said=body.question,
        reply=reply,
        case_id=body.case_id,
    )

    return {
        **reply.as_contract(),
        "member_id": member_id,
        "conversation_id": conversation_id,
        "agent_id": body.agent_id,
        "agent_version": bundle.agent_version,
    }


@router.get("/assistant/conversations/{conversation_id}", summary="One conversation, both sides")
async def assistant_conversation(conversation_id: str, request: Request) -> dict[str, Any]:
    """What was actually said. Scoped to the member in the token: a member may
    read their own conversation and nobody else's."""
    from app.conversations import read_conversation

    turns = await read_conversation(conversation_id)
    member = request.headers.get("X-Principal-Member")
    if member and any(turn["member_id"] != member for turn in turns):
        raise Forbidden("that conversation is not yours")
    return {"conversation_id": conversation_id, "turns": turns, "count": len(turns)}


# ---------------------------------------------------------------------------
# manager copilot (T-072, docs/09 §7.2)
# ---------------------------------------------------------------------------
class PortfolioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    agent_id: str = Field(default="manager_copilot", min_length=1, max_length=64)
    days: int = Field(default=90, ge=1, le=3650)
    product: str | None = None
    run_id: str = ""
    budget_tokens: int = 0


#: What the copilot reads before answering. Fixed, and read in full: a manager
#: asking why approvals fell needs the metrics that could account for it, and a
#: model choosing which to fetch would fetch the one the question named and
#: answer from that alone.
PORTFOLIO_METRICS = (
    "applications",
    "routing",
    "recommendations",
    "tiers",
    "authority",
    "delinquency",
    "early_warning",
)


async def _portfolio(days: int, product: str | None) -> list[dict[str, Any]]:
    from ai.tools import registry as tool_registry

    grants = GrantRegistry(
        [
            Grant(agent_id="manager_copilot", tool="metrics.query", max_calls=len(PORTFOLIO_METRICS)),
            Grant(agent_id="manager_copilot", tool="governance.overrides", max_calls=1),
        ]
    )
    scoped = tool_registry.with_grants(grants)
    context = ToolContext(
        agent_id="manager_copilot",
        run_id=f"portfolio:{days}",
        purpose=PermittedUse.ANALYTICS,
        principal="manager_copilot",
    )

    # An optional input is one that may be absent, not one that may be null.
    # Passing `product: None` fails the tool's own schema, and every metric came
    # back "unavailable" while the model dutifully refused for want of evidence
    # it had never been given.
    scope = {"product": product} if product else {}
    wanted: list[tuple[str, dict[str, Any]]] = [
        ("metrics.query", {"metric": name, "days": days, **scope}) for name in PORTFOLIO_METRICS
    ]
    wanted.append(("governance.overrides", {"days": days, **({"product_code": product} if product else {})}))

    gathered: list[dict[str, Any]] = []
    for name, args in wanted:
        label = f"{name}:{args.get('metric', 'overrides')}"
        try:
            result = await scoped.call(name, args, context)
        except Exception as exc:
            # Named rather than dropped. An answer that could not read
            # delinquency should be able to say so instead of answering as
            # though the book were clean.
            gathered.append({"tool": label, "unavailable": str(exc)})
            continue
        gathered.append({"tool": label, "result": result, "evidence_refs": []})
    return gathered


@router.post("/copilot/portfolio", summary="Answer a manager's question about the book")
async def ask_portfolio(body: PortfolioRequest, request: Request) -> dict[str, Any]:
    """docs/09 §7.2 — ask the portfolio.

    Every figure comes from the same metrics endpoint the cockpit tiles read,
    so a tile and this answer cannot disagree: they are one number rather than
    two calculations that happen to match.
    """
    from ai.guardrails.questions import check_manager_question

    if request.headers.get("X-Principal-Role") == "member":
        raise Forbidden("the portfolio copilot is for staff")

    try:
        bundle = load_bundle(body.agent_id)
    except FileNotFoundError as exc:
        raise NotFound(f"no agent bundle {body.agent_id!r}") from exc
    if bundle.family != "copilot":
        raise ValidationFailed(
            f"{body.agent_id} is a {bundle.family} agent, not a copilot",
            agent_id=body.agent_id,
        )

    tool_results = await _portfolio(body.days, body.product)
    result = await answer_question(
        CopilotQuestion(
            agent_id=body.agent_id,
            question=body.question,
            case_id="",
            tool_results=tool_results,
            run_id=body.run_id or f"portfolio:{body.days}",
            budget_tokens=body.budget_tokens,
        ),
        bundle=bundle,
        client=gateway_client(),
        output_schema=simplify(prune_defs(answer_schema())),
        guard=lambda text: check_manager_question(text),
        require_series=True,
    )

    return {
        **result.as_contract(),
        "agent_id": body.agent_id,
        "agent_version": bundle.agent_version,
        "window_days": body.days,
        "product": body.product,
        # The tables the answer rests on, returned so the cockpit can render
        # them beside the narrative. A paragraph of prose about a book, with no
        # table under it, is a claim rather than a report.
        "metrics": [
            {"metric": entry["tool"].split(":", 1)[-1], "result": entry["result"]}
            for entry in tool_results
            if "result" in entry
        ],
        "tools_read": [entry["tool"] for entry in tool_results if "result" in entry],
        "tools_unavailable": [entry["tool"] for entry in tool_results if "unavailable" in entry],
    }
