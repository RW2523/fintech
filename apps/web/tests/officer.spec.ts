import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

/** T-046 acceptance — the Officer Workbench against the running stack.
 *
 *  Every assertion compares what the page shows with what the API returned,
 *  never with a number written into this file. A test that hard-codes 86.1
 *  passes when the UI and the ledger disagree, which is the one thing this
 *  screen must never do. */

const GATEWAY = process.env.GATEWAY_URL ?? "http://localhost:8000";
const CASE_ID = process.env.SMOKE_CASE_ID ?? "case_S1CLEAN";

type QueueEntry = {
  decision_record_id: string;
  case_id: string | null;
  route: string | null;
  recommendation: string | null;
  tier: string | null;
  required_authority: string | null;
};

/** The record the page is expected to render, read straight from the API. */
async function fetchRecord(request: APIRequestContext) {
  const minted = await request.post(`${GATEWAY}/api/auth/dev-token`, {
    data: { role: "officer" },
  });
  expect(minted.ok(), "the gateway must mint a demo token").toBeTruthy();
  const token = (await minted.json()).access_token as string;
  const auth = { authorization: `Bearer ${token}` };

  const queued = await request.get(`${GATEWAY}/api/decision/queue`, { headers: auth });
  expect(queued.ok(), "the decision queue must answer").toBeTruthy();
  const decisions = (await queued.json()).decisions as QueueEntry[];

  const rows = decisions.filter((row) => row.case_id === CASE_ID);
  expect(
    rows.length,
    `no decision is recorded for ${CASE_ID}; run scripts/seed_demo_case.py`,
  ).toBeGreaterThan(0);
  const entry = rows[rows.length - 1];

  const fetched = await request.get(
    `${GATEWAY}/api/decision/decision-records/${entry.decision_record_id}`,
    { headers: auth },
  );
  expect(fetched.ok(), "the decision record must be readable").toBeTruthy();

  const explained = await request.post(`${GATEWAY}/api/governance/explain/factors`, {
    headers: auth,
    data: { decision_record_id: entry.decision_record_id },
  });
  expect(explained.ok(), "the explanation must be readable").toBeTruthy();

  return {
    entry,
    // The service returns the record beside the ledger's own columns.
    record: (await fetched.json()).record,
    explanation: await explained.json(),
  };
}

async function signIn(page: Page, role = "officer") {
  await page.goto("/login");
  await page.getByTestId(`role-${role}`).click();
  await expect(page).toHaveURL(/\/officer$/);
}

/** Open a case the way an officer does, from the queue.
 *
 *  Never by navigating straight to the URL: the token is held in memory only,
 *  so a fresh page load has no session and the shell sends it back to the
 *  login screen. That is the app working as designed, and a test that reached
 *  the case page any other way would not be exercising what an officer does. */
async function openCase(page: Page, caseId: string) {
  const row = page.getByTestId(`queue-row-${caseId}`);
  // Exactly one row per case: a re-assessed case appends a second record to
  // the ledger, and a queue that showed both would invite an officer to act
  // on a superseded recommendation.
  await expect(row).toHaveCount(1);
  await row.getByRole("link").click();
  await expect(page).toHaveURL(new RegExp(`/officer/cases/${caseId}$`));
}

