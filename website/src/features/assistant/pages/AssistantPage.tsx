import { Bot, FolderOpen, MessageSquarePlus, PanelLeft, Send, Sparkles, Trash2, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useSearchParams } from "react-router-dom";

import { Button, ErrorState, LoadingState, PageHeader, useToast } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { useConfirm } from "../../../hooks/useConfirm";
import { formatDateTime } from "../../../lib/format";
import { assistantApi, STARTER_PROMPTS } from "../api";
import { MessageView } from "../components/AnswerCard";
import type { AssistantMessage, AssistantSession, AssistantStatus } from "../types";

function ProviderBadge({ status }: { status: AssistantStatus | undefined }) {
  if (!status) return null;
  return (
    <span
      className={`badge ai-provider ${status.available ? "tone-resolved" : "tone-warning"}`}
      title={status.detail}
      data-testid="provider-badge"
    >
      <Bot className="icon" aria-hidden="true" />
      {status.provider === "deterministic" ? "Local deterministic engine" : status.provider_label}
      {!status.available && " · unreachable"}
    </span>
  );
}

/** AI Investigation & Security Analytics Chatbot (Org Admin page 19, Supervisor page 15; decision D-027).
 * Read-only: answers come from this campus's records through permission-checked queries; nothing is changed and
 * nothing can be uploaded. */
