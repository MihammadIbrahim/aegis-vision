AI-Powered CCTV Surveillance & Security Management System

Final FYP Proposal & Master Requirements Specification
Version 1.0 | Architecture, Technology Stack, AI Scope, Functional Requirements, and Implementation Plan

# Executive Summary

This Final Year Project (FYP) will develop an AI-powered CCTV surveillance and security management platform for organizations such as universities, companies, offices, warehouses, and industrial facilities. The system will combine camera monitoring, computer-vision analysis, alert handling, investigation tools, a Guard mobile app, a retrieval-augmented generation (RAG) security assistant, and a controlled agentic incident-investigation workflow.

The system is intended to support trained security staff. AI outputs are decision-support signals, not proof that a person is dangerous or guilty. Uncertain identity matches and detections must be reviewed by an authorized human.

## 1. Confirmed Product Scope

- Organizational use only; private homes are outside the initial scope.
- Three user-facing applications: Platform Super Admin web app, Organization Admin/Supervisor web app, and Guard mobile app.
- Shared FastAPI backend, PostgreSQL database, and Python AI services/workers.
- Core computer vision: human detection, single-camera tracking, cross-camera person re-identification, weapon detection, and fight/violence detection.
- Optional, separately controlled restricted-area face-recognition module.
- RAG Security Assistant for approved policies, manuals, and authorized incident information.
- One controlled agentic workflow for incident investigation, with human approval before consequential actions.
- CCTV camera management, alert routing, guard response, incident chat, manual reports, recordings, evidence, audit logs, and organization/branch isolation.
- Initial development using public datasets and recorded videos, followed by planned tests with two IP cameras.

## 2. Architecture Decision

### Selected architecture

**Modular, event-driven microservices architecture, organized using Clean Architecture principles.**

These are complementary concepts, not three competing architectures:

- **Microservices / service-oriented separation:** separates workloads with different needs, especially computer-vision inference, API/business operations, and RAG/agent workflows. Services are split only where independent processing or deployment is useful.
- **Event-driven processing:** long-running video analysis and incident workflows run asynchronously. Services publish and consume events such as `camera.frame_available`, `ai.detection_created`, `alert.created`, `alert.acknowledged`, and `investigation.summary_requested`.
- **Clean Architecture:** keeps business rules and domain logic separate from HTTP routes, database implementation, AI libraries, and external providers. This improves testing and makes components easier to replace.

### Why this fits this project

The system may process video from several cameras and run multiple AI tasks. Video inference can be CPU/GPU intensive and should not block normal API requests. Queues allow work to be scheduled, retried, and monitored. Separating AI workers also makes it possible to scale a heavy task independently when resources are available. Clear business boundaries and contracts help keep the three apps, backend, and AI components consistent.

### Practical implementation boundary

This is a student FYP, so the design must remain achievable. Start with a small number of clearly defined deployable services, not dozens of microservices. Use Docker Compose for local development and integration. Kubernetes is the planned orchestration/learning target for a containerized deployment, but it should only be used for the final demonstration if the team has enough time and a suitable cluster. If not, Docker Compose remains the reliable baseline and Kubernetes deployment can be documented as a deployment extension. Do not claim Kubernetes deployment is complete unless it has actually been deployed and tested.

### Proposed service boundaries

1. **Core API service (FastAPI):** authentication, authorization, organization/site hierarchy, cameras, users, alerts, incidents, settings, audit records, and API contracts.
2. **Video ingestion service:** reads supported RTSP streams or recorded video, extracts frames, and publishes processing tasks.
3. **Computer-vision workers:** run detection, tracking, re-identification, weapon detection, and violence analysis. Workers may be separate processes or containers; split them into independent services only when practical.
4. **Event/notification worker:** consumes validated events, applies alert-priority and recipient rules, records status changes, and sends notifications.
5. **RAG/LLM service:** retrieves approved documents and authorized incident data, generates grounded answers, and returns source references.
6. **Agentic investigation workflow:** orchestrates a limited, stateful sequence of retrieval, summarization, recommendation, approval, and permitted action.
7. **PostgreSQL:** stores structured application data and audit metadata. `pgvector` may be used for document embeddings if chosen during implementation.
8. **Object/video storage:** stores recordings, event clips, snapshots, and evidence separately from ordinary relational records. Azure storage is the proposed cloud option.
9. **Frontend applications:** Platform Super Admin web app, shared Organization Admin/Supervisor web app, and Guard mobile app.

Services must communicate through documented API contracts and versioned event schemas. Queue technology is proposed as **RabbitMQ** for the initial implementation; it can be replaced if deployment constraints require it. Avoid introducing Kafka unless measured throughput or a clear requirement justifies its additional operational cost.

## 3. Technology Stack

The following is the planned stack. A technology is not considered implemented merely because it appears in this proposal.

| Area | Planned technology | Purpose |
|---|---|---|
| Platform Super Admin web app | React + Vite | Platform-level organization and service administration |
| Organization Admin/Supervisor web app | React + Vite | CCTV monitoring, cameras, users, alerts, investigations, settings, and AI assistant |
| Guard mobile app | React Native | Guard alerts, response status, assistance requests, incident chat, and manual reports |
| Backend API | Python + FastAPI | REST APIs, authentication, authorization, business rules, and integration |
| Database | PostgreSQL | Users, organizations, locations, cameras, alerts, incidents, settings, and audit records |
| Vector search (if selected) | pgvector extension for PostgreSQL | Similarity search over approved policy/document chunks |
| Computer vision | Python, OpenCV, PyTorch and/or compatible model runtimes | Video/frame handling and model inference; final library/model choices follow benchmarking |
| Model approach | Pretrained models first; fine-tuning only if justified | Reduce training burden while evaluating performance on selected data |
| RAG framework | LangChain or equivalent | Document loading, chunking, retrieval, and LLM integration |
| Agent workflow | LangGraph | Stateful multi-step investigation workflow and approval checkpoints |
| Event/message queue | RabbitMQ (proposed) | Asynchronous AI tasks and domain events |
| Email OTP | Brevo | Email verification and authentication OTP delivery |
| Containers | Docker | Reproducible service packaging and local development |
| Local integration | Docker Compose | Run and test the initial multi-service setup |
| Container orchestration | Kubernetes (planned/conditional deployment target) | Deploy and manage containers if time and infrastructure allow |
| Cloud platform | Microsoft Azure | Proposed cloud hosting, managed resources, and video/evidence storage |
| Video/evidence storage | Azure Blob Storage or an equivalent approved object store | Recordings, clips, snapshots, and evidence; exact retention/storage configuration to be finalized |
| Version control | Git + GitHub | Source control, branches, issues, code review, and project collaboration |
| API documentation | FastAPI OpenAPI/Swagger | Inspect and test API contracts |
| Testing | Pytest plus frontend/mobile test tooling selected by the team | Unit, integration, permission, AI evaluation, and end-to-end tests |
| Camera connection | RTSP; ONVIF where supported by equipment and library | Connect compatible IP cameras |
| Configuration/secrets | Environment variables and deployment secrets; never commit secrets | Keep credentials and provider keys out of source control |

### Azure and Kubernetes decision

Microsoft Azure, Docker, Kubernetes, and GitHub are part of the intended technology plan, but they serve different purposes. Azure is the cloud platform; Docker packages services; Kubernetes orchestrates containers; GitHub manages source code and collaboration. They are not interchangeable.

