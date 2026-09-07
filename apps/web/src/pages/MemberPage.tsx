import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

import { useApi } from "../api";
import { useAuth } from "../auth";
import { Card, Chip, Empty, Problem } from "../components/primitives";

/** docs/09 §5 — the member assistant.
 *
 *  The officer's panel and this one look alike and are not alike. An officer
 *  is reading a file they are accountable for; a member is reading about their
 *  own money and cannot open anything to check. So this screen shows no score,
 *  no recommendation and no internal state, a refusal reads as a sentence
 *  rather than a code, and a handoff is shown as a promise that has already
 *  been made rather than as a suggestion. */

type Citation = { ref: string; what: string };

type AssistantReply = {
  answer: string;
  citations: Citation[];
  refusal?: { reason: string; code?: string };
  signal?: string;
  handoff_id?: string;
  tools_read: string[];
  tools_unavailable: string[];
  conversation_id: string;
  member_id: string;
};

type Turn = { role: "you" | "assistant"; text: string; reply?: AssistantReply };

type Inbox = {
  messages: { message_id: string; subject?: string; body: string; sent_at?: string }[];
};

/** What each tool is, said the way the member would say it. A chip reading
 *  `get_my_next_payment` tells somebody nothing about where their answer came
 *  from; "your payment schedule" tells them exactly. */
const FROM_YOUR_RECORDS: Record<string, string> = {
  get_my_balance: "your accounts and savings",
  get_my_next_payment: "your payment schedule",
  get_my_application: "your application",
  get_missing_documents: "what we have asked you for",
};

const QUICK = [
  "What is my balance?",
  "When is my next payment due?",
  "How is my application going?",
  "What documents do you still need from me?",
];

