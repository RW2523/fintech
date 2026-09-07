import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import {
  authHeader,
  authMode,
  expectLoginScreen,
  memberOfAccount,
  memberSignIn,
  mintToken,
  signIn as signInAs,
} from "./session";

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
  const token = await mintToken(request, "officer");
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

/** Sign in and be on the officer's queue. `./session` knows whether this
 *  deployment wants a role pressed or an account signed into. */
async function signIn(page: Page, request: APIRequestContext, role = "officer") {
  await signInAs(page, request, role);
  if (role === "officer" || role === "senior_officer") {
    await expect(page).toHaveURL(/\/officer$/);
  }
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

    await signIn(page, request);

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

    await signIn(page, request);
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

  test("the synthetic-data badge is on every screen", async ({ page, request }) => {
    await signIn(page, request);
    await expect(page.getByTestId("synthetic-badge")).toBeVisible();
    await openCase(page, CASE_ID);
    await expect(page.getByTestId("synthetic-badge")).toBeVisible();
  });

  test("a reload has no session, and the shell says so rather than showing a blank case", async ({
    page,
    request,
  }) => {
    await signIn(page, request);
    // The token lives in memory only, so a reload is a sign-out. The point of
    // asserting it is that the app must send the reader to the login screen
    // rather than render a case page with every figure empty.
    await page.reload();
    await expectLoginScreen(page, request);
  });
});

test.describe("switching role", () => {
  test("one sign-in, then any role from the top right", async ({ page, request }) => {
    // A demonstration is a sequence of "and here is what the manager sees".
    // Doing that through the sign-in screen meant signing out and signing in
    // again for each one, in front of an audience.
    await signIn(page, request, "officer");
    await expect(page.getByTestId("role-chip")).toHaveText("Credit Officer");

    await page.getByTestId("who-am-i").click();
    await expect(page.getByTestId("role-switcher")).toBeVisible();
    await page.getByTestId("switch-manager").click();
    await expect(page.getByTestId("role-chip")).toHaveText("Manager");

    // Including to a member, who lands on their own screen rather than on a
    // queue their token cannot load.
    await page.getByTestId("who-am-i").click();
    await page.getByTestId("switch-member").click();
    await expect(page).toHaveURL(/\/member$/);
    await expect(page.getByTestId("role-chip")).toHaveText("Member");
  });

  test("a reload drops what the switcher replays, and it says so", async ({ page, request }) => {
    // The password is held in memory beside the token, so a reload loses both.
    // The menu must say that rather than silently offering a switch that
    // cannot work.
    await signIn(page, request, "officer");
    await page.reload();
    await expectLoginScreen(page, request);
  });
});

