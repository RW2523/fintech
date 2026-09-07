/** The platform's constants, in words a person can read.
 *
 *  Every screen in this app was showing raw enum values — `COMPLIANCE_REVIEW`,
 *  `ROUTING_RULE`, `CREDIT_OFFICER`, `ELG-01` — because that is what the
 *  contracts carry and lowercasing them felt like enough. It was not. Somebody
 *  who did not build this cannot tell from `ROUTING_RULE` that a rule stopped
 *  the case before any score was computed, and that is exactly the fact that
 *  explains why the score beside it says "not scored".
 *
 *  So the code stays the identity and the words sit beside it. Two rules:
 *
 *  - The plain words never replace the code where the code is what somebody
 *    would quote in a ticket or a query. They sit next to it, or in the title.
 *  - Nothing here interprets. `describe()` says what a value *means*, not
 *    whether it is good news. A route is not a verdict.
 *
 *  An unknown value falls back to the code with its underscores removed, so a
 *  contract that gains a member does not leave a blank on a screen. */

type Term = { label: string; means: string };

const ROUTES: Record<string, Term> = {
  AUTONOMOUS: {
    label: "Decided automatically",
    means:
      "Inside the limits the Board set for the platform to act alone. No person reviewed it.",
  },
  OFFICER_REVIEW: {
    label: "Officer review",
    means: "A credit officer decides. The platform has recommended, not decided.",
  },
  SENIOR_REVIEW: {
    label: "Senior officer review",
    means: "Above an officer's limit, or close enough to a boundary to want a second pair of eyes.",
  },
  COMMITTEE: {
    label: "Credit committee",
    means: "Large or unusual enough that the committee decides together.",
  },
  COMPLIANCE: {
    label: "Compliance review",
    means: "Something about integrity or identity. Compliance looks before anybody lends.",
  },
  COMPLIANCE_REVIEW: {
    label: "Compliance review",
    means: "Something about integrity or identity. Compliance looks before anybody lends.",
  },
  ENHANCED_ASSESSMENT: {
    label: "Enhanced assessment",
    means: "Needs more work before a decision: more evidence, or a closer look at what is there.",
  },
  MANUAL_FALLBACK: {
    label: "Manual, platform degraded",
    means:
      "Part of the platform could not run, so nothing was decided automatically. A person takes it from here.",
  },
};

const RECOMMENDATIONS: Record<string, Term> = {
  APPROVE: { label: "Approve", means: "The platform found nothing that should stop this." },
  APPROVE_WITH_CONDITIONS: {
    label: "Approve with conditions",
    means: "Acceptable if the conditions on the record are met first.",
  },
  REVIEW: {
    label: "Needs review",
    means: "Not clear either way. A person should look at it before it goes further.",
  },
  DECLINE: { label: "Decline", means: "The platform found a reason this should not proceed." },
  COMPLIANCE_REVIEW: {
    label: "Send to compliance",
    means: "Not a credit question. Compliance should see it first.",
  },
  MORE_INFORMATION_REQUIRED: {
    label: "More information needed",
    means: "There is not enough on file to decide. Ask for what is missing.",
  },
};

const AUTHORITIES: Record<string, Term> = {
  CREDIT_OFFICER: { label: "Credit officer", means: "An officer may sign this off." },
  SENIOR_OFFICER: { label: "Senior officer", means: "Above an officer's limit." },
  CREDIT_COMMITTEE: { label: "Credit committee", means: "Needs the committee, not one person." },
  HEAD_OF_CREDIT: { label: "Head of Credit", means: "Needs the Head of Credit." },
  HEAD_OF_RISK: { label: "Head of Risk", means: "Needs the Head of Risk." },
  COLLECTIONS: { label: "Collections", means: "Belongs with collections, not underwriting." },
  COMPLIANCE: { label: "Compliance", means: "Belongs with compliance." },
};

const TIERS: Record<string, Term> = {
  FAST: {
    label: "Fast",
    means: "A short deliberation. Straightforward cases do not need the full council.",
  },
  STANDARD: { label: "Standard", means: "The usual council, with the usual budget." },
  EXTENDED: {
    label: "Extended",
    means: "A longer deliberation with more rounds, for cases that warrant it.",
  },
};

