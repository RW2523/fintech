"""Recording and reading fraud assessments (docs/04 §4)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.assess import RULES_VERSION, Assessment

__all__ = ["find", "latest_for_case", "load_graph", "save"]

_ASSESSMENT_COLUMNS = (
    "assessment_id",
    "case_id",
    "snapshot_id",
    "member_id",
    "level",
    "integrity_score",
    "calc_id",
    "graph_ref",
    "anomaly_score",
    "rules_version",
    "evidence_refs",
    "latency_ms",
    "created_at",
)
assert all(name.isidentifier() for name in _ASSESSMENT_COLUMNS), _ASSESSMENT_COLUMNS
_ASSESSMENT = ", ".join(_ASSESSMENT_COLUMNS)
#: `created_at` is the database's to set, never the caller's.
_INSERTABLE = ", ".join(n for n in _ASSESSMENT_COLUMNS if n != "created_at")

_FINDING_COLUMNS = (
    "finding_id",
    "code",
    "severity",
    "rule",
    "advisory",
    "detail",
    "members",
    "documents",
    "evidence_refs",
)
assert all(name.isidentifier() for name in _FINDING_COLUMNS), _FINDING_COLUMNS
_FINDINGS = ", ".join(_FINDING_COLUMNS)


def decode_json(value: Any) -> Any:
    """asyncpg decodes jsonb already; other drivers hand back a string."""
    if isinstance(value, str):
        return json.loads(value)
    return value


async def save(db: AsyncSession, assessment: Assessment) -> bool:
    """Write the assessment, its findings and the graph it read.

    Content-addressed, so re-assessing an unchanged case keeps the row a
    decision already cited rather than writing a second one beside it.
    """
    written = await db.execute(
        text(f"""
        INSERT INTO app_fraud.assessment ({_INSERTABLE})
        VALUES (:assessment_id, :case_id, :snapshot_id, :member_id, :level,
                :integrity_score, :calc_id, :graph_ref, :anomaly_score,
                :rules_version, CAST(:evidence_refs AS jsonb), :latency_ms)
        ON CONFLICT (assessment_id) DO NOTHING
        RETURNING assessment_id
    """),
        {
            "assessment_id": assessment.assessment_id,
            "case_id": assessment.case_id,
            "snapshot_id": assessment.snapshot_id,
            "member_id": assessment.member_id,
            "level": assessment.level,
            "integrity_score": assessment.integrity.score,
            "calc_id": assessment.integrity.calc_id,
            "graph_ref": assessment.graph_ref,
            "anomaly_score": assessment.anomaly_score,
            "rules_version": RULES_VERSION,
            "evidence_refs": json.dumps(assessment.evidence_refs),
            "latency_ms": assessment.latency_ms,
        },
    )
    first_time = written.scalar_one_or_none() is not None

    if first_time:
        for finding in assessment.findings:
            await db.execute(
                text("""
                INSERT INTO app_fraud.finding
                  (finding_id, assessment_id, code, severity, rule, advisory,
                   detail, members, documents, evidence_refs)
                VALUES (:finding_id, :assessment_id, :code, :severity, :rule,
                        :advisory, CAST(:detail AS jsonb), :members, :documents,
                        CAST(:evidence_refs AS jsonb))
                ON CONFLICT DO NOTHING
            """),
                {
                    "finding_id": finding.finding_id,
                    "assessment_id": assessment.assessment_id,
                    "code": finding.code,
                    "severity": finding.severity,
                    "rule": finding.rule,
                    "advisory": finding.advisory,
                    "detail": json.dumps(finding.detail),
                    "members": list(finding.members),
                    "documents": list(finding.documents),
                    "evidence_refs": json.dumps(list(finding.evidence_refs)),
                },
            )

    await db.execute(
        text("""
        INSERT INTO app_fraud.case_graph (graph_ref, case_id, member_id, hops,
                                          nodes, edges)
        VALUES (:graph_ref, :case_id, :member_id, :hops,
                CAST(:nodes AS jsonb), CAST(:edges AS jsonb))
        ON CONFLICT (graph_ref) DO NOTHING
    """),
        {
            "graph_ref": assessment.graph_ref,
            "case_id": assessment.case_id,
            "member_id": assessment.member_id,
            "hops": 1,
            "nodes": json.dumps(assessment.graph["nodes"]),
            "edges": json.dumps(assessment.graph["edges"]),
        },
    )
    await db.commit()
    return first_time


async def _findings_for(db: AsyncSession, assessment_id: str) -> list[dict[str, Any]]:
    rows = (
        (
            await db.execute(
                text(
                    f"SELECT {_FINDINGS} FROM app_fraud.finding "
                    "WHERE assessment_id = :id ORDER BY severity DESC, rule"
                ),
                {"id": assessment_id},
            )
        )
        .mappings()
        .all()
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        body = dict(row)
        body["detail"] = decode_json(body["detail"])
        body["evidence_refs"] = decode_json(body["evidence_refs"])
        body["members"] = list(body["members"] or [])
        body["documents"] = list(body["documents"] or [])
        out.append(body)
    return out


async def find(db: AsyncSession, assessment_id: str) -> dict[str, Any] | None:
    row = (
        (
            await db.execute(
                text(f"SELECT {_ASSESSMENT} FROM app_fraud.assessment WHERE assessment_id = :id"),
                {"id": assessment_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    body = dict(row)
    body["evidence_refs"] = decode_json(body["evidence_refs"])
    body["findings"] = await _findings_for(db, assessment_id)
    return body


async def latest_for_case(db: AsyncSession, case_id: str) -> dict[str, Any] | None:
    row = (
        (
            await db.execute(
                text(
                    f"SELECT {_ASSESSMENT} FROM app_fraud.assessment "
                    "WHERE case_id = :case_id ORDER BY created_at DESC LIMIT 1"
                ),
                {"case_id": case_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    body = dict(row)
    body["evidence_refs"] = decode_json(body["evidence_refs"])
    body["findings"] = await _findings_for(db, body["assessment_id"])
    return body


async def load_graph(db: AsyncSession, *, case_id: str) -> dict[str, Any] | None:
    row = (
        (
            await db.execute(
                text("""
        SELECT graph_ref, case_id, member_id, hops, nodes, edges, created_at
          FROM app_fraud.case_graph
         WHERE case_id = :case_id ORDER BY created_at DESC LIMIT 1
    """),
                {"case_id": case_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    body = dict(row)
    body["nodes"] = decode_json(body["nodes"])
    body["edges"] = decode_json(body["edges"])
    return body
