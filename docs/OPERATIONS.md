# Operations

How to start, stop, reset and recover the Credit Intelligence OS. Written for
somebody who has not built it.

Everything runs on one machine under docker compose. There are eighteen
services, a Postgres with pgvector, Redis, MinIO, Temporal, the observability
stack, and one or two vLLM containers holding model weights. The GPU is a
DGX Spark GB10 with 128 GB shared between everything on it.

---

## 1. First run

```bash
make env         # creates docker/.env from the example, then checks the machine
make install     # syncs the workspace with every optional stack
make up          # core, services and observability, no model
```

`make env` prints what it finds and what it expects. Read it: a port already
taken by something else on the host is the most common reason a first run does
not come up, and it says which.

The model is separate and slow:

```bash
make up-ai-local   # adds vLLM on the GB10; the first start pulls weights
```

Without it the platform still decides every case. The deterministic path does
not need a model, and the narrative says it is degraded rather than being
quietly absent. `make failsafe` is the proof.

### Putting a URL in front of it

A bench signs in with a role picker and `/api/auth/dev-token` mints a
`head_of_credit` token for whoever asks. That is right on a machine nobody else
can reach and an open administrative interface anywhere else: the token it
hands out can pull the kill switch and approve a financing. Before anything can
reach this from outside the machine:

```bash
scripts/generate_secrets.sh              # real signing secrets, not the shipped ones
uv run python scripts/write_test_accounts.py   # the demo accounts, with passwords
scripts/harden.sh --apply                # AUTH_MODE=password, then re-checks the rest
make down && make up && make up-ai-local # every service re-reads it
scripts/harden.sh                        # says yes to all four, or names what is missing
```

Then point a tunnel at the **web** container rather than the gateway: it serves
the app and proxies `/api`, so one hostname covers both and the browser makes
no cross-origin request.

```bash
cloudflared tunnel --url http://localhost:8080
```

The reasoning, the alternatives and what this does *not* make the platform fit
for are in `docs/adr/0001-reachable-from-outside-the-bench.md`. In short: it is
demonstration hardening on synthetic data. No MFA, no password reset, no
session revocation.

The drills and the browser suite keep working either way.
`scripts/dev_token.sh` and `scripts/signin.py` try the dev endpoint and sign in
with an account from `docker/test-accounts.json` when it refuses, so a
published deployment can still be verified rather than only demonstrated:

```bash
cd apps/web && WEB_BASE_URL=https://… GATEWAY_URL=https://… npx playwright test
```

---

## 2. Every day

| What | Command |
|---|---|
| Start | `make up` |
| Start with the model | `make up-ai-local` |
| Stop, keep the data | `make down` |
| Stop and wipe the data | `make down-hard` |
| Health of every container | `make ps` |
| Follow a service's logs | `make logs S=policy` |
| Migrate after a pull | `make migrate` |
| Are the routes served | `scripts/check_routes.sh` |

`scripts/check_routes.sh` compares what each service declares with what it
actually serves. It exists because the app factory registers `/health` before
the routers, so an import error in a router leaves a container reporting itself
healthy while serving nothing. That happened, for eleven hours.

---

## 3. Resetting the demo

```bash
make reset          # wipes the data volumes and rebuilds everything
```

About four and a half minutes. It asks before deleting anything unless you pass
`--yes`. Of that time, ninety seconds is building 453,000 timeline events from
the core records and seventy is loading five thousand members into it; both are
doing real work over the whole population.

It does **not** regenerate the synthetic population: `synthetic/out` is reused
if present, because generating it takes far longer and produces a population
nobody has checked. `make reset` with `--regenerate` builds it again, or
`make generate` on its own.

It leaves the model containers alone. They hold no demo data and take minutes
to load a checkpoint.

```bash
make demo           # reset, then every acceptance the platform has, then the run-book
make demo-quick     # the same without the model-bound checks
```

`make demo` is the thing to run the evening before. It exits non-zero on the
first failure and prints which check failed with its last twenty-five lines.

---

## 4. When something is wrong

**A container is unhealthy.** `make ps` shows which. `make logs S=<service>`
shows why. Most often it is a migration that has not run: `make migrate`.

**A service is healthy and answers 404 for everything.** Its router failed to
import. `scripts/check_routes.sh` names it; the logs have the traceback.

**The model is slow or unreachable.** The platform continues without it. Cases
route to a person, the narrative says `DEGRADED`, and no applicant is declined
for want of a model. To confirm, `make outage-drill` takes the gateway away and
checks exactly that. To switch to a hosted provider, set `LLM_PROVIDER_*` and
`LLM_BASE_URL_*` in `docker/.env` and `make up-ai-remote`.

