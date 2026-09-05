# 04 — Data model (PostgreSQL 16 + pgvector)

One database `cio`; one schema per service; shared schemas `events`, `ledger`, `audit`. Alembic per
service (`services/<svc>/alembic`), schema name from settings. All tables: `created_at timestamptz not null default now()`.
Money `numeric(18,2)`; ids `text` (ULID with prefix); JSON payloads `jsonb` validated at the API boundary.

## 1. Shared: events, outbox, consumer offsets

```sql
create schema events;
create table events.outbox (
  event_id text primary key, name text not null, version int not null, key text not null,
  trace_id text, case_id text, producer text not null, payload jsonb not null,
  occurred_at timestamptz not null, created_at timestamptz not null default now(),
  dispatched_at timestamptz
);
create index on events.outbox (dispatched_at) where dispatched_at is null;
create table events.consumer_offsets (consumer text, event_id text, processed_at timestamptz, primary key (consumer, event_id));
-- trigger: after insert on events.outbox -> pg_notify('cio_events', event_id)
```

## 2. `core` (systems-of-record stub)

```sql
create schema core;
create table core.employer (employer_id text primary key, name text, sector text, template_id text, deduction_day int);
create table core.member (member_id text primary key, name_token text, dob date, joined_at date, status text,
  branch_id text, employer_id text references core.employer, salary_monthly numeric(18,2), identity_verified bool,
  contact_updated_at timestamptz, language text default 'en');
create table core.account (account_id text primary key, member_id text references core.member, product_code text,
  principal numeric(18,2), profit_rate numeric(6,4), tenor_months int, instalment numeric(18,2), due_day int,
  opened_at date, status text, restructured_at date);
create table core.schedule (schedule_id text primary key, account_id text references core.account, seq int, due_date date, amount_due numeric(18,2));
create table core.payment (payment_id text primary key, schedule_id text references core.schedule, paid_at timestamptz,
  amount_paid numeric(18,2), channel text, reversed bool default false);
create table core.deduction (deduction_id text primary key, member_id text, employer_id text, cycle text,
  expected_amount numeric(18,2), received_amount numeric(18,2), received_at timestamptz);
create table core.savings (member_id text, as_of date, balance numeric(18,2), primary key (member_id, as_of));
create table core.share_capital (member_id text, as_of date, units int, value numeric(18,2), primary key (member_id, as_of));
create table core.guarantor (account_id text, guarantor_member_id text, since date, primary key (account_id, guarantor_member_id));
create table core.outage_window (system text, from_ts timestamptz, to_ts timestamptz);
create table core.arrangement (arrangement_id text primary key, account_id text, type text, from_date date, to_date date);
create table core.outcome (account_id text, month date, late7 bool, late30 bool, late60 bool, late90 bool,
  cure bool, restructure bool, charge_off bool, primary key (account_id, month));
create table core.change_feed (seq bigserial primary key, table_name text, pk text, op text, at timestamptz default now());
create table core.write_log (id bigserial primary key, action text, payload jsonb, approval_token text, at timestamptz default now());
```

## 3. `app_member` (member intelligence)

```sql
create table app_member.member_event (
  event_id text not null, member_id text not null, account_id text, occurred_at timestamptz not null,
  ingested_at timestamptz not null default now(), event_type text not null, source_system text not null,
  source_record_id text not null, payload jsonb not null, data_quality jsonb not null, permitted_uses text[] not null,
  evidence_refs jsonb not null default '[]', primary key (member_id, occurred_at, event_id)
) partition by range (occurred_at);   -- monthly partitions created by migration helper
create index on app_member.member_event (member_id, event_type, occurred_at desc);
create table app_member.profile_projection (member_id text primary key, version text not null, body jsonb not null, as_of timestamptz not null);
create table app_member.member_state (member_id text, account_id text, state text not null, since timestamptz not null,
  reason jsonb not null, prev_state text, primary key (member_id, account_id));
```

## 4. Origination schemas

