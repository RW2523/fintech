import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

/** docs/09 §1 — the roles the demo can sign in as. */
export const ROLES = [
  "officer",
  "senior_officer",
  "collections",
  "manager",
  "compliance",
  "head_of_credit",
  "member",
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
  member: { approves: 0, label: "Member", authority: null },
};

type Session = { role: Role; token: string; memberId?: string; name?: string };

/** How this deployment expects somebody to sign in. Asked rather than assumed,
 *  so one build serves a bench where the role picker is the point and a
 *  deployment where an account is required. */
export type AuthMode = "dev" | "password" | "unknown";

type AuthValue = {
  session: Session | null;
  mode: AuthMode;
  signIn: (role: Role, memberId?: string) => Promise<void>;
  /** Resolves with the role the account turned out to have, because that
   *  is what decides where the reader lands and only the server knows it. */
  signInWithPassword: (email: string, password: string) => Promise<Role>;
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

  const signInWithPassword = useCallback(async (email: string, password: string) => {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!response.ok) {
      // The server says the same thing for a wrong address and a wrong
      // password, and so does this: telling them apart is how somebody finds
      // out who has an account here.
      const body = await response.json().catch(() => null);
      throw new Error(body?.error?.message ?? "That email and password do not match an account.");
    }
    const body = (await response.json()) as {
      access_token: string;
      role: Role;
      name?: string;
      member_id?: string;
    };
    setSession({
      role: body.role,
      token: body.access_token,
      memberId: body.member_id ?? undefined,
      name: body.name,
    });
    return body.role;
  }, []);

  const signOut = useCallback(() => setSession(null), []);

  useEffect(() => {
    let cancelled = false;
    void fetch("/api/auth/mode")
      .then((r) => (r.ok ? r.json() : null))
      .then((body) => {
        if (!cancelled && body?.mode) setMode(body.mode as AuthMode);
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
      signIn,
      signInWithPassword,
      signOut,
      lastTraceId,
      noteTraceId: setLastTraceId,
    }),
    [session, mode, signIn, signInWithPassword, signOut, lastTraceId],
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