**Retrieval says "lexical only".** The policy corpus indexed without vectors.
The message says which of the two happened: the embedding route was a
deterministic stand-in, or it could not be reached. If it is behind the API
gateway it needs a token:

```bash
CIO_TOKEN="$(scripts/dev_token.sh system)" uv run python -m ai.rag index
```

**A decision looks wrong.** Open it in the ledger viewer, or:

```bash
curl -H "authorization: Bearer $(scripts/dev_token.sh officer)" \
  localhost:8000/api/decision/decision-records/<id>
```

Every number carries a `calc_id` and every claim an evidence id. If the number
disagrees with the policy pack, the sandbox will say so: replay the case under
the version it was decided against.

**The chains.** Two independent hash chains, deliberately separate
implementations. Verify either:

```bash
curl -H "authorization: Bearer $(scripts/dev_token.sh compliance)" \
  localhost:8000/api/decision/ledger/verify
```

`make tamper-drill` alters a row in each and proves both go red.

---

## 5. Rolling back

**A model.** Artifacts are write-once. `ml/<family>/artifacts/latest.txt` names
the serving version; write the previous one into it and restart the service.
The service reports what it loaded on `GET /version`, and every score carries a
`model_run_id` naming it.

**A policy version.** Also write-once, and adoption never overwrites: adopting
again writes the next version. To go back, adopt the earlier candidate again
through the sandbox, with two approvers. Deleting a version is not a rollback:
decisions cite it, and a document that disappeared is not evidence.

**The autonomy dial.** `POST /api/policy/autonomy/{product}` with two distinct
approvers, both heads. The kill switch is faster and needs one owner:

```bash
curl -X POST -H "authorization: Bearer $(scripts/dev_token.sh head_of_credit)" \
  -H 'content-type: application/json' -d '{"reason":"why"}' \
  localhost:8000/api/policy/kill-switch/PF-STD
```

With the switch on, every route becomes `OFFICER_REVIEW` with `KILL_SWITCH` in
the reasons, and pending autonomous tokens are revoked.

---

## 6. What is where

| Thing | Where |
|---|---|
| Workbench | http://localhost:8080 |
| API and docs | http://localhost:8000/docs |
| Grafana | http://localhost:3001 |
| Prometheus | http://localhost:9090 |
| Temporal UI | http://localhost:8233 |
| MinIO console | http://localhost:9001 |
| Model (vLLM) | http://localhost:8100 |

Ports come from `docker/.env`; these are the defaults. `scripts/env_check.sh`
reports any that clash with something already on the host.

---

## 7. Observability

Six Grafana dashboards under "Credit Intelligence OS": service health, tier
latency, LLM usage, committee run, LMI nightly, ledger and audit growth.

A committee run is one trace across the gateway, the committee, the agent
runtime, the model gateway and the policy service. Find it in the committee-run
dashboard by case id or by the `committee_run_id` a DecisionRecord names.

Health checks and Prometheus scrapes are excluded from tracing. Every service
is polled for both several times a minute, and each poll was a trace: a real
one was a needle in a haystack the platform generated itself.

---

## 8. Safety rails worth knowing about

- **Nothing executes without a token.** L3 actions need an approval token that
  is single-use, scoped, expiring and HMAC-signed. A replayed token is refused.
- **No agent has a write tool.** The two exceptions record a proposal for a
  person and execute nothing.
- **Identity comes from the token.** The member assistant reads the member the
  gateway forwards from a signed token; an id in a request body is refused.
- **PII is masked before any provider call** and restored after. The masked
  field count is logged; the raw prompt is not, unless `CIO_DEBUG_PROMPTS=1`,
  which must never be set outside a local demo.
- **The seed refuses to run against `CIO_ENV=prod`.** There is no real member
  data in this build and nothing here is a place to put any.
- **Dev tokens refuse themselves once a password is required.** `AUTH_MODE=password`
  (and `CIO_ENV` of `pilot` or `prod`) closes `/api/auth/dev-token` and hides
  the role picker. `scripts/harden.sh` checks it rather than trusting it.
- **Guessing runs out, accounts do not lock.** Five failed sign-ins per address
  per minute; successes are not counted, so nobody is locked out of their own
  platform by signing in. A separate, looser cap on sign-in *requests* stops one
  caller spending this machine's CPU on password hashing. Redis being down
  allows the request and logs that the limit is not working — a rate limiter
  that takes the platform down with it has turned a cache outage into an outage.