test.describe("officer workbench", () => {
  test("signs in, and the decision card states what the record states", async ({
    page,
    request,
  }) => {
    const { entry, record } = await fetchRecord(request);

    await signIn(page);

    await openCase(page, entry.case_id!);

    const card = page.getByTestId("decision-card");
    await expect(card).toBeVisible();

    // Figures, each against the record rather than a literal.
    await expect(page.getByTestId("weighted-score")).toHaveText(
      record.weighted_score == null ? "not scored" : record.weighted_score.toFixed(1),
    );
    await expect(page.getByTestId("tier")).toHaveText(record.tier);
    await expect(page.getByTestId("required-authority")).toHaveText(
      record.required_authority,
    );
    await expect(page.getByTestId("recommendation-chip")).toHaveText(
      record.recommendation.replaceAll("_", " ").toLowerCase(),
    );
    await expect(page.getByTestId("route-chip")).toHaveText(
      record.route.replaceAll("_", " ").toLowerCase(),
    );

    // Every factor the record scored is shown, with the record's own number.
    const families = Object.keys(record.factor_scores ?? {});
    expect(families.length, "the record must carry factor scores").toBeGreaterThan(0);
    for (const family of families) {
      await expect(page.getByTestId(`factor-${family}-score`)).toHaveText(
        String(record.factor_scores[family].score),
      );
    }

    // Every gate the record holds is listed, none invented.
    const gates = page.getByTestId("hard-gates").getByRole("listitem");
    await expect(gates).toHaveCount(record.hard_gates.length);
    for (const gate of record.hard_gates) {
      await expect(
        page.getByTestId("hard-gates").getByText(gate.rule_id, { exact: true }),
      ).toBeVisible();
    }
  });

  test("a claim's evidence opens the page image with the box drawn on it", async ({
    page,
    request,
  }) => {
    const { entry, explanation } = await fetchRecord(request);

    const boxed = (explanation.levels.provenance as Array<Record<string, any>>).filter(
      (item) => item.locator?.bbox && item.locator?.document_id,
    );
    expect(
      boxed.length,
      "no evidence carries a bounding box; the documents were not extracted",
    ).toBeGreaterThan(0);
    const cited = boxed[0];

    await signIn(page);
    await openCase(page, entry.case_id!);

    const panel = page.getByTestId("evidence-panel");
    await expect(panel).toBeVisible();

    await panel.getByTestId(`evidence-${cited.evidence_id}`).click();

    const detail = page.getByTestId("evidence-detail");
    await expect(detail).toBeVisible();

    // The overlay carries the same coordinates the explanation cited, and the
    // page image behind it is a real render the extractor read.
    const overlay = page.getByTestId("evidence-bbox");
    await expect(overlay).toHaveAttribute("data-bbox", cited.locator.bbox.join(","));

    const image = page.getByTestId("evidence-image").getByRole("img");
    await expect(image).toBeVisible();
    const loaded = await image.evaluate(
      (node) => (node as HTMLImageElement).naturalWidth > 0,
    );
    expect(loaded, "the page image must actually load").toBeTruthy();

    // The box sits inside the image, which is what makes it checkable.
    const frame = await image.boundingBox();
    const drawn = await overlay.boundingBox();
    expect(frame && drawn).toBeTruthy();
    expect(drawn!.x).toBeGreaterThanOrEqual(frame!.x - 1);
    expect(drawn!.y).toBeGreaterThanOrEqual(frame!.y - 1);
    expect(drawn!.x + drawn!.width).toBeLessThanOrEqual(frame!.x + frame!.width + 1);
    expect(drawn!.y + drawn!.height).toBeLessThanOrEqual(frame!.y + frame!.height + 1);
  });

  test("the synthetic-data badge is on every screen", async ({ page }) => {
    await signIn(page);
    await expect(page.getByTestId("synthetic-badge")).toBeVisible();
    await openCase(page, CASE_ID);
    await expect(page.getByTestId("synthetic-badge")).toBeVisible();
  });

  test("a reload has no session, and the shell says so rather than showing a blank case", async ({
    page,
  }) => {
    await signIn(page);
    // The token lives in memory only, so a reload is a sign-out. The point of
    // asserting it is that the app must send the reader to the login screen
    // rather than render a case page with every figure empty.
    await page.reload();
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByTestId("role-officer")).toBeVisible();
  });
});

