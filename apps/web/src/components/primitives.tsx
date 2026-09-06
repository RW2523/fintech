import React from "react";

/** The few shapes the workbench repeats. Kept together so a change to how a
 *  route is shown happens once, and so the vocabulary stays small. */

const TONE = {
  neutral: "bg-[--color-canvas] text-[--color-muted] border-[--color-line]",
  pass: "bg-emerald-50 text-[--color-pass] border-emerald-200",
  warn: "bg-amber-50 text-[--color-warn] border-amber-200",
  fail: "bg-red-50 text-[--color-fail] border-red-200",
  accent: "bg-sky-50 text-[--color-accent] border-sky-200",
} as const;

export type Tone = keyof typeof TONE;

export function Chip({
  children,
  tone = "neutral",
  title,
  testId,
}: {
  children: React.ReactNode;
  tone?: Tone;
  title?: string;
  testId?: string;
}) {
  return (
    <span
      data-testid={testId}
      title={title}
      className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs font-medium ${TONE[tone]}`}
    >
      {children}
    </span>
  );
}

export function Card({
  title,
  right,
  children,
  testId,
}: {
  title: string;
  right?: React.ReactNode;
  children: React.ReactNode;
  testId?: string;
}) {
  return (
    <section
      data-testid={testId}
      className="rounded-lg border border-[--color-line] bg-[--color-surface]"
    >
      <header className="flex items-center justify-between border-b border-[--color-line] px-4 py-2">
        <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
        {right}
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}

/** A labelled number. Figures live in their own element so a test can read one
 *  without parsing a sentence, which is also how an officer scans them. */
export function Figure({
  label,
  value,
  hint,
  testId,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  testId?: string;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-[--color-muted]">{label}</span>
      <span data-testid={testId} className="text-sm font-semibold tabular-nums">
        {value}
      </span>
      {hint ? <span className="text-xs text-[--color-muted]">{hint}</span> : null}
    </div>
  );
}

/** A proportion, drawn. The number is always shown beside it: a bar alone
 *  invites reading a length as a fact. */
export function Meter({
  label,
  value,
  max = 1,
  tone = "accent",
  testId,
}: {
  label: string;
  value: number | null | undefined;
  max?: number;
  tone?: Tone;
  testId?: string;
}) {
  const share = value == null ? 0 : Math.max(0, Math.min(1, value / max));
  const fill = {
    pass: "bg-[--color-pass]",
    warn: "bg-[--color-warn]",
    fail: "bg-[--color-fail]",
    accent: "bg-[--color-accent]",
    neutral: "bg-[--color-muted]",
  }[tone];
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between">
        <span className="text-xs text-[--color-muted]">{label}</span>
        <span data-testid={testId} className="text-xs font-semibold tabular-nums">
          {value == null ? "not available" : value.toFixed(2)}
        </span>
      </div>
      <div className="h-1.5 w-full rounded bg-[--color-line]">
        <div className={`h-1.5 rounded ${fill}`} style={{ width: `${share * 100}%` }} />
      </div>
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-[--color-muted]">{children}</p>;
}

export function Problem({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div
      role="alert"
      data-testid="problem"
      className="rounded border border-red-200 bg-red-50 p-3 text-sm text-[--color-fail]"
    >
      {message}
    </div>
  );
}

/** Copy a value and say so. Snapshot and trace ids are meant to be pasted
 *  into a ticket or a query, so they are one click away. */
export function Copyable({ value, label }: { value: string; label?: string }) {
  const [copied, setCopied] = React.useState(false);
  return (
    <button
      type="button"
      className="font-mono text-xs text-[--color-muted] underline decoration-dotted"
      onClick={() => {
        void navigator.clipboard?.writeText(value);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1200);
      }}
      title={`Copy ${label ?? value}`}
    >
      {copied ? "copied" : value}
    </button>
  );
}
