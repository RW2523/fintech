import React from "react";

import { Icon, type IconName } from "./icons";

/** The shapes the workbench repeats. Kept together so a change to how a route
 *  is shown happens once, and so the vocabulary stays small.
 *
 *  Nothing here computes. Every component takes a value that a service already
 *  produced and draws it; the sparkline and the donut plot points they are
 *  handed and derive no total, share or trend of their own. A chart that did
 *  arithmetic would be a second place for a number to come from, and this
 *  platform has exactly one (CLAUDE.md §2.1). */

const TONE = {
  neutral: "bg-raised text-muted border-line",
  pass: "bg-pass-soft text-pass-deep border-pass-line",
  warn: "bg-warn-soft text-warn-deep border-warn-line",
  fail: "bg-fail-soft text-fail-deep border-fail-line",
  accent: "bg-accent-soft text-accent-deep border-accent-line",
  note: "bg-note-soft text-note-deep border-note-line",
} as const;

export type Tone = keyof typeof TONE;

export const TONE_FILL: Record<Tone, string> = {
  neutral: "bg-faint",
  pass: "bg-pass",
  warn: "bg-warn",
  fail: "bg-fail",
  accent: "bg-accent",
  note: "bg-note",
};

export const TONE_STROKE: Record<Tone, string> = {
  neutral: "var(--color-faint)",
  pass: "var(--color-pass)",
  warn: "var(--color-warn)",
  fail: "var(--color-fail)",
  accent: "var(--color-accent)",
  note: "var(--color-note)",
};

/** The pastel square an icon sits in. The one piece of colour that repeats
 *  everywhere — on a card title, a stat, a finding — so a reader learns the
 *  five meanings once and then reads them by hue. */
export function Glyph({
  icon,
  tone = "accent",
  size = "md",
}: {
  icon: IconName;
  tone?: Tone;
  size?: "sm" | "md" | "lg";
}) {
  const Drawn = Icon[icon];
  const box = { sm: "p-1.5", md: "p-2", lg: "p-2.5" }[size];
  const glyph = { sm: "h-3.5 w-3.5", md: "h-4 w-4", lg: "h-5 w-5" }[size];
  return (
    <span className={`inline-flex shrink-0 rounded-xl border ${TONE[tone]} ${box}`}>
      <Drawn className={glyph} />
    </span>
  );
}

