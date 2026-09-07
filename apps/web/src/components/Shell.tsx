import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useEffect, useRef, useState } from "react";

import { AUTHORITY, ROLES, useAuth, type Role } from "../auth";
import { Icon, type IconName } from "./icons";
import { Chip, Copyable } from "./primitives";

/** docs/09 §1 — the shell.
 *
 *  A rail down the left rather than a row of links along the top. The row was
 *  eight words competing with the page title for the same line, and the only
 *  way to know where you were was to notice which word had gone bold. The rail
 *  gives each destination an icon and a resting place, marks the current one
 *  plainly, and leaves the top bar to say what a reader needs to know about
 *  the session rather than about navigation: that this is synthetic data, who
 *  they are signed in as, and the id of the last call the platform made on
 *  their behalf.
 *
 *  Destinations are filtered by role. A screen an officer's token cannot load
 *  is not a screen to offer them: the API would refuse it, and a link that
 *  leads to a refusal teaches a reader to distrust the navigation. */

type Destination = {
  to: string;
  label: string;
  icon: IconName;
  testId: string;
  /** Who this is for. Everyone, when absent. */
  roles?: Role[];
};

const DESTINATIONS: Destination[] = [
  { to: "/officer", label: "Applications", icon: "applications", testId: "nav-officer" },
  {
    to: "/collections",
    label: "Collections",
    icon: "collections",
    testId: "nav-collections",
    roles: ["collections", "senior_officer", "manager", "head_of_credit", "head_of_risk", "system"],
  },
  {
    to: "/manager",
    label: "Portfolio",
    icon: "chart",
    testId: "nav-manager",
    roles: ["manager", "head_of_credit", "head_of_risk", "compliance", "system"],
  },
  {
    to: "/sandbox",
    label: "Policy sandbox",
    icon: "sandbox",
    testId: "nav-sandbox",
    roles: ["manager", "head_of_credit", "head_of_risk", "compliance", "system"],
  },
  {
    to: "/compliance",
    label: "Compliance",
    icon: "shield",
    testId: "nav-compliance",
    roles: ["compliance", "manager", "head_of_credit", "head_of_risk", "system"],
  },
  { to: "/ledger", label: "Ledger", icon: "ledger", testId: "nav-ledger" },
  { to: "/member", label: "Member view", icon: "members", testId: "nav-member" },
];

/** Who is signed in, and a way to become somebody else.
 *
 *  A demonstration is a sequence of "and here is what the manager sees". Doing
 *  that through the sign-in screen meant signing out, remembering an address
 *  and typing a password, four times, in front of an audience. This replays
 *  the password already given this session (see `switchRole`) so the roles are
 *  one click apart.
 *
 *  It is not a way past the sign-in screen: with nothing remembered there is
 *  nothing to replay, and the menu says so and offers the way back instead. */
