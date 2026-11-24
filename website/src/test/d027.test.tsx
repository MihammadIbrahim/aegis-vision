// Decision D-027: the read-only AI Investigation & Security Analytics Chatbot — the /assistant page (Org Admin page
// 19, Supervisor page 15) and "Investigate with AI" on Incident Detail. Field names are the Core API's
// (assistant_dto.py). Nothing here changes a record; the page offers no upload.
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { canUseAssistant, navFor } from "../auth/permissions";
import { AssistantPage } from "../features/assistant/pages/AssistantPage";
import type { AssistantMessage, AssistantSession, AssistantStatus } from "../features/assistant/types";
import type { IncidentDetail } from "../features/incidents/api";
import { IncidentDetailPage } from "../features/incidents/pages/IncidentDetailPage";
import { mockApi, renderAs, sent } from "./helpers";

const STATUS: AssistantStatus = {
  provider: "deterministic",
  provider_label: "Local deterministic engine",
  model_name: "rules-v1",
  available: true,
  detail: "Built-in rule-based planner.",
  read_only: true,
  file_uploads: false,
  retention_days: 30,
  role: "SUPERVISOR",
  capabilities: [
    { key: "TRENDS", title: "Time-based trends & historical comparisons", description: "Totals and peaks.", examples: [] },
    { key: "HOTSPOTS", title: "Location & camera hotspots", description: "Where and which cameras.", examples: [] },
  ],
  tools: ["trend_summary"],
  safety_note: "Decision support only: verify people and Re-ID matches.",
};

function session(overrides: Partial<AssistantSession> = {}): AssistantSession {
  return {
    id: "s1",
    title: "How many incidents happened last year?",
    context_incident_id: null,
    context_incident_reference: null,
    created_at: "2026-11-19T10:00:00Z",
    updated_at: "2026-11-19T10:01:00Z",
    last_active_at: "2026-11-19T10:01:00Z",
    message_count: 2,
    ...overrides,
  };
}

function message(role: "USER" | "ASSISTANT", content: string, overrides: Partial<AssistantMessage> = {}): AssistantMessage {
  return {
    id: `${role}-${content.length}`,
    session_id: "s1",
    role,
    content,
    citations: [],
    tool_calls: [],
    blocks: [],
    insufficient_evidence: false,
    provider: role === "ASSISTANT" ? "deterministic" : null,
    model_name: role === "ASSISTANT" ? "rules-v1" : null,
    created_at: "2026-11-19T10:01:00Z",
    ...overrides,
  };
}

const ANSWER = message(
  "ASSISTANT",
  "In last year (2025): 12 alerts (5 weapon, 4 fight / violence, 3 restricted area; 9 high priority), 4 cases opened and 2 Guard manual reports. The most frequent type was Weapon (5).",
  {
    citations: [
      { entity_type: "INCIDENT", id: "i9", label: "INC-000009 · Fight at the gate", route: "/incidents/i9" },
      { entity_type: "ALERT", id: "a1", label: "Weapon alert · Gate Camera", route: "/alerts/a1" },
    ],
    tool_calls: [
      {
        tool: "trend_summary",
        arguments: { period: { since: "2025-01-01", until: "2026-01-01", label: "last year (2025)" } },
        outcome: "OK",
        rows: 18,
        latency_ms: 12.4,
      },
    ],
    blocks: [
      { type: "metrics", items: [{ label: "Alerts", value: 12 }, { label: "Cases opened", value: 4 }] },
      { type: "table", title: "By type", columns: ["Type", "Count"], rows: [["Weapon", 5], ["Fight / Violence", 4]] },
      { type: "bars", title: "By hour of day", items: [{ label: "22:00", value: 3 }, { label: "23:00", value: 1 }] },
    ],
  },
);

function exchange(question: string, answer: AssistantMessage, s: AssistantSession = session()) {
  return { session: s, user_message: message("USER", question), assistant_message: answer };
}

