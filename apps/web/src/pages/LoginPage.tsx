import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { AUTHORITY, ROLES, type Role, useAuth } from "../auth";
import { Chip, Problem } from "../components/primitives";

/** docs/09 §1, docs/13 §1 — two ways in, and the deployment decides which.
 *
 *  On a bench this is a role picker: press "Officer" and you are one, because
 *  there are no accounts and pretending otherwise would be theatre. Anywhere
 *  reachable from outside that bench it is an email and a password, because
 *  the role picker's endpoint mints a head_of_credit token for whoever asks.
 *
 *  The page asks the gateway which it is rather than deciding for itself. A web
 *  app that made that judgement would be a second place to get it wrong. */
export function LoginPage() {
  const { mode, signIn, signInWithPassword } = useAuth();
  const navigate = useNavigate();
  const [busy, setBusy] = useState<Role | "password" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [memberId, setMemberId] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  function onwards(role: Role) {
    navigate(role === "member" ? "/member" : "/officer", { replace: true });
  }

  async function choose(role: Role) {
    setBusy(role);
    setError(null);
    try {
      const member = role === "member" ? memberId.trim() : undefined;
      if (role === "member" && !member) {
        throw new Error("enter a membership number to sign in as a member");
      }
      await signIn(role, member);
      onwards(role);
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(null);
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy("password");
    setError(null);
    try {
      // Where a reader lands is decided by the role the server says the
      // account has, not by the screen they signed in on. Sending everybody to
      // the officer queue put a member on a page that is not theirs and that
      // their token cannot load.
      onwards(await signInWithPassword(email.trim(), password));
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-lg flex-col justify-center gap-6 p-6">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Credit Intelligence OS</h1>
        <p className="mt-1 text-sm text-[--color-muted]">
          {mode === "password"
            ? "Sign in with the account you were given. Every member, document and decision in this build is generated; none of it is real."
            : "Choose a role to sign in. This is a demonstration build: there are no accounts and no real members."}
        </p>
        <div className="mt-2">
          <Chip tone="warn">SYNTHETIC DATA</Chip>
        </div>
      </div>

      {error ? <Problem error={error} /> : null}

      {mode === "password" ? (
        <form className="flex flex-col gap-3" onSubmit={submit} data-testid="password-signin">
          <label className="flex flex-col gap-1 text-xs text-[--color-muted]">
            Email
            <input
              data-testid="login-email"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="rounded border border-[--color-line] bg-[--color-surface] px-2 py-1 text-sm text-[--color-text]"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-[--color-muted]">
            Password
            <input
              data-testid="login-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="rounded border border-[--color-line] bg-[--color-surface] px-2 py-1 text-sm text-[--color-text]"
            />
          </label>
          <button
            type="submit"
            data-testid="login-submit"
            disabled={busy !== null || !email.trim() || !password}
            className="rounded border border-[--color-accent] px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy === "password" ? "Signing in" : "Sign in"}
          </button>
          <p className="text-xs text-[--color-muted]">
            Accounts are created with <code>scripts/add_user.py</code>. There is no
            self-service sign-up and no password reset: this is a demonstration,
            not a product.
          </p>
        </form>
      ) : (
        <>
          <label className="flex flex-col gap-1 text-xs text-[--color-muted]">
            Membership number, to sign in as a member
            <input
              data-testid="member-id"
              value={memberId}
              onChange={(event) => setMemberId(event.target.value)}
              placeholder="M-000042"
              className="rounded border border-[--color-line] bg-[--color-surface] px-2 py-1 text-sm text-[--color-text]"
            />
          </label>

          <ul className="flex flex-col gap-2">
            {ROLES.map((role) => (
              <li key={role}>
                <button
                  type="button"
                  data-testid={`role-${role}`}
                  disabled={busy !== null}
                  onClick={() => void choose(role)}
                  className="flex w-full items-center justify-between rounded border border-[--color-line] bg-[--color-surface] px-3 py-2 text-left text-sm hover:border-[--color-accent] disabled:opacity-50"
                >
                  <span className="font-medium">{AUTHORITY[role].label}</span>
                  <span className="text-xs text-[--color-muted]">
                    {AUTHORITY[role].approves === null
                      ? "approves any amount"
                      : AUTHORITY[role].approves === 0
                        ? "no approval authority"
                        : `approves up to ${AUTHORITY[role].approves.toLocaleString()}`}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
