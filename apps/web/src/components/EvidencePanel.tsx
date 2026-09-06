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
      title="Evidence"
      testId="evidence-panel"
      right={
        selected ? (
          <button
            type="button"
            className="text-xs underline decoration-dotted"
            onClick={() => onSelect(null)}
          >
            clear
          </button>
        ) : null
      }
    >
      {items.length === 0 ? (
        <Empty>
          No evidence is recorded against this decision. That is itself worth
          knowing: a claim without evidence should not have been accepted.
        </Empty>
      ) : (
        <div className="flex flex-col gap-4">
          {current ? <EvidenceDetail item={current} /> : null}

          {grouped.map(([type, entries]) => (
            <div key={type} className="flex flex-col gap-1">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-[--color-muted]">
                {type.replaceAll("_", " ").toLowerCase()}
              </h3>
              <ul className="flex flex-col gap-1">
                {entries.map((item) => {
                  const id = item.evidence_id ?? item.document_id ?? item.finding_id ?? "";
                  const isSelected = id === selected;
                  return (
                    <li key={id}>
                      <button
                        type="button"
                        data-testid={`evidence-${id}`}
                        onClick={() => onSelect(isSelected ? null : id)}
                        className={`w-full rounded border px-2 py-1 text-left text-xs ${
                          isSelected
                            ? "border-[--color-accent] bg-sky-50"
                            : "border-[--color-line] hover:border-[--color-accent]"
                        }`}
                      >
                        <span className="font-mono">{id.slice(0, 22)}</span>
                        {item.code ? (
                          <span className="ml-2">
                            <Chip tone={item.severity === "HIGH" ? "fail" : "warn"}>
                              {item.code}
                            </Chip>
                          </span>
                        ) : null}
                        {item.locator && "field_path" in item.locator ? (
                          <span className="ml-2 text-[--color-muted]">
                            {String(item.locator.field_path)}
                          </span>
                        ) : null}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function EvidenceDetail({ item }: { item: Explanation["levels"]["provenance"][number] }) {
  const bbox = item.locator?.bbox as number[] | undefined;
  const documentId = (item.locator?.document_id as string | undefined) ?? item.document_id;
  const page = (item.locator?.page as number | undefined) ?? 1;

  return (
    <div
      data-testid="evidence-detail"
      className="rounded border border-[--color-accent] bg-sky-50 p-3"
    >
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Chip tone="accent">{item.type ?? "evidence"}</Chip>
        <span className="text-[--color-muted]">from</span>
        <span className="font-mono">{item.source_system ?? "unknown"}</span>
        {item.source_record_id ? (
          <Copyable value={item.source_record_id} label="source record" />
        ) : null}
      </div>

      {bbox && documentId ? (
        <PageWithBox documentId={documentId} page={page} bbox={bbox} />
      ) : null}

      {item.captured_at ? (
        <p className="mt-2 text-xs text-[--color-muted]">
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
        <p className="mt-1 text-xs text-[--color-muted]">
          The value and its coordinates are recorded; only the rendered page is
          missing, so it cannot be checked against the paper here.
        </p>
      </div>
    );
  }

  return (
    <figure className="mt-2" data-testid="evidence-image">
      <div className="relative inline-block border border-[--color-line] bg-white">
        {url ? (
          <img
            src={url}
            alt={`Page ${page} of ${documentId}`}
            className="block max-h-[420px] w-auto"
          />
        ) : (
          <div className="flex h-40 w-64 items-center justify-center text-xs text-[--color-muted]">
            Loading the page.
          </div>
        )}
        <span
          data-testid="evidence-bbox"
          data-bbox={bbox.join(",")}
          className="pointer-events-none absolute border-2 border-[--color-fail]"
          style={{
            left: `${bbox[0] * 100}%`,
            top: `${bbox[1] * 100}%`,
            width: `${(bbox[2] - bbox[0]) * 100}%`,
            height: `${(bbox[3] - bbox[1]) * 100}%`,
          }}
        />
      </div>
      <figcaption className="mt-1 text-xs text-[--color-muted]">
        {documentId}, page {page}. The box is where the value was read from.
      </figcaption>
    </figure>
  );
}
