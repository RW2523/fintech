import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useEffect } from "react";

import { AUTHORITY, useAuth } from "../auth";
import { Chip, Copyable } from "./primitives";

/** docs/09 §1 — the shell. Nav by role, and a header that says which
 *  environment this is, what the last trace id was, and whether the platform
 *  is currently allowed to act on its own. */
export function Shell() {
  const { session, signOut, lastTraceId } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    if (!session) {
      navigate("/login", { replace: true });
    }
  }, [session, navigate]);

  if (!session) {
    return null;
  }

  const authority = AUTHORITY[session.role];

  return (
    <div className="min-h-screen">
      <header className="flex items-center gap-4 border-b border-[--color-line] bg-[--color-surface] px-4 py-2">
        <span className="text-sm font-semibold tracking-tight">
          Credit Intelligence OS
        </span>

        {/* Nothing here is a real member. The badge is permanent and
            deliberately loud: a screenshot of this app must never be mistaken
            for a screenshot of a real portfolio. */}
        <Chip tone="warn" testId="synthetic-badge" title="No real member data exists in this build">
          SYNTHETIC DATA
        </Chip>

        <nav className="flex items-center gap-3 text-sm">
          <NavLink
            to="/officer"
            className={({ isActive }) =>
              isActive ? "font-semibold text-[--color-accent]" : "text-[--color-muted]"
            }
          >
            Officer queue
          </NavLink>
          <NavLink
            to="/ledger"
            data-testid="nav-ledger"
            className={({ isActive }) =>
              isActive ? "font-semibold text-[--color-accent]" : "text-[--color-muted]"
            }
          >
            Ledger
          </NavLink>
        </nav>

        <div className="ml-auto flex items-center gap-3 text-xs text-[--color-muted]">
          {lastTraceId ? (
            <span>
              trace <Copyable value={lastTraceId} label="trace id" />
            </span>
          ) : null}
          <Chip testId="role-chip">{authority.label}</Chip>
          <button
            type="button"
            className="underline decoration-dotted"
            onClick={() => {
              signOut();
              navigate("/login", { replace: true });
            }}
          >
            Sign out
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-[1400px] p-4">
        <Outlet />
      </main>
    </div>
  );
}
