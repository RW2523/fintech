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
/** Two rounded squares, offset and rotated, the second faded.
 *
 *  The prototype's mark, drawn rather than imported: an image would be one
 *  more asset to serve and one more thing a policy could block. */
/** Find a case, a member or a document by id.
 *
 *  Deliberately not a search service: there isn't one, and a box that pretended
 *  to search everything and quietly matched nothing would be worse than no box.
 *  It recognises the three id shapes this platform issues and goes to the
 *  screen that can show them, and says so when it does not recognise one. */
function GlobalSearch() {
  const navigate = useNavigate();
  const [term, setTerm] = useState("");
  const [missed, setMissed] = useState(false);

  function go(event: React.FormEvent) {
    event.preventDefault();
    const query = term.trim();
    if (!query) return;
    setMissed(false);
    if (/^(case_|snap_|dr_)/i.test(query)) {
      navigate(`/ledger?case=${encodeURIComponent(query)}`);
    } else if (/^M-\d+/i.test(query)) {
      navigate(`/collections?member=${encodeURIComponent(query.toUpperCase())}`);
    } else {
      setMissed(true);
      return;
    }
    setTerm("");
  }

  return (
    <form onSubmit={go} className="relative hidden min-w-0 flex-1 md:block md:max-w-md">
      <Icon.search className="pointer-events-none absolute top-1/2 left-3.5 h-4 w-4 -translate-y-1/2 text-[--color-faint]" />
      <input
        data-testid="global-search"
        value={term}
        onChange={(event) => {
          setTerm(event.target.value);
          setMissed(false);
        }}
        placeholder="Search by case, snapshot or membership number…"
        className="h-10 w-full rounded-xl border border-[--color-line] bg-[--color-raised] pr-3 pl-10 text-[13px] outline-none focus:border-[--color-accent-line] focus:bg-white"
      />
      {missed ? (
        <p className="rise absolute top-11 left-0 z-20 rounded-lg border border-[--color-warn-line] bg-[--color-warn-soft] px-3 py-2 text-[11px] text-[--color-warn-deep] shadow-[var(--shadow-card)]">
          Not an id this platform issues. Try a case (case_…), a snapshot
          (snap_…), a decision (dr_…) or a membership number (M-…).
        </p>
      ) : null}
    </form>
  );
}

function BrandMark() {
  return (
    <span className="relative grid h-[42px] w-[42px] shrink-0 place-items-center" aria-hidden="true">
      <span className="absolute left-px h-6 w-6 rotate-45 rounded-[11px] border-[6px] border-[--color-accent]" />
      <span className="absolute right-px h-6 w-6 rotate-45 rounded-[11px] border-[6px] border-[--color-accent] opacity-[.58]" />
    </span>
  );
}

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
        className="flex items-center gap-2 rounded-xl border border-[--color-line] bg-[--color-surface] px-2 py-1.5 transition-all hover:border-[--color-accent-line] hover:bg-[--color-accent-soft]"
      >
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-gradient-to-br from-[--color-accent] to-[--color-note] text-[11px] font-semibold text-white">
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
          className="rise absolute right-0 z-20 mt-2 w-72 rounded-2xl border border-[--color-line] bg-[--color-surface] p-2 shadow-[var(--shadow-pop)]"
        >
          <p className="px-2 py-1.5 text-xs font-medium text-[--color-muted]">
            {canSwitch ? "Switch role" : "Signed in as"}
          </p>

          {problem ? (
            <p className="mx-2 mb-2 rounded-lg border border-[--color-fail-line] bg-[--color-fail-soft] p-2 text-xs text-[--color-fail]">
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
                          ? "bg-[--color-accent-soft] font-semibold text-[--color-accent]"
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
      <aside className="sticky top-0 hidden h-screen w-[232px] shrink-0 flex-col border-r border-[--color-line] bg-[--color-surface]/95 px-4 pt-6 pb-4 lg:flex">
        <div className="flex items-center gap-3 px-3 pb-7">
          <BrandMark />
          <span className="text-[15px] leading-[1.05] font-extrabold tracking-tight text-[--color-ink]">
            Credit
            <br />
            Intelligence
            <span className="mt-1.5 block text-[9px] font-semibold tracking-wide text-[--color-muted]">
              GOVERNED CREDIT OS
            </span>
          </span>
        </div>

        <nav className="flex flex-1 flex-col gap-1 px-3 py-2">
          {visible.map((destination) => {
            const Drawn = Icon[destination.icon];
            return (
              <NavLink
                key={destination.to}
                to={destination.to}
                data-testid={destination.testId}
                className={({ isActive }) =>
                  `group relative flex items-center gap-2.5 rounded-xl px-3 py-2.5 text-[13px] font-semibold transition-all ${
                    isActive
                      ? "bg-[--color-accent-soft] text-[--color-accent]"
                      : "text-[--color-muted] hover:bg-[--color-sunken] hover:text-[--color-ink]"
                  }`
                }
              >
                {({ isActive }) => (
                  <>
                    {/* The mark that says "you are here" without relying on a
                        reader noticing which word went bold. */}
                    <span
                      className={`absolute top-2 bottom-2 -left-1 w-1 rounded-full transition-all ${
                        isActive ? "bg-[--color-accent]" : "bg-transparent"
                      }`}
                    />
                    <Drawn className="h-[18px] w-[18px]" />
                    {destination.label}
                  </>
                )}
              </NavLink>
            );
          })}
        </nav>

        {/* Said quietly, at the bottom, where a reader will find it when they
            wonder what they are looking at rather than while they work. */}
        <div className="px-3 pt-4">
          <p className="text-[13px] leading-snug text-[--color-muted]">
            <strong className="block font-bold text-[--color-ink]">Stronger members.</strong>
            Brighter tomorrows.
          </p>
          <p className="mt-2 text-[10px] leading-relaxed text-[--color-faint]">
            Every member, document and decision in this build is generated.
            None of it is real.
          </p>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 flex h-[72px] flex-wrap items-center gap-3 border-b border-[--color-line] bg-[--color-surface]/90 px-4 backdrop-blur-md lg:px-[26px]">
          {/* On a narrow screen the rail is gone, so the product name comes
              back into the bar rather than disappearing entirely. */}
          <span className="text-sm font-bold tracking-tight lg:hidden">Credit Intelligence OS</span>

          <GlobalSearch />

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
                      ? "bg-[--color-accent-soft] font-semibold text-[--color-accent]"
                      : "text-[--color-muted]"
                  }`
                }
              >
                {destination.label}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-2.5 text-xs text-[--color-muted]">
            {/* The id of the last call this session made. It is here so that
                when somebody asks "why did it say that", the answer starts
                with a value they can paste into a query. */}
            {lastTraceId ? (
              <span className="hidden xl:inline">
                trace <Copyable value={lastTraceId} label="trace id" />
              </span>
            ) : null}
            <WhoAmI />
          </div>
        </header>

        <main className="mx-auto w-full max-w-[1510px] flex-1 px-4 py-6 lg:px-[26px] lg:pb-10">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