For a manageable FYP, the baseline must run locally with Docker Compose. The team can then deploy to Azure. Kubernetes is an additional deployment responsibility and must not delay the core AI and security workflows. Choose an Azure service and budget after estimating GPU/video requirements; do not assume that every Azure resource or a GPU VM is free.

## 4. AI Fields and Exactly How They Are Used

### Layer 1 — Core Computer Vision

**A. Human detection**
- Input: video frames from a recorded clip or supported camera stream.
- Task: locate people and return bounding boxes, confidence scores, camera ID, and timestamp.
- Approach: evaluate suitable pretrained object-detection models first; fine-tune only if the chosen dataset and evaluation results justify it.
- Output: person observations for tracking and investigation.
- Evaluation: precision, recall, mAP where appropriate, false-positive examples, and inference speed.

**B. Single-camera multi-object tracking**
- Input: detections from consecutive frames of one camera.
- Task: maintain temporary track IDs as people move through that camera's view.
- Output: track ID, frame/time range, and trajectory within that camera.
- Important: a track ID is local to its tracking context; it is not a verified real-world identity.
- Evaluation: ID switches, tracking continuity, and appropriate tracking metrics on selected test clips.

**C. Cross-camera person re-identification (Re-ID)**
- Input: person crops/appearance features from different cameras.
- Task: rank possible appearance matches across cameras and time windows.
- Output: candidate matches with similarity/confidence, camera, timestamp, snapshot, and clip where available.
- Important: appearance similarity is not proof of identity. Low-confidence matches must be marked uncertain and reviewed by an authorized Admin/Supervisor.
- Evaluation: retrieval/ranking metrics such as Rank-k and mAP where the dataset supports them, plus false-match review.

**D. Weapon detection**
- Input: video frames.
- Task: detect configured weapon classes supported by the chosen model/dataset.
- Output: candidate detection and a high-priority alert for authorized review.
- Important: the model can produce false positives and misses. The system must retain the relevant frame/clip and confidence information where available.
- Evaluation: precision, recall, per-class results, false alerts, and performance under different lighting/camera angles.

**E. Fight/violence detection**
- Input: a sequence of video frames or a clip, not just one isolated image when temporal context is required.
- Task: identify possible fighting/violent activity according to the selected model.
- Output: candidate event, time interval, camera/location, confidence, and event clip where available.
- Important: a model prediction is a signal for human review, not a final factual/legal conclusion.
- Evaluation: precision, recall, false alarms, missed events, and clip-level latency.

**F. Optional restricted-area face recognition**
- Purpose: compare faces in a specifically configured restricted area with authorized-person records.
- Status: optional and feasibility-dependent; separate from the core CV scope.
- Safeguards: restricted access, documented purpose, suitable data handling, human review of uncertain/unknown results, and no claim of identity when image quality or confidence is insufficient.
- Final acceptance and role permissions must be agreed before this module is treated as in-scope.

### Layer 2 — RAG-Based Security Assistant

**Purpose:** answer security questions using approved organizational documents and data that the current user is permitted to access.

**Initial knowledge sources**
- Approved security manuals and operating procedures.
- Organization-approved policy documents.
- Authorized incident/alert records retrieved through permission-checked database tools.

**Processing flow**
1. Admin-authorized documents are ingested.
2. Text is extracted and divided into searchable chunks with document metadata.
3. Embeddings are generated and stored in a vector index, potentially using PostgreSQL with pgvector.
4. The user's question is authenticated and checked against organization/site permissions.
5. The system retrieves relevant document chunks or runs a controlled database query for structured facts.
6. An LLM produces an answer grounded in retrieved material.
7. The response includes document/source references when applicable and clearly indicates when evidence is insufficient.

**Example questions**
- “What is the approved procedure when a weapon alert occurs?”
- “Summarize unresolved high-priority incidents from the last 24 hours that I am authorized to view.”
- “Which approved policy explains the escalation process?”

**Important design rule:** RAG is not the right tool for every query. Exact counts, statuses, and timestamps should come from permission-checked database queries. Video interpretation belongs to the computer-vision pipeline. The chatbot must not bypass role or organization isolation and must not invent a source when retrieval fails.

**Evaluation:** answer correctness against a prepared question set, source-grounding/citation correctness, retrieval relevance, permission-denial tests, and hallucination/unsupported-answer checks.

### Layer 3 — Agentic Incident Investigation

**Purpose:** coordinate a limited investigation process using controlled tools. LangGraph is the proposed workflow framework; it is not itself an AI model.

**Initial workflow**
1. Receive an eligible AI-generated alert or a user-requested investigation.
2. Verify the caller's role and scope.
3. Retrieve the incident record, camera/location metadata, relevant clip/snapshot references, and related authorized events.
4. Retrieve relevant approved policy guidance.
5. Generate an evidence-linked incident summary.
6. Recommend next steps based on available evidence and policy.
7. Pause for Supervisor/Admin review before consequential actions.
8. After approval, execute only explicitly authorized actions, such as updating an incident status or sending an approved notification.
9. Record tool calls, approval, actor, timestamp, result, and errors in the audit trail.

**Safety and reliability rules**
- No autonomous accusation, punishment, law-enforcement claim, or identity confirmation.
- No access to a camera, clip, incident, or organization outside the user's permissions.
- No consequential action without the required human approval.
- Tools must validate inputs and enforce authorization on the backend.
- Handle retries idempotently to avoid duplicate alerts or actions.
- Keep LLM output, retrieved evidence, recommendations, approval, and final action separately identifiable in the audit record.
- If information is missing or conflicting, the workflow must state that limitation and request human review.

**Evaluation:** correct tool selection, summary quality against reviewed examples, evidence/source linkage, permission enforcement, safe pause/approval behavior, audit completeness, and duplicate-action prevention.

### How the three AI layers work together

Computer vision produces candidate observations/events from footage. The backend validates and stores those events and creates alerts according to configured rules. RAG retrieves approved policies and relevant authorized records when users ask questions. The agentic workflow coordinates investigation steps and requests approval for permitted actions. The Guard app and web dashboards remain the operational interfaces.

## 5. AI Scope Priority and Feasibility

| Priority | Feature | Delivery approach |
|---|---|---|
| Core | Human detection | Pretrained model, evaluated on selected data |
| Core | Single-camera tracking | Integrate tracker with human detections |
| Core | Weapon detection | Pretrained model/dataset evaluation; fine-tune only if needed |
| Core | Fight/violence detection | Evaluate a suitable clip-based model and document limitations |
| Core | Cross-camera Re-ID | Implement candidate ranking and human verification; scope depends on dataset quality |
| Added AI feature | RAG Security Assistant | Implement document ingestion, retrieval, source-linked answers, and access controls |
| Added AI feature | Agentic incident-investigation workflow | Implement one bounded workflow with explicit approval and audit trail |
| Optional | Restricted-area face recognition | Only if core scope is stable and privacy/feasibility review is completed |

These priorities describe intended scope, not evidence that the features already work. A working subset with honest evaluation is preferable to claiming every AI capability without tests. If schedule or compute becomes a constraint, reduce the number of live simultaneous models before sacrificing access controls, test evidence, or the investigation workflow's human approval safeguards.