export function MemberPage() {
  const request = useApi();
  const { session } = useAuth();
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [conversation, setConversation] = useState<string | null>(null);
  const [tab, setTab] = useState<"chat" | "inbox">("chat");

  const ask = useMutation({
    mutationFn: (text: string) =>
      request<AssistantReply>("/api/agent_runtime/assistant/ask", {
        method: "POST",
        body: conversation ? { question: text, conversation_id: conversation } : { question: text },
      }),
    onSuccess: (reply) => {
      setConversation(reply.conversation_id);
      setTurns((previous) => [
        ...previous,
        { role: "assistant", text: reply.refusal?.reason ?? reply.answer, reply },
      ]);
    },
  });

  const inbox = useQuery({
    queryKey: ["member-inbox", session?.memberId],
    enabled: tab === "inbox" && Boolean(session?.memberId),
    queryFn: () => request<Inbox>(`/api/notification/members/${session?.memberId}/inbox`),
  });

  function submit(text: string) {
    const trimmed = text.trim();
    if (trimmed.length < 3 || ask.isPending) return;
    setTurns((previous) => [...previous, { role: "you", text: trimmed }]);
    setQuestion("");
    ask.mutate(trimmed);
  }

  if (!session?.memberId) {
    return (
      <Card title="Your account" icon="members" tone="accent" testId="member-page">
        <Empty>
          Sign in with a membership number to use the assistant. It only ever
          reads the records of the member signed in.
        </Empty>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-5" data-testid="member-page">
      <header>
        <h1 className="text-[26px] font-bold tracking-tight">Your account</h1>
        <p className="text-sm text-[--color-muted]">
          What we hold about you, and somebody to ask.
        </p>
      </header>
      <div className="flex gap-2">
        {(["chat", "inbox"] as const).map((name) => (
          <button
            key={name}
            type="button"
            data-testid={`member-tab-${name}`}
            onClick={() => setTab(name)}
            className={`rounded border px-3 py-1 text-sm ${
              tab === name
                ? "border-[--color-accent]"
                : "border-[--color-line] text-[--color-muted]"
            }`}
          >
            {name === "chat" ? "Ask us" : "Your messages"}
          </button>
        ))}
      </div>

      {tab === "inbox" ? (
        <Card title="Your messages" icon="chat" tone="accent" testId="member-inbox">
          {inbox.error ? <Problem error={inbox.error} /> : null}
          {inbox.data && inbox.data.messages.length > 0 ? (
            <ul className="flex flex-col gap-2">
              {inbox.data.messages.map((message) => (
                <li key={message.message_id} className="rounded border border-[--color-line] p-3">
                  <p className="text-xs text-[--color-muted]">{message.sent_at ?? "not sent yet"}</p>
                  <p className="mt-1 text-sm">{message.body}</p>
                </li>
              ))}
            </ul>
          ) : (
            <Empty>Nothing has been sent to you.</Empty>
          )}
        </Card>
      ) : (
        <Card title="Ask us" icon="spark" tone="note" testId="member-chat">
          <div className="flex flex-col gap-3">
            <ul className="flex flex-col gap-3" data-testid="member-turns">
              {turns.map((turn, index) => (
                <li
                  key={index}
                  className={turn.role === "you" ? "text-right" : ""}
                  data-testid={turn.role === "you" ? "member-said" : "assistant-said"}
                >
                  <div
                    className={`inline-block max-w-[85%] rounded border p-3 text-left text-sm ${
                      turn.role === "you"
                        ? "border-[--color-line] bg-[--color-surface]"
                        : "border-[--color-line]"
                    }`}
                  >
                    <p className="whitespace-pre-line">{turn.text}</p>

                    {turn.reply?.handoff_id ? (
                      // The promise, shown as already kept. The row exists
                      // before this renders: the assistant does not say
                      // somebody will call unless somebody has been asked to.
                      <div
                        className="mt-2 rounded border border-[--color-accent] p-2 text-xs"
                        data-testid="member-handoff"
                      >
                        <Chip tone="warn">A colleague will contact you</Chip>
                        <p className="mt-1 text-[--color-muted]">
                          Reference {turn.reply.handoff_id.slice(0, 16)}. You do not need to
                          do anything else.
                        </p>
                      </div>
                    ) : null}

                    {turn.reply && !turn.reply.refusal && turn.reply.tools_read.length > 0 ? (
                      <p
                        className="mt-2 text-[11px] text-[--color-muted]"
                        data-testid="member-provenance"
                      >
                        from your records:{" "}
                        {turn.reply.tools_read
                          .map((tool) => FROM_YOUR_RECORDS[tool] ?? tool)
                          .join(", ")}
                      </p>
                    ) : null}
                  </div>
                </li>
              ))}
              {ask.isPending ? (
                <li className="text-sm text-[--color-muted]" data-testid="member-thinking">
                  Looking at your records…
                </li>
              ) : null}
            </ul>

            {ask.error ? <Problem error={ask.error} /> : null}

            <form
              className="flex gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                submit(question);
              }}
            >
              <input
                data-testid="member-input"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="Ask about your account"
                className="flex-1 rounded border border-[--color-line] bg-[--color-surface] px-2 py-1 text-sm"
              />
              <button
                type="submit"
                data-testid="member-send"
                disabled={ask.isPending || question.trim().length < 3}
                className="rounded border border-[--color-accent] px-3 py-1 text-sm disabled:cursor-not-allowed disabled:opacity-50"
              >
                Send
              </button>
            </form>

            <div className="flex flex-wrap gap-1">
              {QUICK.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  data-testid={`member-quick-${QUICK.indexOf(suggestion)}`}
                  onClick={() => submit(suggestion)}
                  className="rounded border border-[--color-line] px-2 py-0.5 text-xs text-[--color-muted] hover:border-[--color-accent]"
                >
                  {suggestion}
                </button>
              ))}
              <button
                type="button"
                data-testid="member-quick-person"
                onClick={() => submit("I would like to talk to a person")}
                className="rounded border border-[--color-line] px-2 py-0.5 text-xs text-[--color-muted] hover:border-[--color-accent]"
              >
                Talk to a person
              </button>
            </div>

            <p className="text-xs text-[--color-muted]" data-testid="member-limits">
              Answers come from your own records. This assistant cannot tell you
              whether an application will be approved, and it does not give
              financial advice. If anything is difficult, ask to talk to a
              person and somebody will contact you.
            </p>
          </div>
        </Card>
      )}
    </div>
  );
}
