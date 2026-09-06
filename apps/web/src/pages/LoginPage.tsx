import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { AUTHORITY, ROLES, type Role, useAuth } from "../auth";
import { Chip, Problem } from "../components/primitives";

/** docs/09 §1 — a role picker, not a login.
 *
 *  There is no password because there are no accounts: this is a stub in front
 *  of the gateway's dev-token endpoint, and it says so on the page rather than
 *  imitating a sign-in screen. */
export function LoginPage() {
  const { signIn } = useAuth();
  const navigate = useNavigate();
  const [busy, setBusy] = useState<Role | null>(null);
  const [error, setError] = useState<unknown>(null);
  // A member signs in as themselves, so this is the one role that needs to
  // say who. Staff roles are a role picker; this is a membership number.
  const [memberId, setMemberId] = useState("");

  async function choose(role: Role) {
    setBusy(role);
    setError(null);
    try {
      const member = role === "member" ? memberId.trim() : undefined;
      if (role === "member" && !member) {
        throw new Error("enter a membership number to sign in as a member");
      }
      await signIn(role, member);
      navigate(role === "member" ? "/member" : "/officer", { replace: true });
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-lg flex-col justify-center gap-6 p-6">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">
          Credit Intelligence OS
        </h1>
        <p className="mt-1 text-sm text-[--color-muted]">
          Choose a role to sign in. This is a demonstration build: there are no
          accounts and no real members.
        </p>
        <div className="mt-2">
          <Chip tone="warn">SYNTHETIC DATA</Chip>
        </div>
      </div>

      {error ? <Problem error={error} /> : null}

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
    </div>
  );
}
