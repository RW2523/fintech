import { useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { useApi } from "../api";
import { Card, Chip, Problem } from "./primitives";
import type { CopilotAnswer } from "../types";

/** docs/09 §3.5 — ask the file.
 *
 *  A question about the case in front of the officer, answered from that case
 *  and nothing else. The panel is built around one idea: an officer must be
 *  able to tell an answer from a guess without reading the model's mind. So
 *  every answer shows what it rests on, a refusal is displayed as plainly as
 *  an answer, and an answer that failed the output screen never reaches this
 *  component at all. */
export function AskTheFile({
  caseId,
  decisionRecordId,
  onCitation,
}: {
  caseId: string;
  decisionRecordId?: string;
  onCitation?: (ref: string) => void;
}) {
  const request = useApi();
  const [question, setQuestion] = useState("");
  const [asked, setAsked] = useState<string | null>(null);

  const ask = useMutation({
    mutationFn: (text: string) =>
      request<CopilotAnswer>("/api/agent_runtime/copilot/ask", {
        method: "POST",
        body: {
          case_id: caseId,
          question: text,
          decision_record_id: decisionRecordId,
        },
      }),
  });

  function submit(text: string) {
    const trimmed = text.trim();
    if (trimmed.length < 3) return;
    setAsked(trimmed);
    ask.mutate(trimmed);
  }

  return (
    <Card title="Ask the file" testId="ask-the-file">
      <div className="flex flex-col gap-3">
        <form
          className="flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            submit(question);
          }}
        >
          <input
            data-testid="ask-input"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="Which gates failed, and why was this routed here?"
            className="flex-1 rounded border border-[--color-line] bg-[--color-surface] px-2 py-1 text-sm"
          />
          <button
            type="submit"
            data-testid="ask-submit"
            disabled={ask.isPending || question.trim().length < 3}
            className="rounded border border-[--color-accent] px-3 py-1 text-sm disabled:cursor-not-allowed disabled:opacity-50"
          >
            {ask.isPending ? "Reading" : "Ask"}
          </button>
        </form>

        <div className="flex flex-wrap gap-1">
          {[
            "Which hard gates failed?",
            "Why was this routed here?",
            "What would change the outcome?",
            "What is missing from this file?",
          ].map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              onClick={() => {
                setQuestion(suggestion);
                submit(suggestion);
              }}
              className="rounded border border-[--color-line] px-2 py-0.5 text-xs text-[--color-muted] hover:border-[--color-accent]"
            >
              {suggestion}
            </button>
          ))}
        </div>

        {ask.error ? <Problem error={ask.error} /> : null}

        {ask.data ? (
          <div className="rounded border border-[--color-line] p-3" data-testid="ask-answer">
            <p className="text-xs text-[--color-muted]">{asked}</p>

            {ask.data.refusal ? (
              <div className="mt-2" data-testid="ask-refusal">
                {/* A refusal is displayed as plainly as an answer. Hiding it
                    would leave the officer wondering whether the question
                    failed or the assistant did. */}
                <Chip tone="warn">{ask.data.refusal.code ?? "refused"}</Chip>
                <p className="mt-1 text-sm">{ask.data.refusal.reason}</p>
              </div>
            ) : (
              <p className="mt-2 whitespace-pre-line text-sm" data-testid="ask-text">
                {ask.data.answer}
              </p>
            )}

            {ask.data.citations.length > 0 ? (
              <ul className="mt-2 flex flex-wrap gap-2 text-xs" data-testid="ask-citations">
                {ask.data.citations.map((citation) => (
                  <li key={citation.ref}>
                    <button
                      type="button"
                      onClick={() => onCitation?.(citation.ref)}
                      title={citation.what}
                      className="rounded border border-[--color-line] px-2 py-0.5 font-mono hover:border-[--color-accent]"
                    >
                      {citation.ref.slice(0, 20)}
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}

            <p className="mt-2 text-[11px] text-[--color-muted]" data-testid="ask-provenance">
              {/* What it read, and what it could not. An answer that silently
                  skipped the decision record is one the officer should weigh
                  differently. */}
              read {ask.data.tools_read.join(", ")}
              {ask.data.tools_unavailable.length > 0
                ? `; could not read ${ask.data.tools_unavailable.join(", ")}`
                : ""}
              {ask.data.attempts > 1 ? `; answered on attempt ${ask.data.attempts}` : ""}
            </p>
          </div>
        ) : null}

        <p className="text-xs text-[--color-muted]">
          Answers come from this case only, and cite what they rest on. The
          assistant does not predict outcomes and does not recommend decisions.
        </p>
      </div>
    </Card>
  );
}
