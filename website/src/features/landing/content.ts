// Every claim on the public landing page, with its real implementation status (project context and
// requirements documents, 2026-11-12). "AVAILABLE" = built and tested; "PARTIAL" = partly built;
// "PLANNED" = in the requirements, not built. Keep these in step with the documentation.
import type { LucideIcon } from "lucide-react";
import {
  Bell,
  BrainCircuit,
  Building2,
  DoorClosed,
  FileSearch,
  MessagesSquare,
  Radar,
  ScanSearch,
  ShieldAlert,
  Swords,
  Users,
  Video,
} from "lucide-react";

export type Availability = "AVAILABLE" | "PARTIAL" | "PLANNED";

export interface Capability {
  icon: LucideIcon;
  title: string;
  text: string;
  status: Availability;
  note?: string;
}

export const CAPABILITY_GROUPS: Array<{ id: string; title: string; lead: string; items: Capability[] }> = [
  {
    id: "vision",
    title: "Computer-vision detection",
    lead: "Video is analysed by separate workers; what they find becomes an alert for people to review.",
    items: [
      {
        icon: ScanSearch,
        title: "Human detection & single-camera tracking",
        text: "A pretrained YOLOX-Tiny detector with a ByteTrack-style tracker follows people within one camera view. People never raise alerts on their own.",
        status: "PARTIAL",
        note: "Runs on recorded video on a CPU; accuracy on real CCTV footage is not yet measured.",
      },
      { icon: ShieldAlert, title: "Weapon detection", text: "High-priority alerts for possible weapons, always for human review.", status: "PLANNED" },
      { icon: Swords, title: "Fight / violence detection", text: "High-priority alerts for possible fights or violent activity.", status: "PLANNED" },
      {
        icon: Radar,
        title: "Cross-camera person re-identification",
        text: "Ranked candidate matches across cameras, labelled as likely or uncertain, confirmed only by a Supervisor.",
        status: "PLANNED",
      },
    ],
  },
  {
    id: "routing",
    title: "Alerts, routing & response",
    lead: "Every alert carries its camera and location, and reaches the people responsible for that place.",
    items: [
      {
        icon: Bell,
        title: "Location-aware alert routing",
        text: "Detections become alerts with a priority (weapon and fight: high). They go to the Guards stationed at the camera's building, floor or area; Standby Guards are reached by escalation.",
        status: "AVAILABLE",
      },
      {
        icon: MessagesSquare,
        title: "Response workflow & alert chat",
        text: "New → Acknowledged → Responding → Resolved, with Escalated and Dismissed. Guard assessments, an alert chat for staff and the alert's Guards, and manual Guard reports kept apart from AI alerts.",
        status: "AVAILABLE",
      },
      {
        icon: DoorClosed,
        title: "Restricted-area access",
        text: "Personnel records per campus, restricted areas and access grants with validity and weekly schedules. Unknown people are never reported as named people.",
        status: "AVAILABLE",
        note: "Face recognition for restricted areas is optional and not built.",
      },
      {
        icon: Video,
        title: "Live CCTV monitoring",
        text: "Camera grid, live streams and recordings from RTSP/ONVIF cameras.",
        status: "PLANNED",
        note: "Cameras are registered with their location today; video ingestion is not built.",
      },
    ],
  },
  {
    id: "assist",
    title: "Assistance with human approval",
    lead: "Language models help staff find and summarise information; they never act or accuse on their own.",
    items: [
      {
        icon: FileSearch,
        title: "RAG security assistant",
        text: "Answers from the organization's approved procedures with source references, and says when the sources are insufficient. Exact counts come from permission-checked queries, not the model.",
        status: "PLANNED",
      },
      {
        icon: BrainCircuit,
        title: "Guided incident investigation",
        text: "One bounded LangGraph workflow: gather evidence and policy, summarise, recommend, then pause for Supervisor or Admin approval before any action. Every step is audited.",
        status: "PLANNED",
      },
    ],
  },
];

