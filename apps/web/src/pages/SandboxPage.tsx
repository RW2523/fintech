import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { useApi } from "../api";
import { Card, Chip, Empty, Problem } from "../components/primitives";

/** docs/09 §7.3 — the policy sandbox.
 *
 *  The Board writes the policy and tests it first. Every number on this page
 *  comes from a replay over stored cases: the page proposes a change, the
 *  service replays it, and nothing here computes an outcome of its own.
 *
 *  Adoption is deliberately the last thing and deliberately awkward. Two named
 *  approvers, and the sandbox run that produced the report is what the new
 *  version is written from, so a version nobody replayed cannot be adopted at
 *  all. */

const FACTORS = ["CAPACITY", "CONDUCT", "COMMITMENT", "CONDITIONS", "INTEGRITY"] as const;
type Factor = (typeof FACTORS)[number];

type Pack = {
  policy_version: string;
  dff: {
    weights: Record<Factor, number>;
    thresholds: { approve: number; decline: number };
  };
};

type Outcome = {
  cases: number;
  approval_rate: number | null;
  decline_rate: number | null;
  review_rate: number | null;
  more_information_rate: number | null;
  compliance_rate: number | null;
  autonomous_share: number | null;
  approved_exposure: string;
  projected_delinquency_12m: number | null;
};

type Diff = {
  snapshot_id: string;
  case_id?: string;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  decisive_change?: string | null;
};

type Report = {
  sandbox_id: string;
  product_code: string;
  policy_version: string;
  cases_replayed: number;
  baseline: Outcome;
  candidate: Outcome;
  segments: Record<string, Record<string, Record<string, Outcome>>>;
  diffs: Diff[];
};

type Adoption = {
  version: string;
  policy_version: string;
  previous_version: string;
  approved_by: string[];
};

/** Exactly as the API gave it. The sandbox is where a Board reads numbers it
 *  may act on, so nothing here rounds or reformats. */
function show(value: unknown): string {
  if (value === null || value === undefined) return "—";
  return String(value);
}

function delta(candidate: number | null, baseline: number | null): string {
  if (candidate === null || baseline === null) return "—";
  const difference = candidate - baseline;
  // Rounded only to kill floating-point noise: 0.7999999999999999 is not a
  // different approval rate from 0.8 and printing it as one is worse than
  // rounding it.
  const cleaned = Math.round(difference * 1e6) / 1e6;
  return cleaned > 0 ? `+${cleaned}` : String(cleaned);
}

const ROWS: { key: keyof Outcome; label: string }[] = [
  { key: "cases", label: "cases" },
  { key: "approval_rate", label: "approval rate" },
  { key: "decline_rate", label: "decline rate" },
  { key: "review_rate", label: "review rate" },
  { key: "more_information_rate", label: "more information" },
  { key: "compliance_rate", label: "compliance review" },
  { key: "autonomous_share", label: "autonomous share" },
  { key: "approved_exposure", label: "approved exposure" },
  { key: "projected_delinquency_12m", label: "projected delinquency 12m" },
];

