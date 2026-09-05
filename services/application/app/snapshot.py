"""Freezing a CaseSnapshot (docs/03 §1, docs/08 §1).

Submitting an application freezes everything the case will be judged against.
Submitting again does not edit the snapshot: it writes a new version, so the
earlier decision remains reconstructable.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.versions import VersionSource
from cio_common.hashing import canonical_json, sha256
from cio_common.ids import new_id
from cio_common.outbox import emit

__all__ = ["freeze"]


async def freeze(
    db: AsyncSession,
    *,
    application: dict[str, Any],
    case_id: str,
    versions: VersionSource,
    document_ids: list[str],
    actor_id: str,
    actor_role: str,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """Build, store and announce a new snapshot for the case."""
    stamps = await versions.gather(
        member_id=application["member_id"],
        product_code=application["product_code"],
        document_ids=document_ids,
    )

    next_version = (
        await db.execute(
            text("""
        SELECT COALESCE(MAX(version), 0) + 1 FROM app_application.case_snapshot
        WHERE case_id = :case_id
    """),
            {"case_id": case_id},
        )
    ).scalar_one()

    body: dict[str, Any] = {
        "schema": "case_snapshot/1.0",
        "snapshot_id": new_id("snap"),
        "case_id": case_id,
        "case_type": "ORIGINATION",
        "member_id": application["member_id"],
        "product_code": application["product_code"],
        "requested_amount": f"{Decimal(str(application['amount'])):.2f}",
        "tenor_months": int(application["tenor_months"]),
        "purpose": application["purpose"],
        "member_snapshot_ver": stamps.member_snapshot_ver,
        "document_bundle_ver": stamps.document_bundle_ver,
        "feature_snapshot_id": new_id("fs"),
        "policy_version": stamps.policy_version,
        "dff_version": stamps.dff_version,
        "autonomy_version": stamps.autonomy_version,
        "model_versions": stamps.model_versions,
        "evidence_index_ver": stamps.evidence_index_ver,
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "created_by": {
            "actor_id": actor_id,
            "kind": "USER" if actor_role != "SYSTEM" else "SYSTEM",
            "role": actor_role,
        },
    }
    body["hash"] = sha256(canonical_json(body))

    await db.execute(
        text("""
        INSERT INTO app_application.case_snapshot
          (snapshot_id, case_id, version, body, hash)
        VALUES (:snapshot_id, :case_id, :version, CAST(:body AS jsonb), :hash)
    """),
        {
            "snapshot_id": body["snapshot_id"],
            "case_id": case_id,
            "version": next_version,
            "body": json.dumps(body),
            "hash": body["hash"],
        },
    )

    await db.execute(
        text("""
        UPDATE app_application.case SET current_snapshot_id = :snapshot_id
        WHERE case_id = :case_id
    """),
        {"snapshot_id": body["snapshot_id"], "case_id": case_id},
    )

    await emit(
        db,
        "application.submitted",
        {
            "snapshot_id": body["snapshot_id"],
            "product_code": body["product_code"],
            "member_id": body["member_id"],
            "application_id": application["application_id"],
            "snapshot_version": next_version,
        },
        key=case_id,
        producer="application",
        case_id=case_id,
        trace_id=trace_id,
    )

    return {**body, "snapshot_version": next_version}
