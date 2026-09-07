# Demo run-book

Forty-five minutes, Board format. Ten scenarios, every one of them live against
the running platform. Nothing here is a recording or a mock-up.

Written so somebody who did not build this can run it. Every URL and case id is
real and every claim in the talking points is one the platform will demonstrate
on the screen in front of you.

---

## Before you start

The evening before:

```bash
make demo
```

Reset, every acceptance, then this table printed back at you. It exits
non-zero on the first failure and shows the last twenty-five lines of whatever
broke. Do not walk into a room on a red one.

Thirty minutes before:

```bash
make up-ai-local     # if the model is not already loaded
make warmup          # one call per route, so the first question is not the slow one
make ps              # everything healthy
```

Open these tabs in this order and sign in on each:

| Tab | URL | Sign in as |
|---|---|---|
| 1 | http://localhost:8080/officer | Officer |
| 2 | http://localhost:8080/collections | Collections |
| 3 | http://localhost:8080/sandbox | Manager |
| 4 | http://localhost:8080/manager | Manager |
| 5 | http://localhost:8080/member | Member |
| 6 | http://localhost:8080/ledger | Compliance |
| 7 | http://localhost:3001 | Grafana |

**How you sign in depends on the deployment.** On a bench it is a role picker:
press "Officer" and you are one. On anything reachable from outside this
machine — anything with `AUTH_MODE=password` — it is an email and a password,
and the picker is not shown (docs/adr/0001). The login screen tells you which
you have; you do not need to know in advance.

With the role picker, the member tab also needs a membership number.
`scripts/member_assistant_eval.py` prints one that has both an application and
a facility, and so does the last run of `make demo`. With accounts, the member
account is already one particular member and there is nothing to type.

Accounts come from `docker/users.yaml`, which is not in git. Whoever set the
deployment up has them; `scripts/add_user.py` adds one and
`scripts/write_test_accounts.py` creates the full demo set.

---

## The forty-five minutes

### 0–3 · Frame

The architecture figure. One sentence: this is a system of intelligence beside
the core, not a replacement for it, and nothing executes without the Autonomy
Dial saying so.

Worth saying early, because everything after it lands differently: **a model
never computes a number and never makes a decision.** Every figure on every
screen carries a `calc_id` or a `model_run_id`. The models argue; the policy
decides.

### 3–8 · S1, a clean application

Tab 1. Queue → `case_S1CLEAN` → approve.

- The queue is derived from the ledger, not from a work table beside it.
- Open the decision card. The weighted score, the five factors, the decisive
  one, and the gate results.
- Every number is clickable back to the calculation that produced it.

Talking point: speed, and completeness. This case took the fast path because
the policy pack says a clean case under 10,000 may.

### 8–16 · S2, an income discrepancy

Tab 1 → `case_S2INCOME`.

- Open the agent drawer. Five agents, their stances, and where they disagree.
- Click an evidence chip: the payslip, with the box the extractor read.
- "What would change the outcome" is computed, not written.
- Approve with a condition.

Talking point: deliberation with dissent recorded, and evidence you can open.
The Challenger raised an unresolved question and it did not block: a question
is not a veto.

### 16–20 · S3 and S4, when the rules bite

`case_S3TAMPER`: forensics and the reused image, with the graph link.
`case_S4BREACH`: the affordability card.

Talking point: a hard gate cannot be outvoted by a good score. S3 routes to
compliance because fraud is HIGH, whatever the affordability says. S4 fails
AFF-01 and needs a senior officer, because the policy says so and the policy is
a file you can read.

### 20–26 · S6, the Board changes the weights

Tab 3, the policy sandbox.

- Move 0.10 from CONDUCT to COMMITMENT. The sum must be exactly 1.00 and the
  page will not let you run it otherwise.
- Replay. Fifty-five decided cases, baseline against candidate, and the list of
  cases that would decide differently.
- Adopt, with two named approvers. Both must be heads and they must be two
  people.
