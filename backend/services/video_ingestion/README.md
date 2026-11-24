# video_ingestion

Reads every camera of the active campuses from the Core API and, per camera, runs **one ffmpeg process**
that decodes the stream once and produces:

| Output | Where (under `INGEST_STORAGE_ROOT`, the API's `CCTV_STORAGE_ROOT`) | Used by |
|---|---|---|
| Routine recording, MP4 segments (60 s by default, `+faststart`) | `recordings/<org>/<campus>/<camera>/<YYYY-MM-DD>/<UTC start>.mp4` | Recordings page, evidence clips, retention daemon |
| Newest sampled frame (JPEG, replaced atomically) | `live/<org>/<campus>/<camera>/latest.jpg` | Live Cameras page (`GET /live/cameras/{id}/frame`) |
| Sampled frames for analysis (bounded, oldest dropped) | `frames/<camera>/<unix ms>-<n>.jpg` | `cv_inference --spool-dir …` (consumes and deletes) |

Finished segments are registered with `POST /internal/recordings/segments`; ONLINE / OFFLINE / UNKNOWN is
reported with `POST /internal/cameras/{id}/status` (changes only, plus a heartbeat while online). The Core API
notifies the campus's Admins and Supervisors when a camera goes offline and when it comes back (decision D-023).

## Sources

- **RTSP / RTSPS** stream URL of the camera (TCP transport by default). No credentials are stored (D-012), so
  the camera must accept the server without a password, or sit on a protected network.
- **ONVIF** cameras are reported **UNKNOWN** ("ONVIF stream discovery needs device credentials (not stored)").
- **Recorded-file fallback**: `INGEST_FILE_SOURCES={"<camera id>": "/path/video.mp4"}` plays a file in real time,
  looped, as that camera — for development and demonstrations until equipment is connected. It exercises the
  whole chain (segments, live frames, status, CV spool).

Nothing here has been tested with a physical camera yet (requirements Section 13).

## Run

```bash
cd backend/services/video_ingestion
INGEST_API_BASE_URL=http://127.0.0.1:8000 INGEST_SERVICE_TOKEN=<INTERNAL_SERVICE_TOKEN of the API> \
INGEST_STORAGE_ROOT=/mnt/cctv_storage python -m src.main            # --config settings.yaml, --run-seconds N
```

Needs `ffmpeg` (with libx264) on the `PATH`; Python requirements in `requirements.txt`.

| Setting | Default | Meaning |
|---|---|---|
| `INGEST_API_BASE_URL` | `http://127.0.0.1:8000` | Core API |
| `INGEST_SERVICE_TOKEN` | — (required) | the API's `INTERNAL_SERVICE_TOKEN` |
| `INGEST_STORAGE_ROOT` | `/mnt/cctv_storage` | the same disk as the API's `CCTV_STORAGE_ROOT` |
| `INGEST_FRAME_SAMPLE_FPS` | 5 | frames per second for the live frame and CV |
| `INGEST_FRAME_WIDTH` | 640 | sampled frames are scaled to this width |
| `INGEST_SEGMENT_SECONDS` | 60 | length of one recording file |
| `INGEST_OFFLINE_AFTER_SECONDS` | 15 | no frame for this long → OFFLINE |
| `INGEST_HEARTBEAT_SECONDS` | 60 | "last seen" refresh while online |
| `INGEST_REGISTRY_REFRESH_SECONDS` | 60 | camera list re-read (added, changed, removed cameras) |
| `INGEST_RECONNECT_MIN_SECONDS` / `_MAX_SECONDS` | 2 / 30 | reconnect back-off |
| `INGEST_RTSP_TRANSPORT` | `tcp` | or `udp` |
| `INGEST_RTSP_TRANSCODE` | false | false: H.264 copied into MP4 (segments split at the camera's keyframes); true: re-encoded |
| `INGEST_FILE_SOURCES` | `{}` | recorded-file fallback per camera |
| `INGEST_CAMPUS_ID` | — | only this campus's cameras |
| `INGEST_SPOOL_MAX_FRAMES` | 300 | CV spool size per camera |
| `INGEST_FFMPEG_PATH` | `ffmpeg` | |

## Tests

`python -m pytest -q` (13 tests): MJPEG splitting, wall-clock sampling, chunk rotation into the dated layout,
health transitions and retries, source resolution, and one end-to-end run of the real ffmpeg pipeline on a
generated file (segments, live frame, spool, ONLINE report, clean stop).

## Not built

Video-rate live streaming (HLS / WebRTC; the page shows the newest frame about once per second), ONVIF device
discovery and PTZ, a credential vault for cameras, GPU decoding, a Dockerfile / compose service entry.
