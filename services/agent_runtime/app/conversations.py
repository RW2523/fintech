"""The member conversation log (T-071, docs/06 §2.3).

An assistant nobody can be held to is not a service, it is a liability. When a
member says "your app told me my payment was fine", the answer is a row saying
what it actually told them, which tools it read, and whether it refused.

Both sides are written in one transaction with the hardship event, so a
disclosure and the reply to it cannot come apart: either the cooperative has
the member's words and knows a person is needed, or it has neither and the
member was told the request could not be raised.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from app.db import session
from cio_common.ids import new_id
from cio_common.outbox import emit

__all__ = ["log_turns", "read_conversation"]


async def log_turns(
    *,
    member_id: str,
    conversation_id: str | None,
    said: str,
    reply: Any,
    case_id: str | None = None,
) -> str:
    """Write the member's turn and the assistant's, and emit any signal.

    `member.hardship_signal.v1` is produced here rather than by the
    notification service because this is where the disclosure was heard, and
    contracts/events.yaml names the agent runtime as its producer. It goes onto
    the outbox in the same transaction as the words that caused it.
    """
    async with session() as db:
        if conversation_id:
            existing = (
                await db.execute(
                    text(
                        "SELECT conversation_id FROM app_agent.conversation "
                        "WHERE conversation_id = :c AND member_id = :m"
                    ),
                    {"c": conversation_id, "m": member_id},
                )
            ).first()
            if existing is None:
                # A conversation id from somebody else's session is not
                # continued, it is replaced. Silently appending to it would put
                # one member's words in another member's log.
                conversation_id = None

        if not conversation_id:
            conversation_id = new_id("cnv")
            await db.execute(
                text("INSERT INTO app_agent.conversation (conversation_id, member_id) VALUES (:c, :m)"),
                {"c": conversation_id, "m": member_id},
            )

        await db.execute(
            text("""
            INSERT INTO app_agent.conversation_turn
              (turn_id, conversation_id, member_id, role, said, signal)
            VALUES (:turn_id, :c, :m, 'MEMBER', :said, :signal)
        """),
            {
                "turn_id": new_id("trn"),
                "c": conversation_id,
                "m": member_id,
                "said": said,
                "signal": reply.signal,
            },
        )

        answer = reply.answer or {}
        refusal = answer.get("refusal")
        await db.execute(
            text("""
            INSERT INTO app_agent.conversation_turn
              (turn_id, conversation_id, member_id, role, said, tools_used, refusal,
               signal, handoff_id)
            VALUES (:turn_id, :c, :m, 'ASSISTANT', :said, CAST(:tools AS jsonb),
                    CAST(:refusal AS jsonb), :signal, :handoff_id)
        """),
            {
                "turn_id": new_id("trn"),
                "c": conversation_id,
                "m": member_id,
                "said": answer.get("answer") or "",
                "tools": json.dumps(list(reply.tools_read)),
                "refusal": json.dumps(refusal) if refusal else None,
                "signal": reply.signal,
                "handoff_id": reply.handoff_id,
            },
        )

        if reply.signal:
            await emit(
                db,
                "member.hardship_signal",
                {
                    "source": "member_assistant",
                    "note_ref": reply.handoff_id or conversation_id,
                    "member_id": member_id,
                    "signal": reply.signal,
                },
                key=member_id,
                producer="agent_runtime",
                case_id=case_id,
            )

        await db.commit()

    return conversation_id


async def read_conversation(conversation_id: str) -> list[dict[str, Any]]:
    """Every turn, oldest first. An answer without its question cannot be judged."""
    async with session() as db:
        rows = (
            (
                await db.execute(
                    text("""
            SELECT turn_id, conversation_id, member_id, role, said, tools_used,
                   refusal, signal, handoff_id, at
              FROM app_agent.conversation_turn
             WHERE conversation_id = :c
             ORDER BY at, turn_id
        """),
                    {"c": conversation_id},
                )
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]
