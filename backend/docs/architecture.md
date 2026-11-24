# Architecture

The maintained description is `FYP_Documentation/FYP_Final_Backend_Architecture.txt`:
- **Part A:** what is built;
- **Part B:** the real structure, migrations, endpoints and tests;
- **Part C:** the original planned design.

In short (2026-11-01):
- **`services/core_api`:** FastAPI in Clean Architecture layers; PostgreSQL 16 with Row-Level
  Security for campus isolation and two database roles; migrations 0001–0017. The latest module is
  Personnel Management with Excel/CSV import and restricted-area access (D-016,
  `personnel_management.md`).
- **`services/cv_inference`:** the CV pipeline, with a YOLOX-Tiny person detector and tracking
  (D-014, D-015).
- **`website/`:** the Organization Admin and Supervisor web app (React + Vite + TypeScript). So far
  it has sign-in and the personnel, import and restricted-access pages.

Every other service, the messaging, the storage tiers, Docker/Kubernetes, the Super Admin web app
and the Guard mobile app are placeholders. Decisions: `decision_log.md`. History: `implementation_log.md`.
