import { MessagesSquare, Sparkles } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Button } from "../../../components/ui";
import { assistantApi } from "../api";
import type { AssistantExchange } from "../types";
import { MessageView } from "./AnswerCard";

/** "Investigate with AI" on Incident Detail (Org Admin / Supervisor page 6 items 9–12; decision D-027): one click
 * builds the case's full timeline, related events within ±30 minutes nearby and the missing-evidence / footage-gap
 * check, read-only, in a case chat the user can continue on the AI Investigation page. */
export function InvestigatePanel({ incidentId, reference }: { incidentId: string; reference: string }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<AssistantExchange | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setResult(await assistantApi.investigate(incidentId));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card ai-investigate" aria-labelledby="investigate-title" data-testid="investigate-panel">
      <div className="card-head">
        <h2 id="investigate-title">Investigate with AI</h2>
        <Button icon={Sparkles} loading={busy} onClick={() => void run()} data-testid="investigate-run">
          {result ? "Run again" : "Investigate with AI"}
        </Button>
      </div>
      {!result && !error && (
        <p className="muted small m-0">
          Builds {reference}'s timeline, finds related alerts, manual reports and Re-ID observations within ±30 minutes
          nearby, and checks for missing footage or evidence. Read-only; it never names a culprit.
        </p>
      )}
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {result && (
        <>
          <ol className="plain ai-thread">
            <MessageView message={result.assistant_message} />
          </ol>
          <Link className="btn btn-secondary btn-md" to={`/assistant?session=${result.session.id}`} data-testid="continue-chat">
            <MessagesSquare className="icon" aria-hidden="true" /> Continue in the AI chat
          </Link>
        </>
      )}
    </section>
  );
}
