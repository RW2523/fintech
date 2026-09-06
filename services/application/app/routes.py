"""application-service endpoints (docs/08 §1)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import text

from app.db import session
from app.models import CreateApplication, SubmitApplication
from app.snapshot import freeze
from app.versions import HttpVersionSource, VersionSource
from cio_common.errors import NotFound
from cio_common.ids import new_id
from cio_common.outbox import emit

router = APIRouter(tags=["application"])

#: Swapped for a static source in tests.
_version_source: VersionSource | None = None


def version_source() -> VersionSource:
    global _version_source
    if _version_source is None:
        _version_source = HttpVersionSource(
            {
                "policy": os.environ.get("POLICY_URL", "http://policy:8004"),
                "core_stub": os.environ.get("CORE_STUB_URL", "http://core_stub:8010"),
                "risk": os.environ.get("RISK_URL", "http://risk:8006"),
                "fraud": os.environ.get("FRAUD_URL", "http://fraud:8007"),
                "lmi": os.environ.get("LMI_URL", "http://lmi:8008"),
                "document": os.environ.get("DOCUMENT_URL", "http://document:8002"),
                "llm_gateway": os.environ.get("LLM_GATEWAY_URL", "http://llm_gateway:8020"),
            }
        )
    return _version_source


def set_version_source(source: VersionSource | None) -> None:
    global _version_source
    _version_source = source


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


@router.post("/applications", summary="Create an application and open its case")
async def create_application(body: CreateApplication) -> dict[str, Any]:
    application_id = new_id("app")
    case_id = new_id("case")

    async with session() as db:
        await db.execute(
            text("""
            INSERT INTO app_application.application
              (application_id, member_id, product_code, amount, tenor_months, purpose, status)
            VALUES (:id, :member_id, :product_code, :amount, :tenor, :purpose, 'DRAFT')
        """),
            {
                "id": application_id,
                "member_id": body.member_id,
                "product_code": body.product_code,
                "amount": body.amount,
                "tenor": body.tenor_months,
                "purpose": body.purpose,
            },
        )
        await db.execute(
            text("""
            INSERT INTO app_application.case
              (case_id, case_type, member_id, application_id, state)
            VALUES (:case_id, 'ORIGINATION', :member_id, :application_id, 'OPEN')
        """),
            {"case_id": case_id, "member_id": body.member_id, "application_id": application_id},
        )
        await emit(
            db,
            "application.created",
            {
                "application_id": application_id,
                "member_id": body.member_id,
                "product_code": body.product_code,
            },
            key=case_id,
            producer="application",
            case_id=case_id,
        )

    return {"application_id": application_id, "case_id": case_id, "status": "DRAFT"}


@router.post("/applications/{application_id}/submit", summary="Freeze a CaseSnapshot and start underwriting")
async def submit_application(
    application_id: str, body: SubmitApplication, request: Request
) -> dict[str, Any]:
    async with session() as db:
        application = (
            (
                await db.execute(
                    text("""
            SELECT a.*, c.case_id FROM app_application.application a
            JOIN app_application.case c ON c.application_id = a.application_id
            WHERE a.application_id = :id
        """),
                    {"id": application_id},
                )
            )
            .mappings()
            .first()
        )
        if application is None:
            raise NotFound(f"no application {application_id!r}")

        snapshot = await freeze(
            db,
            application=dict(application),
            case_id=application["case_id"],
            versions=version_source(),
            document_ids=body.document_ids,
            actor_id=body.actor_id,
            actor_role=body.actor_role,
            trace_id=request.headers.get("X-Trace-Id"),
        )

        await db.execute(
            text("""
            UPDATE app_application.application
            SET status = 'SUBMITTED', submitted_at = :at
            WHERE application_id = :id
        """),
            {"id": application_id, "at": datetime.now(UTC)},
        )

    return {
        "application_id": application_id,
        "case_id": application["case_id"],
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_version": snapshot["snapshot_version"],
        "workflow_id": f"underwrite-{snapshot['snapshot_id']}",
    }


@router.get("/applications/by-member", summary="Every application this member has")
async def applications_by_member(member_id: str) -> dict[str, Any]:
    """One member's applications, for the member assistant (T-071).

    Declared before the `/applications/{application_id}` route below, which
    would otherwise match "by-member" as an application id and answer 404.

    The case state comes back as it is stored. Translating it into something a
    member should read is the assistant's job, not this service's: `SUBMITTED`
    means one thing to an underwriter and something more hopeful to an
    applicant, and only one of them is reading this response.
    """
    async with session() as db:
        rows = (
            (
                await db.execute(
                    text("""
            SELECT a.application_id, a.member_id, a.product_code, a.amount,
                   a.tenor_months, a.purpose, a.status, a.created_at, a.submitted_at,
                   c.case_id, c.state
              FROM app_application.application a
              LEFT JOIN app_application.case c ON c.application_id = a.application_id
             WHERE a.member_id = :member_id
             ORDER BY a.created_at DESC
             LIMIT 50
        """),
                    {"member_id": member_id},
                )
            )
            .mappings()
            .all()
        )
    entries = [dict(row) for row in rows]
    return {"member_id": member_id, "applications": entries, "count": len(entries)}


@router.get("/applications/{application_id}", summary="An application and its case")
async def get_application(application_id: str) -> dict[str, Any]:
    async with session() as db:
        application = (
            (
                await db.execute(
                    text("""
            SELECT a.*, c.case_id, c.state, c.current_snapshot_id
            FROM app_application.application a
            LEFT JOIN app_application.case c ON c.application_id = a.application_id
            WHERE a.application_id = :id
        """),
                    {"id": application_id},
                )
            )
            .mappings()
            .first()
        )
        if application is None:
            raise NotFound(f"no application {application_id!r}")

        snapshots = (
            (
                await db.execute(
                    text("""
            SELECT snapshot_id, version, hash, created_at
            FROM app_application.case_snapshot
            WHERE case_id = :case_id ORDER BY version
        """),
                    {"case_id": application["case_id"]},
                )
            )
            .mappings()
            .all()
        )

    return {
        "application_id": application["application_id"],
        "member_id": application["member_id"],
        "product_code": application["product_code"],
        "amount": f"{application['amount']:.2f}",
        "tenor_months": application["tenor_months"],
        "purpose": application["purpose"],
        "status": application["status"],
        "case_id": application["case_id"],
        "case_state": application["state"],
        "current_snapshot_id": application["current_snapshot_id"],
        "snapshots": [
            {
                "snapshot_id": s["snapshot_id"],
                "version": s["version"],
                "hash": s["hash"],
                "created_at": s["created_at"].isoformat(),
            }
            for s in snapshots
        ],
    }


@router.get("/cases/{case_id}/snapshot", summary="The current frozen snapshot")
async def get_snapshot(case_id: str, version: int | None = None) -> dict[str, Any]:
    async with session() as db:
        if version is None:
            row = (
                (
                    await db.execute(
                        text("""
                SELECT body FROM app_application.case_snapshot
                WHERE case_id = :case_id ORDER BY version DESC LIMIT 1
            """),
                        {"case_id": case_id},
                    )
                )
                .mappings()
                .first()
            )
        else:
            row = (
                (
                    await db.execute(
                        text("""
                SELECT body FROM app_application.case_snapshot
                WHERE case_id = :case_id AND version = :version
            """),
                        {"case_id": case_id, "version": version},
                    )
                )
                .mappings()
                .first()
            )
    if row is None:
        raise NotFound(f"no snapshot for case {case_id!r}")
    return _json(row["body"])


@router.get("/cases/{case_id}", summary="The case and its snapshot history")
async def get_case(case_id: str) -> dict[str, Any]:
    async with session() as db:
        case = (
            (
                await db.execute(
                    text("""
            SELECT * FROM app_application.case WHERE case_id = :case_id
        """),
                    {"case_id": case_id},
                )
            )
            .mappings()
            .first()
        )
        if case is None:
            raise NotFound(f"no case {case_id!r}")
        snapshots = (
            (
                await db.execute(
                    text("""
            SELECT snapshot_id, version, hash FROM app_application.case_snapshot
            WHERE case_id = :case_id ORDER BY version
        """),
                    {"case_id": case_id},
                )
            )
            .mappings()
            .all()
        )

    return {
        "case_id": case["case_id"],
        "case_type": case["case_type"],
        "member_id": case["member_id"],
        "application_id": case["application_id"],
        "state": case["state"],
        "current_snapshot_id": case["current_snapshot_id"],
        "snapshots": [dict(s) for s in snapshots],
    }
