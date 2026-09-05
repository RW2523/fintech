-- pgvector for the policy-clause index (docs/06 §7) and entity embeddings.
\connect cio
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