## 6. Model Development and Evaluation Rules

- Begin with pretrained models and suitable public datasets/recorded video.
- Verify dataset terms, licensing, class labels, and suitability before use.
- Separate training/validation/test data appropriately to avoid evaluating on training examples.
- Record model name/version, dataset/version, configuration, threshold, hardware, and test results.
- Measure task-appropriate quality and runtime; do not use a single generic “accuracy” number for every task.
- Record false positives, false negatives, low-light cases, occlusion, camera angle, and other known limitations.
- Test each model independently before integrating it into the event pipeline.
- Do not promise a numeric acceptance threshold until the team has defined the test dataset, measurement method, and target.
- Human review is required for uncertain identity matches and security-critical detections.

## 7. Implementation Phases

**Phase 0 — Freeze requirements and contracts**
- Confirm role/permission matrix and unresolved decisions.
- Define API request/response schemas, event names, status enums, database entities, and acceptance tests.
- Create GitHub repository, issue board, branching rules, and environment-variable template.

**Phase 1 — Foundation**
- Build FastAPI project structure, PostgreSQL schema, authentication/OTP, role checks, organization/site isolation, and audit logging.
- Create the React web apps and React Native app shells.
- Start with Docker Compose and documented local setup.

**Phase 2 — Camera and core CV**
- Process recorded clips first.
- Integrate human detection and single-camera tracking.
- Add weapon and violence inference, event metadata, and initial evaluation.
- Add cross-camera Re-ID candidate search and human verification.
- Integrate RTSP/ONVIF where supported and test two IP cameras.

**Phase 3 — Event and security operations**
- Add message queue, alert lifecycle, location-based recipients, guard acknowledgements, escalation, incident chat, manual reports, clips/evidence, retention, and audit logs.

**Phase 4 — RAG assistant**
- Ingest approved policy documents, implement retrieval and source-linked answers, and test access isolation and unsupported questions.

**Phase 5 — Agentic workflow**
- Implement one incident-investigation workflow with controlled tools, approval checkpoint, retries, and audit trail.

**Phase 6 — Deployment and final evaluation**
- Deploy the tested baseline to the selected Azure setup.
- Use Kubernetes only if the team has the resources and time to validate it; otherwise retain Docker Compose as the demonstrated deployment and document Kubernetes manifests as an extension.
- Run role/security tests, AI evaluation, mobile/web integration tests, and a final demo rehearsal.
- Publish limitations and results in the report.

## 8. Deliverables

- Requirements and architecture documentation.
- Role/permission matrix and API/event contracts.
- React Platform Super Admin web app.
- React Organization Admin/Supervisor web app.
- React Native Guard app.
- FastAPI backend and PostgreSQL schema/migrations.
- Versioned AI inference modules and evaluation results.
- RAG assistant with approved-source references.
- One agentic investigation workflow with human approval and audit trail.
- Camera/recording integration and a documented two-camera test if equipment is available.
- Docker setup, environment template, GitHub repository, deployment instructions, and tests.
- Final report containing measurable results, limitations, dataset/model attribution, and future work.

## 9. Remaining Decisions Before Implementation

The existing requirements include unresolved items that must be finalized rather than silently guessed. The key ones are:
- Exact Main Admin versus Branch Admin permissions.
- Organization activation/suspension controls and duplicate-organization checks.
- Who may regenerate another user's ID.
- Historical audit-data treatment after permanent deletion.
- Evidence deletion permissions and approval.
- Authorized-person record permissions if face recognition is included.
- Timeout/escalation rules for unacknowledged alerts.
- Numerical AI acceptance thresholds and test protocol.
- Azure region/resources, video storage limits, GPU/CPU capacity, and budget.
- Final API routes, event payload schemas, database field names, and status values.
- RAG model/provider, approved document types, retention, and prompt-injection defenses.
- Agent tool allowlist, approval requirements, and permitted state-changing actions.
- Final feature-by-feature pass/fail acceptance criteria.

These decisions should be tracked in a decision log. Until decided, mark them **TBD**, assign an owner, and do not build hidden assumptions into the implementation.

# Detailed Functional Requirements (Preserved and Extended)

AI-Powered CCTV Surveillance & Security Management System

Complete FYP Requirements — Revised Master Specification

This document consolidates the project requirements, including the
updated Guard login, password reset, alert escalation, incident chat, and
audit-log visibility rules. It is intended to be shared with the teacher,
team, or AI coding agent.

Confirmed functional requirements are stated directly. Technical
parameters and acceptance criteria that still require measurable choices
remain identified separately rather than being silently assumed.

1. Project Purpose

We will build an AI-Powered CCTV Surveillance & Security Management
System to help with security monitoring in organizations and agencies.

The system may be used in companies, universities, offices, warehouses,
industrial facilities, and other organizational security locations. The
project does not focus on private homes.

The main purposes of the system are to:

-   Monitor CCTV cameras.
-   Detect and track humans in camera footage.
-   Find possible matches for a person appearing across different
    cameras.
-   Help detect weapons and fights/violence.
-   Generate and manage security alerts.
-   Show Guards alerts relevant to their assigned locations.
-   Help Admins and Supervisors investigate incidents.
-   Manage recordings and important evidence.
-   Maintain a record of every important activity.
-   Manage organizations, their branches/workspaces, users, cameras, and
    permissions.

AI results will help the security team with investigations. The system
must not automatically declare a person a criminal or treat an uncertain
AI result as a confirmed identity.

The project will not include a door-unlocking feature. Theft detection
is also not included in the core AI modules.

2. Technology Stack and Applications

The software will be divided into different applications, but their
backend and data structures will remain consistent.

Platform Super Admin App

React + Vite

For managing the platform and organizations.

Organization Admin + Supervisor App

React + Vite

A shared web application for CCTV, alerts, users, investigations,
settings, and organizational operations.

Guard App

React Native

For receiving alerts related to assigned locations and submitting manual
reports.

Shared Backend and AI Services

FastAPI + PostgreSQL + Python Computer Vision / Deep Learning

For authentication, permissions, records, camera-related processing, AI
detection, and communication between applications.

Additional Infrastructure and AI Frameworks

- Git and GitHub will be used for version control, issues, collaboration, and code review.
- Docker and Docker Compose will provide reproducible service packaging and local integration.
- Microsoft Azure is the proposed cloud platform for hosting and object storage, subject to budget and resource validation.
- Kubernetes is a planned deployment/orchestration target only if the team has sufficient time and a suitable cluster. Docker Compose is the baseline development setup; Kubernetes must not be claimed as deployed until tested.
- RabbitMQ is the proposed initial message broker for asynchronous processing and event delivery.
- LangChain (or an equivalent framework) is proposed for RAG retrieval and LLM integration.
- LangGraph is proposed for the bounded agentic investigation workflow.
- PostgreSQL with pgvector may be used for vector search if confirmed during implementation.
- Python/OpenCV and a suitable deep-learning runtime/model library will support video processing and inference. Exact model/library choices will follow benchmarking.

Email Verification

Brevo email service

For email OTP and related verification flows.

The initial plan is to use managed storage for video footage and
evidence. The system should have a scalable structure for future
organizational deployments and possible SaaS/on-premises use.

