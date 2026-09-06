import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { useApi } from "../api";
import { AUTHORITY, type Role } from "../auth";
import { Card, Chip, Problem } from "./primitives";
import type { DecisionRecord } from "../types";

/** docs/03 §7 — why a person departed from the recommendation.
 *
 *  A closed list, because a free-text reason cannot be counted, and the whole
 *  point of recording overrides is to see where the platform is wrong often. */
export const OVERRIDE_CODES = [
  { code: "OVR-01", label: "Income evidence stronger than the file shows" },
  { code: "OVR-02", label: "Commitment ending shortly" },
  { code: "OVR-03", label: "Employer verified directly" },
  { code: "OVR-04", label: "Document quality, not substance" },
  { code: "OVR-05", label: "Member relationship not captured by the score" },
  { code: "OVR-06", label: "Security or guarantor not in the model" },
  { code: "OVR-07", label: "Known data error in the core" },
  { code: "OVR-08", label: "Policy exception already approved" },
  { code: "OVR-09", label: "Fraud finding explained" },
  { code: "OVR-10", label: "Hardship handled outside this case" },
  { code: "OVR-11", label: "Model output implausible on the facts" },
  { code: "OVR-12", label: "Other" },
] as const;

/** OVR-12 has no shared meaning, so it has to carry its own. */
const OTHER_MIN_TEXT = 60;
const MIN_TEXT = 20;

const COMMITTING = ["APPROVE", "APPROVE_WITH_CONDITIONS", "DECLINE"] as const;
const OPEN = ["REQUEST_INFO", "ESCALATE", "DEFER"] as const;

type Action = (typeof COMMITTING)[number] | (typeof OPEN)[number];

const LABELS: Record<Action, string> = {
  APPROVE: "Approve",
  APPROVE_WITH_CONDITIONS: "Approve with conditions",
  DECLINE: "Decline",
  REQUEST_INFO: "Request information",
  ESCALATE: "Escalate",
  DEFER: "Defer",
};

/** docs/09 §3.4 — what this person may do, and doing it.
 *
 *  An action beyond the role's authority is shown disabled with the reason,
 *  never hidden: an officer needs to know the action exists and who can take
 *  it, so they can send it on rather than wonder. The API enforces the same
 *  rule regardless, so what the screen shows is a courtesy and not the
 *  control. */