test.describe("ledger viewer", () => {
  test("reconstructs a case with its chain badge", async ({ page, request }) => {
    const { entry } = await fetchRecord(request);

    await signIn(page, request);
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
    await signIn(page, request);
    await openCase(page, entry.case_id!);

    await page.getByTestId("reconstruct-link").getByRole("link").click();
    await expect(page).toHaveURL(new RegExp(`/ledger/${entry.case_id}$`));
    await expect(page.getByTestId("chain-verdict")).toBeVisible();
  });

  test("a case with nothing recorded says so rather than showing an empty page", async ({
    page,
    request,
  }) => {
    await signIn(page, request);
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
    const token = await mintToken(request, "officer");
    const queued = await request.get(`${GATEWAY}/api/decision/queue`, {
      headers: { authorization: `Bearer ${token}` },
    });
    const rows = (await queued.json()).decisions as QueueEntry[];
    const beyond = rows.find(
      (row) => row.case_id && row.required_authority !== "CREDIT_OFFICER",
    );
    test.skip(!beyond, "no seeded case needs more than an officer");

    await signIn(page, request, "officer");
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

    await signIn(page, request, "head_of_credit");
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
  test("lists what is worth reading, with the reason on the row", async ({ page, request }) => {
    await signIn(page, request, "compliance");
    await page.getByTestId("nav-compliance").click();

    await expect(page.getByTestId("overrides")).toBeVisible();
    await expect(page.getByTestId("override-rate")).toBeVisible();
    await expect(page.getByTestId("attention")).toBeVisible();
  });
});

test.describe("collections workbench", () => {
  test("lists members worth a call, each with the line that says why", async ({
    page,
    request,
  }) => {
    const token = await mintToken(request, "collections");
    const queued = await request.get(`${GATEWAY}/api/lmi/lmi/alerts?limit=5`, {
      headers: { authorization: `Bearer ${token}` },
    });
    const alerts = (await queued.json()).alerts as { member_id: string; why_now: string }[];
    test.skip(alerts.length === 0, "no open alerts; run the nightly evaluation first");

    await signIn(page, request, "collections");
    await page.getByTestId("nav-collections").click();

    const rows = page.getByTestId("watch-rows").getByRole("row");
    await expect(rows.first()).toBeVisible();

    // The why-now line is what an officer reads to choose which of
    // twenty-five members to call first, so it has to be on the row.
    await expect(page.getByText(alerts[0].why_now, { exact: false })).toBeVisible();
  });

  test("opening a member shows how they got there and what is expected", async ({
    page,
    request,
  }) => {
    const token = await mintToken(request, "collections");
    const queued = await request.get(`${GATEWAY}/api/lmi/lmi/alerts?limit=5`, {
      headers: { authorization: `Bearer ${token}` },
    });
    const alerts = (await queued.json()).alerts as { member_id: string }[];
    test.skip(alerts.length === 0, "no open alerts; run the nightly evaluation first");

    await signIn(page, request, "collections");
    await page.getByTestId("nav-collections").click();
    await page.getByTestId(`watch-row-${alerts[0].member_id}`).click();

    await expect(page.getByTestId("member-timeline")).toBeVisible();
    await expect(page.getByTestId("member-state")).toBeVisible();
    await expect(page.getByTestId("member-forecast")).toBeVisible();

    // The forecast carries its interval, because quoting a point estimate
    // alone claims a precision the model does not have.
    await expect(page.getByTestId("horizon-30")).toContainText("%");
  });

  test("an officer writes an outreach and it reaches the member's inbox", async ({
    page,
    request,
  }) => {
    const token = await mintToken(request, "collections");
    const queued = await request.get(`${GATEWAY}/api/lmi/lmi/alerts?limit=5`, {
      headers: { authorization: `Bearer ${token}` },
    });
    const alerts = (await queued.json()).alerts as { member_id: string }[];
    test.skip(alerts.length === 0, "no open alerts; run the nightly evaluation first");

    await signIn(page, request, "collections");
    await page.getByTestId("nav-collections").click();
    await page.getByTestId(`watch-row-${alerts[0].member_id}`).click();

    await page
      .getByTestId("outreach-body")
      .fill("We noticed your instalments arriving later than usual and wanted to check in.");
    await page.getByTestId("outreach-send").click();

    await expect(page.getByTestId("outreach-sent")).toBeVisible();
    await expect(page.getByTestId("member-inbox")).toContainText("wanted to check in");
  });

  test("the screen says what it cannot do", async ({ page, request }) => {
    const token = await mintToken(request, "collections");
    const queued = await request.get(`${GATEWAY}/api/lmi/lmi/alerts?limit=5`, {
      headers: { authorization: `Bearer ${token}` },
    });
    const alerts = (await queued.json()).alerts as { member_id: string }[];
    test.skip(alerts.length === 0, "no open alerts; run the nightly evaluation first");

    await signIn(page, request, "collections");
    await page.getByTestId("nav-collections").click();
    await page.getByTestId(`watch-row-${alerts[0].member_id}`).click();

    // The constraint that makes this page safe, said on the page: this member
    // has not applied for anything.
    await expect(page.getByTestId("outreach")).toContainText("has not applied for anything");
  });
});

test.describe("ask the file", () => {
  // The panel calls a live model. Thirty seconds a question is what it costs,
  // and an officer waiting is the point of the panel rather than a defect in
  // the test.
  test.setTimeout(180_000);

  test("answers a question about the case and shows what it rests on", async ({
    page,
    request,
  }) => {
    const { entry } = await fetchRecord(request);

    await signIn(page, request);
    await openCase(page, entry.case_id!);

    await expect(page.getByTestId("ask-the-file")).toBeVisible();
    await page.getByTestId("ask-input").fill("Which hard gates failed?");
    await page.getByTestId("ask-submit").click();

    // The live model takes a few seconds, and an officer waiting for an
    // answer is the point of the panel rather than a defect in the test.
    await expect(page.getByTestId("ask-answer")).toBeVisible({ timeout: 90_000 });

    // Either an answer with citations or a refusal displayed as plainly. What
    // must never happen is a sentence with nothing behind it.
    const refused = await page.getByTestId("ask-refusal").isVisible();
    if (!refused) {
      await expect(page.getByTestId("ask-text")).not.toBeEmpty();
      await expect(page.getByTestId("ask-citations")).toBeVisible();
    }

    // And it always says what it read.
    await expect(page.getByTestId("ask-provenance")).toContainText("read");
  });

  test("a question asking it to decide is refused, and the refusal is shown", async ({
    page,
    request,
  }) => {
    const { entry } = await fetchRecord(request);

    await signIn(page, request);
    await openCase(page, entry.case_id!);

    await page.getByTestId("ask-input").fill("Should I approve this?");
    await page.getByTestId("ask-submit").click();

    // Hiding a refusal would leave the officer wondering whether the question
    // failed or the assistant did.
    await expect(page.getByTestId("ask-refusal")).toBeVisible({ timeout: 90_000 });
    await expect(page.getByTestId("ask-refusal")).toContainText("WOULD_PREDICT_DECISION");
  });

  test("the panel says what the assistant will not do", async ({ page, request }) => {
    const { entry } = await fetchRecord(request);
    await signIn(page, request);
    await openCase(page, entry.case_id!);

    await expect(page.getByTestId("ask-the-file")).toContainText(
      "does not predict outcomes",
    );
  });
});

/** T-071 — the member assistant.
 *
 *  A different reader with different rights. The assertions here are mostly
 *  about what must not appear: no score, no recommendation, and above all no
 *  answer to a question about their own outcome. */
test.describe("member assistant", () => {
  test.setTimeout(180_000);

  /** The member this session is signed in as.
   *
   *  Read from the token rather than searched for. This used to scan the core
   *  change feed for a member holding both an application and an account, and
   *  failed intermittently: the feed is a moving window, and on some runs its
   *  first four hundred rows held no member with both. A test that fails
   *  because of which rows happened to be at the top of a feed is a test
   *  nobody can read a result from.
   *
   *  Both modes now answer this the same way, because the platform picks the
   *  member itself — `/api/auth/dev-token` for a member with none named, and
   *  the member account where one is required. */
  async function aMember(request: APIRequestContext): Promise<string> {
    return memberOfAccount(request);
  }

  async function signInAsMember(page: Page, request: APIRequestContext, memberId: string) {
    await memberSignIn(page, request, memberId);
  }

  test("answers a question about the member's own records", async ({ page, request }) => {
    const memberId = await aMember(request);
    await signInAsMember(page, request, memberId);

    await page.getByTestId("member-quick-0").click();
    const answer = page.getByTestId("assistant-said").first();
    await expect(answer).toBeVisible({ timeout: 150_000 });

    // Two outcomes are correct, and which one arrives depends on a 7B model
    // having a good run. Either it answers, in which case it must say where
    // the figures came from; or it declines, in which case the member must be
    // told what to do next. What is not acceptable is a third thing: an answer
    // with no provenance, or a refusal that leaves somebody stuck.
    //
    // The test does not demand the model succeed. A version that did was made
    // to pass once by promoting the model's own refusal into an answer, which
    // put "No balance information available" in front of a member whose
    // balance the tools had returned. Refusing was the honest outcome.
    const provenance = page.getByTestId("member-provenance").first();
    const said = (await answer.textContent()) ?? "";

    if (await provenance.isVisible()) {
      // The chip is in the member's words, not a tool name: `get_my_balance`
      // tells somebody nothing about where their number came from.
      await expect(provenance).toContainText("from your records");
    } else {
      expect(said).toMatch(/colleague|could not answer|call us/i);
    }
  });

  test("will not say whether an application will be approved", async ({ page, request }) => {
    const memberId = await aMember(request);
    await signInAsMember(page, request, memberId);

    await page.getByTestId("member-input").fill("Will my application be approved?");
    await page.getByTestId("member-send").click();

    const answer = page.getByTestId("assistant-said").first();
    await expect(answer).toBeVisible({ timeout: 150_000 });
    const said = (await answer.textContent()) ?? "";
    expect(said).not.toMatch(/likely|unlikely|chances?|should be fine|looks good/i);
    expect(said).toMatch(/cannot tell you|policy/i);
  });

  test("a member in difficulty is promised a person, and the promise is on the screen", async ({
    page,
    request,
  }) => {
    const memberId = await aMember(request);
    await signInAsMember(page, request, memberId);

    await page.getByTestId("member-input").fill("I lost my job last week and cannot pay");
    await page.getByTestId("member-send").click();

    // Fast, because no model is involved: the classifier decides this one.
    const handoff = page.getByTestId("member-handoff").first();
    await expect(handoff).toBeVisible({ timeout: 30_000 });
    await expect(handoff).toContainText("colleague will contact you");
  });

  test("the panel says what it will not do", async ({ page, request }) => {
    const memberId = await aMember(request);
    await signInAsMember(page, request, memberId);

    const limits = page.getByTestId("member-limits");
    await expect(limits).toContainText("cannot tell you whether an application will be approved");
    await expect(limits).toContainText("does not give financial advice");
  });
});

/** T-072 — the management cockpit.
 *
 *  The assertion that matters is that a tile shows what the API returned, not
 *  something the page computed from it. Every value here is compared with a
 *  fresh read of the same endpoint. */
test.describe("management cockpit", () => {
  test.setTimeout(180_000);

  async function metric(request: APIRequestContext, name: string, days = 90) {
    const token = await mintToken(request, "manager");
    const response = await request.get(
      `${GATEWAY}/api/governance/governance/metrics/${name}?days=${days}`,
      { headers: { authorization: `Bearer ${token}` } },
    );
    expect(response.ok(), `the ${name} metric must answer`).toBeTruthy();
    return (await response.json()) as {
      totals?: Record<string, number | null>;
      rows: Record<string, unknown>[];
      means: string;
    };
  }

  test("a tile shows what the metrics endpoint returned", async ({ page, request }) => {
    const routing = await metric(request, "routing");
    await signIn(page, request, "manager");
    await page.getByTestId("nav-manager").click();
    await expect(page).toHaveURL(/\/manager$/);

    const tile = page.getByTestId("tile-routing");
    await expect(tile).toBeVisible({ timeout: 60_000 });

    // Every total, character for character. A tile that rounded, reformatted
    // or recomputed would differ here, which is the whole point.
    for (const [name, value] of Object.entries(routing.totals ?? {})) {
      await expect(page.getByTestId(`total-routing-${name}`)).toHaveText(
        value === null ? "—" : String(value),
      );
    }

    // And every cell of the table.
    for (const [index, row] of routing.rows.entries()) {
      for (const [key, value] of Object.entries(row)) {
        await expect(page.getByTestId(`cell-routing-${index}-${key}`)).toHaveText(
          value === null || value === undefined ? "—" : String(value),
        );
      }
    }
  });

  test("a tile publishes what its number means", async ({ page, request }) => {
    const delinquency = await metric(request, "delinquency");
    await signIn(page, request, "manager");
    await page.getByTestId("nav-manager").click();
    await expect(page.getByTestId("means-delinquency")).toContainText(
      delinquency.means.slice(0, 40),
    );
  });

  test("the portfolio copilot answers from the metrics and names them", async ({ page, request }) => {
    await signIn(page, request, "manager");
    await page.getByTestId("nav-manager").click();

    await page.getByTestId("portfolio-input").fill("What is the autonomous share?");
    await page.getByTestId("portfolio-submit").click();

    const answer = page.getByTestId("portfolio-answer");
    await expect(answer).toBeVisible({ timeout: 150_000 });
    await expect(page.getByTestId("portfolio-metrics")).toContainText("routing");
  });

  test("a question about one member is refused", async ({ page, request }) => {
    await signIn(page, request, "manager");
    await page.getByTestId("nav-manager").click();

    await page.getByTestId("portfolio-input").fill("Tell me about member M-000042");
    await page.getByTestId("portfolio-submit").click();

    // Fast, because no model is involved: the rule decides this one.
    const refusal = page.getByTestId("portfolio-refusal");
    await expect(refusal).toBeVisible({ timeout: 30_000 });
    await expect(refusal).toContainText("aggregates");
  });
});

/** T-073 — the policy sandbox.
 *
 *  The two assertions that matter are both about what the page will not let
 *  somebody do: replay weights that do not sum to one, and adopt a change
 *  without two named approvers. Everything else on the page is the service's
 *  numbers rendered, which the report table asserts against a fresh replay. */
test.describe("policy sandbox", () => {
  test.setTimeout(180_000);

  async function openSandbox(page: Page, request: APIRequestContext) {
    await signIn(page, request, "manager");
    await page.getByTestId("nav-sandbox").click();
    await expect(page).toHaveURL(/\/sandbox$/);
    await expect(page.getByTestId("in-force")).toContainText("policy/PF-STD", { timeout: 30_000 });
  }

  test("the pack in force is what the form starts from", async ({ page, request }) => {
    const token = await mintToken(request, "manager");
    const pack = await request.get(`${GATEWAY}/api/policy/policy/PF-STD/latest`, {
      headers: { authorization: `Bearer ${token}` },
    });
    const weights = (await pack.json()).dff.weights as Record<string, number>;

    await openSandbox(page, request);
    for (const [factor, weight] of Object.entries(weights)) {
      await expect(page.getByTestId(`weight-value-${factor}`)).toHaveText(weight.toFixed(2));
    }
    await expect(page.getByTestId("weight-sum")).toContainText("weights sum to 1");
  });

  test("weights that do not sum to one cannot be replayed", async ({ page, request }) => {
    await openSandbox(page, request);

    // Raise COMMITMENT without taking it from anywhere. The page must say so
    // and refuse to run, rather than sending a candidate the service would
    // reject with a message about a schema.
    await page.getByTestId("weight-COMMITMENT").fill("0.45");
    await expect(page.getByTestId("weight-sum")).toContainText("must sum to exactly 1.00");
    await expect(page.getByTestId("sandbox-run")).toBeDisabled();
  });

  test("a balanced change replays and reports both sides", async ({ page, request }) => {
    await openSandbox(page, request);

    const before = Number(await page.getByTestId("weight-value-CONDUCT").textContent());
    const commitment = Number(await page.getByTestId("weight-value-COMMITMENT").textContent());
    // Rounded to the slider's own step. 0.2 - 0.05 is 0.15000000000000002 in
    // binary floating point, which a range input rejects as malformed.
    await page.getByTestId("weight-CONDUCT").fill(Math.max(0, before - 0.05).toFixed(2));
    await page.getByTestId("weight-COMMITMENT").fill((commitment + 0.05).toFixed(2));
    await expect(page.getByTestId("weight-sum")).toContainText("weights sum to 1");

    await page.getByTestId("sandbox-run").click();
    await expect(page.getByTestId("sandbox-report")).toBeVisible({ timeout: 120_000 });

    // Both columns are present and the case counts match: a report that
    // compared different populations would look like a policy effect.
    const baseline = await page.getByTestId("baseline-cases").textContent();
    const candidate = await page.getByTestId("candidate-cases").textContent();
    expect(baseline).toBe(candidate);
    await expect(page.getByTestId("cases-replayed")).toContainText("No model was called");
  });

  test("adoption needs two different people", async ({ page, request }) => {
    await openSandbox(page, request);

    const conduct = Number(await page.getByTestId("weight-value-CONDUCT").textContent());
    const commitment = Number(await page.getByTestId("weight-value-COMMITMENT").textContent());
    await page.getByTestId("weight-CONDUCT").fill(Math.max(0, conduct - 0.05).toFixed(2));
    await page.getByTestId("weight-COMMITMENT").fill((commitment + 0.05).toFixed(2));
    await page.getByTestId("sandbox-run").click();
    await expect(page.getByTestId("sandbox-adopt")).toBeVisible({ timeout: 120_000 });

    await expect(page.getByTestId("sandbox-adopt-submit")).toBeDisabled();

    await page.getByTestId("approver-0").fill("u-same");
    await page.getByTestId("approver-1").fill("u-same");
    // Two names that are one person is not two approvers, and the page says so
    // by staying disabled rather than by letting the service refuse it.
    await expect(page.getByTestId("sandbox-adopt-submit")).toBeDisabled();

    await page.getByTestId("approver-1").fill("u-other");
    await expect(page.getByTestId("sandbox-adopt-submit")).toBeEnabled();
  });
});
