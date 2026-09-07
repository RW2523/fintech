import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Card, Chip, Copyable, Empty, Problem } from "./primitives";
import { useBlobApi } from "../api";
import type { Explanation } from "../types";

/** docs/09 §3.7 — the evidence panel.
 *
 *  A claim is only worth as much as what it cites, so clicking one brings the
 *  reader here rather than asking them to trust it. A document field opens the
 *  page image with the box the value was read from drawn on it: the point is
 *  to let somebody check the number against the paper, not to illustrate. */
export function EvidencePanel({
  explanation,
  selected,
  onSelect,
}: {
  explanation: Explanation | undefined;
  selected: string | null;
  onSelect: (evidenceId: string | null) => void;
}) {
  const items = explanation?.levels.provenance ?? [];

  const grouped = useMemo(() => {
    const byType = new Map<string, typeof items>();
    for (const item of items) {
      const key = item.type ?? (item.finding_id ? "FINDING" : "DOCUMENT");
      byType.set(key, [...(byType.get(key) ?? []), item]);
    }
    return [...byType.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [items]);

  const current = items.find(
    (item) => (item.evidence_id ?? item.document_id ?? item.finding_id) === selected,
  );

  return (
    <Card
      title="Evidence & files"
      icon="documents"
      tone="accent"
      testId="evidence-panel"
      right={
        <span className="flex items-center gap-3">
          <span className="text-xs text-muted">{items.length} cited</span>
          {selected ? (
            <button
              type="button"
              className="text-xs underline decoration-dotted hover:text-accent"
              onClick={() => onSelect(null)}
            >
              clear
            </button>
          ) : null}
        </span>
      }
    >
      {items.length === 0 ? (
        <Empty>
          No evidence is recorded against this decision. That is itself worth
          knowing: a claim without evidence should not have been accepted.
        </Empty>
      ) : (
        /* Two columns, because a case can cite hundreds of things. Rendering
           them all in one flow made this page twelve thousand pixels tall and
           the officer's actions unreachable: the list scrolls in its own
           column and the thing being examined stays put beside it. */
        <div className="grid gap-4 lg:grid-cols-[minmax(0,17rem)_minmax(0,1fr)]">
          <div className="flex max-h-[28rem] flex-col gap-4 overflow-y-auto pr-1">
            {grouped.map(([type, entries]) => (
              <EvidenceGroup
                key={type}
                type={type}
                entries={entries}
                selected={selected}
                onSelect={onSelect}
              />
            ))}
          </div>

          <div className="min-w-0">
            {current ? (
              <EvidenceDetail item={current} />
            ) : (
              <div className="flex h-full min-h-[12rem] items-center justify-center rounded-xl border border-dashed border-line bg-raised p-6 text-center">
                <p className="max-w-xs text-sm text-muted">
                  Pick something on the left to see where it came from. A
                  document field opens the page image with the box the value was
                  read from drawn on it.
                </p>
              </div>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

/** One kind of evidence, collapsed to a readable number of rows.
 *
 *  A case can cite two hundred core fields. All of them at once is not a list
 *  anybody reads; it is a wall that hides the four findings underneath it. */
function EvidenceGroup({
  type,
  entries,
  selected,
  onSelect,
}: {
  type: string;
  entries: Explanation["levels"]["provenance"];
  selected: string | null;
  onSelect: (evidenceId: string | null) => void;
}) {
  const FIRST = 8;
  const [all, setAll] = useState(false);
  // Whatever is selected is always rendered, or clicking a claim's citation
  // would select a row the reader cannot see.
  const holdsSelected = entries.some(
    (item) => (item.evidence_id ?? item.document_id ?? item.finding_id) === selected,
  );
  const shown = all || holdsSelected ? entries : entries.slice(0, FIRST);

  return (
    <div className="flex flex-col gap-1.5">
      <h3 className="flex items-center justify-between text-xs font-semibold tracking-wide text-muted uppercase">
        {type.replaceAll("_", " ").toLowerCase()}
        <span className="rounded-full bg-sunken px-1.5 py-0.5 text-[10px] tabular-nums">
          {entries.length}
        </span>
      </h3>
      <ul className="flex flex-col gap-1">
        {shown.map((item) => {
          const id = item.evidence_id ?? item.document_id ?? item.finding_id ?? "";
          const isSelected = id === selected;
          const field = item.locator && "field_path" in item.locator
            ? String(item.locator.field_path)
            : null;
          return (
            <li key={id}>
              <button
                type="button"
                data-testid={`evidence-${id}`}
                onClick={() => onSelect(isSelected ? null : id)}
                className={`w-full rounded-lg border px-2.5 py-1.5 text-left text-xs transition ${
                  isSelected
                    ? "border-accent bg-accent-soft"
                    : "border-line hover:border-accent-line hover:bg-raised"
                }`}
              >
                {/* What it is, before what it is called. A row that leads with
                    an opaque id makes a reader decode before they can choose. */}
                <span className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium">{field ?? id.slice(0, 20)}</span>
                  {item.code ? (
                    <Chip tone={item.severity === "HIGH" ? "fail" : "warn"}>{item.code}</Chip>
                  ) : null}
                </span>
                <span className="mt-0.5 block truncate font-mono text-[10px] text-faint">
                  {id}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {!all && !holdsSelected && entries.length > FIRST ? (
        <button
          type="button"
          onClick={() => setAll(true)}
          className="self-start text-xs text-accent underline decoration-dotted"
        >
          show the other {entries.length - FIRST}
        </button>
      ) : null}
    </div>
  );
}

function EvidenceDetail({ item }: { item: Explanation["levels"]["provenance"][number] }) {
  const bbox = item.locator?.bbox as number[] | undefined;
  const documentId = (item.locator?.document_id as string | undefined) ?? item.document_id;
  const page = (item.locator?.page as number | undefined) ?? 1;

  return (
    <div
      data-testid="evidence-detail"
      className="rise rounded-xl border border-accent-line bg-accent-soft p-4"
    >
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Chip tone="accent">{item.type ?? "evidence"}</Chip>
        <span className="text-muted">from</span>
        <span className="font-mono">{item.source_system ?? "unknown"}</span>
        {item.source_record_id ? (
          <Copyable value={item.source_record_id} label="source record" />
        ) : null}
      </div>

      {bbox && documentId ? (
        <PageWithBox documentId={documentId} page={page} bbox={bbox} />
      ) : null}

      {item.captured_at ? (
        <p className="mt-2 text-xs text-muted">
          Captured {item.captured_at}
        </p>
      ) : null}
    </div>
  );
}

/** The page image with the box the value was read from drawn on it.
 *
 *  The box is drawn over the page rather than described, because the question
 *  a reader has is "is that what the paper says", and only the paper answers
 *  it. Coordinates are normalised to the page (docs/03 §2), so the overlay is
 *  a percentage of the image and stays correct at any rendered size. */
function PageWithBox({
  documentId,
  page,
  bbox,
}: {
  documentId: string;
  page: number;
  bbox: number[];
}) {
  const fetchBlob = useBlobApi();
  const [url, setUrl] = useState<string | null>(null);

  const image = useQuery({
    queryKey: ["page-image", documentId, page],
    queryFn: () => fetchBlob(`/api/document/documents/${documentId}/page/${page}.png`),
    staleTime: Infinity,
    gcTime: 0,
  });

  // The blob URL is owned by this component: held while it is on screen and
  // released when the reader moves on, so a long session does not accumulate
  // page images the browser can never collect.
  useEffect(() => {
    if (!image.data) return;
    setUrl(image.data);
    return () => {
      URL.revokeObjectURL(image.data);
      setUrl(null);
    };
  }, [image.data]);

  if (image.error) {
    return (
      <div className="mt-2">
        <Problem error={image.error} />
        <p className="mt-1 text-xs text-muted">
          The value and its coordinates are recorded; only the rendered page is
          missing, so it cannot be checked against the paper here.
        </p>
      </div>
    );
  }

  return (
    <figure className="mt-2" data-testid="evidence-image">
      <div className="relative inline-block border border-line bg-white">
        {url ? (
          <img
            src={url}
            alt={`Page ${page} of ${documentId}`}
            className="block max-h-[420px] w-auto"
          />
        ) : (
          <div className="flex h-40 w-64 items-center justify-center text-xs text-muted">
            Loading the page.
          </div>
        )}
        <span
          data-testid="evidence-bbox"
          data-bbox={bbox.join(",")}
          className="pointer-events-none absolute border-2 border-fail"
          style={{
            left: `${bbox[0] * 100}%`,
            top: `${bbox[1] * 100}%`,
            width: `${(bbox[2] - bbox[0]) * 100}%`,
            height: `${(bbox[3] - bbox[1]) * 100}%`,
          }}
        />
      </div>
      <figcaption className="mt-1 text-xs text-muted">
        {documentId}, page {page}. The box is where the value was read from.
      </figcaption>
    </figure>
  );
}
