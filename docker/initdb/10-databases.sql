-- Temporal keeps its own databases in the same PostgreSQL instance (docs/02 §3).
CREATE DATABASE temporal;
CREATE DATABASE temporal_visibility;

-- A separate database for tests whose fixtures truncate shared tables. Without
-- it, running the suite wipes the seeded demo population.
CREATE DATABASE cio_test;
