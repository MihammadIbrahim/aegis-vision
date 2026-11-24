import { Bot, Database, Info, Link2, ShieldAlert, TriangleAlert, User } from "lucide-react";
import { Link } from "react-router-dom";

import { formatDateTime } from "../../../lib/format";
import { ENTITY_LABEL, providerText } from "../api";
import type { AssistantMessage, Block } from "../types";

function BlockView({ block }: { block: Block }) {
  if (block.type === "notice") {
    return (
      <p className={`notice ${block.tone === "warning" ? "warning" : "info"} ai-notice`} role="status">
        {block.text}
      </p>
    );
  }
  if (block.type === "metrics") {
    return (
      <dl className="ai-metrics">
        {block.items.map((m) => (
          <div key={m.label} className="ai-metric">
            <dt>{m.label}</dt>
            <dd>{m.value}</dd>
          </div>
        ))}
      </dl>
    );
  }
  if (block.type === "bars") {
    const max = Math.max(1, ...block.items.map((i) => i.value));
    return (
      <figure className="ai-bars">
        <figcaption>{block.title}</figcaption>
        <ul className="plain">
          {block.items.map((i) => (
            <li key={i.label}>
              <span className="ai-bar-label">{i.label}</span>
              <span className="ai-bar-track" aria-hidden="true">
                <span className="ai-bar-fill" style={{ width: `${(i.value / max) * 100}%` }} />
              </span>
              <span className="ai-bar-value">{i.value}</span>
            </li>
          ))}
        </ul>
      </figure>
    );
  }
  if (block.type === "timeline") {
    return (
      <figure className="ai-timeline">
        <figcaption>{block.title}</figcaption>
        <ol className="plain">
          {block.items.map((item, index) => (
            <li key={`${item.at}-${index}`}>
              <time>{item.at}</time>
              <span className="ai-timeline-label">{item.label}</span>
              {item.detail && <span className="muted small"> — {item.detail}</span>}
            </li>
          ))}
        </ol>
      </figure>
    );
  }
  return (
    <div className="table-scroll ai-table">
      <table>
        <caption>{block.title}</caption>
        <thead>
          <tr>
            {block.columns.map((c) => (
              <th key={c} scope="col">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {block.rows.map((row, r) => (
            <tr key={r}>
              {row.map((cell, c) => (
                <td key={c} className={typeof cell === "number" ? "num" : undefined}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** One message of the conversation. Assistant answers show the grounded text, structured blocks, clickable
 * references, the tools and queries used, a missing-data banner and the decision-support notice. */
export function MessageView({ message, safetyNote }: { message: AssistantMessage; safetyNote?: string }) {
  if (message.role === "USER") {
    return (
      <li className="ai-msg ai-msg-user">
        <span className="ai-avatar" aria-hidden="true">
          <User className="icon" />
        </span>
        <div className="ai-bubble">
          <p className="sr-only">You asked:</p>
          <p className="m-0 prewrap">{message.content}</p>
        </div>
      </li>
    );
  }
  return (
    <li className="ai-msg ai-msg-assistant" data-testid="assistant-answer">
      <span className="ai-avatar" aria-hidden="true">
        <Bot className="icon" />
      </span>
      <article className="ai-answer" aria-label="Assistant answer">
        {message.insufficient_evidence && (
          <p className="notice warning ai-missing" role="note" data-testid="insufficient-evidence">
            <TriangleAlert className="icon" aria-hidden="true" /> Missing data: no matching records were found for at
            least part of this question, so nothing was estimated.
          </p>
        )}
        {message.content.split("\n").map((line, i) => (
          <p key={i} className="ai-text">
            {line}
          </p>
        ))}
        {message.blocks.map((block, i) => (
          <BlockView key={i} block={block} />
        ))}
        {message.citations.length > 0 && (
          <div className="ai-citations">
            <p className="ai-citations-title">
              <Link2 className="icon" aria-hidden="true" /> References
            </p>
            <ul className="plain" aria-label="References">
              {message.citations.map((c) => (
                <li key={`${c.entity_type}-${c.id}`}>
                  <Link className="ai-citation" to={c.route}>
                    <span className="ai-citation-kind">{ENTITY_LABEL[c.entity_type] ?? c.entity_type}</span>
                    {c.label}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        )}
        {message.tool_calls.length > 0 && (
          <details className="ai-tools">
            <summary>
              <Database className="icon" aria-hidden="true" /> Tools &amp; queries used ({message.tool_calls.length})
            </summary>
            <ul className="plain">
              {message.tool_calls.map((t, i) => (
                <li key={`${t.tool}-${i}`}>
                  <code>{t.tool}</code> · {t.outcome.toLowerCase()} · {t.rows} rows · {Math.round(t.latency_ms)} ms
                  <pre className="ai-args">{JSON.stringify(t.arguments, null, 2)}</pre>
                </li>
              ))}
            </ul>
            <p className="muted small m-0">
              Read-only queries on this campus's records, run with your own permissions. Answered by{" "}
              {providerText(message.provider, message.model_name)}.
            </p>
          </details>
        )}
        <p className="ai-safety">
          <ShieldAlert className="icon" aria-hidden="true" />
          {safetyNote ??
            "Decision support only: figures come from this campus's records; people and Re-ID matches must be verified by your staff."}
        </p>
        <p className="muted small m-0">
          <Info className="icon ai-inline-icon" aria-hidden="true" />
          <time dateTime={message.created_at}>{formatDateTime(message.created_at)}</time>
        </p>
      </article>
    </li>
  );
}