export function DecideForm({
  record,
  caseId,
  role,
}: {
  record: DecisionRecord | undefined;
  caseId: string;
  role: Role;
}) {
  const request = useApi();
  const queryClient = useQueryClient();

  const [action, setAction] = useState<Action | null>(null);
  const [conditions, setConditions] = useState("");
  const [override, setOverride] = useState(false);
  const [code, setCode] = useState<string>("OVR-01");
  const [note, setNote] = useState("");

  const required = record?.required_authority ?? "CREDIT_OFFICER";
  const authority = AUTHORITY[role].authority;
  const mayCommit = reaches(authority, required);
  const blocked = mayCommit
    ? null
    : `This case needs ${required.replaceAll("_", " ").toLowerCase()}. You sign as ${
        authority?.replaceAll("_", " ").toLowerCase() ?? "no credit authority"
      }.`;

  // An override is a departure from what the platform recommended, so what
  // counts as one is derived from the record rather than left to the officer
  // to declare. Declaring it by hand would make the count meaningless.
  const departs =
    action !== null &&
    COMMITTING.includes(action as (typeof COMMITTING)[number]) &&
    record?.recommendation !== undefined &&
    !agreesWith(action, record.recommendation);

  const minimum = code === "OVR-12" ? OTHER_MIN_TEXT : MIN_TEXT;
  const noteTooShort = departs && override && note.trim().length < minimum;

  const decide = useMutation({
    mutationFn: () =>
      request<{ human_decision_id: string; final_action: string }>(
        "/api/decision/human-decisions",
        {
          method: "POST",
          body: {
            decision_record_id: record?.decision_record_id,
            case_id: caseId,
            actor_id: `${role}-demo`,
            role,
            final_action: action,
            conditions: conditions
              .split("\n")
              .map((line) => line.trim())
              .filter(Boolean),
            override: departs && override,
            override_reason:
              departs && override ? { code, text: note.trim() } : undefined,
            evidence_acknowledged: [],
          },
        },
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["queue"] });
      void queryClient.invalidateQueries({ queryKey: ["record"] });
    },
  });

  return (
    <Card title="What you can do" testId="actions">
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap gap-2">
          {COMMITTING.map((value) => (
            <button
              key={value}
              type="button"
              disabled={!mayCommit}
              aria-pressed={action === value}
              data-testid={`action-${value.toLowerCase()}`}
              title={blocked ?? undefined}
              onClick={() => setAction(value)}
              className={`rounded border px-3 py-1.5 text-sm disabled:cursor-not-allowed disabled:opacity-50 ${
                action === value
                  ? "border-[--color-accent] bg-sky-50"
                  : "border-[--color-line] hover:border-[--color-accent]"
              }`}
            >
              {LABELS[value]}
            </button>
          ))}
          {OPEN.map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={action === value}
              data-testid={`action-${value.toLowerCase()}`}
              onClick={() => setAction(value)}
              className={`rounded border px-3 py-1.5 text-sm ${
                action === value
                  ? "border-[--color-accent] bg-sky-50"
                  : "border-[--color-line] hover:border-[--color-accent]"
              }`}
            >
              {LABELS[value]}
            </button>
          ))}
        </div>

        {blocked ? (
          <p data-testid="authority-reason" className="text-xs text-[--color-warn]">
            {blocked}
          </p>
        ) : null}

        {action === "APPROVE_WITH_CONDITIONS" ? (
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-medium">Conditions, one per line</span>
            <textarea
              data-testid="conditions"
              rows={3}
              value={conditions}
              onChange={(event) => setConditions(event.target.value)}
              className="rounded border border-[--color-line] bg-[--color-surface] p-2 text-xs"
            />
          </label>
        ) : null}

        {departs ? (
          <div className="flex flex-col gap-2 rounded border border-[--color-warn] p-3">
            <div className="flex items-center gap-2 text-xs">
              <Chip tone="warn">departs from the recommendation</Chip>
              <span className="text-[--color-muted]">
                recommended {record?.recommendation?.replaceAll("_", " ").toLowerCase()}
              </span>
            </div>
            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                data-testid="override-confirm"
                checked={override}
                onChange={(event) => setOverride(event.target.checked)}
              />
              I am overriding the recommendation, and this is why
            </label>
            {override ? (
              <>
                <select
                  data-testid="override-code"
                  value={code}
                  onChange={(event) => setCode(event.target.value)}
                  className="rounded border border-[--color-line] bg-[--color-surface] p-1 text-xs"
                >
                  {OVERRIDE_CODES.map((option) => (
                    <option key={option.code} value={option.code}>
                      {option.code} — {option.label}
                    </option>
                  ))}
                </select>
                <textarea
                  data-testid="override-note"
                  rows={3}
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                  placeholder={`At least ${minimum} characters`}
                  className="rounded border border-[--color-line] bg-[--color-surface] p-2 text-xs"
                />
                {noteTooShort ? (
                  <p data-testid="override-note-short" className="text-xs text-[--color-warn]">
                    {/* Said before the API refuses it, so the officer is not
                        told off after the fact for something the screen could
                        have told them while they typed. */}
                    {minimum - note.trim().length} more characters needed.
                  </p>
                ) : null}
              </>
            ) : null}
          </div>
        ) : null}

        {decide.error ? <Problem error={decide.error} /> : null}
        {decide.data ? (
          <p data-testid="decision-recorded" className="text-xs text-[--color-pass]">
            Recorded as {decide.data.final_action.replaceAll("_", " ").toLowerCase()}.
          </p>
        ) : null}

        <div className="flex items-center gap-3">
          <button
            type="button"
            data-testid="submit-decision"
            disabled={
              action === null ||
              decide.isPending ||
              Boolean(decide.data) ||
              (departs && !override) ||
              noteTooShort
            }
            onClick={() => decide.mutate()}
            className="rounded border border-[--color-accent] px-3 py-1.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50"
          >
            {decide.isPending ? "Recording" : "Record this decision"}
          </button>
          <p className="text-xs text-[--color-muted]">
            Every decision is recorded against you with its reason, and the
            service checks your authority whatever this screen allowed.
          </p>
        </div>
      </div>
    </Card>
  );
}

/** docs/05 §4 — the approval ladder. */
const LADDER = ["CREDIT_OFFICER", "SENIOR_OFFICER", "CREDIT_COMMITTEE"];

function reaches(authority: string | null, required: string): boolean {
  const actor = LADDER.indexOf(authority ?? "");
  const needed = LADDER.indexOf(required);
  if (needed < 0) return false;
  return actor >= needed;
}

/** Whether the action agrees with what was recommended. */
function agreesWith(action: string, recommendation: string): boolean {
  if (action === "DECLINE") return recommendation === "DECLINE";
  return recommendation === "APPROVE" || recommendation === "APPROVE_WITH_CONDITIONS";
}