Consistency rule: IDs, field names, account statuses, alert statuses,
and data structures must match across the frontend apps, backend, and
database. A field or status must not have a different name in one
application from the contract used by another application.

2A. Architecture Decision and Implementation Guidance

The selected design is a modular, event-driven microservices architecture using Clean Architecture principles.

- Microservices/service separation isolates different workloads such as core APIs, video ingestion, computer-vision inference, event/notification processing, RAG, and the investigation workflow.
- Event-driven processing allows video analysis and incident workflows to run asynchronously so long-running inference does not block normal API requests.
- Clean Architecture separates business rules from API routes, database access, model libraries, and external providers.

For the FYP, start with a small number of deployable services. Do not create dozens of microservices. Use versioned API contracts and event schemas. RabbitMQ is the proposed queue; the team may revise this after a documented technical review. Run the baseline with Docker Compose. Azure deployment is planned; Kubernetes deployment is conditional on time, infrastructure, and successful testing.

Illustrative event flow:
Camera/recorded video → video ingestion → AI task queue → CV worker → validated detection event → alert/event service → PostgreSQL + clip/evidence storage → web/mobile notification. The RAG assistant and investigation workflow use permission-checked backend tools and do not bypass the normal authorization layer.

3. Organization and Location Structure

The system will support multiple organizations and their
branches/workspaces.

The location structure will be:

Organization → Workspace / Site / Branch → Building or Outdoor Perimeter
→ Optional Floor → Area / Zone → Camera

Not every level is required for every organization. For example, an
outdoor perimeter will not have a floor.

In the location setup:

-   Relevant sites, branches, or workspaces will be created within an
    organization.
-   Each building or outdoor perimeter can be identified.
-   Floors can be added where applicable.
-   Areas/zones can be defined within buildings and outdoor locations.
-   A camera will be linked to its appropriate location.
-   Boundaries can be identified as north, south, east, west, or by a
    custom name.
-   A boundary can be labelled as inside/outside.
-   Custom area and boundary names will also be supported.

When creating or assigning a Guard, locations can be selected through
dropdowns, such as building, applicable floor, and area/zone.

Each user will only be able to access data for the organization,
workspace, site, or branch for which they have permission.

4. Platform Super Admin

The Super Admin will be a platform-level role. Its job is to manage the
overall platform and organizations, not to conduct operational CCTV
monitoring for organizations.

What can the Super Admin do?

-   View the total number of organizations.
-   View the list of organizations and relevant details.
-   Edit/update Organization Admin information.
-   Manage platform AI availability and settings.
-   View platform/system health information.
-   View platform audit logs.
-   Re-enter their own password when making sensitive changes.

Super Admin restrictions

-   Cannot access an organization’s live CCTV feeds.
-   Cannot access organizations’ recordings.
-   Cannot view an Organization Admin’s existing password.
-   Password changes or resets will use a secure process.

The Super Admin will log in using username + password. Email OTP or
authenticator-based MFA will not be required at login.

5. Organization Admin and Main Admin

The first Admin of an organization will be the Main Admin by default.

The Main Admin will play an important role in organization operations
and administration related to approved branches/workspaces.

Confirmed functions of the Organization Admin:

-   Update their complete profile information.
-   Create, update, and remove Supervisors.
-   Create, update, and remove Guards.
-   Add, edit, and remove CCTV cameras.
-   Manage the organization’s AI configuration.
-   Manage per-camera AI module settings.
-   Manage alerts and investigations.
-   Use person search.
-   Set the normal footage retention duration.
-   Manage authorized evidence.
-   View user information and status records.
-   View audit/log history of Supervisor and Guard activities.
-   Receive notifications about relevant Guard-related activities
    performed by Supervisors.
-   Restore an old Guard or Supervisor account through rejoining,
    according to their role permissions.
-   Permanently delete saved user information within their permitted
    authority.

The Main Admin will be authorized to approve branch/workspace requests,
as described in the organization onboarding section.

The Admin’s own actions must be recorded in audit logs. Notifications do
not need to be sent to other users for the Admin’s own actions.

6. Supervisor

The Supervisor will work with the Admin in the organization’s security
operations, but will have permissions different from the Admin’s.

What can the Supervisor do?

-   Update their complete profile information.
-   Create, edit, and remove Guards in the authorized
    organization/workspace.
-   Assign Guards to relevant locations.
-   Update permitted Guard information.
-   Add, edit, and remove cameras.
-   Manage permitted settings in the organization’s AI configuration.
-   Enable/disable permitted AI modules on individual cameras.
-   Manage alerts.
-   Manage investigations and person search.
-   Change the normal footage retention duration.
-   Manage authorized evidence.
-   Complete the Guard rejoining process.
-   Perform security operations according to their permitted settings
    and assigned responsibilities.

What can the Supervisor not do?

-   Cannot create the first or any new Supervisor account themselves.
-   Cannot rejoin another Supervisor.
-   Cannot permanently delete anyone’s saved information.
-   Cannot use the Main Admin’s branch/workspace approval controls.
-   Cannot access data outside their authorized organization/workspace.
-   Cannot perform Admin-level operations beyond their permissions.

How will the Admin know about Supervisor activity?

When a Supervisor creates, updates, or removes Guard information:

1.  The activity will be recorded in audit logs.
2.  The Admin will receive a relevant notification.
3.  The Admin will be able to view the activity on the Logs page.

Supervisor activity will be recorded, but passwords and OTPs will never
be recorded.

7. Guard and Guard App

A Guard is an operational security staff user. Their account will be
created by an Admin or Supervisor.

Guard functions

-   Log in using their unique Guard ID or registered email and password.
-   Complete email OTP verification after entering their credentials.
-   Reset their own forgotten password through email OTP without Admin or
    Supervisor approval.
-   Receive alerts according to their location assignment and the alert
    escalation rules.
-   Acknowledge an alert and select a response option, such as
    “Under Control” or “Additional Guards Required.”
-   Confirm when they are responding to an escalated alert.
-   Participate in the incident-specific group chat with the relevant
    Guards, Supervisor, and Admin.
-   Submit manual security reports.
-   Use their permitted app functionality.

A Guard cannot edit their own profile details or change their assigned
location. An Admin or Supervisor will manage those details. A Guard may
change their own password through the secure forgot-password process.

A Guard will only receive organizational data and alerts that they are
authorized to access. Location assignment is optional; when a Guard has
no location assignment, the system will not assume that they should
receive every alert. The Admin/Supervisor must explicitly target that
Guard or include them through an authorized escalation.

Difference between a manual report and an AI alert

A report submitted manually by a Guard will be a separate record from an
AI-generated alert. They must not be treated as the same type of event.

What happens if the internet is unavailable?

The Guard app should support queuing offline manual reports and retrying
them when the connection returns. Idempotency handling should also
prevent duplicate submissions.

However, when the device is offline, the system will not claim that
real-time server alerts are being received at that time.

8. Organization Registration and Branch Approval

Official email verification will be mandatory during organization
registration. The organization’s phone number will be a required field,
but it will not be verified through SMS/phone OTP.

Initial setup for a new organization

1.  The organization will enter its details.
2.  An official organization email must be provided.
3.  An OTP will be sent to the official email.
4.  After the email OTP is verified, the initial organization
    registration process will continue.
5.  The organization’s first Admin will be the Main Admin.

