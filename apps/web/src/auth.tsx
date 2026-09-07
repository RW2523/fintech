import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

/** docs/09 §1 — the roles the demo can sign in as.
 *
 *  Every role `cio_common.auth.ROLES` issues a token for. The three missing
 *  from this list — committee, head_of_risk, system — could sign in perfectly
 *  well once accounts existed, and then landed in an app whose `AUTHORITY`
 *  table had no entry for them, so the shell read their authority as
 *  `undefined`. A role the platform will authenticate must be a role this app
 *  can render. */
export const ROLES = [
  "officer",
  "senior_officer",
  "committee",
  "collections",
  "manager",
  "compliance",
  "head_of_credit",
  "head_of_risk",
  "member",
  "system",
] as const;

export type Role = (typeof ROLES)[number];

/** What each role may do on a case (docs/09 §3.4, docs/05 §4 authority).
 *  The UI disables what a role may not do and says why; it never hides it,
 *  because an officer needs to know an action exists and who can take it. */
export const AUTHORITY: Record<
  Role,
  { approves: number | null; label: string; authority: string | null }
> = {
  // `authority` is the rung on the approval ladder the role signs at, which is
  // what the decision service checks. `approves` is the amount it may commit,
  // shown to the reader. They are different questions and were previously
  // conflated, which let the screen offer an approval the API would refuse.
  officer: { approves: 20000, label: "Credit Officer", authority: "CREDIT_OFFICER" },
  senior_officer: { approves: 75000, label: "Senior Officer", authority: "SENIOR_OFFICER" },
  collections: { approves: 0, label: "Collections", authority: null },
  manager: { approves: 0, label: "Manager", authority: null },
  compliance: { approves: 0, label: "Compliance", authority: null },
  head_of_credit: { approves: null, label: "Head of Credit", authority: "CREDIT_COMMITTEE" },
  // Sits on the ladder at committee level, and is the role the kill switch and
  // the tamper drill are exercised as.
  head_of_risk: { approves: null, label: "Head of Risk", authority: "CREDIT_COMMITTEE" },
  committee: { approves: null, label: "Credit Committee", authority: "CREDIT_COMMITTEE" },
  member: { approves: 0, label: "Member", authority: null },
  // The platform acting as itself: the drills and the seeder sign in as this.
  // It approves nothing, because a decision with no person behind it is the
  // one thing the authority ladder exists to prevent.
  system: { approves: 0, label: "System", authority: null },
};

type Session = { role: Role; token: string; memberId?: string; name?: string };

/** How this deployment expects somebody to sign in. Asked rather than assumed,
 *  so one build serves a bench where the role picker is the point and a
 *  deployment where an account is required. */
export type AuthMode = "dev" | "password" | "unknown";

/** A role somebody could sign in as here, from the gateway. Roles and names,
 *  never addresses: `/api/auth/login` will not say whether an account exists,
 *  and a list of emails on the sign-in screen would give that away. */
export type Signable = { role: Role; name: string };

