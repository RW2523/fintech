"""Persistence for feature snapshots (docs/04 §4)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.compute import ComputedSnapshot
from app.registry import FEATURES, REGISTRY_VERSION
from cio_common.ids import new_id

__all__ = ["find_equivalent", "load_snapshot", "save_registry", "save_snapshot"]


async def save_registry(db: AsyncSession) -> int:
    """Publish the declared features so other services can read them."""
    for definition in FEATURES:
        row = definition.as_row()
        await db.execute(
            text("""
            INSERT INTO app_feature.feature_def
              (name, family, description, source, window_days, permitted_uses,
               dtype, version, monotone)
            VALUES (:name, :family, :description, :source, :window_days,
                    CAST(:permitted_uses AS text[]), :dtype, :version, :monotone)
            ON CONFLICT (name) DO UPDATE SET
              family = EXCLUDED.family, description = EXCLUDED.description,
              source = EXCLUDED.source, window_days = EXCLUDED.window_days,
              permitted_uses = EXCLUDED.permitted_uses, dtype = EXCLUDED.dtype,
              version = EXCLUDED.version, monotone = EXCLUDED.monotone,
              updated_at = now()
        """),
            row,
        )
    return len(FEATURES)


async def find_equivalent(db: AsyncSession, member_id: str, digest: str) -> str | None:
    """An existing snapshot with the same inputs, so identical work is not repeated."""
    return (
        await db.execute(
            text("""
        SELECT snapshot_id FROM app_feature.feature_snapshot
        WHERE member_id = :member_id AND inputs_digest = :digest
        ORDER BY created_at DESC LIMIT 1
    """),
            {"member_id": member_id, "digest": digest},
        )
    ).scalar_one_or_none()


async def save_snapshot(
    db: AsyncSession, snapshot: ComputedSnapshot, *, window_set: str = "origination"
) -> str:
    snapshot_id = new_id("fs")
    await db.execute(
        text("""
        INSERT INTO app_feature.feature_snapshot
          (snapshot_id, member_id, account_id, as_of, window_set, registry_version,
           inputs_digest)
        VALUES (:snapshot_id, :member_id, :account_id, :as_of, :window_set,
                :registry_version, :digest)
    """),
        {
            "snapshot_id": snapshot_id,
            "member_id": snapshot.member_id,
            "account_id": snapshot.account_id,
            "as_of": snapshot.as_of,
            "window_set": window_set,
            "registry_version": REGISTRY_VERSION,
            "digest": snapshot.inputs_digest,
        },
    )

    for name, value in snapshot.values.items():
        await db.execute(
            text("""
            INSERT INTO app_feature.feature_value
              (snapshot_id, name, value, provenance)
            VALUES (:snapshot_id, :name, :value, CAST(:provenance AS jsonb))
        """),
            {
                "snapshot_id": snapshot_id,
                "name": name,
                "value": value,
                "provenance": json.dumps(snapshot.provenance.get(name, {})),
            },
        )

    for name, text_value in snapshot.text_values.items():
        await db.execute(
            text("""
            INSERT INTO app_feature.feature_value
              (snapshot_id, name, text_value, provenance)
            VALUES (:snapshot_id, :name, :text_value, CAST(:provenance AS jsonb))
        """),
            {
                "snapshot_id": snapshot_id,
                "name": name,
                "text_value": text_value,
                "provenance": json.dumps(snapshot.provenance.get(name, {})),
            },
        )

    return snapshot_id


async def load_snapshot(db: AsyncSession, snapshot_id: str) -> dict[str, Any] | None:
    header = (
        (
            await db.execute(
                text("""
        SELECT * FROM app_feature.feature_snapshot WHERE snapshot_id = :snapshot_id
    """),
                {"snapshot_id": snapshot_id},
            )
        )
        .mappings()
        .first()
    )
    if header is None:
        return None

    rows = (
        (
            await db.execute(
                text("""
        SELECT name, value, text_value, provenance FROM app_feature.feature_value
        WHERE snapshot_id = :snapshot_id ORDER BY name
    """),
                {"snapshot_id": snapshot_id},
            )
        )
        .mappings()
        .all()
    )

    def decode(value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value

    return {
        "snapshot_id": header["snapshot_id"],
        "member_id": header["member_id"],
        "account_id": header["account_id"],
        "as_of": header["as_of"].isoformat().replace("+00:00", "Z"),
        "window_set": header["window_set"],
        "registry_version": header["registry_version"],
        "inputs_digest": header["inputs_digest"],
        "features": {r["name"]: (r["text_value"] if r["value"] is None else r["value"]) for r in rows},
        "provenance": {r["name"]: decode(r["provenance"]) for r in rows},
    }