The organization’s official phone number will be recorded, but it will
not be verified through phone OTP.

Branch/workspace request for an existing organization

The sign-in page will have an “Already Registered Organization?” option
below the form.

When someone wants to register a new branch, campus, site, or workspace
under an existing organization:

1.  They will enter the exact name of the original organization—the same
    spelling entered by the first Admin.
2.  They will enter their Admin details.
3.  They will provide an official email.
4.  They will enter the proposed branch/campus/site name.
5.  They will verify the official email OTP.
6.  Even after verification, they will not receive dashboard access
    until the Main Admin approves the request.

Main Admin approval

The Main Admin’s dashboard will have a dedicated page where:

-   Pending branch/workspace requests will be displayed.
-   Proposed branch/workspace details will be displayed.
-   Relevant details of the proposed Admin will be displayed.
-   The Main Admin can approve the request.

The Branch Admin will be able to:

-   View their verification/approval status.
-   View the relevant branch list and permitted details.
-   Not approve any request.
-   Not access the branch dashboard before approval.

After approval, the Branch Admin will manage only their approved
workspace/branch. Merely appearing under the organization name or in its
list will not grant additional permissions.

Important: The exact duplicate-organization detection rules and the
secure method for verifying an applicant’s association with an existing
organization have not yet been finalized. Entering the exact name alone
will not be enough to grant access.

9. Account Creation, Profile Updates, and Authentication

Each role will have separate account and login rules.

9.1 Admin, Supervisor, and Guard login

For Organization Admins, Supervisors, and Guards, every login will
require:

-   Registered email or role-specific login identifier, as applicable.
-   Password.
-   Email OTP after the credentials are accepted.

This rule applies to every login, not only the first login.

Guard login is different from organization registration: a Guard will not
be asked to enter the organization name. The Guard will enter their
unique Guard ID or registered email and password, then proceed to the
email OTP verification page.

The exception is the Platform Super Admin, who will log in using
username + password and will not require email OTP.

9.2 Supervisor account creation

Only the Organization Admin can create a Supervisor account.

The Admin will enter the following information for the Supervisor:

-   Name
-   Username
-   Email
-   Phone number

The system will generate a unique Supervisor ID.

During account creation:

1.  An OTP will be sent to the Supervisor’s email.
2.  The Admin will enter that OTP to complete verification.
3.  The Admin will verify their own password and OTP because creating a
    Supervisor is an Admin-authorized operation.
4.  Once account creation is complete, the Supervisor will set their
    password through the secure password setup process.

Only the Admin can update the Supervisor’s information and remove their
account.

9.3 Guard account creation

An Admin or Supervisor can create a Guard account.

The creator will enter the relevant details and assign a location using
dropdowns. The system will generate a unique Guard ID.

The process will be:

1.  An OTP will be sent to the Guard’s email.
2.  The authorized creator will enter the OTP to complete verification.
3.  The acting Admin/Supervisor will verify their own password and OTP
    to create the Guard account.
4.  The Guard will set their password through the secure setup process.

The Admin and Supervisor can update Guard details or remove the account,
but removal will not mean that historical data is deleted.

9.4 Updating one’s own profile

Both Admin and Supervisor can update their complete profile information.

A profile update will require verification using the current password
and OTP. If a Supervisor is only changing their own password, they will
still verify the current password and OTP.

When changing an email address, verification of the new email address
will be mandatory.

A displayed system-generated ID will not be edited manually. A dedicated
ID regeneration process will be used to change it.

9.5 Guard profile updates

A Guard cannot edit their own profile.

An Admin or Supervisor can update Guard information according to the
authorized rules. For normal Guard create/edit/remove operations, the
acting Admin/Supervisor must verify their password and OTP, except for
ID regeneration and the specially defined rejoining confirmation rules.

9.6 Forgot password

The forgot-password process will:

-   Be available from the sign-in page.
-   Also be available from the dashboard after login.
-   Reset the password through an OTP sent to the account’s registered
    email address.
-   Not require Admin approval.
-   Be available to Organization Admins, Supervisors, and Guards for
    their own accounts, subject to secure identity verification.

Guards can reset their own password without asking an Admin or
Supervisor to perform the reset. This does not give Guards permission to
edit other profile information or account settings.

No Admin or Supervisor will be able to view users’ actual passwords.
Passwords will be stored using a secure password-hashing method, and
password setup/reset will use a secure process.

10. System-Generated IDs and ID Regeneration

The system will assign a unique displayed ID to each user.

  Role         ID format
  ------------ --------------------------
  Admin        ADM- + 11 numeric digits
  Supervisor   SUP- + 11 numeric digits
  Guard        GRD- + 11 numeric digits

These are example formats. Actual IDs will be generated uniquely.

ID rules

-   The displayed ID will be unique.
-   The internal database identifier will remain stable.
-   When a displayed ID is regenerated, historical alerts, incidents,
    assignments, and audit logs will remain linked to the same person.
-   A Guard cannot regenerate their own ID.
-   An Admin or Supervisor can regenerate a Guard’s ID through the
    authorized process.
-   An Admin can regenerate their own displayed ID.
-   A Supervisor can regenerate their own displayed ID through Settings.
-   ID regeneration requires the acting Admin/Supervisor’s own password,
    but not OTP.
-   ID regeneration will not link history to a new user.
-   When a Supervisor’s ID is regenerated/changed, the activity will be
    recorded in Admin logs and the Admin will receive a relevant
    notification.

It has not yet been specifically defined whether any role other than the
Admin will be allowed to regenerate another Supervisor’s ID. This
permission will not be granted automatically without a decision.

11. Rejoining — Reactivating a Former Guard or Supervisor

The system will have separate Add and Rejoining buttons. Rejoining is
intended to restore an existing registered user’s profile rather than
create a new duplicate profile.

11.1 Guard rejoining

Both Admin and Supervisor can rejoin a Guard.

1.  The authorized user will select the Rejoining button.
2.  They will enter the Guard’s existing Guard ID.
3.  The system will retrieve the Guard’s existing profile.
4.  Before confirming rejoining, the Admin or Supervisor may update
    permitted profile information.
5.  At the final Add/Rejoin confirmation, the acting Admin/Supervisor
    will enter their own password.
6.  Rejoining will not be completed without the password.
7.  After successful rejoining, the Guard’s existing account will become
    Active again.

The Guard’s old profile and history will be preserved. Their old ID and
existing records will continue to be used. Their password can be
securely updated/reset if needed.

11.2 Supervisor rejoining

Only the Admin can rejoin a Supervisor.

1.  The Admin will select Rejoining instead of Add Supervisor.
2.  They will enter the existing Supervisor ID.
3.  The system will retrieve the old profile.
4.  The Admin can update the details.
5.  At the final Add/Rejoin confirmation, the Admin will enter their own
    password.
6.  Rejoining will not be completed without the password.
7.  After successful rejoining, the existing account will become Active
    and its previous history will be preserved.

A Supervisor cannot rejoin another Supervisor.

11.3 Rejoining will not create a duplicate profile

If the same Guard or Supervisor joins again:

-   The existing profile will be used.
-   The existing user ID will be preserved.
-   Historical alerts, incidents, investigations, and relevant records
    will remain.
-   The password can be securely updated/reset.
-   The account status will be updated.
-   The person will regain login access.