export function AssistantPage() {
  const toast = useToast();
  const { confirm, dialog } = useConfirm();
  const [params, setParams] = useSearchParams();
  const sessionId = params.get("session");
  const status = useAsync(() => assistantApi.status(), []);
  const sessions = useAsync(() => assistantApi.sessions(), []);
  const [messages, setMessages] = useState<AssistantMessage[]>([]);
  const [loadingThread, setLoadingThread] = useState(false);
  const [threadError, setThreadError] = useState<unknown>(null);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [showChats, setShowChats] = useState(false);
  const end = useRef<HTMLDivElement | null>(null);
  const reloadSessions = sessions.reload;

  const loadThread = useCallback((id: string | null) => {
    setThreadError(null);
    if (!id) {
      setMessages([]);
      return;
    }
    setLoadingThread(true);
    assistantApi
      .get(id)
      .then((detail) => setMessages(detail.messages))
      .catch((e: unknown) => setThreadError(e))
      .finally(() => setLoadingThread(false));
  }, []);

  useEffect(() => loadThread(sessionId), [sessionId, loadThread]);
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [messages.length, pending]);

  const current: AssistantSession | undefined = sessions.data?.find((s) => s.id === sessionId);

  async function ask(question: string) {
    const text = question.trim();
    if (!text || pending) return;
    setPending(text);
    setSendError(null);
    setDraft("");
    try {
      let id = sessionId;
      if (!id) {
        id = (await assistantApi.create()).id;
      }
      const exchange = await assistantApi.send(id, text);
      setMessages((list) => [...list, exchange.user_message, exchange.assistant_message]);
      if (id !== sessionId) setParams({ session: id }, { replace: true });
      reloadSessions();
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setSendError(message);
      setDraft(text);
      toast("error", message);
    } finally {
      setPending(null);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    void ask(draft);
  }

  function onKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void ask(draft);
    }
  }

  async function remove(session: AssistantSession) {
    const answer = await confirm({
      title: "Delete this chat?",
      message: `"${session.title}" and its ${session.message_count} messages are deleted for good. Nothing else changes.`,
      confirmLabel: "Delete chat",
      tone: "danger",
    });
    if (!answer.confirmed) return;
    try {
      await assistantApi.remove(session.id);
      toast("success", "Chat deleted.");
      if (session.id === sessionId) setParams({}, { replace: true });
      reloadSessions();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    }
  }

  function newChat() {
    setParams({}, { replace: true });
    setMessages([]);
    setSendError(null);
    setShowChats(false);
  }

  const capabilities = status.data?.capabilities ?? [];
  const empty = messages.length === 0 && !pending && !loadingThread;

  return (
    <section className="ai-page">
      <PageHeader
        title="AI Investigation Chatbot"
        description="Ask about alerts, cases, places, cameras, Guard responses, manual reports and evidence. Read-only: it never changes anything and takes no uploads."
        actions={
          <>
            <ProviderBadge status={status.data} />
            <Button variant="secondary" icon={MessageSquarePlus} onClick={newChat}>
              New chat
            </Button>
          </>
        }
      />
      {status.data && !status.data.available && (
        <p className="notice warning" role="alert" data-testid="provider-unavailable">
          <TriangleAlert className="icon" aria-hidden="true" /> The configured language model ({status.data.provider_label}) is
          not reachable: {status.data.detail} Answers still come from the same records through the built-in
          deterministic engine.
        </p>
      )}
      <div className="ai-layout">
        <aside className={`ai-sidebar card${showChats ? " is-open" : ""}`} aria-label="Your chats">
          <div className="card-head">
            <h2>Your chats</h2>
            <button
              type="button"
              className="btn btn-ghost btn-sm ai-chats-toggle"
              aria-expanded={showChats}
              onClick={() => setShowChats((v) => !v)}
            >
              <PanelLeft className="icon" aria-hidden="true" /> {showChats ? "Hide" : "Show"}
            </button>
          </div>
          <div className="ai-sidebar-body">
            {sessions.error ? (
              <ErrorState error={sessions.error} onRetry={sessions.reload} />
            ) : !sessions.data ? (
              <LoadingState />
            ) : sessions.data.length === 0 ? (
              <p className="muted small">No chats yet. Ask a question to start one.</p>
            ) : (
              <ul className="plain ai-sessions" data-testid="session-list">
                {sessions.data.map((s) => (
                  <li key={s.id} className={s.id === sessionId ? "is-current" : undefined}>
                    <button
                      type="button"
                      className="ai-session"
                      aria-current={s.id === sessionId ? "true" : undefined}
                      onClick={() => {
                        setParams({ session: s.id });
                        setShowChats(false);
                      }}
                    >
                      <span className="ai-session-title">{s.title}</span>
                      <span className="muted small">
                        {s.context_incident_reference && (
                          <>
                            <FolderOpen className="icon ai-inline-icon" aria-hidden="true" /> {s.context_incident_reference} ·{" "}
                          </>
                        )}
                        {formatDateTime(s.last_active_at)}
                      </span>
                    </button>
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm ai-session-delete"
                      aria-label={`Delete chat ${s.title}`}
                      title="Delete chat"
                      onClick={() => void remove(s)}
                    >
                      <Trash2 className="icon" aria-hidden="true" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <p className="muted small">
              Chats are private to you and deleted after {status.data?.retention_days ?? 30} days without use.
            </p>
          </div>
        </aside>

        <div className="ai-main card">
          {current?.context_incident_reference && (
            <p className="notice info m-0" data-testid="case-context">
              This chat is about {current.context_incident_reference}: follow-up questions use that case.
            </p>
          )}
          {threadError ? (
            <ErrorState error={threadError} onRetry={() => loadThread(sessionId)} />
          ) : loadingThread ? (
            <LoadingState label="Loading the chat…" />
          ) : empty ? (
            <div className="ai-welcome">
              <Sparkles className="icon ai-welcome-icon" aria-hidden="true" />
              <h2>What would you like to investigate?</h2>
              <ul className="plain ai-capabilities">
                {capabilities.map((c) => (
                  <li key={c.key}>
                    <strong>{c.title}</strong> <span className="muted small">— {c.description}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {!threadError && (
            <ol className="plain ai-thread" aria-label="Conversation" aria-live="polite">
              {messages.map((m) => (
                <MessageView key={m.id} message={m} safetyNote={status.data?.safety_note} />
              ))}
              {pending && (
                <>
                  <li className="ai-msg ai-msg-user">
                    <span className="ai-avatar" aria-hidden="true" />
                    <div className="ai-bubble">
                      <p className="m-0 prewrap">{pending}</p>
                    </div>
                  </li>
                  <li className="ai-msg ai-msg-assistant">
                    <span className="ai-avatar" aria-hidden="true">
                      <Bot className="icon" />
                    </span>
                    <LoadingState label="Querying the records…" />
                  </li>
                </>
              )}
            </ol>
          )}
          <div ref={end} />
          {sendError && (
            <p className="notice error" role="alert" data-testid="send-error">
              {sendError}
            </p>
          )}
          <div className="ai-chips" aria-label="Starter questions">
            {STARTER_PROMPTS.map((prompt) => (
              <button
                key={prompt}
                type="button"
                className="ai-chip"
                disabled={Boolean(pending)}
                onClick={() => void ask(prompt)}
              >
                {prompt}
              </button>
            ))}
          </div>
          <form className="ai-composer" onSubmit={submit}>
            <label className="sr-only" htmlFor="ai-question">
              Your question
            </label>
            <textarea
              id="ai-question"
              value={draft}
              maxLength={1000}
              rows={2}
              placeholder="e.g. Which building had the most incidents in October?"
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onKey}
            />
            <Button type="submit" icon={Send} loading={Boolean(pending)} disabled={!draft.trim()}>
              Ask
            </Button>
          </form>
        </div>
      </div>
      {dialog}
    </section>
  );
}
