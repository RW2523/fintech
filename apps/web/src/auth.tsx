import React, {
  createContext,
  useCallback,
  useContext,
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
export const AUTHORITY: Record<Role, { approves: number | null; label: string }> = {
  officer: { approves: 20000, label: "Credit Officer" },
  senior_officer: { approves: 75000, label: "Senior Officer" },
  collections: { approves: 0, label: "Collections" },
  manager: { approves: 0, label: "Manager" },
  compliance: { approves: 0, label: "Compliance" },
  head_of_credit: { approves: null, label: "Head of Credit" },
  member: { approves: 0, label: "Member" },
};

type Session = { role: Role; token: string };

type AuthValue = {
  session: Session | null;
  signIn: (role: Role) => Promise<void>;
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

  const signIn = useCallback(async (role: Role) => {
    const response = await fetch("/api/auth/dev-token", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ role }),
    });
    if (!response.ok) {
      throw new Error(`sign-in failed: ${response.status}`);
    }
    const body = (await response.json()) as { access_token: string };
    setSession({ role, token: body.access_token });
  }, []);

  const signOut = useCallback(() => setSession(null), []);

  const value = useMemo(
    () => ({ session, signIn, signOut, lastTraceId, noteTraceId: setLastTraceId }),
    [session, signIn, signOut, lastTraceId],
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
