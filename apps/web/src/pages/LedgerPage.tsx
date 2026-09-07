import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";

import { useApi } from "../api";
import { Card, Chip, Copyable, Empty, Problem, type Tone } from "../components/primitives";
import type { Reconstruction, TimelineEvent } from "../types";

const SOURCE_TONE: Record<string, Tone> = {
  ledger: "accent",
  committee: "neutral",
  audit: "warn",
};

/** docs/09 §6 — the ledger viewer.
 *
 *  One case, in the order it happened, with the hash of every entry and a
 *  badge saying whether the chain still verifies. The badge is the point: a
 *  timeline without one asks the reader to trust it on sight, which is the one
 *  thing a ledger view must never do. */
export function LedgerPage() {
  const { caseId = "" } = useParams();
  const navigate = useNavigate();
  const [search, setSearch] = useState(caseId);

  const request = useApi();
  const reconstruction = useQuery({
    queryKey: ["reconstruct", caseId],
    enabled: Boolean(caseId),
    queryFn: () => request<Reconstruction>(`/api/audit/reconstruct/${encodeURIComponent(caseId)}`),
  });

  return (
    <div className="flex flex-col gap-5">
      <header>
        <h1 className="text-[26px] font-bold tracking-tight">Decision ledger</h1>
        <p className="text-sm text-[--color-muted]">
          One case, in the order it happened, with the hash of every entry and
          whether the chain still verifies.
        </p>
      </header>
      <Card title="Reconstruct a case" icon="search" tone="accent" testId="ledger-search">
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            navigate(`/ledger/${encodeURIComponent(search.trim())}`);
          }}
        >
          <input
            aria-label="case id"
            data-testid="ledger-search-input"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="case id"
            className="w-96 rounded border border-[--color-line] bg-[--color-surface] px-2 py-1 font-mono text-xs"
          />
          <button
            type="submit"
            data-testid="ledger-search-submit"
            className="rounded border border-[--color-line] px-3 py-1 text-sm hover:border-[--color-accent]"
          >
            Reconstruct
          </button>
          {reconstruction.data ? (
            <a
              data-testid="ledger-export"
              className="text-xs underline decoration-dotted"
              href={URL.createObjectURL(
                new Blob([JSON.stringify(reconstruction.data, null, 2)], {
                  type: "application/json",
                }),
              )}
              download={`${caseId}.json`}
            >
              export JSON
            </a>
          ) : null}
        </form>
      </Card>

      {reconstruction.error ? <Problem error={reconstruction.error} /> : null}
      {reconstruction.isPending && caseId ? (
        <p className="text-sm text-[--color-muted]">Reading the chain.</p>
      ) : null}

      {reconstruction.data ? (
        <>
          <ChainBadge data={reconstruction.data} />
          <Timeline events={reconstruction.data.timeline} />
          <Alongside data={reconstruction.data} />
        </>
      ) : null}
    </div>
  );
}