The system will not automatically link old investigation history to an
uncertain match based only on the same name or email. The existing ID
and available identity information will be verified. If the identity
cannot be confirmed, Admin verification will be required.

12. User Removal, Historical Records, and Permanent Deletion

Account access and historical data will be treated separately.

When an account is removed

If a Guard is removed:

-   The Guard’s login access will be disabled.
-   Their existing profile information will remain saved on the Admin
    dashboard.
-   Previous alerts, incidents, and investigations will be preserved.
-   Related records and history will remain linked to the same person.
-   If the same Guard rejoins, their old profile will be restored; their
    history will not start over.

A Supervisor cannot permanently delete any user’s saved information,
whether it belongs to a Guard or another Supervisor.

Only the Admin has authority to permanently delete saved user
information. For permanent deletion, the Admin must re-enter their
password and confirm the deletion.

User history on the Admin dashboard

The Admin dashboard will retain records of registered/created users,
including relevant information and status. There will also be an option
to download user records.

Status information will track Active and Left. After successful
rejoining, the user’s status will become Active again.

Important distinction: Preventing login to an account and permanently
deleting saved information are two separate actions. Removing a Guard
will not automatically delete their investigation history.

13. CCTV Camera Management

Admins and Supervisors will manage cameras within their permissions.

Camera operations

-   Add a camera.
-   Edit camera details.
-   Remove a camera.
-   Link a camera to the correct organization/site/building/floor/area.
-   Manage camera AI configuration.
-   Enable/disable permitted AI modules on an individual camera.
-   View whether the camera is online/offline.

Information for each camera

The system will maintain the camera’s:

-   Camera ID
-   Display name
-   Associated location
-   Online/offline status
-   AI configuration

The CCTV dashboard will support grid view, available camera filters, and
fullscreen viewing of a camera.

A camera’s location will follow the defined hierarchy so that alerts and
Guard assignments can be associated with the relevant area.

Camera connection and testing

Real-time CCTV monitoring is a project target. Development will first
use public datasets and recorded video. After that, two IP cameras are
planned for initial physical testing.

RTSP/ONVIF support is targeted for camera connections; an Ethernet/PoE
connection is preferred.

Real-time AI processing or camera integration will not be claimed as
completed until it has been tested with actual equipment.

14. AI Modules

The project’s core AI functionalities will be as follows.

14.1 Human Detection and Single-Camera Tracking

Detect humans/persons in CCTV footage and track their movement within a
single camera’s view.

14.2 Cross-Camera Person Re-Identification

Compare observations of a person appearing on different cameras to
identify possible matches. An uncertain match must not be declared a
confirmed identity.

14.3 Weapon Detection

Detect potential weapons in CCTV footage according to the configured
detection model and help generate a relevant high-priority security
alert.

14.4 Fight/Violence Detection

Detect potential fights or violence in footage and help generate a
relevant high-priority alert.

14.5 Optional: Restricted-Area Face Recognition

An optional face-recognition capability for authorized persons in
restricted areas. This module will remain a separate optional feature,
distinct from the core required modules.

AI settings

-   The Organization Admin can manage the organization’s AI
    configuration.
-   The Supervisor can also manage permitted AI settings.
-   The Supervisor can toggle permitted AI modules on individual
    cameras.
-   Applicable AI configuration will be maintained separately for each
    camera.
-   The Platform Super Admin can manage platform AI
    availability/settings but will not access an organization’s live
    feeds or recordings.

Use of AI results

AI detections will provide alerts and observations for security staff to
investigate. The system must not conclusively declare a person’s
identity or criminal activity based on an uncertain result.

15. Alerts, Guard Response, Escalation, and Incident Chat

The system will create security alerts for relevant AI detections and
will also allow Guards to submit separate manual security reports.

15.1 Alert management

Both the Organization Admin and Supervisor will be able to manage
alerts. The alert status workflow will include:

-   New
-   Acknowledged
-   Responding
-   Resolved
-   Supervisor Escalated
-   Dismissed

Weapon and fight/violence alerts will have high priority. The system will
record the alert’s location, relevant camera, timestamp, priority,
current status, assigned/targeted recipients, and response history where
available.

15.2 Location-based delivery

-   If a Guard is assigned to the building/area associated with an alert,
    the alert will be sent to that Guard according to the configured
    assignment and alert rules.
-   Guard location assignment is optional during account creation.
-   A Guard without a location assignment will not automatically receive
    every alert. An Admin or Supervisor can explicitly target that Guard
    or include the Guard in an authorized escalation.
-   The Supervisor can override or expand the recipients when operational
    circumstances require it.
-   The system will record who received, acknowledged, escalated, or
    responded to an alert.

15.3 Guard acknowledgement and response options

When a Guard receives an alert, the Guard can acknowledge it to confirm
that it has been seen. The Guard can then select a response option, such
as:

-   “Under Control” — the Guard reports that the situation appears to be
    under control.
-   “Additional Guards Required” — the Guard requests more security
    staff at the location.

These options report the Guard’s assessment; they do not automatically
prove that an incident is safe or resolved. The alert status and response
details will remain visible to authorized Admins and Supervisors.

15.4 Escalating an alert to additional Guards

When a Guard selects “Additional Guards Required,” the system will notify
the relevant Admin and Supervisor and send an assistance request to
eligible Guards across the organization, subject to access permissions
and the alert’s target location.

Other Guards can acknowledge the request by selecting a response such
as “I’m Responding.” The system will show which Guards have confirmed
that they are coming and will record their responses.

The Admin or Supervisor can also:

-   Escalate an alert to all eligible Guards across the organization.
-   Search for and target a specific Guard with an alert/assistance
    request.
-   Send an assistance request to Guards assigned to a particular
    building, area, or site.
-   Post a message in the incident chat asking specified Guards to
    attend a location.

The system must distinguish the original alert from later escalation
requests and must retain the relationship between them.

15.5 Incident-specific group chat

Each relevant alert/incident will have a chat thread for operational
coordination. Depending on their permissions and the alert’s recipient
scope, participants may include:

-   Guards who received the alert or were specifically invited.
-   The responsible Supervisor.
-   The Organization Admin.

Participants will be identified by their account identity and role.
Messages will be associated with the relevant alert/incident and include
the sender and timestamp. Users can share text updates such as the
current situation, help required, arrival status, and instructions.

The Admin and Supervisor can use the chat to direct Guards to a
particular location. Chat messages do not replace the alert status or
formal response actions.

Access to each chat will be restricted by organization, incident
permissions, and recipient scope. Users outside the authorized incident
will not be able to read its messages. Relevant chat and escalation
actions will be included in the audit history. Ordinary message content
will be retained according to the system’s configured data-retention
policy; evidence-related content must follow the evidence-preservation
rules.

15.6 Manual reports and offline app

Reports submitted manually by Guards will remain separate records from
AI-generated alerts. A Guard can submit a manual report with a type,
location, description, and optional photo.

If the Guard app is offline, queued manual reports will be retried after
the connection is restored. The system will handle duplicate report
creation and will not falsely claim real-time alert delivery or chat
delivery while offline.

16. Person Search and Investigation

Admins and Supervisors will have access to person search and
investigation functionality.

Person search process

1.  The user will upload a reference photo of a person.
2.  The system will search for possible matches among available live
    observations and retained footage.
