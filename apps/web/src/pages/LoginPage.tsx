import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { AUTHORITY, ROLES, type Role, useAuth } from "../auth";
import { Icon } from "../components/icons";
import { Button, Chip, Problem } from "../components/primitives";

/** docs/09 §1, docs/13 §1 — two ways in, and the deployment decides which.
 *
 *  On a bench this is a role picker: press "Officer" and you are one, because
 *  there are no accounts and pretending otherwise would be theatre. Anywhere
 *  reachable from outside that bench an account is required, because the
 *  picker's endpoint mints a head_of_credit token for whoever asks.
 *
 *  What both versions look like is the same: a list of roles, each saying what
 *  it may approve, and you choose the one you want to be. The difference is
 *  that the second asks for a password before it believes you. A demonstration
 *  is a sequence of "now watch what the manager sees", and a screen that made
 *  somebody recall an email address for each of those was making them work to
 *  prove something the password already proved.
 *
 *  The page asks the gateway which mode it is in rather than deciding for
 *  itself. A web app that made that judgement would be a second place to get
 *  it wrong. */
export function LoginPage() {
  const { mode, signable, signIn, signInAsRole, signInWithPassword } = useAuth();
  const navigate = useNavigate();
  const [busy, setBusy] = useState<Role | "password" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [memberId, setMemberId] = useState("");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [byEmail, setByEmail] = useState(false);

  function onwards(role: Role) {
    navigate(role === "member" ? "/member" : role === "collections" ? "/collections" : "/officer", {
      replace: true,
    });
  }

  /** The roles this deployment will actually sign somebody in as.
   *
   *  From the gateway when it requires a password — an account has to exist —
   *  and from the built-in list on a bench, where any role can be minted. */
  const offered: Role[] =
    mode === "password"
      ? signable.map((entry) => entry.role).filter((role) => role in AUTHORITY)
      : [...ROLES];

  async function choose(role: Role) {
    setBusy(role);
    setError(null);
    try {
      if (mode === "password") {
        onwards(await signInAsRole(role, password));
        return;
      }
      // A membership number is optional now: left blank, the gateway picks a
      // member who has records to talk about. Nobody demonstrating this knows
      // a membership number off the top of their head, and being made to find
      // one was the only step on this screen that required homework.
      await signIn(role, role === "member" ? memberId.trim() || undefined : undefined);
      onwards(role);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(null);
    }
  }

  async function submitEmail(event: React.FormEvent) {
    event.preventDefault();
    setBusy("password");
    setError(null);
    try {
      // Where a reader lands is decided by the role the server says the
      // account has, not by the screen they signed in on.
      onwards(await signInWithPassword(email.trim(), password));
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(null);
    }
  }

  const needsPassword = mode === "password";

  return (
    <div className="flex min-h-screen items-center justify-center bg-[--color-canvas] p-6">
      <div className="w-full max-w-xl">
        <header className="mb-6 flex items-center gap-3">
          <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-[--color-accent] text-white">
            <Icon.spark className="h-5 w-5" />
          </span>
          <div>
            <h1 className="text-lg font-semibold tracking-tight">Credit Intelligence OS</h1>
            <p className="text-sm text-[--color-muted]">
              {needsPassword
                ? "Choose who to sign in as, and give the demo password once."
                : "Choose a role. This is a demonstration build: there are no accounts."}
            </p>
          </div>
        </header>

        <div className="rounded-xl border border-[--color-line] bg-[--color-surface] p-5 shadow-[0_1px_3px_rgba(20,23,31,0.06)]">
          <div className="mb-4 flex items-center justify-between gap-3">
            <Chip tone="warn">SYNTHETIC DATA</Chip>
            {needsPassword ? (
              <button
                type="button"
                onClick={() => setByEmail((was) => !was)}
                className="text-xs text-[--color-muted] underline decoration-dotted hover:text-[--color-accent]"
              >
                {byEmail ? "Pick a role instead" : "Sign in with an email address"}
              </button>
            ) : null}
          </div>

          {error ? (
            <div className="mb-4">
              <Problem error={error} />
            </div>
          ) : null}

          {needsPassword && byEmail ? (
            <form className="flex flex-col gap-3" onSubmit={submitEmail} data-testid="password-signin">
              <Field label="Email">
                <input
                  data-testid="login-email"
                  type="email"
                  autoComplete="username"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  className="w-full rounded-lg border border-[--color-line] bg-[--color-surface] px-3 py-2 text-sm"
                />
              </Field>
              <Field label="Password">
                <input
                  data-testid="login-password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  className="w-full rounded-lg border border-[--color-line] bg-[--color-surface] px-3 py-2 text-sm"
                />
              </Field>
              <Button
                type="submit"
                variant="primary"
                testId="login-submit"
                disabled={busy !== null || !email.trim() || !password}
              >
                {busy === "password" ? "Signing in" : "Sign in"}
              </Button>
            </form>
          ) : (
            <div className="flex flex-col gap-4">
              {needsPassword ? (
                <Field label="Demo password">
                  <input
                    data-testid="login-password"
                    type="password"
                    autoComplete="current-password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder="Asked once. Every role below uses it."
                    className="w-full rounded-lg border border-[--color-line] bg-[--color-surface] px-3 py-2 text-sm"
                  />
                </Field>
              ) : (
                <Field label="Membership number — optional, only for the member persona">
                  <input
                    data-testid="member-id"
                    value={memberId}
                    onChange={(event) => setMemberId(event.target.value)}
                    placeholder="Left blank, a member with a history is chosen"
                    className="w-full rounded-lg border border-[--color-line] bg-[--color-surface] px-3 py-2 text-sm"
                  />
                </Field>
              )}

              <div>
                <p className="mb-2 text-xs font-medium text-[--color-muted]">Sign in as</p>
                <ul className="grid gap-2 sm:grid-cols-2">
                  {offered.map((role) => (
                    <li key={role}>
                      <button
                        type="button"
                        data-testid={`role-${role}`}
                        disabled={busy !== null || (needsPassword && !password)}
                        onClick={() => void choose(role)}
                        className="flex w-full flex-col gap-0.5 rounded-lg border border-[--color-line] bg-[--color-surface] px-3 py-2.5 text-left transition hover:border-[--color-accent] hover:bg-[--color-accent-soft] disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <span className="text-sm font-medium">
                          {busy === role ? "Signing in…" : AUTHORITY[role].label}
                        </span>
                        <span className="text-xs text-[--color-muted]">
                          {AUTHORITY[role].approves === null
                            ? "Approves any amount"
                            : AUTHORITY[role].approves === 0
                              ? "No approval authority"
                              : `Approves up to ${AUTHORITY[role].approves.toLocaleString()}`}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
                {needsPassword && !password ? (
                  <p className="mt-2 text-xs text-[--color-faint]">
                    Enter the password above to choose a role.
                  </p>
                ) : null}
              </div>
            </div>
          )}
        </div>

        <p className="mt-4 text-xs leading-relaxed text-[--color-faint]">
          {needsPassword ? (
            <>
              Every demo account shares one password, so a reader can move
              between roles without signing in again. That is a demonstration
              trade-off and not how a real deployment should be run: one
              password here is all of them. Accounts are created with{" "}
              <code>scripts/add_user.py</code>; there is no self-service sign-up
              and no password reset.
            </>
          ) : (
            <>
              No account is required on a bench, and none exists. Every member,
              document and decision in this build is generated.
            </>
          )}
        </p>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-[--color-muted]">{label}</span>
      {children}
    </label>
  );
}