export function Chip({
  children,
  tone = "neutral",
  title,
  testId,
  dot = false,
}: {
  children: React.ReactNode;
  tone?: Tone;
  title?: string;
  testId?: string;
  /** A filled dot in the tone, for a chip whose word alone is not a status. */
  dot?: boolean;
}) {
  return (
    <span
      data-testid={testId}
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium whitespace-nowrap ${TONE[tone]}`}
    >
      {dot ? <span className={`h-1.5 w-1.5 rounded-full ${TONE_FILL[tone]}`} /> : null}
      {children}
    </span>
  );
}

export function Card({
  title,
  icon,
  right,
  children,
  testId,
  tone = "neutral",
  padded = true,
}: {
  title?: string;
  icon?: IconName;
  right?: React.ReactNode;
  children: React.ReactNode;
  testId?: string;
  /** Colours the title's icon. The card itself stays white: a whole panel in
   *  a colour says the section is the problem, when it is one row inside it. */
  tone?: Tone;
  padded?: boolean;
}) {
  return (
    <section
      data-testid={testId}
      className="rounded-2xl border border-line bg-surface shadow-card transition-shadow hover:shadow-lift"
    >
      {title ? (
        <header className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
          <h2 className="flex items-center gap-2.5 text-[15px] font-semibold tracking-tight">
            {icon ? <Glyph icon={icon} tone={tone} size="sm" /> : null}
            {title}
          </h2>
          {right}
        </header>
      ) : null}
      <div className={padded ? "p-5" : ""}>{children}</div>
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
      <span className="text-xs text-muted">{label}</span>
      <span data-testid={testId} className="text-sm font-semibold tabular-nums">
        {value}
      </span>
      {hint ? <span className="text-xs text-muted">{hint}</span> : null}
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
  format,
}: {
  label: string;
  value: number | null | undefined;
  max?: number;
  tone?: Tone;
  testId?: string;
  format?: (value: number) => string;
}) {
  const share = value == null ? 0 : Math.max(0, Math.min(1, value / max));
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-xs text-muted">{label}</span>
        <span data-testid={testId} className="text-xs font-semibold tabular-nums">
          {value == null ? "not available" : (format ?? ((v: number) => v.toFixed(2)))(value)}
        </span>
      </div>
      <div className="h-1.5 w-full rounded-full bg-line">
        <div
          className={`h-1.5 rounded-full ${TONE_FILL[tone]}`}
          style={{ width: `${share * 100}%` }}
        />
      </div>
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-muted">{children}</p>;
}

export function Problem({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div
      role="alert"
      data-testid="problem"
      className="flex items-start gap-2 rounded-lg border border-fail-line bg-fail-soft p-3 text-sm text-fail"
    >
      <Icon.alert className="mt-0.5 h-4 w-4 shrink-0" />
      <span>{message}</span>
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
      className="font-mono text-xs text-muted underline decoration-dotted hover:text-accent"
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

// ---------------------------------------------------------------------------
// the new vocabulary
// ---------------------------------------------------------------------------

/** A line through a handful of points.
 *
 *  Draws what it is given and nothing else: no smoothing, no interpolation, no
 *  axis it invents. Fewer than two points draws nothing rather than a flat
 *  line, because a flat line reads as "steady" and one reading is not a trend.
 */
export function Sparkline({
  points,
  tone = "accent",
  width = 72,
  height = 24,
  testId,
}: {
  points: number[];
  tone?: Tone;
  width?: number;
  height?: number;
  testId?: string;
}) {
  if (points.length < 2) {
    return <span className="text-xs text-faint">—</span>;
  }
  const low = Math.min(...points);
  const high = Math.max(...points);
  const span = high - low || 1;
  const step = width / (points.length - 1);
  const path = points
    .map((value, index) => {
      const x = index * step;
      const y = height - ((value - low) / span) * (height - 4) - 2;
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg
      data-testid={testId}
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      fill="none"
      aria-hidden="true"
      className="overflow-visible"
    >
      <path d={path} stroke={TONE_STROKE[tone]} strokeWidth={1.5} strokeLinejoin="round" />
    </svg>
  );
}

/** A headline number with what it means underneath.
 *
 *  `delta` is a change some service computed, never one this component works
 *  out from `series`. The arrow's direction comes from the sign; whether that
 *  direction is good comes from `deltaTone`, because a rising approval rate
 *  and a rising delinquency rate are the same arrow and opposite news.
 */
export function Stat({
  label,
  value,
  icon,
  tone = "accent",
  hint,
  delta,
  deltaTone,
  series,
  testId,
}: {
  label: string;
  value: React.ReactNode;
  icon?: IconName;
  tone?: Tone;
  hint?: string;
  delta?: string | null;
  deltaTone?: Tone;
  series?: number[];
  testId?: string;
}) {
  const rising = delta?.trim().startsWith("+");
  const Arrow = delta ? (rising ? Icon.up : Icon.down) : null;
  return (
    <section className="group rounded-2xl border border-line bg-surface p-5 shadow-card transition-all hover:-translate-y-0.5 hover:shadow-lift">
      <div className="flex items-start gap-3.5">
        {icon ? <Glyph icon={icon} tone={tone} size="lg" /> : null}
        <div className="min-w-0 flex-1">
          <p className="text-xs text-muted">{label}</p>
          <div className="mt-1 flex items-end justify-between gap-2">
            <div className="flex items-baseline gap-2">
              <span
                data-testid={testId}
                className="text-3xl font-semibold tracking-tight tabular-nums"
              >
                {value}
              </span>
              {delta ? (
                <span
                  className={`inline-flex items-center gap-0.5 text-xs font-medium ${
                    TONE[deltaTone ?? "neutral"].split(" ")[1]
                  }`}
                >
                  {Arrow ? <Arrow className="h-3 w-3" /> : null}
                  {delta}
                </span>
              ) : null}
            </div>
            {series ? <Sparkline points={series} tone={deltaTone ?? tone} /> : null}
          </div>
          {hint ? <p className="mt-1 text-xs text-faint">{hint}</p> : null}
        </div>
      </div>
    </section>
  );
}

/** The strip of facts at the top of a case, a member or a document pack.
 *
 *  The point of it is that a reader never has to ask what they are looking at.
 *  It is the same shape on every detail screen so the answer is always in the
 *  same place. */
export function FactStrip({
  initials,
  title,
  subtitle,
  facts,
  testId,
}: {
  initials?: string;
  title: string;
  subtitle?: React.ReactNode;
  facts: { label: string; value: React.ReactNode; icon?: IconName }[];
  testId?: string;
}) {
  return (
    <section
      data-testid={testId}
      className="flex flex-wrap items-center gap-x-9 gap-y-4 rounded-2xl border border-line bg-surface px-5 py-4 shadow-card"
    >
      <div className="flex items-center gap-3">
        {initials ? (
          <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-accent to-accent-deep text-sm font-semibold text-white shadow-card">
            {initials}
          </span>
        ) : null}
        <div>
          <p className="text-sm font-semibold tracking-tight">{title}</p>
          {subtitle ? <p className="text-xs text-muted">{subtitle}</p> : null}
        </div>
      </div>
      {facts.map((fact) => {
        return (
          <div key={fact.label} className="flex items-center gap-2.5">
            {fact.icon ? <Glyph icon={fact.icon} tone="accent" size="sm" /> : null}
            <div>
              <p className="text-xs text-muted">{fact.label}</p>
              <div className="text-sm font-semibold tracking-tight">{fact.value}</div>
            </div>
          </div>
        );
      })}
    </section>
  );
}

/** A row of counted filters. The count is the caller's; this draws it. */
export function FilterChips<T extends string>({
  options,
  value,
  onChange,
  testIdPrefix,
}: {
  options: { key: T; label: string; count?: number; tone?: Tone }[];
  value: T;
  onChange: (key: T) => void;
  testIdPrefix?: string;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {options.map((option) => {
        const active = option.key === value;
        return (
          <button
            key={option.key}
            type="button"
            data-testid={testIdPrefix ? `${testIdPrefix}-${option.key}` : undefined}
            aria-pressed={active}
            onClick={() => onChange(option.key)}
            className={`inline-flex items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-xs font-medium transition-all ${
              active
                ? "border-accent-deep bg-gradient-to-b from-accent to-accent-deep text-white shadow-card"
                : "border-line bg-surface text-muted hover:border-accent-line hover:bg-accent-soft hover:text-accent-deep"
            }`}
          >
            {option.label}
            {option.count == null ? null : (
              <span
                className={`rounded-full px-1.5 text-[11px] tabular-nums ${
                  active ? "bg-white/20" : (option.tone ? TONE[option.tone] : "bg-canvas")
                }`}
              >
                {option.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

const BUTTON = {
  primary:
    "bg-gradient-to-b from-accent to-accent-deep text-white border-accent-deep shadow-card hover:brightness-110 hover:shadow-lift disabled:from-faint disabled:to-faint disabled:border-faint disabled:shadow-none",
  quiet:
    "bg-surface text-ink border-line hover:border-accent-line hover:bg-accent-soft disabled:text-faint",
  danger:
    "bg-surface text-fail border-fail-line hover:bg-fail-soft disabled:text-faint disabled:border-line",
  soft:
    "bg-accent-soft text-accent-deep border-accent-line hover:bg-white disabled:text-faint",
} as const;

export function Button({
  children,
  onClick,
  variant = "quiet",
  icon,
  disabled,
  title,
  testId,
  type = "button",
}: {
  children: React.ReactNode;
  onClick?: () => void;
  variant?: keyof typeof BUTTON;
  icon?: IconName;
  disabled?: boolean;
  title?: string;
  testId?: string;
  type?: "button" | "submit";
}) {
  const Glyph = icon ? Icon[icon] : null;
  return (
    <button
      type={type}
      data-testid={testId}
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={`inline-flex items-center justify-center gap-2 rounded-xl border px-3.5 py-2 text-sm font-medium transition-all disabled:cursor-not-allowed ${BUTTON[variant]}`}
    >
      {Glyph ? <Glyph className="h-4 w-4" /> : null}
      {children}
    </button>
  );
}

/** A ring of proportions.
 *
 *  Each slice's share is given, not computed: these come from a metrics
 *  endpoint that already worked out the denominator, and a component that
 *  re-divided would be able to disagree with the service it is drawing. */
export function Donut({
  slices,
  total,
  caption,
  testId,
}: {
  slices: { label: string; share: number; count: number; tone: Tone }[];
  total: number;
  caption: string;
  testId?: string;
}) {
  const size = 132;
  const stroke = 18;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;

  return (
    <div data-testid={testId} className="flex flex-wrap items-center gap-6">
      <div className="relative" style={{ width: size, height: size }}>
        <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} aria-hidden="true">
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke="var(--color-line)"
            strokeWidth={stroke}
          />
          {slices.map((slice) => {
            const length = Math.max(0, Math.min(1, slice.share)) * circumference;
            const dash = `${length} ${circumference - length}`;
            const element = (
              <circle
                key={slice.label}
                cx={size / 2}
                cy={size / 2}
                r={radius}
                fill="none"
                stroke={TONE_STROKE[slice.tone]}
                strokeWidth={stroke}
                strokeDasharray={dash}
                strokeDashoffset={-offset}
                transform={`rotate(-90 ${size / 2} ${size / 2})`}
              />
            );
            offset += length;
            return element;
          })}
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-xl font-semibold tabular-nums">{total}</span>
          <span className="text-xs text-muted">{caption}</span>
        </div>
      </div>
      <ul className="flex min-w-[10rem] flex-1 flex-col gap-2">
        {slices.map((slice) => (
          <li key={slice.label} className="flex items-center gap-2 text-sm">
            <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${TONE_FILL[slice.tone]}`} />
            <span className="flex-1 text-muted">{slice.label}</span>
            <span className="font-semibold tabular-nums">{slice.count}</span>
            <span className="w-12 text-right text-xs text-faint tabular-nums">
              {(slice.share * 100).toFixed(0)}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Grouped bars over a handful of periods, with the value written above each.
 *
 *  Bars are read by length, which makes a chart the easiest place in an
 *  interface to mislead. The number is always on the bar. */
export function BarSeries({
  rows,
  bars,
  testId,
}: {
  rows: { label: string; values: number[] }[];
  bars: { label: string; tone: Tone }[];
  testId?: string;
}) {
  const high = Math.max(1, ...rows.flatMap((row) => row.values));
  return (
    <div data-testid={testId} className="flex flex-col gap-3">
      <div className="flex items-end gap-3 overflow-x-auto pb-1">
        {rows.map((row) => (
          <div key={row.label} className="flex min-w-[3.5rem] flex-1 flex-col items-center gap-1.5">
            <div className="flex h-32 w-full items-end justify-center gap-1">
              {row.values.map((value, index) => (
                <div
                  key={bars[index]?.label ?? index}
                  className="flex w-full max-w-[1.4rem] flex-col items-center justify-end"
                  title={`${bars[index]?.label ?? ""}: ${value}`}
                >
                  <span className="text-[10px] text-faint tabular-nums">
                    {value || ""}
                  </span>
                  <div
                    className={`w-full rounded-t ${TONE_FILL[bars[index]?.tone ?? "accent"]}`}
                    style={{ height: `${(value / high) * 100}%`, minHeight: value ? 2 : 0 }}
                  />
                </div>
              ))}
            </div>
            <span className="text-xs text-muted">{row.label}</span>
          </div>
        ))}
      </div>
      <ul className="flex flex-wrap items-center gap-4">
        {bars.map((bar) => (
          <li key={bar.label} className="flex items-center gap-1.5 text-xs text-muted">
            <span className={`h-2.5 w-2.5 rounded-sm ${TONE_FILL[bar.tone]}`} />
            {bar.label}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A table that stays readable at a glance: sticky head, quiet rules, and a
 *  selected row that is obvious without being loud. */
export function Table({
  head,
  children,
  testId,
  bodyTestId,
}: {
  head: React.ReactNode[];
  children: React.ReactNode;
  testId?: string;
  /** Named separately because a test that means "the rows" should not have to
   *  reach through the table element to find them. */
  bodyTestId?: string;
}) {
  return (
    <div className="overflow-x-auto">
      <table data-testid={testId} className="w-full min-w-[40rem] border-collapse text-sm">
        <thead>
          <tr className="border-b border-line text-left">
            {head.map((cell, index) => (
              <th
                key={index}
                className="px-3 py-2 text-xs font-medium whitespace-nowrap text-muted"
              >
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody data-testid={bodyTestId}>{children}</tbody>
      </table>
    </div>
  );
}

export function Row({
  children,
  selected = false,
  onClick,
  testId,
}: {
  children: React.ReactNode;
  selected?: boolean;
  onClick?: () => void;
  testId?: string;
}) {
  return (
    <tr
      data-testid={testId}
      onClick={onClick}
      aria-selected={selected}
      className={`border-b border-line last:border-0 ${
        onClick ? "cursor-pointer" : ""
      } ${selected ? "bg-accent-soft" : "hover:bg-raised"}`}
    >
      {children}
    </tr>
  );
}

export function Cell({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <td className={`px-3 py-2.5 align-middle ${className}`}>{children}</td>;
}

/** The circle of initials that stands in for a member everywhere. */
export function Avatar({ name, id }: { name: string; id?: string }) {
  // A person's initials where there is a name, and the last two characters of
  // an identifier where there is not — "M-004732" as "MM" would make every
  // member in the queue look like the same one.
  const words = name.split(/[\s]+/).filter(Boolean);
  const initials = /^[A-Za-z]/.test(name) && words.length > 1
    ? words.slice(0, 2).map((part) => part[0]!.toUpperCase()).join("")
    : name.replace(/[^A-Za-z0-9]/g, "").slice(-2).toUpperCase();
  return (
    <span className="flex items-center gap-2.5">
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-accent to-accent-deep text-xs font-semibold text-white">
        {initials || "?"}
      </span>
      <span className="min-w-0">
        <span className="block truncate text-sm font-medium">{name}</span>
        {id ? <span className="block truncate text-xs text-muted">{id}</span> : null}
      </span>
    </span>
  );
}

/** A row in a list of findings: an icon, what was found, and how it lands. */
export function Finding({
  icon,
  tone,
  title,
  detail,
  right,
  onClick,
  testId,
}: {
  icon?: IconName;
  tone: Tone;
  title: React.ReactNode;
  detail?: React.ReactNode;
  right?: React.ReactNode;
  onClick?: () => void;
  testId?: string;
}) {
  const body = (
    <>
      {icon ? <Glyph icon={icon} tone={tone} size="sm" /> : null}
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium">{title}</span>
        {detail ? <span className="block text-xs text-muted">{detail}</span> : null}
      </span>
      {right}
    </>
  );
  return onClick ? (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      className="flex w-full items-start gap-3 rounded-xl border border-line bg-surface p-3.5 text-left transition-all hover:border-accent-line hover:bg-accent-soft hover:shadow-card"
    >
      {body}
    </button>
  ) : (
    <div
      data-testid={testId}
      className="flex items-start gap-3 rounded-xl border border-line bg-surface p-3.5"
    >
      {body}
    </div>
  );
}

/** A left-to-right progression of states, with the one in force marked.
 *  Used for a member's early-warning path and a case's route. */
export function Path({
  steps,
  testId,
}: {
  steps: { label: string; caption?: string; tone: Tone; current?: boolean }[];
  testId?: string;
}) {
  return (
    <ol data-testid={testId} className="flex flex-wrap items-center gap-2">
      {steps.map((step, index) => (
        <li key={step.label} className="flex items-center gap-2">
          <span
            className={`flex flex-col items-center rounded-lg border px-3 py-1.5 ${TONE[step.tone]} ${
              step.current ? "ring-2 ring-offset-1 ring-accent-line" : ""
            }`}
          >
            <span className="text-xs font-semibold tracking-wide">{step.label}</span>
            {step.caption ? <span className="text-[11px] opacity-80">{step.caption}</span> : null}
          </span>
          {index < steps.length - 1 ? (
            <Icon.arrowRight className="h-4 w-4 text-faint" />
          ) : null}
        </li>
      ))}
    </ol>
  );
}

/** Tabs that carry a count, so a reader knows what is behind one before
 *  opening it. */
export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
  testIdPrefix,
}: {
  tabs: { key: T; label: string; count?: number }[];
  value: T;
  onChange: (key: T) => void;
  testIdPrefix?: string;
}) {
  return (
    <div className="flex gap-1 overflow-x-auto border-b border-line">
      {tabs.map((tab) => {
        const active = tab.key === value;
        return (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={active}
            data-testid={testIdPrefix ? `${testIdPrefix}-${tab.key}` : undefined}
            onClick={() => onChange(tab.key)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm whitespace-nowrap transition ${
              active
                ? "border-accent font-semibold text-accent"
                : "border-transparent text-muted hover:text-ink"
            }`}
          >
            {tab.label}
            {tab.count == null ? null : (
              <span className="ml-1.5 rounded-full bg-canvas px-1.5 text-xs tabular-nums">
                {tab.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