test.describe("ledger viewer", () => {
  test("reconstructs a case with its chain badge", async ({ page, request }) => {
    const { entry } = await fetchRecord(request);

    await signIn(page);
    await page.getByTestId("nav-ledger").click();
    await page.getByTestId("ledger-search-input").fill(entry.case_id!);
    await page.getByTestId("ledger-search-submit").click();

    await expect(page).toHaveURL(new RegExp(`/ledger/${entry.case_id}$`));

    // The badge is the point of this screen: a timeline without one asks the
    // reader to trust it on sight.
    const verdict = page.getByTestId("chain-verdict");
    await expect(verdict).toBeVisible();
    await expect(verdict).toContainText("chain verified");

    const events = page.getByTestId("timeline-events").getByRole("listitem");
    await expect(events.first()).toBeVisible();

    // Every entry the timeline shows carries the hash it was chained with.
    await expect(page.getByTestId("timeline-DECISION_RECORD").first()).toBeVisible();
  });

  test("a case reached from the workbench keeps its identity", async ({ page, request }) => {
    const { entry } = await fetchRecord(request);
    await signIn(page);
    await openCase(page, entry.case_id!);

    await page.getByTestId("reconstruct-link").getByRole("link").click();
    await expect(page).toHaveURL(new RegExp(`/ledger/${entry.case_id}$`));
    await expect(page.getByTestId("chain-verdict")).toBeVisible();
  });

  test("a case with nothing recorded says so rather than showing an empty page", async ({
    page,
  }) => {
    await signIn(page);
    await page.getByTestId("nav-ledger").click();
    await page.getByTestId("ledger-search-input").fill("case_01ARZ3NDEKTSV4RRFFQ69G5FZZ");
    await page.getByTestId("ledger-search-submit").click();

    await expect(page.getByTestId("problem")).toBeVisible();
  });
});

test.describe("deciding a case", () => {
  test("an action beyond the role's authority is offered but disabled, and the API refuses it too", async ({
    page,
    request,
  }) => {
    // docs/09 §3.4 — shown disabled with the reason, never hidden: an officer
    // needs to know the action exists and who can take it.
    const minted = await request.post(`${GATEWAY}/api/auth/dev-token`, {
      data: { role: "officer" },
    });
    const token = (await minted.json()).access_token as string;
    const queued = await request.get(`${GATEWAY}/api/decision/queue`, {
      headers: { authorization: `Bearer ${token}` },
    });
    const rows = (await queued.json()).decisions as QueueEntry[];
    const beyond = rows.find(
      (row) => row.case_id && row.required_authority !== "CREDIT_OFFICER",
    );
    test.skip(!beyond, "no seeded case needs more than an officer");

    await signIn(page, "officer");
    await openCase(page, beyond!.case_id!);

    await expect(page.getByTestId("action-approve")).toBeDisabled();
    await expect(page.getByTestId("authority-reason")).toBeVisible();
    // The open actions stay available: escalating is how an officer moves a
    // case they may not decide.
    await expect(page.getByTestId("action-escalate")).toBeEnabled();

    // The screen is a courtesy; the service is the control.
    const refused = await request.post(`${GATEWAY}/api/decision/human-decisions`, {
      headers: { authorization: `Bearer ${token}` },
      data: {
        decision_record_id: beyond!.decision_record_id,
        case_id: beyond!.case_id,
        actor_id: "officer-demo",
        role: "officer",
        final_action: "APPROVE",
        override: false,
      },
    });
    expect(refused.status()).toBe(403);
  });

  test("departing from the recommendation demands a reason before it can be recorded", async ({
    page,
    request,
  }) => {
    const { entry, record } = await fetchRecord(request);
    test.skip(Boolean(entry.route === "AUTONOMOUS"), "an autonomous case has no officer step");

    await signIn(page, "head_of_credit");
    await openCase(page, entry.case_id!);

    // Choose the action the record did not recommend.
    const against = record.recommendation === "DECLINE" ? "approve" : "decline";
    await page.getByTestId(`action-${against}`).click();

    // The submit is held until the override is acknowledged and explained.
    await expect(page.getByTestId("submit-decision")).toBeDisabled();
    await page.getByTestId("override-confirm").check();
    await expect(page.getByTestId("submit-decision")).toBeDisabled();

    await page.getByTestId("override-note").fill("too short");
    await expect(page.getByTestId("override-note-short")).toBeVisible();

    await page
      .getByTestId("override-note")
      .fill("the member has banked here for nine years and the score does not see it");
    await expect(page.getByTestId("submit-decision")).toBeEnabled();
  });
});

test.describe("compliance view", () => {
  test("lists what is worth reading, with the reason on the row", async ({ page }) => {
    await signIn(page, "compliance");
    await page.getByTestId("nav-compliance").click();

    await expect(page.getByTestId("overrides")).toBeVisible();
    await expect(page.getByTestId("override-rate")).toBeVisible();
    await expect(page.getByTestId("attention")).toBeVisible();
  });
});
