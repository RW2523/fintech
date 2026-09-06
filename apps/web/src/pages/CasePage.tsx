import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { useApi } from "../api";
import { AUTHORITY, useAuth } from "../auth";
import { AgentDiscussion } from "../components/AgentDiscussion";
import { DecisionCard } from "../components/DecisionCard";
import { EvidencePanel } from "../components/EvidencePanel";
import { Card, Chip, Copyable, Empty, Figure, Problem } from "../components/primitives";
import { NARRATIVE_AUDIENCES } from "../types";
import type { DecisionRecord, Explanation, HeldRecord, Opinion, Queue } from "../types";

/** docs/09 §3 — the case page.
 *
 *  Four things, in the order an officer needs them: what the case is, what was
 *  decided, what the decision rests on, and what they may do about it. The
 *  discussion sits underneath, because it explains the decision rather than
 *  being it. */
export function CasePage() {
  const { caseId = "" } = useParams();
  const request = useApi();
  const { session } = useAuth();
  const [selectedEvidence, setSelectedEvidence] = useState<string | null>(null);

  // The queue carries the record id for a case. Reading it from there keeps
  // this page from needing a second lookup service just to find the record.
  const queue = useQuery({
    queryKey: ["queue", ""],
    queryFn: () => request<Queue>("/api/decision/queue"),
  });

  const entry = queue.data?.decisions.find(
    (row) => row.case_id === caseId || row.snapshot_id === caseId
      || row.decision_record_id === caseId,
  );
  const recordId = entry?.decision_record_id;

  const record = useQuery({
    queryKey: ["record", recordId],
    enabled: Boolean(recordId),
    // The service returns the record beside what the ledger knows about it,
    // so the record stays exactly a DecisionRecord.
    queryFn: () => request<HeldRecord>(`/api/decision/decision-records/${recordId}`),
  });

  const explanation = useQuery({
    queryKey: ["explanation", recordId],
    enabled: Boolean(recordId),
    queryFn: () =>
      request<Explanation>("/api/governance/explain/factors", {
        method: "POST",
        body: { decision_record_id: recordId },
      }),
  });

  const run = useQuery({
    queryKey: ["run", record.data?.record.committee_run_id],
    enabled: Boolean(record.data?.record.committee_run_id),
    queryFn: () =>
      request<{ opinions: { body: Opinion }[] }>(
        `/api/committee/committee/runs/${record.data?.record.committee_run_id}/opinions`,
      ),
  });

  if (queue.isPending) {
    return <p className="text-sm text-[--color-muted]">Loading the case.</p>;
  }
  if (queue.error) {
    return <Problem error={queue.error} />;
  }
  if (!entry) {
    return (
      <Empty>
        No decision is recorded for {caseId}. A case appears here once it has
        been assessed.
      </Empty>
    );
  }

  const decision = record.data?.record;
  const opinions = (run.data?.opinions ?? []).map((row) => row.body);

  // An audience with nothing written for it is left out rather than shown
  // empty: a blank passage reads as "there is nothing to say about this case".
  const written = NARRATIVE_AUDIENCES.flatMap((audience) => {
    const passage = decision?.narrative?.[audience];
    return passage?.text ? ([[audience, passage]] as const) : [];
  });

  return (
    <div className="flex flex-col gap-4">
      <Card title="Case" testId="case-header">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
          <Figure label="Case" testId="case-id" value={
            <span className="font-mono text-xs">{caseId.slice(0, 22)}</span>} />
          <Figure label="Member" value={
            <span className="font-mono text-xs">{entry.member_id ?? "not recorded"}</span>} />
          <Figure label="Tier" value={entry.tier ?? "unknown"} />
          <Figure
            label="Snapshot"
            value={entry.snapshot_id ? <Copyable value={entry.snapshot_id} label="snapshot id" /> : "none"}
          />
          <Figure
            label="Versions"
            testId="versions"
            value={
              <span className="text-xs font-normal text-[--color-muted]">
                {decision?.policy_version ?? "policy unknown"}
              </span>
            }
            hint={decision?.dff_version}
          />
        </div>
      </Card>

      {record.error ? <Problem error={record.error} /> : null}
      {decision ? <DecisionCard record={decision} /> : null}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <EvidencePanel
          explanation={explanation.data}
          selected={selectedEvidence}
          onSelect={setSelectedEvidence}
        />
        <Actions
          record={decision}
          role={session?.role ?? "officer"}
          amount={Number(entry.weighted_score ?? 0)}
        />
      </div>

      <AgentDiscussion opinions={opinions} onEvidence={setSelectedEvidence} />

      {written.length > 0 ? (
        <Card title="Narrative" testId="narrative">
          <dl className="flex flex-col gap-3 text-sm">
            {written.map(([audience, passage]) => (
              <div key={audience}>
                <dt className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-[--color-muted]">
                  {audience}
                  {/* The status belongs beside the passage, not above the
                      card: one audience can be written from the record while
                      another was written by the model, and a single banner
                      would misdescribe both. */}
                  {passage.status === "DEGRADED" ? (
                    <Chip tone="warn">written without the model</Chip>
                  ) : null}
                </dt>
                <dd data-testid={`narrative-${audience}`}>{passage.text}</dd>
              </div>
            ))}
          </dl>
        </Card>
      ) : null}
    </div>
  );
}

/** docs/09 §3.4 — what this person may do.
 *
 *  An action beyond the role's authority is shown disabled with the reason,
 *  never hidden: an officer needs to know the action exists and who can take
 *  it, so they can send it on rather than wonder. */
function Actions({
  record,
  role,
  amount,
}: {
  record: DecisionRecord | undefined;
  role: keyof typeof AUTHORITY;
  amount: number;
}) {
  const ceiling = AUTHORITY[role].approves;
  const required = record?.required_authority ?? "CREDIT_OFFICER";
  const mayApprove =
    ceiling === null || (ceiling > 0 && required === "CREDIT_OFFICER") ||
    (ceiling >= 75000 && required !== "CREDIT_COMMITTEE");

  const reason = mayApprove
    ? null
    : `This case needs ${required.replaceAll("_", " ").toLowerCase()}. Your role approves ${
        ceiling === 0 ? "nothing" : `up to ${ceiling?.toLocaleString()}`
      }.`;

  return (
    <Card title="What you can do" testId="actions">
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap gap-2">
          {["Approve", "Approve with conditions", "Decline"].map((label) => (
            <button
              key={label}
              type="button"
              disabled={!mayApprove}
              data-testid={`action-${label.split(" ")[0].toLowerCase()}`}
              title={reason ?? undefined}
              className="rounded border border-[--color-line] px-3 py-1.5 text-sm hover:border-[--color-accent] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {label}
            </button>
          ))}
          {["Request information", "Escalate", "Defer"].map((label) => (
            <button
              key={label}
              type="button"
              data-testid={`action-${label.split(" ")[0].toLowerCase()}`}
              className="rounded border border-[--color-line] px-3 py-1.5 text-sm hover:border-[--color-accent]"
            >
              {label}
            </button>
          ))}
        </div>
        {reason ? (
          <p data-testid="authority-reason" className="text-xs text-[--color-warn]">
            {reason}
          </p>
        ) : null}
        <p className="text-xs text-[--color-muted]">
          Every decision is recorded against you with its reason. An override of
          the recommendation needs a reason code and a note.
        </p>
        {amount ? null : null}
      </div>
    </Card>
  );
}