function ChainBadge({ data }: { data: Reconstruction }) {
  const { verified, entries_checked: checked, breaks } = data.chain;
  const tone: Tone = verified === true ? "pass" : verified === false ? "fail" : "warn";
  const label =
    verified === true
      ? `chain verified (${checked ?? 0} entries)`
      : verified === false
        ? `chain broken (${breaks?.length ?? 0})`
        : "chain not verified";

  return (
    <Card title="Chain" icon="shield" tone="pass" testId="chain-badge">
      <div className="flex flex-wrap items-center gap-3">
        <Chip tone={tone} testId="chain-verdict">
          {label}
        </Chip>
        {data.unavailable.length > 0 ? (
          <span className="text-xs text-[--color-warn]" data-testid="chain-unavailable">
            {/* Named rather than hidden: a gap the reader takes for an absence
                of events is worse than no timeline at all. */}
            could not be read: {data.unavailable.join(", ")}
          </span>
        ) : null}
      </div>
      {breaks && breaks.length > 0 ? (
        <ul className="mt-2 flex flex-col gap-1 text-xs" data-testid="chain-breaks">
          {breaks.map((entry) => (
            <li key={`${entry.seq}-${entry.reason}`}>
              <span className="font-mono">#{entry.seq}</span> {entry.reason}
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}

function Timeline({ events }: { events: TimelineEvent[] }) {
  return (
    <Card title="Timeline" icon="clock" tone="accent" testId="timeline">
      {events.length === 0 ? (
        <Empty>Nothing is recorded against this case.</Empty>
      ) : (
        <ol className="flex flex-col gap-2" data-testid="timeline-events">
          {events.map((event, index) => (
            <TimelineRow key={`${event.entry_id ?? event.kind}-${index}`} event={event} />
          ))}
        </ol>
      )}
    </Card>
  );
}

function TimelineRow({ event }: { event: TimelineEvent }) {
  const [open, setOpen] = useState(false);
  return (
    <li
      className="rounded border border-[--color-line] px-3 py-2"
      data-testid={`timeline-${event.kind}`}
    >
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Chip tone={SOURCE_TONE[event.source] ?? "neutral"}>{event.source}</Chip>
        <span className="font-medium">{event.kind.replaceAll("_", " ").toLowerCase()}</span>
        {event.agent_id ? <span className="text-[--color-muted]">{event.agent_id}</span> : null}
        {event.stance ? <Chip>{event.stance.replaceAll("_", " ").toLowerCase()}</Chip> : null}
        <span className="ml-auto text-[--color-muted]">{event.at ?? "no timestamp"}</span>
      </div>

      {event.hash ? (
        <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-[--color-muted]">
          <span>
            hash <Copyable value={event.hash} label="hash" />
          </span>
          <span>
            prev <Copyable value={event.prev_hash ?? ""} label="previous hash" />
          </span>
        </div>
      ) : null}

      <button
        type="button"
        className="mt-1 text-xs underline decoration-dotted"
        onClick={() => setOpen((was) => !was)}
      >
        {open ? "hide" : "show"} the entry
      </button>
      {open ? (
        <pre
          data-testid="timeline-payload"
          className="mt-1 max-h-72 overflow-auto rounded bg-[--color-canvas] p-2 text-[11px]"
        >
          {JSON.stringify(event.payload, null, 2)}
        </pre>
      ) : null}
    </li>
  );
}

function Alongside({ data }: { data: Reconstruction }) {
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <Card title="Documents" icon="documents" tone="accent" testId="ledger-documents">
        {data.documents.length === 0 ? (
          <Empty>No document is held for this case.</Empty>
        ) : (
          <ul className="flex flex-col gap-1 text-xs">
            {data.documents.map((document) => (
              <li key={document.document_id} className="flex items-center gap-2">
                <Chip>{document.type}</Chip>
                <span className="font-mono">{document.document_id.slice(0, 18)}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Findings" icon="alert" tone="warn" testId="ledger-findings">
        {data.findings.length === 0 ? (
          <Empty>Nothing was found against this case.</Empty>
        ) : (
          <ul className="flex flex-col gap-1 text-xs">
            {data.findings.map((finding, index) => (
              <li key={`${finding.code}-${index}`}>
                <Chip tone={finding.severity === "HIGH" ? "fail" : "warn"}>{finding.code}</Chip>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="What was carried out" icon="check" tone="pass" testId="ledger-actions">
        {data.actions.length === 0 ? (
          <Empty>Nothing was executed on this case.</Empty>
        ) : (
          <ul className="flex flex-col gap-1 text-xs">
            {data.actions.map((action) => (
              <li key={action.action_id} className="flex items-center gap-2">
                <Chip tone={action.state === "EXECUTED" ? "pass" : "warn"}>{action.state}</Chip>
                <span className="font-mono">{action.action_id.slice(0, 18)}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
