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
