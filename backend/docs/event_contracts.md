# Event contracts

**Status (2026-10-31): no message-broker events exist.** RabbitMQ is not used. AI workers deliver
detections over HTTP instead.

## Detection intake (built, decision D-013)

`POST /api/v1/internal/detections`
- **Header:** `X-Detection-Token`. It must equal the server's `DETECTION_INGEST_TOKEN`; when that is
  empty, the intake answers `503 DETECTION_INTAKE_DISABLED`.

```json
{
  "camera_id": "<camera UUID>",
  "detection_type": "WEAPON | FIGHT_VIOLENCE | RESTRICTED_AREA",
  "confidence": 0.91,
  "detected_at": "2026-10-31T10:00:00Z",
  "detection_id": "<worker's own ID, optional>",
  "snapshot_ref": "<reference, optional, not served>"
}
```

**Answer:** `{"alert_id", "outcome"}`. The outcome is one of:
- `CREATED`;
- `REPEATED`: merged into an open alert of the same camera and type within `ALERT_DEDUP_SECONDS`;
- `DUPLICATE`: this `detection_id` was already received. Since migration 0016 this covers every accepted
  detection, including ones merged into an alert.

**Sender:** the `cv_inference` pipeline (`services/cv_inference/src/detection_publisher.py`, D-014)
sends exactly this body. It currently runs only with the **synthetic test detector**, and no real AI
model sends detections yet. The end-to-end test `core_api/tests/integration/test_cv_pipeline_e2e.py`
checks the contract over real HTTP.

## Identity candidates (planned, not built)

Restricted-area alerts carry **no** personnel association today. A future identity module (for
example face recognition) would create reviewable *identity candidates* linking an alert to a
personnel record, each checked with the D-016 access policy. An unknown or uncertain identity is
never reported as a named person. The proposed shape is in `personnel_management.md`, section 10.

## Planned

The design (`FYP_Documentation/FYP_Final_Backend_Architecture.txt`, Part 3) proposes RabbitMQ events
such as `camera.frame.captured`, `ai.detection.flagged` and `alert.dispatched`. None are implemented,
and event names and payloads remain an open decision (requirements Section 9).