3.  Matching results will be displayed with relevant evidence details.

Where available, results will include:

-   Match score
-   Camera
-   Location
-   Date and time
-   Snapshot
-   Relevant video clip

Result classifications will include labels such as Likely Match and
Uncertain, making the distinction between a possible match and a
confirmed identity clear.

Missing footage or offline camera

If a required camera was offline or relevant footage is unavailable, the
system will clearly show this limitation. The search will not be claimed
as complete or conclusive when footage is missing.

Investigation records

The Supervisor can review person-search results and link a relevant
result to an incident. Admins and Supervisors can manage investigation
operations.

Associated alerts, clips, snapshots, and available history should be
preserved with an investigation. Removing a user account will not
automatically delete linked investigation history.

17. CCTV Recordings and Video Storage

The system will handle normal CCTV footage separately from investigation
evidence.

Normal recordings

-   Normal CCTV recordings will be stored.
-   Playback of historical footage will be supported.
-   Relevant clips from AI-generated events will be available.
-   Recordings will be linked to the relevant camera and location.
-   Organization Admins and Supervisors can change the retention
    duration for normal footage.
-   Retention duration can be configured in minutes, hours, or days.
-   Storage capacity will be checked and warnings issued when storage is
    low.
-   Normal footage will be automatically deleted after the configured
    retention duration.

There will be no fixed seven-day retention rule for normal footage.
Retention will depend on the duration configured by the organization.

Investigation evidence

Evidence saved for an investigation will be preserved separately from
normal footage.

-   Evidence will not be automatically deleted when normal footage
    expires.
-   Evidence will remain preserved until an Organization Admin or
    authorized Supervisor manually deletes it.
-   Saving, viewing, updating, and deleting evidence will be recorded in
    audit logs.
-   Evidence will remain linked to the relevant investigation/incident.

This allows normal footage storage to be managed while preventing
important investigation evidence from being automatically lost due to
normal retention.

18. Notifications and Audit Logs

The system’s Logs page will be important for security and
administration. Relevant actions will be recorded so the Admin can track
organizational activity.

What will be recorded in logs?

-   Login and access activities.
-   Successful password changes and password-reset events (without
    recording passwords, OTPs, reset tokens, or other secrets).
-   User creation, updates, and removal.
-   Account status changes.
-   Camera configuration changes.
-   AI configuration changes.
-   Changes to authorized-person information, where applicable.
-   Alert creation, assignment, and status changes.
-   Alert acknowledgement, response-option selection, escalation, and
    Guard response confirmations.
-   Incident-chat access and message activity metadata, including sender,
    incident, and timestamp, subject to the audit policy.
-   Activities related to incidents and investigations.
-   Evidence save, view, update, and delete actions.
-   Changes to retention settings.
-   ID regeneration.
-   Relevant Supervisor/Guard management activities.

Admin notifications

-   When a Supervisor creates, updates, or removes a Guard, the Admin
    will receive a notification.
-   When a Supervisor’s ID is regenerated/changed, the Admin will
    receive a relevant notification.
-   Relevant Guard-management activities will be visible on the Logs
    page.
-   The Admin’s own actions will be recorded in Logs, but will not
    trigger notifications to other users.

Security rule

Passwords, OTPs, and their secret values will not be recorded in audit
logs. Logs will record the action, relevant user, time, and necessary
activity details for accountability without exposing secrets.

The Platform Super Admin will be able to view platform audit logs,
including organization-level activity records needed for platform
oversight (for example, which organization and account performed a
password-reset event). This permission will not allow access to an
organization’s live CCTV feeds, recordings, investigation evidence, or
incident-chat content.

Organization Admins will be able to view relevant activity logs for
Supervisors and Guards in their organization. Supervisors will be able
to view permitted operational logs for Guards within their authorized
scope. Admins and Supervisors will not be able to view passwords, OTPs,
reset tokens, or other authentication secrets in logs.

Every audit record must identify the acting account, action, timestamp,
and relevant target record where applicable. The audit system must not
store password or OTP secret values.

19. Settings and Authorized Persons

Organization settings

Organization Admins and Supervisors will manage operational settings
according to their permissions. Confirmed requirements include normal
footage retention and AI configuration.

Authorized persons

Authorized-person information and configuration will be relevant to the
optional restricted-area face-recognition module.

The exact role-level permissions for authorized-person management have
not yet been fully specified. It will therefore not be assumed that
every Admin or Supervisor has unlimited permission for every
authorized-person operation.

14A. RAG-Based Security Assistant (Added Requirement)

The system will include a RAG-based Security Assistant that answers questions using approved organizational policies/manuals and permission-checked incident data.

- Admin-authorized documents will be ingested, chunked, indexed, and retrieved for relevant questions.
- Answers based on documents will include source references where available.
- Exact counts, statuses, and timestamps will be retrieved from permission-checked database queries rather than guessed by the language model.
- The assistant will enforce organization, site, and role permissions on every retrieval.
- If evidence is missing or retrieval is weak, the assistant must state that it cannot confidently answer.
- The assistant must not invent policies, incident details, or citations.
- Proposed tools: LangChain or equivalent, an LLM provider selected by the team, embeddings, and PostgreSQL with pgvector if suitable.
- Acceptance tests will cover answer correctness, source grounding, unsupported questions, and cross-organization access denial.

14B. Agentic Incident Investigation Workflow (Added Requirement)

The system will include one bounded agentic workflow to assist authorized staff with incident investigation. LangGraph is the proposed orchestration framework.

Workflow:
1. Receive an eligible alert or an authorized investigation request.
2. Verify the user's permissions.
3. Retrieve the incident, camera/location metadata, related events, and available evidence references.
4. Retrieve relevant approved policy guidance.
5. Generate an evidence-linked incident summary and recommended next steps.
6. Pause for Supervisor/Admin review before consequential actions.
7. After approval, perform only explicitly permitted actions, such as updating a status or sending an approved notification.
8. Record the actor, approval, timestamp, tool/action, result, and errors in audit logs.

The agent must not autonomously accuse a person, confirm an uncertain identity, punish a user, or execute consequential actions without required human approval. Tool calls must enforce backend authorization and use safe retry/idempotency controls. LLM output, source evidence, recommendation, approval, and resulting action must remain distinguishable.

Acceptance tests will cover correct retrieval/tool selection, permission enforcement, human approval, audit completeness, and prevention of duplicate actions.

14C. AI Scope and Evaluation Matrix (Added Requirement)

| AI feature | Scope | Initial approach | Evaluation |
| Human detection | Core | Evaluate suitable pretrained detector; fine-tune only if justified | Precision, recall, mAP where appropriate, false positives, inference speed |
| Single-camera tracking | Core | Tracker connected to detections | ID switches, continuity, appropriate tracking metrics |
| Cross-camera person Re-ID | Core | Candidate ranking with human verification | Rank-k/mAP where dataset supports it; false-match review |
| Weapon detection | Core | Suitable pretrained model and licensed dataset; fine-tune if needed | Per-class precision/recall, false alerts, missed detections |
| Fight/violence detection | Core | Evaluate suitable clip-based model | Clip-level precision/recall, false alarms, latency |
| RAG Security Assistant | Added core AI feature | Approved documents + permission-checked database retrieval | Correctness, source grounding, access-control tests |
| Agentic investigation | Added core AI feature | One controlled LangGraph workflow | Tool correctness, approval behavior, audit completeness |
| Restricted-area face recognition | Optional | Only after feasibility/privacy review | Match quality, false matches, human-review behavior |