function WhoAmI() {
  const { session, signOut, switchRole, canSwitch, signable, mode } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<Role | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function away(event: MouseEvent) {
      if (!box.current?.contains(event.target as Node)) setOpen(false);
    }
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  if (!session) return null;
  const authority = AUTHORITY[session.role];

  // On a bench any role can be minted; with accounts, only the roles somebody
  // actually made an account for.
  const offered = (mode === "password" ? signable.map((entry) => entry.role) : [...ROLES]).filter(
    (role) => role in AUTHORITY,
  );

  async function become(role: Role) {
    setBusy(role);
    setProblem(null);
    try {
      const switched = await switchRole(role);
      if (!switched) {
        // Nothing remembered — a reload, most likely, which drops the token
        // and the password together.
        navigate("/login", { replace: true });
        return;
      }
      setOpen(false);
      navigate(role === "member" ? "/member" : role === "collections" ? "/collections" : "/officer");
    } catch (caught) {
      setProblem(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div ref={box} className="relative">
      <button
        type="button"
        data-testid="who-am-i"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((was) => !was)}
        className="flex items-center gap-2 rounded-lg border border-[--color-line] px-2 py-1 transition hover:border-[--color-accent-line]"
      >
        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-[--color-accent-bg] text-[11px] font-semibold text-[--color-accent]">
          {(session.name || session.role).slice(0, 2).toUpperCase()}
        </span>
        <span className="hidden text-left sm:block">
          <span className="block text-xs font-medium text-[--color-ink]">
            {session.name || session.role}
          </span>
          <span className="block text-[11px]" data-testid="role-chip">
            {authority.label}
          </span>
        </span>
        <Icon.down className="h-3.5 w-3.5" />
      </button>

      {open ? (
        <div
          role="menu"
          data-testid="role-switcher"
          className="absolute right-0 z-20 mt-2 w-72 rounded-xl border border-[--color-line] bg-[--color-surface] p-2 shadow-lg"
        >
          <p className="px-2 py-1.5 text-xs font-medium text-[--color-muted]">
            {canSwitch ? "Switch role" : "Signed in as"}
          </p>

          {problem ? (
            <p className="mx-2 mb-2 rounded-lg border border-[--color-fail-line] bg-[--color-fail-bg] p-2 text-xs text-[--color-fail]">
              {problem}
            </p>
          ) : null}

          {canSwitch ? (
            <ul className="max-h-80 overflow-y-auto">
              {offered.map((role) => {
                const here = role === session.role;
                return (
                  <li key={role}>
                    <button
                      type="button"
                      role="menuitem"
                      data-testid={`switch-${role}`}
                      disabled={busy !== null || here}
                      onClick={() => void become(role)}
                      className={`flex w-full items-center justify-between gap-2 rounded-lg px-2 py-2 text-left text-sm transition disabled:cursor-default ${
                        here
                          ? "bg-[--color-accent-bg] font-semibold text-[--color-accent]"
                          : "hover:bg-[--color-canvas]"
                      }`}
                    >
                      <span>
                        <span className="block">{AUTHORITY[role].label}</span>
                        <span className="block text-xs font-normal text-[--color-muted]">
                          {AUTHORITY[role].approves === null
                            ? "Approves any amount"
                            : AUTHORITY[role].approves === 0
                              ? "No approval authority"
                              : `Approves up to ${AUTHORITY[role].approves.toLocaleString()}`}
                        </span>
                      </span>
                      {busy === role ? (
                        <span className="text-xs text-[--color-muted]">…</span>
                      ) : here ? (
                        <Icon.check className="h-4 w-4 shrink-0" />
                      ) : null}
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="px-2 pb-2 text-xs text-[--color-muted]">
              Switching needs the password this session signed in with, and a
              reload drops it. Sign in again to switch freely.
            </p>
          )}

          <div className="mt-1 border-t border-[--color-line] pt-1">
            <button
              type="button"
              role="menuitem"
              className="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left text-sm text-[--color-muted] transition hover:bg-[--color-canvas] hover:text-[--color-ink]"
              onClick={() => {
                signOut();
                navigate("/login", { replace: true });
              }}
            >
              <Icon.signOut className="h-4 w-4" />
              Sign out
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function Shell() {
  const { session, lastTraceId } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    if (!session) {
      navigate("/login", { replace: true });
    }
  }, [session, navigate]);

  if (!session) {
    return null;
  }

  const visible = DESTINATIONS.filter(
    (destination) => !destination.roles || destination.roles.includes(session.role),
  );

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r border-[--color-line] bg-[--color-surface] lg:flex">
        <div className="flex items-center gap-2.5 px-5 py-4">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-[--color-accent] text-white">
            <Icon.spark className="h-4.5 w-4.5" />
          </span>
          <span className="text-sm leading-tight font-semibold tracking-tight">
            Credit
            <br />
            Intelligence OS
          </span>
        </div>

        <nav className="flex flex-1 flex-col gap-0.5 px-3 py-2">
          {visible.map((destination) => {
            const Glyph = Icon[destination.icon];
            return (
              <NavLink
                key={destination.to}
                to={destination.to}
                data-testid={destination.testId}
                className={({ isActive }) =>
                  `flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition ${
                    isActive
                      ? "bg-[--color-accent-bg] font-semibold text-[--color-accent]"
                      : "text-[--color-muted] hover:bg-[--color-canvas] hover:text-[--color-ink]"
                  }`
                }
              >
                <Glyph className="h-4.5 w-4.5" />
                {destination.label}
              </NavLink>
            );
          })}
        </nav>

        {/* Said quietly, at the bottom, where a reader will find it when they
            wonder what they are looking at rather than while they work. */}
        <p className="px-5 pb-5 text-xs leading-relaxed text-[--color-faint]">
          Every member, document and decision in this build is generated. None
          of it is real.
        </p>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 flex flex-wrap items-center gap-3 border-b border-[--color-line] bg-[--color-surface]/95 px-4 py-2.5 backdrop-blur">
          {/* On a narrow screen the rail is gone, so the product name comes
              back into the bar rather than disappearing entirely. */}
          <span className="text-sm font-semibold tracking-tight lg:hidden">
            Credit Intelligence OS
          </span>

          {/* Nothing here is a real member. The badge is permanent and
              deliberately loud: a screenshot of this app must never be
              mistaken for a screenshot of a real portfolio. */}
          <Chip tone="warn" testId="synthetic-badge" title="No real member data exists in this build">
            SYNTHETIC DATA
          </Chip>

          <nav className="flex items-center gap-1 overflow-x-auto lg:hidden">
            {visible.map((destination) => (
              <NavLink
                key={destination.to}
                to={destination.to}
                className={({ isActive }) =>
                  `rounded-md px-2 py-1 text-xs whitespace-nowrap ${
                    isActive
                      ? "bg-[--color-accent-bg] font-semibold text-[--color-accent]"
                      : "text-[--color-muted]"
                  }`
                }
              >
                {destination.label}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-3 text-xs text-[--color-muted]">
            {/* The id of the last call this session made. It is here so that
                when somebody asks "why did it say that", the answer starts
                with a value they can paste into a query. */}
            {lastTraceId ? (
              <span className="hidden sm:inline">
                trace <Copyable value={lastTraceId} label="trace id" />
              </span>
            ) : null}
            <WhoAmI />
          </div>
        </header>

        <main className="mx-auto w-full max-w-[1500px] flex-1 p-4 lg:p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
