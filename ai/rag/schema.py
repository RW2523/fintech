"""The clause index (docs/06 §7, docs/04 §4).

One table in `app_agent`, carrying both a full-text vector and an embedding,
because neither alone is enough. Lexical search finds a clause when the
question uses its words, and fails when it uses a synonym. Vector search finds
it by meaning, and drifts on identifiers: `ELG-02` and `ELG-03` embed almost
identically. Retrieval scores both and adds them.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

__all__ = ["DDL", "EMBEDDING_DIMENSIONS", "apply_ddl", "ddl_statements"]

#: docs/06 §7 names `vector(1024)`; bge-m3 produces 1024 dimensions.
EMBEDDING_DIMENSIONS = 1024

DDL = f"""
CREATE SCHEMA IF NOT EXISTS app_agent;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS app_agent.clause (
  key          text PRIMARY KEY,
  clause_id    text NOT NULL,
  doc          text NOT NULL,
  product      text NOT NULL,
  version      text NOT NULL,
  heading      text NOT NULL,
  text         text NOT NULL,
  part         int NOT NULL DEFAULT 1,
  parts        int NOT NULL DEFAULT 1,
  tsv          tsvector,
  embedding    vector({EMBEDDING_DIMENSIONS}),
  indexed_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS clause_by_id ON app_agent.clause (clause_id);
CREATE INDEX IF NOT EXISTS clause_by_product ON app_agent.clause (product, version);
CREATE INDEX IF NOT EXISTS clause_tsv ON app_agent.clause USING gin (tsv);

-- Kept in step with the text by the database, so a row cannot be inserted with
-- a search vector that describes something else.
CREATE OR REPLACE FUNCTION app_agent.clause_tsv_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- Dots and underscores are split before indexing, so `member.tenure_months`
  -- contributes "tenure" and "months" as well as itself. Without that the
  -- searchable nouns of a rule are locked inside its identifiers and a
  -- question phrased in English matches nothing.
  NEW.tsv := setweight(to_tsvector('english', coalesce(NEW.clause_id, '')), 'A')
          || setweight(to_tsvector('english', coalesce(NEW.heading, '')), 'B')
          || setweight(to_tsvector('english',
               regexp_replace(coalesce(NEW.text, ''), '[._]', ' ', 'g')), 'C');
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS clause_tsv_sync ON app_agent.clause;
CREATE TRIGGER clause_tsv_sync BEFORE INSERT OR UPDATE ON app_agent.clause
  FOR EACH ROW EXECUTE FUNCTION app_agent.clause_tsv_update();
"""


def ddl_statements(ddl: str = DDL) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    in_body = False
    for line in ddl.splitlines():
        if "$$" in line:
            in_body = not in_body if line.count("$$") == 1 else in_body
        current.append(line)
        if line.rstrip().endswith(";") and not in_body:
            statement = "\n".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
    tail = "\n".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


async def apply_ddl(connection: Any) -> None:
    for statement in ddl_statements():
        await connection.execute(text(statement))