export const PIPELINE: Array<{ step: string; title: string; text: string; status: Availability }> = [
  {
    step: "01",
    title: "Map the site",
    text: "Organization → campus → building or outdoor perimeter → floor → area. Cameras and Guards are linked to these locations.",
    status: "AVAILABLE",
  },
  {
    step: "02",
    title: "Detect",
    text: "Workers sample video, detect and track, and send detections to the Core API with a shared token. Repeats are merged into one alert.",
    status: "PARTIAL",
  },
  {
    step: "03",
    title: "Route",
    text: "The alert goes to the Guards stationed at that location. Admins and Supervisors can escalate it to the whole campus, a location or chosen Guards.",
    status: "AVAILABLE",
  },
  {
    step: "04",
    title: "Respond",
    text: "Guards acknowledge, confirm they are responding and report whether the situation is under control or more Guards are needed.",
    status: "AVAILABLE",
  },
  {
    step: "05",
    title: "Review & record",
    text: "Staff resolve or dismiss with a reason. Every change is recorded in the organization's audit trail.",
    status: "AVAILABLE",
  },
];

export const HIERARCHY = ["Organization", "Campus", "Building / Perimeter", "Floor", "Area / Zone", "Camera"];

export const ROLES: Array<{ icon: LucideIcon; title: string; where: string; status: Availability; points: string[] }> = [
  {
    icon: Building2,
    title: "Organization Admin",
    where: "Web app",
    status: "AVAILABLE",
    points: [
      "Registers the organization and becomes its Main Admin",
      "Adds Supervisors and Guards; deactivates accounts",
      "Manages buildings, cameras, personnel and restricted-area access",
      "Handles alerts and reviews manual reports",
    ],
  },
  {
    icon: Users,
    title: "Supervisor",
    where: "Web app",
    status: "AVAILABLE",
    points: [
      "Runs day-to-day operations of one campus",
      "Resolves, dismisses and escalates alerts",
      "Manages floors, areas, cameras and Guard posts",
      "Reads personnel basics; changes are reported to the Admins",
    ],
  },
  {
    icon: ShieldAlert,
    title: "Guards",
    where: "Mobile app",
    status: "PARTIAL",
    points: [
      "Stationed Guards receive the alerts of their post automatically",
      "Standby Guards form the campus-wide quick-response pool",
      "Sign in with their mode; a changed assignment signs them out",
      "Acknowledge, respond, chat and file manual reports",
    ],
  },
];

export const SECURITY: Array<{ title: string; text: string; status: Availability }> = [
  {
    title: "Tenant and campus isolation",
    text: "PostgreSQL Row-Level Security keeps every organization's data apart, and every campus's operations within it. The API checks again.",
    status: "AVAILABLE",
  },
  {
    title: "Email codes and step-up checks",
    text: "Every organization sign-in ends with an emailed code. Creating staff accounts needs the Admin's password and a code, then the new user's code.",
    status: "AVAILABLE",
  },
  {
    title: "Audit trails without secrets",
    text: "Alerts, staff changes and platform actions are recorded with field names, never passwords, codes or tokens.",
    status: "AVAILABLE",
  },
  {
    title: "Sessions that end when they should",
    text: "A password change, a deactivated account, a suspended organization or a changed Guard assignment ends existing sessions.",
    status: "AVAILABLE",
  },
  {
    title: "Separate platform administration",
    text: "The platform's Super Admin uses a separate app and database role, and cannot see any organization's footage or records.",
    status: "AVAILABLE",
  },
  {
    title: "Recordings and evidence",
    text: "Routine recordings expire after the retention period; footage kept as evidence for an incident is protected separately.",
    status: "PLANNED",
  },
];

export const STACK: Array<{ layer: string; tech: string; status: Availability }> = [
  { layer: "Web apps", tech: "React · Vite · TypeScript (organization app, platform app)", status: "AVAILABLE" },
  { layer: "Core API", tech: "Python · FastAPI · Clean Architecture", status: "AVAILABLE" },
  { layer: "Database", tech: "PostgreSQL 16 · Row-Level Security · Alembic", status: "AVAILABLE" },
  { layer: "Vision workers", tech: "OpenCV · ONNX Runtime · YOLOX-Tiny · tracking", status: "PARTIAL" },
  { layer: "Guard mobile app", tech: "React Native", status: "PLANNED" },
  { layer: "Messaging & storage", tech: "RabbitMQ · object storage · Docker Compose", status: "PLANNED" },
];
