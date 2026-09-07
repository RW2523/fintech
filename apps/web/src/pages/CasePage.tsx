import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { useApi } from "../api";
import { useMemberNames } from "../api/members";
import { useAuth } from "../auth";
import { AgentDiscussion } from "../components/AgentDiscussion";
import { AskTheFile } from "../components/AskTheFile";
import { DecideForm } from "../components/DecideForm";
import { DecisionCard } from "../components/DecisionCard";
import { EvidencePanel } from "../components/EvidencePanel";
import {
  Card,
  Chip,
  Copyable,
  Empty,
  FactStrip,
  Glyph,
  Problem,
  Stat,
  Tabs,
  type Tone,
} from "../components/primitives";
import { NARRATIVE_AUDIENCES } from "../types";
import type { Explanation, HeldRecord, Opinion, Queue } from "../types";
import { means, say } from "../vocabulary";

/** docs/09 §3 — the officer workbench.
 *
 *  Four things, in the order an officer needs them: what the case is, what was
 *  decided, what the decision rests on, and what they may do about it.
 *
 *  The strip along the top is the answer to "what am I looking at", in the
 *  same place on every detail screen in this app. Under it are the three
 *  signals that decide how much attention the case needs, then the work
 *  itself. The discussion sits behind a tab rather than below the fold,
 *  because it explains the decision rather than being it — and an officer who
 *  scrolls past the deciding factors to reach the actions has been made to
 *  read six agent opinions to get to a button. */
type Panel = "evidence" | "narrative";