describe("AI Investigation page", () => {
  it("asks a starter question, creates the chat and shows a grounded, cited answer", async () => {
    const question = "How many incidents happened last year and which type was most frequent?";
    const calls = mockApi([
      { method: "GET", url: /\/assistant\/status$/, body: STATUS },
      { method: "GET", url: /\/assistant\/sessions$/, body: [] },
      { method: "POST", url: /\/assistant\/sessions$/, body: session({ title: "New chat", message_count: 0 }), status: 201 },
      { method: "POST", url: /\/assistant\/sessions\/s1\/messages$/, body: exchange(question, ANSWER), status: 201 },
    ]);
    renderAs("SUPERVISOR", <AssistantPage />, "/assistant");
    expect(await screen.findByTestId("provider-badge")).toHaveProperty("textContent", "Local deterministic engine");
    expect(await screen.findByText("No chats yet. Ask a question to start one.")).toBeTruthy();
    expect(screen.getByText("Time-based trends & historical comparisons")).toBeTruthy();
    expect(screen.queryByLabelText(/upload/i)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: question }));
    const answer = await screen.findByTestId("assistant-answer");
    expect(sent(calls, "POST", /\/assistant\/sessions$/)[0].body).toEqual({});
    expect(sent(calls, "POST", /\/messages$/)[0].body).toEqual({ content: question });
    expect(within(answer).getByText(/The most frequent type was Weapon \(5\)/)).toBeTruthy();
    expect(within(answer).getByText("Cases opened").nextSibling?.textContent).toBe("4");
    expect(within(answer).getByRole("table", { name: "By type" })).toBeTruthy();
    expect(within(answer).getByText("By hour of day")).toBeTruthy();
    const references = within(answer).getByRole("list", { name: "References" });
    expect(within(references).getByRole("link", { name: /INC-000009 · Fight at the gate/ }).getAttribute("href")).toBe(
      "/incidents/i9",
    );
    expect(within(references).getByRole("link", { name: /Weapon alert · Gate Camera/ }).getAttribute("href")).toBe("/alerts/a1");
    const tools = within(answer).getByText(/Tools & queries used \(1\)/);
    fireEvent.click(tools);
    expect(within(answer).getByText("trend_summary")).toBeTruthy();
    expect(within(answer).getByText(/"label": "last year \(2025\)"/)).toBeTruthy();
    expect(within(answer).getByText("Decision support only: verify people and Re-ID matches.")).toBeTruthy();
    expect(within(answer).queryByTestId("insufficient-evidence")).toBeNull();
    expect(within(screen.getByRole("list", { name: "Conversation" })).getByText(question)).toBeTruthy();
  });

  it("continues the same chat for follow-up questions", async () => {
    const calls = mockApi([
      { method: "GET", url: /\/assistant\/status$/, body: STATUS },
      { method: "GET", url: /\/assistant\/sessions$/, body: [session()] },
      { method: "GET", url: /\/assistant\/sessions\/s1$/, body: { ...session(), messages: [message("USER", "Which building had the most incidents in October?"), message("ASSISTANT", "Top buildings by cases in October 2026: 1. Library — 3.")] } },
      {
        method: "POST",
        url: /\/assistant\/sessions\/s1\/messages$/,
        body: exchange("How many of those are still unresolved and who is handling them?", message("ASSISTANT", "2 unresolved cases at Library in October 2026. Handled by: Sana Malik (1); Unassigned (1).")),
        status: 201,
      },
    ]);
    renderAs("ADMIN", <AssistantPage />, "/assistant?session=s1");
    expect(await screen.findByText("Top buildings by cases in October 2026: 1. Library — 3.")).toBeTruthy();
    const box = screen.getByLabelText("Your question");
    fireEvent.change(box, { target: { value: "How many of those are still unresolved and who is handling them?" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(await screen.findByText(/Handled by: Sana Malik \(1\); Unassigned \(1\)/)).toBeTruthy();
    expect(sent(calls, "POST", /\/assistant\/sessions$/)).toHaveLength(0);
    expect(sent(calls, "POST", /\/sessions\/s1\/messages$/)).toHaveLength(1);
  });

  it("shows the missing-data banner and the case context of a case chat", async () => {
    mockApi([
      { method: "GET", url: /\/assistant\/status$/, body: STATUS },
      { method: "GET", url: /\/assistant\/sessions$/, body: [session({ context_incident_id: "i9", context_incident_reference: "INC-000009" })] },
      {
        method: "GET",
        url: /\/assistant\/sessions\/s1$/,
        body: { ...session({ context_incident_reference: "INC-000009" }), messages: [message("ASSISTANT", "No alerts, cases or manual reports were found in 2019.", { insufficient_evidence: true })] },
      },
    ]);
    renderAs("SUPERVISOR", <AssistantPage />, "/assistant?session=s1");
    expect(await screen.findByTestId("insufficient-evidence")).toBeTruthy();
    expect(screen.getByText("No alerts, cases or manual reports were found in 2019.")).toBeTruthy();
    expect((await screen.findByTestId("case-context")).textContent).toContain("INC-000009");
    expect(within(screen.getByTestId("session-list")).getByText(/INC-000009/)).toBeTruthy();
  });

  it("deletes a chat after confirmation", async () => {
    const list = [session(), session({ id: "s2", title: "Camera downtime" })];
    const calls = mockApi([
      { method: "GET", url: /\/assistant\/status$/, body: STATUS },
      { method: "GET", url: /\/assistant\/sessions$/, body: list },
      { method: "DELETE", url: /\/assistant\/sessions\/s2$/, body: null, status: 204 },
    ]);
    renderAs("ADMIN", <AssistantPage />, "/assistant");
    fireEvent.click(await screen.findByRole("button", { name: "Delete chat Camera downtime" }));
    const dialog = screen.getByRole("dialog", { name: "Delete this chat?" });
    list.splice(1, 1);
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete chat" }));
    await waitFor(() => expect(sent(calls, "DELETE", /\/assistant\/sessions\/s2$/)).toHaveLength(1));
    expect(await screen.findByText("Chat deleted.")).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Delete chat Camera downtime" })).toBeNull());
  });

  it("says when the configured model is unreachable and keeps the question after an error", async () => {
    mockApi([
      {
        method: "GET",
        url: /\/assistant\/status$/,
        body: { ...STATUS, provider: "ollama", provider_label: "Ollama qwen2.5:7b", model_name: "qwen2.5:7b", available: false, detail: "Ollama at http://127.0.0.1:11434 is unreachable (ConnectError)." },
      },
      { method: "GET", url: /\/assistant\/sessions$/, body: [session()] },
      { method: "GET", url: /\/assistant\/sessions\/s1$/, body: { ...session(), messages: [] } },
      { method: "POST", url: /\/messages$/, body: { detail: "Chat session not found.", code: "ASSISTANT_SESSION_NOT_FOUND" }, status: 404 },
    ]);
    renderAs("ADMIN", <AssistantPage />, "/assistant?session=s1");
    expect((await screen.findByTestId("provider-unavailable")).textContent).toContain("unreachable (ConnectError)");
    expect(screen.getByTestId("provider-badge").textContent).toBe("Ollama qwen2.5:7b · unreachable");
    const box = screen.getByLabelText("Your question") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "Which cameras went offline most often this month?" } });
    fireEvent.click(screen.getByRole("button", { name: "Ask" }));
    expect((await screen.findByTestId("send-error")).textContent).toBe("Chat session not found.");
    expect(box.value).toBe("Which cameras went offline most often this month?");
  });

  it("is offered to Admins and Supervisors only", () => {
    for (const role of ["ADMIN", "SUPERVISOR"] as const) {
      expect(canUseAssistant(role)).toBe(true);
      const labels = navFor(role).map((i) => i.label);
      expect(labels.indexOf("AI Investigation")).toBe(labels.indexOf("Incidents") + 1);
    }
    expect(canUseAssistant("GUARD" as never)).toBe(false);
    expect(navFor("GUARD" as never)).toEqual([]);
  });
});