/** Which rung of the synthesis hierarchy settled the case (docs/05 §6). */
const STEPS: Record<string, Term> = {
  HARD_GATES: {
    label: "A hard gate stopped it",
    means:
      "A policy rule ruled it out before anything was weighed. That is why there is no score: nothing was scored.",
  },
  INTEGRITY_CRITICAL: {
    label: "An integrity signal stopped it",
    means: "Something about the documents or identity has to be resolved before credit is assessed.",
  },
  ROUTING_RULE: {
    label: "A routing rule sent it here",
    means:
      "A rule decided who should see this before any score was computed, so the case was never weighed.",
  },
  EVIDENCE_VALIDITY: {
    label: "The evidence would not stand",
    means: "What the case rests on could not be verified, so it was not scored on it.",
  },
  AUTHORITY: {
    label: "Authority decided the route",
    means: "The amount decides who signs it off, whatever the score said.",
  },
  WEIGHTED_SCORE: {
    label: "The weighted score decided it",
    means: "Every factor was scored and the weighted total fell where the policy says it falls.",
  },
  NO_FACTOR_SCORED: {
    label: "Nothing could be scored",
    means:
      "No Decision Factor was produced, so there was nothing to weigh. The case goes to a person rather than being judged on an absence.",
  },
};

/** Prefixes of the hard-gate rule ids, so a code is not the only thing shown. */
const GATE_FAMILIES: Record<string, string> = {
  ELG: "Eligibility",
  DOC: "Documents",
  AFF: "Affordability",
  EXP: "Exposure",
  INT: "Integrity",
  FRD: "Fraud",
  KYC: "Identity",
};

const STANCES: Record<string, Term> = {
  SUPPORT: { label: "Supports", means: "This agent found nothing against the recommendation." },
  LEAN_SUPPORT: { label: "Leans support", means: "Broadly in favour, with a reservation." },
  REVIEW: { label: "Wants a look", means: "Not opposed, but thinks a person should check." },
  NEED_MORE_EVIDENCE: {
    label: "Needs more evidence",
    means: "Cannot take a position on what it was given.",
  },
  LEAN_OPPOSE: { label: "Leans against", means: "Has a concern it thinks is material." },
  OPPOSE: { label: "Opposes", means: "Thinks the recommendation is wrong." },
  BLOCK: { label: "Blocks", means: "Found something that should stop this outright." },
};

const STATES: Record<string, Term> = {
  STABLE: { label: "Stable", means: "Behaving in line with their own history." },
  WATCH: { label: "Watch", means: "Something moved. Not yet a problem, worth knowing." },
  ELEVATED: {
    label: "Elevated",
    means: "A sustained change from their own baseline. Worth a conversation.",
  },
  CRITICAL: { label: "Critical", means: "The change is severe or accelerating. Act now." },
  RECOVERY: { label: "Recovering", means: "Moving back towards how they used to behave." },
};

const BOOKS: Record<string, Record<string, Term>> = {
  route: ROUTES,
  recommendation: RECOMMENDATIONS,
  authority: AUTHORITIES,
  tier: TIERS,
  step: STEPS,
  stance: STANCES,
  state: STATES,
};

export type Vocabulary = keyof typeof BOOKS;

/** The words for a code, or the code made readable when it is not known. */
export function say(book: Vocabulary, code: string | null | undefined): string {
  if (!code) return "not recorded";
  return BOOKS[book][code]?.label ?? code.replaceAll("_", " ").toLowerCase();
}

/** What the code means, for a tooltip or a line under a heading. Empty when
 *  there is nothing useful to add — a caller shows nothing rather than a
 *  restatement of the label. */
export function means(book: Vocabulary, code: string | null | undefined): string {
  if (!code) return "";
  return BOOKS[book][code]?.means ?? "";
}

/** What a hard gate is about, from its rule id. `ELG-01` is Eligibility. */
export function gateFamily(ruleId: string): string {
  return GATE_FAMILIES[ruleId.split("-")[0]?.toUpperCase() ?? ""] ?? "Policy";
}