Pretrained models are the starting point. Custom training is not required by default. Dataset suitability and licensing must be verified. Each model and integrated workflow must be evaluated separately, and untested performance must not be claimed as proven.

20. Dataset and AI Development Plan

The initial plan is to develop/test AI using public datasets and
recorded videos. The system will then be tested with actual IP cameras.

The following dataset candidates were mentioned in the discussion:

-   CrowdHuman — a candidate for human detection.
-   AI City — a candidate for relevant multi-camera/video analytics
    tasks.
-   Market-1501 — a candidate for person re-identification.
-   Weapon-detection datasets — relevant datasets must be identified and
    evaluated; licenses must be verified.
-   RWF-2000 / UCF-Crime — candidates for violence-related video
    analysis.

These are candidate datasets; this does not guarantee that each dataset
is directly suitable for every module or licensed for commercial use.
Dataset suitability and usage licenses will be verified as part of
development.

Development order

1.  Develop AI modules using public datasets and recorded videos.
2.  Test each module on its relevant task.
3.  Integrate CCTV/video processing with the backend.
4.  Connect camera management and AI configuration to the web app.
5.  Integrate alerts, recordings, investigations, and user permissions.
6.  Connect the Guard app to the backend and alert/report functionality.
7.  Conduct initial physical testing with two IP cameras.
8.  Document confirmed test results and limitations.

Building a model alone does not prove that real-time CCTV integration,
cross-camera re-identification, or mobile alert delivery works at
production level. Each capability must be tested separately.

21. Security, Reliability, and Overall Quality Requirements

The system must not be merely a collection of features; role
permissions, user isolation, and data reliability must also work
correctly.

Authentication and permissions

-   Operations will be restricted according to role.
-   The backend will check permissions for every protected operation.
-   Hiding a button in the frontend alone will not be sufficient for
    security.
-   Organization/workspace isolation will be enforced by the backend.
-   An unauthorized user must not be able to access another
    organization’s footage, profile, evidence, or investigation.

Data and records

-   User IDs will be unique.
-   Rejoining will not create duplicate profiles/history.
-   Historical alerts, incidents, and investigations will remain linked
    to the correct person.
-   Camera, user, alert, and investigation records will remain
    consistent with one another.
-   Passwords and OTPs will not be exposed in logs.
-   Evidence and normal recording retention will be handled separately.

Reliability and performance

-   Camera online/offline status will be reflected accurately.
-   Offline Guard reports can be retried later.
-   Retries will not create duplicate submissions.
-   Storage capacity and retention will be monitored.
-   AI results will not be declared reliable or accurate without actual
    testing.
-   The system will be designed with privacy, performance, reliability,
    auditability, and future scalability in mind.

Testing

Testing must verify at least the following:

-   Each role can perform only its allowed operations.
-   The Super Admin cannot access organization feeds/recordings.
-   A Branch Admin cannot access the dashboard before approval.
-   Admin and Supervisor account-management restrictions are followed.
-   Required password/OTP rules apply to every login.
-   Guard login does not require the organization name.
-   A Guard can reset their own password through email OTP without Admin
    approval.
-   Location-based alert delivery respects assignments and permissions.
-   Guards can acknowledge alerts and request additional assistance.
-   Escalated alerts reach the intended eligible Guards, and their
    response confirmations are recorded.
-   Incident chat access is restricted to authorized participants and
    messages show sender identity and timestamp.
-   Admin, Supervisor, and Platform Super Admin audit visibility follows
    the defined scopes without exposing authentication secrets.
-   Rejoining restores the existing profile and history.
-   Unauthorized users cannot permanently delete saved information.
-   Normal footage expires according to the configured duration.
-   Investigation evidence is not automatically deleted along with
    normal footage.
-   Alerts are associated with the correct location and assigned Guard.
-   Logs and notifications record expected activities.
-   The backend rejects unauthorized requests even if a direct API
    request is made.
-   Actual camera tests and AI model tests are recorded separately.

22. Details Still to Be Finalized Separately

This section is intentionally separate because these points have not
been fully decided in our discussion. They must not be silently assumed
to be final requirements.

  -----------------------------------------------------------------------
  Area                                Remaining detail
  ----------------------------------- -----------------------------------
  Branch permissions                  The exact permission matrix for all
                                      organization/location-management
                                      permissions of the Main Admin and
                                      Branch Admin.

  Organization management             Exact permissions for Super Admin
                                      organization activation,
                                      suspension, or additional controls.

  Duplicate organization checks       Complete rules for identifying the
                                      same organization and securely
                                      associating it.

  User IDs                            Complete permission matrix for
                                      regenerating other users’ IDs for
                                      each role.

  Permanent deletion                  Exact policy for retaining or
                                      removing user-linked historical
                                      audit records after permanent
                                      deletion by an Admin.

  Evidence management                 Confirmation process for evidence
                                      deletion and the exact boundary of
                                      authorized Supervisor permissions.

  Authorized persons                  Who can add, edit, remove, or
                                      review authorized-person records.

  Alert escalation                    Exact timeout/escalation duration
                                      for unacknowledged alerts.

  AI evaluation                       Acceptance thresholds and numerical
                                      performance targets for each model.

  Infrastructure                      Required server/GPU capacity, exact
                                      storage limits, and deployment
                                      resources.

  API/database contracts              Exact field names, API routes,
                                      request/response formats, and
                                      status values across the frontend,
                                      backend, and database.

  Final acceptance criteria           Pass/fail tests and measurable
                                      completion conditions for each
                                      feature.
  -----------------------------------------------------------------------

This list of missing details does not replace the project requirements.
Its purpose is only to show which functionality has been decided and
which implementation details still require a decision.

23. Expected Final Project Result

The expected result is a connected CCTV security management platform in
which:

-   The Platform Super Admin manages the platform and organizations but
    cannot access organizational footage.
-   Organization Admins and Supervisors manage CCTV, AI configuration,
    alerts, users, and investigations according to their permitted
    responsibilities.
-   Guards handle relevant alerts, acknowledge incidents, request
    additional assistance, coordinate through incident chat, and submit
    manual reports through the mobile app.
-   AI helps with human detection/tracking, cross-camera person
    re-identification, weapon detection, and fight/violence detection.
-   Optional restricted-area face recognition is handled as a separate
    module.
-   Recordings, evidence, and investigations are managed according to
    defined retention rules.
-   Removing a user account does not automatically delete historical
    information.
-   Rejoining preserves the existing ID and profile history.
-   Password/OTP verification and backend permissions protect sensitive
    operations.
-   The Admin receives notifications about relevant Supervisor
    activities, and an auditable record is available on the Logs page.
-   Organization and branch data isolation is maintained.
-   Feature completion is confirmed through actual testing, not
    assumptions.

This is the master requirements document consolidated from our
discussion so far. It brings together user roles, onboarding, account
creation, profile updates, IDs, rejoining, permissions, AI modules, CCTV
management, alerts, recordings, investigations, and audit logs. Details
that have not yet been formally decided are kept in a separate section
so that no requirement is missed or accidentally changed when
implementation begins.
