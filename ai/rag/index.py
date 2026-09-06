"""Building and querying the clause index (docs/06 §7).

Retrieval is hybrid and the weighting is fixed at half each, because the two
halves fail in different places: lexical search misses a paraphrase, vector
search confuses one clause id for its neighbour. Neither is trusted alone.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ai.rag.chunk import Clause, chunk_corpus
from ai.rag.schema import EMBEDDING_DIMENSIONS, apply_ddl

__all__ = ["Hit", "index_corpus", "retrieve"]

#: docs/06 §7 — the two halves are weighted equally.
LEXICAL_WEIGHT = 0.5
VECTOR_WEIGHT = 0.5

#: docs/06 §7 — twenty candidates, reranked to five.
CANDIDATES = 20
RETURNED = 5


@dataclass(frozen=True, slots=True)
class Hit:
    """One retrieved clause and why it was retrieved."""

    clause_id: str
    doc: str
    version: str
    text: str
    score: float
    lexical: float
    vector: float
    rerank: float | None = None

    def as_evidence(self) -> dict[str, Any]:
        """The shape docs/06 §7 asks for: a RETRIEVED_CLAUSE."""
        return {
            "clause_id": self.clause_id,
            "doc": self.doc,
            "version": self.version,
            "text": self.text,
            "score": round(self.score, 4),
            "components": {
                "lexical": round(self.lexical, 4),
                "vector": round(self.vector, 4),
                "rerank": self.rerank,
            },
        }


def database_url() -> str:
    if url := os.environ.get("DATABASE_URL"):
        return url
    values: dict[str, str] = {}
    env = Path(__file__).resolve().parents[2] / "docker" / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return (
        f"postgresql+asyncpg://{values.get('POSTGRES_USER', 'cio')}:"
        f"{values.get('POSTGRES_PASSWORD', '')}@localhost:"
        f"{values.get('POSTGRES_PORT', '5432')}/{values.get('POSTGRES_DB', 'cio')}"
    )


def engine() -> AsyncEngine:
    return create_async_engine(database_url(), pool_pre_ping=True)


async def _embed(
    texts: list[str], *, gateway_url: str | None = None, timeout: float = 60.0
) -> list[list[float]] | None:
    """Vectors from the LLM gateway, or None when it cannot be reached.

    None rather than zeros: a zero vector is a point in space, and the index
    would happily rank against it. An index built without embeddings is
    lexical-only and says so.
    """
    base = (
        gateway_url or os.environ.get("LLM_GATEWAY_URL") or "http://localhost:8000/api/llm_gateway"
    ).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{base}/llm/embed", json={"texts": texts})
            response.raise_for_status()
            vectors = response.json().get("vectors") or []
    except (httpx.HTTPError, ValueError):
        return None
    if not vectors or len(vectors) != len(texts):
        return None
    return [list(v) for v in vectors]


def _pad(vector: list[float]) -> list[float]:
    """Fit a vector to the column width.

    The demo's embedding route may be a stand-in with a shorter vector than
    bge-m3's 1024. Padding keeps the index usable rather than failing the
    insert, and the shortfall is visible in the index report.
    """
    if len(vector) >= EMBEDDING_DIMENSIONS:
        return [float(v) for v in vector[:EMBEDDING_DIMENSIONS]]
    return [float(v) for v in vector] + [0.0] * (EMBEDDING_DIMENSIONS - len(vector))


async def index_corpus(corpus: Path, *, gateway_url: str | None = None, reset: bool = True) -> dict[str, Any]:
    """Chunk the corpus and write it to the index."""
    clauses: list[Clause] = chunk_corpus(corpus)
    if not clauses:
        raise FileNotFoundError(f"no policy corpus under {corpus}")

    vectors = await _embed([c.text for c in clauses], gateway_url=gateway_url)
    embedded = vectors is not None
    rows = [
        dict(clause.as_row(), embedding=str(_pad(vectors[position])) if vectors else None)
        for position, clause in enumerate(clauses)
    ]

    connection = engine()
    async with connection.begin() as conn:
        await apply_ddl(conn)
        if reset:
            await conn.execute(text("TRUNCATE app_agent.clause"))
        for row in rows:
            await conn.execute(
                text("""
                INSERT INTO app_agent.clause
                  (key, clause_id, doc, product, version, heading, text, part,
                   parts, embedding)
                VALUES (:key, :clause_id, :doc, :product, :version, :heading,
                        :text, :part, :parts, CAST(:embedding AS vector))
                ON CONFLICT (key) DO UPDATE SET
                  text = EXCLUDED.text, heading = EXCLUDED.heading,
                  version = EXCLUDED.version, embedding = EXCLUDED.embedding,
                  indexed_at = now()
            """),
                row,
            )
    await connection.dispose()

    return {
        "clauses": len(clauses),
        "documents": len({c.doc for c in clauses}),
        "clause_ids": len({c.clause_id for c in clauses}),
        "embedded": embedded,
        "retrieval": "hybrid" if embedded else "lexical only",
        "note": None
        if embedded
        else "the embedding route did not answer; retrieval is lexical "
        "until the index is rebuilt with it available",
    }


async def retrieve(
    query: str,
    *,
    product: str | None = None,
    version: str | None = None,
    limit: int = RETURNED,
    candidates: int = CANDIDATES,
    gateway_url: str | None = None,
    rerank: bool = True,
) -> list[Hit]:
    """The clauses most likely to answer this question."""
    vectors = await _embed([query], gateway_url=gateway_url) if rerank else None
    vector = str(_pad(vectors[0])) if vectors else None

    connection = engine()
    async with connection.connect() as conn:
        rows = (
            (
                await conn.execute(
                    text(f"""
            WITH asked AS (
              -- The question's terms, joined with OR. `websearch_to_tsquery`
              -- and `plainto_tsquery` both AND them, so a question carrying one
              -- noun the clause does not use scores zero however well the rest
              -- matches: "minimum membership tenure" missed a clause reading
              -- "tenure below minimum" for the word "membership" alone.
              SELECT to_tsquery('english',
                       coalesce(nullif(array_to_string(
                         tsvector_to_array(to_tsvector('english', :query)),
                         ' | '), ''), 'zzzznomatch')) AS any_term,
                     websearch_to_tsquery('english', :query) AS all_terms
            ),
            scored AS (
              SELECT clause_id, doc, version, text,
                     ts_rank_cd(tsv, (SELECT any_term FROM asked))
                     -- A clause matching every term is worth more than one
                     -- matching some of them, so exact phrasing still wins.
                     * CASE WHEN tsv @@ (SELECT all_terms FROM asked)
                            THEN 2.0 ELSE 1.0 END AS lexical,
                     -- Cast before the NULL test as well: asyncpg cannot
                     -- infer a parameter's type from `IS NULL` alone.
                     CASE WHEN CAST(:vector AS text) IS NULL OR embedding IS NULL
                          THEN 0.0
                          ELSE 1.0 - (embedding <=> CAST(:vector AS vector))
                     END AS vector
                FROM app_agent.clause
               WHERE (CAST(:product AS text) IS NULL
                      OR product = CAST(:product AS text) OR product = 'ALL')
                 AND (CAST(:version AS text) IS NULL
                      OR version = CAST(:version AS text))
            )
            -- The only values interpolated are the two module constants
            -- above, both floats. Everything from the caller is a bound
            -- parameter.
            SELECT clause_id, doc, version, text, lexical, vector,
                   {LEXICAL_WEIGHT} * (lexical / NULLIF(MAX(lexical) OVER (), 0))
                 + {VECTOR_WEIGHT} * vector AS score
              FROM scored
             ORDER BY score DESC NULLS LAST
             LIMIT :candidates
        """),
                    {
                        "query": query,
                        "vector": vector,
                        "product": product,
                        "version": version,
                        "candidates": candidates,
                    },
                )
            )
            .mappings()
            .all()
        )
    await connection.dispose()

    hits = [
        Hit(
            clause_id=r["clause_id"],
            doc=r["doc"],
            version=r["version"],
            text=r["text"],
            score=float(r["score"] or 0.0),
            lexical=float(r["lexical"] or 0.0),
            vector=float(r["vector"] or 0.0),
        )
        for r in rows
    ]
    return hits[:limit]