export function CasePage() {
  const { caseId = "" } = useParams();
  const request = useApi();
  const { session } = useAuth();
  const [selectedEvidence, setSelectedEvidence] = useState<string | null>(null);
  const [panel, setPanel] = useState<Panel>("evidence");
  const names = useMemberNames([]);

  // The queue carries the record id for a case. Reading it from there keeps
  // this page from needing a second lookup service just to find the record.
  const queue = useQuery({
    queryKey: ["queue", ""],
    queryFn: () => request<Queue>("/api/decision/queue"),
  });

  const entry = queue.data?.decisions.find(
    (row) =>
      row.case_id === caseId || row.snapshot_id === caseId || row.decision_record_id === caseId,
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
    return <p className="text-sm text-muted">Loading the case.</p>;
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

  const named = names.get(entry.member_id ?? "") ?? entry.member_id ?? "member not recorded";
  const gates = decision?.hard_gates ?? [];
  const failedGates = gates.filter((gate) => gate.result === "FAIL");
  const gateTone: Tone = gates.length === 0 ? "neutral" : failedGates.length ? "fail" : "pass";

  return (
    <div className="flex flex-col gap-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[34px] leading-tight font-extrabold tracking-tight">Officer workbench</h1>
          <p className="text-sm text-muted">
            Evidence-first decision support for one application.
          </p>
        </div>
        <Link
          to={`/ledger/${encodeURIComponent(caseId)}`}
          data-testid="reconstruct-link"
          className="text-sm font-medium text-accent underline decoration-dotted"
        >
          Reconstruct from the ledger
        </Link>
      </header>

      <FactStrip
        testId="case-header"
        initials={
          /^[A-Za-z]/.test(named)
            ? named.split(/\s+/).slice(0, 2).map((part) => part[0]!.toUpperCase()).join("")
            : named.replace(/[^A-Za-z0-9]/g, "").slice(-2)
        }
        title={named}
        subtitle={
          <span className="font-mono text-[11px]">
            {entry.member_id ?? "no member"} · {caseId.slice(0, 22)}
          </span>
        }
        facts={[
          { label: "Deliberation", value: say("tier", entry.tier), icon: "scale" },
          {
            label: "Goes to",
            value: <span title={means("route", entry.route)}>{say("route", entry.route)}</span>,
            icon: "route",
          },
          {
            label: "Who signs it off",
            value: (
              <span title={means("authority", entry.required_authority)}>
                {say("authority", entry.required_authority)}
              </span>
            ),
            icon: "shield",
          },
          {
            label: "Snapshot",
            value: entry.snapshot_id ? (
              <Copyable value={entry.snapshot_id} label="snapshot id" />
            ) : (
              "none"
            ),
            icon: "file",
          },
          {
            label: "Policy in force",
            value: (
              <span className="text-xs" data-testid="versions">
                {decision?.policy_version ?? "policy unknown"}
              </span>
            ),
            icon: "sandbox",
          },
        ]}
      />

      {/* The three signals that decide how much attention this case needs, and
          each is a value the record carries rather than one read off a chart. */}
      <div className="grid gap-4 sm:grid-cols-3">
        <Stat
          label="Hard gates"
          icon="shield"
          tone={gateTone}
          value={
            gates.length === 0
              ? "none run"
              : failedGates.length === 0
                ? "all passed"
                : `${failedGates.length} failed`
          }
          hint={`${gates.length} evaluated`}
        />
        <Stat
          label="Confidence"
          icon="chart"
          tone="accent"
          value={entry.confidence == null ? "not scored" : entry.confidence.toFixed(2)}
          hint={decision?.deciding_step ? say("step", decision.deciding_step) : undefined}
        />
        <Stat
          label="Disagreement"
          icon="chat"
          tone={entry.disagreement && entry.disagreement > 0.4 ? "warn" : "neutral"}
          value={entry.disagreement == null ? "not scored" : entry.disagreement.toFixed(2)}
          hint={
            decision?.challenger_open ? "the challenger is still open" : "across the council"
          }
        />
      </div>

      {/* One sentence saying what happened and what happens next, before any
          of the detail. Somebody who has never seen this platform can read the
          screen from here; somebody who has can skip it. */}
      {decision ? (
        <Card testId="what-happened" padded={false}>
          <div className="flex items-start gap-3.5 p-5">
            <Glyph icon="info" tone="accent" size="lg" />
            <div>
              <p className="text-sm">
                The platform assessed this and recommends{" "}
                <strong>{say("recommendation", decision.recommendation)}</strong>. It goes
                to <strong>{say("route", decision.route)}</strong>, and{" "}
                <strong>{say("authority", decision.required_authority)}</strong> signs it off.
              </p>
              <p className="mt-1 text-xs text-muted">
                {say("step", decision.deciding_step)}
                {means("step", decision.deciding_step)
                  ? ` — ${means("step", decision.deciding_step)}`
                  : ""}
              </p>
              <p className="mt-1.5 text-xs text-muted">
                Nothing here was decided by a model. The council argues; the policy
                decides; a person signs.
              </p>
            </div>
          </div>
        </Card>
      ) : null}

      {record.error ? <Problem error={record.error} /> : null}
      {decision ? <DecisionCard record={decision} /> : null}

      {/* The recommendation and the council that produced it, side by side.
          They were stacked, and an officer reading why a case was routed to
          them had to hold the recommendation in their head while scrolling
          past six opinions to find the one that disagreed. */}
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <AgentDiscussion
          opinions={opinions}
          onEvidence={(reference) => {
            setSelectedEvidence(reference);
            setPanel("evidence");
          }}
        />
        <AskTheFile
          caseId={caseId}
          decisionRecordId={recordId}
          onCitation={(reference) => {
            setSelectedEvidence(reference);
            setPanel("evidence");
          }}
        />
      </div>

      {/* What the decision rests on. Behind the tabs because an officer needs
          one of these three at a time and all three at once is a screen nobody
          reads to the bottom of. */}
      <Tabs
        value={panel}
        onChange={setPanel}
        testIdPrefix="panel"
        tabs={[
          { key: "evidence", label: "Evidence" },
          { key: "narrative", label: "Narrative", count: written.length || undefined },
        ]}
      />

      {panel === "narrative" ? (
        written.length > 0 ? (
          <Card title="Narrative" icon="chat" tone="note" testId="narrative">
            <dl className="flex flex-col gap-4 text-sm">
              {written.map(([audience, passage]) => (
                <div key={audience}>
                  <dt className="flex items-center gap-2 text-xs font-semibold tracking-wide text-muted uppercase">
                    {audience}
                    {/* The status belongs beside the passage, not above the
                        card: one audience can be written from the record while
                        another was written by the model, and a single banner
                        would misdescribe both. */}
                    {passage.status === "DEGRADED" ? (
                      <Chip tone="warn">written without the model</Chip>
                    ) : null}
                  </dt>
                  <dd className="mt-1 leading-relaxed" data-testid={`narrative-${audience}`}>
                    {passage.text}
                  </dd>
                </div>
              ))}
            </dl>
          </Card>
        ) : (
          <Card title="Narrative" icon="chat" tone="note" testId="narrative">
            <Empty>
              Nothing has been written for this case yet. A narrative is
              regenerated from the record rather than stored, so an empty one
              means it has not been asked for.
            </Empty>
          </Card>
        )
      ) : (
        <EvidencePanel
          explanation={explanation.data}
          selected={selectedEvidence}
          onSelect={setSelectedEvidence}
        />
      )}

      {/* The actions, pinned. An officer who has read to the bottom of a long
          case should not have to scroll back up to act on it, and one who has
          not read to the bottom should still be able to see what acting on it
          would mean. */}
      <div className="sticky bottom-4 z-[8] rounded-2xl border border-line bg-surface/95 shadow-lift backdrop-blur-md">
        <DecideForm record={decision} caseId={caseId} role={session?.role ?? "officer"} />
      </div>
    </div>
  );
}
