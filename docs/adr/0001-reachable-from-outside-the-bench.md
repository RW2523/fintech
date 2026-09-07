# ADR 0001 — Reaching the platform from outside the machine it runs on

- Status: accepted
- Date: 2026-09-06
- Task: post-P8, at the owner's request

## Context

`CLAUDE.md` §9 says the demo host is the DGX Spark itself and the web UIs are
served on the Spark's LAN address. Everything in the build follows from that:
sign-in is a role picker, `/api/auth/dev-token` mints a token for any role with
no password, and the signing secrets are the `dev-only-insecure-...` and
`change-me` defaults that ship in `docker/.env.example`.

The owner asked for a link other people can open.

On the LAN assumption those three facts are reasonable. Off it they compose
into an open administrative interface: anyone who can reach the URL can
`POST /api/auth/dev-token {"role": "head_of_credit"}`, receive a valid token
and then pull the kill switch, adopt a policy version, or approve a financing.
The secrets being public in git means a token can be forged even with that
endpoint closed. The data is synthetic, so nothing about a real person leaks;
what is at stake is a credit system anybody can operate and a demonstration
that could be tampered with mid-presentation.

This changes a documented architectural assumption, which `CLAUDE.md` §2.2 says
requires an ADR.

## Decision

The platform may be reachable from outside its machine, and when it is, it
requires an account.

1. **The environment decides.** `Settings.passwords_required` is true on
   `pilot` and `prod`, and on anything that sets `AUTH_MODE=password`. It gates
   both the sign-in screen and `/api/auth/dev-token`, which refuses outright.
   Derived from `CIO_ENV` rather than added as a second switch: that setting
   already gates prompt logging and placeholder secrets, and a platform with
   two switches for "is this exposed" is one where a deployment has the wrong
   answer in one of them.

2. **Accounts are a file.** `docker/users.yaml`, git-ignored, holding Argon2id
   hashes, written by `scripts/add_user.py`, read at startup. `CLAUDE.md` §4
   already says Keycloak is a pilot concern; a database table for eight demo
   accounts would be a migration for no gain.

3. **Real secrets are enforced, not requested.** The existing validator already
   refuses `dev-only-insecure-...` on `pilot` and `prod`, so a hardened
   deployment cannot start without real ones. `scripts/generate_secrets.sh`
   writes them.

4. **Rate limiting at the gateway**, in the Redis already in the stack. Three
   numbers, guarding two different things:

   - five **failed** sign-ins per address per minute. This stands in for a
     lockout, and what a lockout stops is guessing. The password store has no
     lockout of its own, deliberately: anybody who knows an address could then
     lock its owner out of their own platform.
   - sixty sign-in **requests** per address per minute, successes included,
     because verifying a password is deliberately expensive and an endpoint
     that will run Argon2 as often as it is asked is a way to spend this
     machine's CPU from outside it.
   - 240 API requests per principal per minute.

   The first two were one number to begin with, counting every attempt. The
   browser suite found the problem: it signs in for every test, spent the
   allowance in the first minute and locked itself out of the platform it was
   testing. A person who signs in, signs out and signs in again would have hit
   the same wall. Counting failures is both the correct rule and the one that
   lets the suite run.

5. **One origin.** The `web` container serves the built app and proxies `/api`
   to the gateway, with security headers and a `connect-src 'self'` policy.
   Whatever terminates TLS has one thing to point at and the browser makes no
   cross-origin request.

6. **TLS and the public name are somebody else's job.** A Cloudflare Tunnel or
   equivalent terminates TLS and holds the hostname. No certificate handling,
   no port forwarding, and no inbound firewall change on the Spark.

## Alternatives considered

**Leave it on the LAN and share a recording.** Still the right answer for most
audiences and costs nothing. Rejected because the owner asked for a link people
can use, and a recording cannot be interrogated.

**A private link gated by Cloudflare Access.** Email-gated, no code change,
twenty minutes. Genuinely safer than what is built here, because an attacker
never reaches the application at all. Rejected by the owner in favour of open
access; the option remains and stacks on top of this.

**Keycloak now.** The real answer for a pilot and too much for a demonstration:
an identity provider to run, a realm to configure, and a dependency in the
compose profile for eight accounts.

**Deploy to a cloud host instead of the Spark.** The Council needs the GB10.
Anywhere else the platform runs its deterministic path and degrades every
agent, which is a working demonstration of the fail-safe design and a poor
demonstration of the product.

## Consequences

- A published deployment cannot use the role picker, so the demo run-book's
  "sign in as Officer" step becomes "sign in as the officer account". The
  run-book says so.
- Losing `docker/users.yaml` means losing access; it is not in git by design.
  Whoever operates it keeps a copy.
- Rotating the signing secrets signs everybody out and invalidates every
  unredeemed approval token. `generate_secrets.sh` refuses to rotate real
  secrets without `--force`.
- Rate limiting depends on Redis. When Redis is unreachable the limiter allows
  the request and logs that it is not working: a platform that stops serving
  because its rate limiter is down has turned a cache outage into an outage.
- None of this makes the platform fit for real member data. It is
  demonstration hardening: no MFA, no password reset, no session revocation, no
  audit of sign-in attempts beyond the rate limiter's log.

## Contract / policy changes

No contract or schema version changes. `Settings` gains `auth_mode`,
`user_store`, `login_attempts_per_minute` and `requests_per_minute`.
`/api/auth/login` and `/api/auth/mode` are new and public;
`/api/auth/dev-token` is unchanged on a bench and refuses elsewhere.

## Tests affected

`services/gateway/tests/test_auth.py` is new: the store, the switch, and the
endpoint, including that a wrong password and an unknown address fail
identically, that dev tokens are refused once a password is required, that
repeated *successful* sign-ins are not treated as an attack, and that guessing
runs out without the account locking.

Everything that signs in had to learn both ways in, or this change would have
been made by breaking every check that could have caught it:

- `apps/web/tests/session.ts` is new. The Playwright suite asks
  `/api/auth/mode` and signs in the way that deployment signs people in. It
  passes in both modes and against a published URL — `WEB_BASE_URL` points it
  at one instead of a dev server, so what is actually serving can be tested and
  not merely demonstrated.
- `scripts/dev_token.sh` and `scripts/signin.py` do the same for the drills, the
  harness and the security suite, all of which minted dev tokens inline and all
  of which stopped working the first time a password was required. A drill that
  cannot start has not passed.
- `docker/test-accounts.json`, git-ignored, holds the demo passwords for those
  scripts, beside the store it was generated from and at the same trust level.

The one thing the suite cannot check for itself is the mode it is not in, so it
was run in both.
