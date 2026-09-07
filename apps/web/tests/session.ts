import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/** Signing in, however this deployment signs people in.
 *
 *  On a bench that is the role picker: press Officer and you are one. A
 *  deployment reachable from outside the machine sets AUTH_MODE=password, the
 *  picker is not rendered and /api/auth/dev-token refuses — at which point
 *  every test in the suite would fail on `getByTestId('role-officer')`.
 *
 *  A browser suite that cannot sign in has not passed; it has not run. So
 *  these ask the gateway which mode it is in and sign in that way, and the
 *  tests below say what an officer does rather than how this build
 *  authenticates them. */

const GATEWAY = process.env.GATEWAY_URL ?? "http://localhost:8000";

/** Passwords for the demo accounts, written by scripts/write_test_accounts.py
 *  and git-ignored. Only read when the gateway actually requires a password. */
const ACCOUNTS_FILE =
  process.env.CIO_TEST_ACCOUNTS ??
  resolve(dirname(fileURLToPath(import.meta.url)), "../../../docker/test-accounts.json");

type Account = { email: string; password: string };

let cachedMode: "dev" | "password" | undefined;
let cachedAccounts: Record<string, Account> | undefined;

export async function authMode(request: APIRequestContext): Promise<"dev" | "password"> {
  if (cachedMode) return cachedMode;
  const asked = await request.get(`${GATEWAY}/api/auth/mode`);
  expect(asked.ok(), "the gateway must say how it signs people in").toBeTruthy();
  cachedMode = (await asked.json()).mode === "password" ? "password" : "dev";
  return cachedMode;
}

function accounts(): Record<string, Account> {
  if (cachedAccounts) return cachedAccounts;
  try {
    cachedAccounts = JSON.parse(readFileSync(ACCOUNTS_FILE, "utf8"));
  } catch {
    throw new Error(
      `This deployment requires a password and there are no accounts at ${ACCOUNTS_FILE}.\n` +
        "Run: uv run python scripts/write_test_accounts.py",
    );
  }
  return cachedAccounts!;
}

export function accountFor(role: string): Account {
  const account = accounts()[role];
  if (!account) throw new Error(`no test account for role ${role} in ${ACCOUNTS_FILE}`);
  return account;
}

/** A token for a role, for the parts of a test that read the API directly to
 *  work out what the page ought to be showing. */
export async function mintToken(request: APIRequestContext, role: string): Promise<string> {
  if ((await authMode(request)) === "dev") {
    const minted = await request.post(`${GATEWAY}/api/auth/dev-token`, { data: { role } });
    expect(minted.ok(), "the gateway must mint a demo token").toBeTruthy();
    return (await minted.json()).access_token as string;
  }
  const account = accountFor(role);
  const signed = await request.post(`${GATEWAY}/api/auth/login`, {
    data: { email: account.email, password: account.password },
  });
  expect(signed.ok(), `${account.email} must be able to sign in`).toBeTruthy();
  return (await signed.json()).access_token as string;
}

export async function authHeader(request: APIRequestContext, role: string) {
  return { authorization: `Bearer ${await mintToken(request, role)}` };
}

/** Sign in through the browser, and land where that role lands.
 *
 *  Both modes press the same button. The sign-in screen is a list of roles
 *  either way; requiring a password adds a field above it rather than a
 *  different screen, so a test says "sign in as the officer" and does not have
 *  to know which. */
export async function signIn(page: Page, request: APIRequestContext, role = "officer") {
  await page.goto("/login");
  if ((await authMode(request)) === "password") {
    await page.getByTestId("login-password").fill(accountFor(role).password);
  }
  await page.getByTestId(`role-${role}`).click();
  await expect(page).toHaveURL(/\/(officer|collections|manager|member)$/);
}

/** The member whose records the assistant will be asked about.
 *
 *  In password mode this is not a choice: the member account is tied to one
 *  membership number and the assistant may only answer about that one. In dev
 *  mode the caller finds a suitable member and passes it. */
export async function memberSignIn(
  page: Page,
  request: APIRequestContext,
  memberId: string,
): Promise<void> {
  await page.goto("/login");
  if ((await authMode(request)) === "password") {
    await page.getByTestId("login-password").fill(accountFor("member").password);
  } else {
    await page.getByTestId("member-id").fill(memberId);
  }
  await page.getByTestId("role-member").click();
  await expect(page).toHaveURL(/\/member$/);
}

/** Which member a member token names.
 *
 *  With accounts that is the member account's own membership number. On a
 *  bench the gateway picks one who has both an account and an application, so
 *  a test never has to know a membership number and never has to hunt for one.
 */
export async function memberOfAccount(request: APIRequestContext): Promise<string> {
  const token = await mintToken(request, "member");
  const claims = JSON.parse(Buffer.from(token.split(".")[1], "base64url").toString());
  const memberId = claims.member_id as string | undefined;
  expect(memberId, "the member account must be tied to a membership number").toBeTruthy();
  return memberId!;
}

/** The sign-in screen, whichever one this deployment shows. */
export async function expectLoginScreen(page: Page, request: APIRequestContext) {
  await expect(page).toHaveURL(/\/login$/);
  // The same screen in both modes: a list of roles to sign in as.
  await expect(page.getByTestId("role-officer")).toBeVisible();
}