type AuthValue = {
  session: Session | null;
  mode: AuthMode;
  /** The roles this deployment can sign in as, for the picker. */
  signable: Signable[];
  signIn: (role: Role, memberId?: string) => Promise<void>;
  /** Resolves with the role the account turned out to have, because that
   *  is what decides where the reader lands and only the server knows it. */
  signInWithPassword: (email: string, password: string) => Promise<Role>;
  /** Sign in as whichever account holds a role. The demo's way in: pick a
   *  role, give the password once. */
  signInAsRole: (role: Role, password: string) => Promise<Role>;
  /** Change role without signing in again.
   *
   *  Only possible once somebody has proved they belong here: the password
   *  they signed in with is kept for the life of the tab and replayed. It is
   *  not a way past the password — with no session there is nothing to
   *  replay — it is a way to stop typing the same one nine times while
   *  showing nine screens. Resolves false when there is nothing remembered,
   *  so the caller can send the reader back to the sign-in screen. */
  switchRole: (role: Role) => Promise<boolean>;
  /** Whether switching is available: a password was remembered this session. */
  canSwitch: boolean;
  signOut: () => void;
  /** The trace id of the last API call, so a reader can find it in the logs. */
  lastTraceId: string | null;
  noteTraceId: (traceId: string | null) => void;
};

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  // Held in memory only. A token in localStorage outlives the tab and the
  // demo, which is more exposure than a stub token needs (docs/09 §1).
  const [session, setSession] = useState<Session | null>(null);
  const [lastTraceId, setLastTraceId] = useState<string | null>(null);
  const [mode, setMode] = useState<AuthMode>("unknown");
  const [signable, setSignable] = useState<Signable[]>([]);

  // The password this session signed in with, so the role switcher can replay
  // it. In a ref rather than state: nothing re-renders when it changes, and it
  // never reaches localStorage, so it dies with the tab exactly as the token
  // does. A demo convenience, and the trade-off is stated on the screen that
  // offers it.
  const secret = useRef<string | null>(null);

  // A member signs in as themselves, so the token has to name them. The
  // member assistant reads the member from the token and nothing else: an id
  // typed into a request body is ignored the moment a real member is signed
  // in, because identity that can be typed is not identity.
  const signIn = useCallback(async (role: Role, memberId?: string) => {
    const response = await fetch("/api/auth/dev-token", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(memberId ? { role, member_id: memberId } : { role }),
    });
    if (!response.ok) {
      throw new Error(`sign-in failed: ${response.status}`);
    }
    const body = (await response.json()) as { access_token: string };
    setSession({ role, token: body.access_token, memberId });
  }, []);

  /** One sign-in, however it was addressed. Every way in goes through here so
   *  the three of them cannot drift apart in what they store. */
  const login = useCallback(
    async (body: { email?: string; role?: Role }, password: string): Promise<Role> => {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ ...body, password }),
      });
      if (!response.ok) {
        // The server says the same thing for a wrong address and a wrong
        // password, and so does this: telling them apart is how somebody
        // finds out who has an account here.
        const problem = await response.json().catch(() => null);
        throw new Error(
          problem?.error?.message ?? "That email and password do not match an account.",
        );
      }
      const issued = (await response.json()) as {
        access_token: string;
        role: Role;
        name?: string;
        member_id?: string;
      };
      setSession({
        role: issued.role,
        token: issued.access_token,
        memberId: issued.member_id ?? undefined,
        name: issued.name,
      });
      secret.current = password;
      return issued.role;
    },
    [],
  );

  const signInWithPassword = useCallback(
    (email: string, password: string) => login({ email }, password),
    [login],
  );

  const signInAsRole = useCallback(
    (role: Role, password: string) => login({ role }, password),
    [login],
  );

  const switchRole = useCallback(
    async (role: Role) => {
      // On a bench there is nothing to prove, so switching is just another
      // token. Where a password is required, the one this session already
      // gave is replayed; with none remembered there is nothing to replay and
      // the caller sends the reader back to the sign-in screen.
      if (mode !== "password") {
        await signIn(role);
        return true;
      }
      if (!secret.current) return false;
      await login({ role }, secret.current);
      return true;
    },
    [login, mode, signIn],
  );

  const signOut = useCallback(() => {
    // The remembered password goes with the session. A switcher that still
    // worked after signing out would mean signing out had not signed you out.
    secret.current = null;
    setSession(null);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void fetch("/api/auth/mode")
      .then((r) => (r.ok ? r.json() : null))
      .then((body) => {
        if (cancelled || !body?.mode) return;
        setMode(body.mode as AuthMode);
        if (Array.isArray(body.roles)) setSignable(body.roles as Signable[]);
      })
      .catch(() => {
        // A gateway that cannot be reached is not a reason to offer the role
        // picker: the safer screen is the one that asks for a password.
        if (!cancelled) setMode("password");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const value = useMemo(
    () => ({
      session,
      mode,
      signable,
      signIn,
      signInWithPassword,
      signInAsRole,
      switchRole,
      // On a bench, always. With a password, only once one has been given.
      canSwitch: mode !== "password" || secret.current != null,
      signOut,
      lastTraceId,
      noteTraceId: setLastTraceId,
    }),
    [
      session,
      mode,
      signable,
      signIn,
      signInWithPassword,
      signInAsRole,
      switchRole,
      signOut,
      lastTraceId,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth used outside AuthProvider");
  }
  return value;
}