export function SandboxPage() {
  const request = useApi();
  const [product, setProduct] = useState("PF-STD");
  const [weights, setWeights] = useState<Record<Factor, number> | null>(null);
  const [approve, setApprove] = useState<number | null>(null);
  const [decline, setDecline] = useState<number | null>(null);
  const [months, setMonths] = useState(12);
  const [approvers, setApprovers] = useState([
    { role: "HEAD_OF_CREDIT", actor_id: "" },
    { role: "HEAD_OF_RISK", actor_id: "" },
  ]);

  const products = useQuery({
    queryKey: ["policy-products"],
    queryFn: () => request<{ products: string[] }>("/api/policy/policy/products"),
  });

  const pack = useQuery({
    queryKey: ["policy-pack", product],
    queryFn: () => request<Pack>(`/api/policy/policy/${product}/latest`),
  });

  // The form starts from the pack in force rather than from defaults, so what
  // a reader edits is what the platform is actually deciding by today.
  const current = pack.data?.dff;
  const editedWeights = weights ?? current?.weights ?? null;
  const editedApprove = approve ?? current?.thresholds.approve ?? null;
  const editedDecline = decline ?? current?.thresholds.decline ?? null;

  const sum = useMemo(
    () =>
      editedWeights
        ? Math.round(Object.values(editedWeights).reduce((total, w) => total + w, 0) * 1e6) / 1e6
        : 0,
    [editedWeights],
  );
  const balanced = Math.abs(sum - 1) < 1e-9;

  const candidate = useMemo(() => {
    if (!current || !editedWeights) return {};
    const dff: Record<string, unknown> = {};
    const changedWeights = FACTORS.some((f) => editedWeights[f] !== current.weights[f]);
    if (changedWeights) dff.weights = editedWeights;
    if (editedApprove !== current.thresholds.approve || editedDecline !== current.thresholds.decline) {
      dff.thresholds = { approve: editedApprove, decline: editedDecline };
    }
    return Object.keys(dff).length > 0 ? { dff } : {};
  }, [current, editedWeights, editedApprove, editedDecline]);

  const changed = Object.keys(candidate).length > 0;

  const replay = useMutation({
    mutationFn: () => {
      const from = new Date();
      from.setMonth(from.getMonth() - months);
      return request<Report>("/api/policy/policy/sandbox/replay", {
        method: "POST",
        body: {
          product_code: product,
          candidate,
          range: { date_from: from.toISOString() },
        },
      });
    },
  });

  const adopt = useMutation({
    mutationFn: (sandboxId: string) =>
      request<Adoption>(`/api/policy/policy/${product}/versions`, {
        method: "POST",
        body: {
          sandbox_id: sandboxId,
          approvers: approvers.filter((a) => a.actor_id.trim()),
          reason: "adopted from the sandbox",
        },
      }),
    onSuccess: () => {
      void pack.refetch();
      void products.refetch();
    },
  });

  const bothNamed = approvers.every((a) => a.actor_id.trim()) &&
    approvers[0].actor_id.trim() !== approvers[1].actor_id.trim();

  return (
    <div className="flex flex-col gap-5" data-testid="sandbox-page">
      <header>
        <h1 className="text-[26px] font-bold tracking-tight">Policy sandbox</h1>
        <p className="text-sm text-[--color-muted]">
          Change what the platform decides by, replay it over stored cases, and
          adopt it only when two people say so.
        </p>
      </header>
      <Card title="What we decide by" icon="sandbox" tone="accent" testId="sandbox-form">
        {pack.error ? <Problem error={pack.error} /> : null}
        {!current ? (
          <Empty>Reading the pack in force…</Empty>
        ) : (
          <div className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-[--color-muted]">Product</span>
              {(products.data?.products ?? [product]).map((code) => (
                <button
                  key={code}
                  type="button"
                  data-testid={`product-${code}`}
                  onClick={() => {
                    setProduct(code);
                    setWeights(null);
                    setApprove(null);
                    setDecline(null);
                    replay.reset();
                    adopt.reset();
                  }}
                  className={`rounded border px-2 py-0.5 text-xs ${
                    product === code
                      ? "border-[--color-accent]"
                      : "border-[--color-line] text-[--color-muted]"
                  }`}
                >
                  {code}
                </button>
              ))}
              <span className="text-xs text-[--color-muted]" data-testid="in-force">
                in force: {pack.data?.policy_version}
              </span>
            </div>

            <div className="flex flex-col gap-2">
              {FACTORS.map((factor) => (
                <label key={factor} className="flex items-center gap-3 text-sm">
                  <span className="w-28 text-xs text-[--color-muted]">{factor}</span>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.01}
                    data-testid={`weight-${factor}`}
                    value={editedWeights?.[factor] ?? 0}
                    onChange={(event) =>
                      setWeights({
                        ...(editedWeights as Record<Factor, number>),
                        [factor]: Number(event.target.value),
                      })
                    }
                    className="flex-1"
                  />
                  <span className="w-14 text-right font-mono text-xs" data-testid={`weight-value-${factor}`}>
                    {(editedWeights?.[factor] ?? 0).toFixed(2)}
                  </span>
                </label>
              ))}

              {/* The sum is shown because it must be exactly one, and a Board
                  that has moved two sliders needs to see which way it is out
                  rather than be told the run failed. */}
              <p
                className={`text-xs ${balanced ? "text-[--color-muted]" : "text-[--color-fail]"}`}
                data-testid="weight-sum"
              >
                weights sum to {sum}
                {balanced ? "" : " — they must sum to exactly 1.00 before this can be replayed"}
              </p>
            </div>

            <div className="flex flex-wrap gap-4">
              <label className="flex items-center gap-2 text-sm">
                <span className="text-xs text-[--color-muted]">approve at or above</span>
                <input
                  type="number"
                  data-testid="threshold-approve"
                  value={editedApprove ?? 0}
                  onChange={(event) => setApprove(Number(event.target.value))}
                  className="w-20 rounded border border-[--color-line] bg-[--color-surface] px-2 py-1 font-mono text-sm"
                />
              </label>
              <label className="flex items-center gap-2 text-sm">
                <span className="text-xs text-[--color-muted]">decline below</span>
                <input
                  type="number"
                  data-testid="threshold-decline"
                  value={editedDecline ?? 0}
                  onChange={(event) => setDecline(Number(event.target.value))}
                  className="w-20 rounded border border-[--color-line] bg-[--color-surface] px-2 py-1 font-mono text-sm"
                />
              </label>
              <label className="flex items-center gap-2 text-sm">
                <span className="text-xs text-[--color-muted]">replay the last</span>
                <input
                  type="number"
                  data-testid="replay-months"
                  value={months}
                  onChange={(event) => setMonths(Number(event.target.value))}
                  className="w-16 rounded border border-[--color-line] bg-[--color-surface] px-2 py-1 font-mono text-sm"
                />
                <span className="text-xs text-[--color-muted]">months</span>
              </label>
            </div>

            <div>
              <button
                type="button"
                data-testid="sandbox-run"
                disabled={!balanced || !changed || replay.isPending}
                onClick={() => replay.mutate()}
                className="rounded border border-[--color-accent] px-3 py-1 text-sm disabled:cursor-not-allowed disabled:opacity-50"
              >
                {replay.isPending ? "Replaying" : "Replay"}
              </button>
              {!changed ? (
                <span className="ml-2 text-xs text-[--color-muted]" data-testid="sandbox-unchanged">
                  nothing has changed yet
                </span>
              ) : null}
            </div>
          </div>
        )}
      </Card>

      {replay.error ? <Problem error={replay.error} /> : null}

      {replay.data ? (
        <>
          <Card title="Baseline against candidate" icon="chart" tone="accent" testId="sandbox-report">
            <p className="mb-2 text-xs text-[--color-muted]" data-testid="cases-replayed">
              {replay.data.cases_replayed} decided cases replayed under{" "}
              {replay.data.policy_version}. No model was called: stored opinions
              were reused and the code re-decided.
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs" data-testid="report-table">
                <thead className="text-[--color-muted]">
                  <tr>
                    <th className="py-1 pr-3 font-normal" />
                    <th className="py-1 pr-3 font-normal">baseline</th>
                    <th className="py-1 pr-3 font-normal">candidate</th>
                    <th className="py-1 pr-3 font-normal">change</th>
                  </tr>
                </thead>
                <tbody className="font-mono">
                  {ROWS.map(({ key, label }) => (
                    <tr key={key} className="border-t border-[--color-line]">
                      <td className="py-1 pr-3 font-sans text-[--color-muted]">{label}</td>
                      <td className="py-1 pr-3" data-testid={`baseline-${key}`}>
                        {show(replay.data.baseline[key])}
                      </td>
                      <td className="py-1 pr-3" data-testid={`candidate-${key}`}>
                        {show(replay.data.candidate[key])}
                      </td>
                      <td className="py-1 pr-3" data-testid={`delta-${key}`}>
                        {typeof replay.data.baseline[key] === "number" ||
                        replay.data.baseline[key] === null
                          ? delta(
                              replay.data.candidate[key] as number | null,
                              replay.data.baseline[key] as number | null,
                            )
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Card title="Cases that would decide differently" icon="alert" tone="warn" testId="sandbox-diffs">
            {replay.data.diffs.length === 0 ? (
              <Empty>
                No case changes outcome under this candidate. The rates above
                moved without any decision moving with them.
              </Empty>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-xs" data-testid="diff-table">
                  <thead className="text-[--color-muted]">
                    <tr>
                      <th className="py-1 pr-3 font-normal">case</th>
                      <th className="py-1 pr-3 font-normal">before</th>
                      <th className="py-1 pr-3 font-normal">after</th>
                      <th className="py-1 pr-3 font-normal">what changed</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono">
                    {replay.data.diffs.slice(0, 40).map((diff) => (
                      <tr key={diff.snapshot_id} className="border-t border-[--color-line]">
                        <td className="py-1 pr-3">{(diff.case_id ?? diff.snapshot_id).slice(0, 18)}</td>
                        <td className="py-1 pr-3">{show(diff.before.recommendation)}</td>
                        <td className="py-1 pr-3">{show(diff.after.recommendation)}</td>
                        <td className="py-1 pr-3 font-sans">{show(diff.decisive_change)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          <Card title="Adopt as a new version" icon="shield" tone="pass" testId="sandbox-adopt">
            <div className="flex flex-col gap-3">
              <p className="text-xs text-[--color-muted]">
                Two named approvers, both heads, and they must be two people.
                The new version is written from this replay, so a change nobody
                replayed cannot be adopted.
              </p>

              {approvers.map((approver, index) => (
                <label key={approver.role} className="flex items-center gap-2 text-sm">
                  <span className="w-36 text-xs text-[--color-muted]">{approver.role}</span>
                  <input
                    data-testid={`approver-${index}`}
                    value={approver.actor_id}
                    onChange={(event) =>
                      setApprovers(
                        approvers.map((a, i) =>
                          i === index ? { ...a, actor_id: event.target.value } : a,
                        ),
                      )
                    }
                    placeholder="who is approving"
                    className="flex-1 rounded border border-[--color-line] bg-[--color-surface] px-2 py-1 text-sm"
                  />
                </label>
              ))}

              {adopt.error ? <Problem error={adopt.error} /> : null}

              {adopt.data ? (
                <div
                  className="rounded border border-[--color-accent] p-3 text-sm"
                  data-testid="adopted"
                >
                  <Chip tone="pass">{adopt.data.policy_version}</Chip>
                  <p className="mt-1 text-xs text-[--color-muted]">
                    Adopted from {replay.data.sandbox_id}, replacing{" "}
                    {adopt.data.previous_version}. Approved by{" "}
                    {adopt.data.approved_by.join(" and ")}. Every case decided
                    from now cites this version.
                  </p>
                </div>
              ) : (
                <button
                  type="button"
                  data-testid="sandbox-adopt-submit"
                  disabled={!bothNamed || adopt.isPending}
                  onClick={() => adopt.mutate(replay.data.sandbox_id)}
                  className="self-start rounded border border-[--color-accent] px-3 py-1 text-sm disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {adopt.isPending ? "Adopting" : "Adopt as new version"}
                </button>
              )}
            </div>
          </Card>
        </>
      ) : null}
    </div>
  );
}