function incidentDetail(): IncidentDetail {
  return {
    incident: {
      id: "i9", reference: "INC-000009", title: "Fight at the gate", description: "", status: "OPEN", priority: "HIGH",
      occurred_at: "2026-11-19T10:00:00Z", camera_id: null, camera_name: null, building_id: null, floor_id: null, area_id: null,
      location: null, assigned_to: null, assigned_name: null, created_by: "X", created_by_name: "Supervisor S", resolution_note: null,
      closed_at: null, created_at: "2026-11-19T10:00:00Z", updated_at: "2026-11-19T10:00:00Z", alert_count: 0, report_count: 0, evidence_count: 0,
    },
    alerts: [], reports: [], evidence: [], participants: [], footage_gaps: [], history: [], person_search: [], ai_assistance_available: false,
  };
}

describe("Investigate with AI on Incident Detail", () => {
  it("runs the read-only investigation and links to the case chat", async () => {
    const investigation = message(
      "ASSISTANT",
      "INC-000009 · Fight at the gate — open, high priority, handled by nobody yet.\nPossible evidence gaps for INC-000009 (1): no evidence saved.",
      {
        blocks: [
          { type: "timeline", title: "INC-000009 timeline", items: [{ at: "2026-11-19 15:00", label: "Case opened (Supervisor S)", detail: "" }] },
          { type: "table", title: "Related events (±30 min)", columns: ["Time", "Kind", "What", "Where"], rows: [["2026-11-19 15:05", "Alert", "Weapon alert · Gate Camera", "Admin Block"]] },
        ],
        tool_calls: ["case_timeline", "related_events", "evidence_gaps"].map((tool) => ({ tool, arguments: { case: { reference: "INC-000009" } }, outcome: "OK" as const, rows: 1, latency_ms: 3 })),
      },
    );
    const calls = mockApi([
      { method: "GET", url: /\/incidents\/i9$/, body: incidentDetail() },
      { method: "GET", url: /\/incidents\/i9\/messages$/, body: [] },
      { method: "POST", url: /\/assistant\/incidents\/i9\/investigate$/, body: exchange("Investigate INC-000009", investigation, session({ id: "case-chat", context_incident_id: "i9", context_incident_reference: "INC-000009" })), status: 201 },
    ]);
    renderAs("SUPERVISOR", <IncidentDetailPage />, "/incidents/i9", "/incidents/:id");
    const panel = await screen.findByTestId("investigate-panel");
    expect(panel.textContent).toContain("Read-only; it never names a culprit.");
    fireEvent.click(within(panel).getByTestId("investigate-run"));
    expect(await within(panel).findByText("INC-000009 timeline")).toBeTruthy();
    expect(sent(calls, "POST", /\/investigate$/)).toHaveLength(1);
    expect(within(panel).getByText("Case opened (Supervisor S)")).toBeTruthy();
    expect(within(panel).getByRole("table", { name: "Related events (±30 min)" })).toBeTruthy();
    expect(within(panel).getByText(/no evidence saved/)).toBeTruthy();
    expect(within(panel).getByTestId("continue-chat").getAttribute("href")).toBe("/assistant?session=case-chat");
    expect(within(panel).getByRole("button", { name: "Run again" })).toBeTruthy();
  });
});