- Reopen S1: the score has moved.

Talking point: the Board writes the policy and tests it first. Adoption is
write-once, so the version every past decision cites still reads back exactly
as it did.

### 26–31 · S7, bounded autonomy

Tab 4 → governance, or the API.

- Set PF-STD to AUTONOMOUS_WITHIN_LIMITS, two approvers.
- Submit a clean 6,000 case: it routes AUTONOMOUS, issues a token, executes
  against the core, and appends both to the ledger.
- Pull the kill switch. Submit the same case: OFFICER_REVIEW, with
  `KILL_SWITCH` in the reasons.

Talking point: authority that is bounded, revocable and sampled. One in ten
autonomous decisions goes to a senior officer to read afterwards.

`make autonomy-drill` does this from the command line if the room prefers it.

### 31–39 · S8 and S9, prevention

Tab 2, collections.

- A member who has paid on time for fourteen months and is now three days late,
  then eight. No missed payment yet.
- The change-point, with the date it was detected and the date it started.
- The corroboration table: deductions confirm, savings confirm, no outage
  explains it.
- Approve the outreach.

Then S9: an outage delayed three hundred receipts by two days. Nobody was
escalated. The alerts say the outage explains it.

Talking point: prevention with reasons, and no overreaction. The system that
notices drift must also notice when the drift is the bank's fault.

### 39–43 · S10, the member and the auditor

Tab 5, the member assistant.

- "What is my balance?" answered from the record, with what it rests on.
- "Will I be approved?" refused, with what happens instead.
- "I lost my job last week": no answer to a question nobody asked. A colleague
  is asked to call, and the row exists before the member is told.

Then tab 6, the ledger. Reconstruct S2: every input, every opinion, every
number, and the chain verifying green in under two minutes.

Talking point: safe member service, and an audit that takes minutes rather than
a fortnight.

### 43–45 · Close

Tab 4, the management cockpit. The tiles, and one question typed into "ask the
portfolio".

Talking point: what the Client provides, and eight weeks to a pilot.

---

## The ninety-minute technical version

Everything above, plus:

- **Grafana.** Open the committee-run dashboard, paste `case_S2INCOME`, and
  open the trace: 181 spans across five services for one deliberation.
- **Contracts.** `GET /api/decision/decision-records/{id}` beside the schema
  it validates against.
- **The harness.** `make harness` over the golden and adversarial sets.
- **Tool denial and injection.** `make security`: seventeen checks that each do
  the thing that must not work.
- **The outage.** `make outage-drill` stops the model gateway and submits a
  case. It completes, routes to a person, says it is degraded, and declines
  nobody.
- **The models.** `ml/credit_risk/artifacts/*/card.md` — including the
  limitations section, which is the part worth reading aloud.

---

## When something goes wrong

**The model is slow.** Say so and keep going. The deterministic path is the
demo; the narration is the garnish. If it is very slow, switch to the remote
provider profile or show `make outage-drill` instead, which turns the problem
into the point.

**A case routes somewhere unexpected.** Open the golden expectation:
`uv run python -m ai.evals.harness --set golden --no-agents` prints what each
case should decide and what it did. If they disagree, that is the harness doing
its job and it is a better conversation than the one you planned.

**A screen misbehaves.** Everything on it is available from the API. The ledger
viewer and `/docs` are the fallback, and they are more convincing anyway.

**Total failure.** The recordings in `docs/recordings/`, and questions. There
is more to talk about than there is time.

---

## What is not real

Every member, document, payment and decision in this build is generated. There
is no real member data anywhere in it, and the seed script refuses to run
against a production environment.

Two numbers in the model cards do not meet their targets, and they are in the
cards rather than in a footnote: the credit-risk champion reaches 0.7190
discrimination against a 0.72 target on a hold-out with 43 defaults, and the
early-warning calibration slope is 1.118 against a 0.9–1.1 band. Both are worth
saying out loud before somebody asks.