```sql
-- app_application
create table app_application.application (application_id text primary key, member_id text, product_code text,
  amount numeric(18,2), tenor_months int, purpose text, status text, created_at timestamptz, submitted_at timestamptz);
create table app_application.case (case_id text primary key, case_type text, member_id text, application_id text,
  account_id text, state text, opened_at timestamptz, closed_at timestamptz, current_snapshot_id text);
create table app_application.case_snapshot (snapshot_id text primary key, case_id text references app_application.case,
  body jsonb not null, hash text not null, created_at timestamptz not null);   -- body validated against CaseSnapshot schema; immutable (trigger rejects update/delete)

-- app_document
create table app_document.document (document_id text primary key, case_id text, member_id text, type text, version int,
  object_key text, sha256 text, phash text, pages int, classified_conf numeric(5,4), status text, uploaded_at timestamptz);
create table app_document.extraction (extraction_id text primary key, document_id text references app_document.document,
  field text, value text, norm_value jsonb, conf numeric(5,4), page int, bbox numeric[] , method text, created_at timestamptz);
create table app_document.finding (finding_id text primary key, case_id text, document_id text, code text, severity text,
  detail jsonb, evidence_refs jsonb, created_at timestamptz);
create table app_document.ground_truth (document_id text primary key, body jsonb);   -- synthetic only

-- app_feature
create table app_feature.feature_def (name text primary key, family text, window_days int, source text, permitted_uses text[], version text, description text);
create table app_feature.feature_snapshot (snapshot_id text primary key, member_id text, account_id text, as_of timestamptz, window_set text, created_at timestamptz);
create table app_feature.feature_value (snapshot_id text references app_feature.feature_snapshot, name text, value double precision, provenance jsonb, primary key (snapshot_id, name));
create table app_feature.baseline (member_id text, signal text, as_of date, median double precision, mad double precision, n int, primary key (member_id, signal, as_of));

-- app_policy
create table app_policy.policy_version (version text primary key, product_code text, kind text check (kind in ('policy','dff','autonomy')),
  body jsonb not null, approved_by text[], approved_at timestamptz, effective_from timestamptz, status text);
create table app_policy.calc (calc_id text primary key, tool text, version text, inputs_digest text, inputs jsonb, outputs jsonb, created_at timestamptz);
create table app_policy.kill_switch (product_code text primary key, enabled bool, activated_by text, activated_at timestamptz, reason text);
create table app_policy.sandbox_run (sandbox_id text primary key, product_code text, candidate jsonb, range jsonb, results jsonb, created_by text, created_at timestamptz);

-- app_risk / app_fraud / app_lmi
create table app_risk.model_run (model_run_id text primary key, snapshot_id text, model text, version text, outputs jsonb, created_at timestamptz);
create table app_fraud.assessment (assessment_id text primary key, snapshot_id text, findings jsonb, integrity_score int, level text, graph jsonb, created_at timestamptz);
create table app_lmi.forecast (forecast_id text primary key, member_id text, account_id text, as_of timestamptz, horizons jsonb, survival jsonb, drivers jsonb, model_versions jsonb);
create table app_lmi.change_point (member_id text, account_id text, signal text, detected_at timestamptz, cp_date date, method text, stats jsonb, primary key (member_id, account_id, signal, detected_at));
create table app_lmi.alert (alert_id text primary key, member_id text, account_id text, state text, why_now text, rank_value double precision, created_at timestamptz, closed_at timestamptz, case_id text);

-- app_committee / app_agent
create table app_committee.run (run_id text primary key, snapshot_id text, case_type text, tier text, state text, body jsonb, started_at timestamptz, ended_at timestamptz);
create table app_committee.opinion (opinion_id text primary key, run_id text references app_committee.run, agent_id text, agent_version text, round text, body jsonb not null, signature text not null, created_at timestamptz);
create table app_agent.agent_version (agent_id text, agent_version text, prompt_sha text, tools jsonb, schema_ref text, route text, created_at timestamptz, primary key (agent_id, agent_version));
create table app_agent.invocation (invocation_id text primary key, run_id text, agent_id text, agent_version text, tokens_in int, tokens_out int, seconds numeric(8,3), status text, tool_calls jsonb, created_at timestamptz);
```

## 5. `ledger` (append-only, hash-chained)

```sql
create schema ledger;
create table ledger.entry (
  seq bigserial primary key, entry_id text unique not null, kind text not null
    check (kind in ('SNAPSHOT','COMMITTEE_RUN','OPINION','DECISION_RECORD','HUMAN_DECISION','TOKEN','ACTION','OUTCOME','AUTONOMY_CHANGE','KILL_SWITCH','SAMPLE_REVIEW')),
  case_id text, member_id text, payload jsonb not null, hash text not null, prev_hash text not null,
  created_at timestamptz not null default now());
create index on ledger.entry (case_id, seq);
create or replace function ledger.reject_mutation() returns trigger language plpgsql as $$ begin raise exception 'ledger is append-only'; end $$;
create trigger ledger_no_update before update or delete on ledger.entry for each row execute function ledger.reject_mutation();
-- hash = sha256(prev_hash || canonical_json(payload)); prev_hash of first row = '0'*64; verified by GET /ledger/verify
create table ledger.token (token_id text primary key, action_id text, decision_record_id text, human_decision_id text, issued_to jsonb, scope jsonb,
  idempotency_key text unique, issued_at timestamptz, expires_at timestamptz, used_at timestamptz, signature text);
create table ledger.sample_review (sample_id text primary key, decision_record_id text, assigned_role text, due_at timestamptz, reviewed_by text, verdict text, notes text);
```

## 6. `audit`

```sql
create schema audit;
create table audit.entry (seq bigserial primary key, entry_id text unique, actor jsonb not null, action text not null,
  service text not null, case_id text, run_id text, object_ref jsonb, before jsonb, after jsonb, policy_version text,
  model_versions jsonb, trace_id text, ip text, hash text not null, prev_hash text not null, created_at timestamptz default now());
-- same append-only trigger; daily export to MinIO bucket 'audit-worm' with object lock (retention) enabled
```

## 7. Common helpers (`libs/cio_common`)

- `db.py`: async engine, `session()` context manager, `schema_for(service)`; migrations helper to create monthly partitions for `member_event` for the seeded date range plus 12 months.
- `outbox.py`: `emit(session, name, version, key, payload, case_id=None)` inside the caller's transaction; dispatcher task `run_dispatcher(consumers)`.
- `hashing.py`: `canonical_json(obj)` (sorted keys, no whitespace, UTF-8), `sha256`, `chain_hash(prev, payload)`, `hmac_sign(secret, obj)`.
- `audit.py`: `record(actor, action, ...)` writes to `audit.entry` via the audit-service API (or directly when running inside audit-service).

## 8. Retention classes (demo defaults)

operational 2 y · ledger 10 y · audit 10 y · documents 7 y · prompt/tool logs 90 d · synthetic: unlimited (no PII).
