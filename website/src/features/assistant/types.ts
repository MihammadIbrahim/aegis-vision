/** AI Investigation & Security Analytics Chatbot contract (decision D-027). Field names are the Core API's
 * (backend/services/core_api/src/application/dtos/assistant_dto.py). */

export type EntityType = "ALERT" | "INCIDENT" | "CAMERA" | "EVIDENCE" | "MANUAL_REPORT" | "LOCATION";
export type Provider = "deterministic" | "ollama" | "openai_compatible";
export type ToolOutcome = "OK" | "EMPTY" | "DENIED" | "ERROR";

export interface Citation {
  entity_type: EntityType;
  id: string;
  label: string;
  /** Web app path of the record, e.g. /incidents/<id>. */
  route: string;
}

export interface ToolCallSummary {
  tool: string;
  arguments: Record<string, unknown>;
  outcome: ToolOutcome;
  rows: number;
  latency_ms: number;
  error?: string | null;
}

export type Block =
  | { type: "metrics"; items: { label: string; value: string | number }[] }
  | { type: "table"; title: string; columns: string[]; rows: (string | number)[][] }
  | { type: "bars"; title: string; items: { label: string; value: number }[] }
  | {
      type: "timeline";
      title: string;
      items: { at: string; label: string; detail: string; entity_type?: EntityType; entity_id?: string }[];
    }
  | { type: "notice"; tone: "info" | "warning"; text: string };

export interface AssistantMessage {
  id: string;
  session_id: string;
  role: "USER" | "ASSISTANT";
  content: string;
  citations: Citation[];
  tool_calls: ToolCallSummary[];
  blocks: Block[];
  insufficient_evidence: boolean;
  provider: Provider | null;
  model_name: string | null;
  created_at: string;
}

export interface AssistantSession {
  id: string;
  title: string;
  context_incident_id: string | null;
  context_incident_reference: string | null;
  created_at: string;
  updated_at: string;
  last_active_at: string;
  message_count: number;
}

export interface AssistantSessionDetail extends AssistantSession {
  messages: AssistantMessage[];
}

export interface AssistantExchange {
  session: AssistantSession;
  user_message: AssistantMessage;
  assistant_message: AssistantMessage;
}

export interface Capability {
  key: string;
  title: string;
  description: string;
  examples: string[];
}

export interface AssistantStatus {
  provider: Provider;
  provider_label: string;
  model_name: string;
  available: boolean;
  detail: string;
  read_only: true;
  file_uploads: false;
  retention_days: number;
  role: "ADMIN" | "SUPERVISOR";
  capabilities: Capability[];
  tools: string[];
  safety_note: string;
}
