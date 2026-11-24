import { Maximize2, VideoOff } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ApiError } from "../../../api/client";
import { Button, StatusBadge } from "../../../components/ui";
import { formatDateTime, formatLocationPath } from "../../../lib/format";
import { aiConfig, SHORT_LABELS, type ModuleKey, type Overlay } from "../../ai-config/api";
import type { Alert } from "../../alerts";
import { live } from "../api";
import { statusHint } from "../labels";
import type { MonitoredCamera } from "../types";

/** How often the newest frame is fetched (the API serves the ingestion service's latest JPEG). */
export const FRAME_INTERVAL_MS = 1000;

type Connection = "connecting" | "live" | "error";

/** Polls the camera's newest frame with the session token and shows it. The picture is replaced
 * through object URLs (never an <img src> to the API), and a frame that cannot be fetched turns the
 * tile into an explicit connection-error state instead of freezing on an old image.
 *
 * Alert badges come from the camera's **open alerts** (weapon, fight / violence, restricted area). With
 * `showOverlay`, the vision worker's newest detections are drawn on the picture (boxes, track IDs,
 * confidence, capture time) and the modules it reports running are listed; when the worker publishes
 * nothing recent, the tile says so instead of drawing old boxes (D-024, Live items 16–22). */
export function LiveTile({
  item,
  alerts = [],
  onFullscreen,
  interval = FRAME_INTERVAL_MS,
  showOverlay = false,
}: {
  item: MonitoredCamera;
  /** Open alerts of this camera (any status but Resolved / Dismissed). */
  alerts?: Alert[];
  onFullscreen?: (item: MonitoredCamera) => void;
  interval?: number;
  /** Draw the AI detections of the camera's vision worker. */
  showOverlay?: boolean;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [connection, setConnection] = useState<Connection>("connecting");
  const [problem, setProblem] = useState<string | null>(null);
  const current = useRef<string | null>(null);

  useEffect(() => {
    let active = true;
    let timer = 0;
    async function tick() {
      try {
        const blob = await live.frame(item.camera.id);
        if (!active) return;
        const next = URL.createObjectURL(blob);
        if (current.current) URL.revokeObjectURL(current.current);
        current.current = next;
        setUrl(next);
        setConnection("live");
        setProblem(null);
      } catch (e) {
        if (!active) return;
        setConnection("error");
        setProblem(e instanceof ApiError && e.code === "LIVE_FRAME_STALE" ? "No recent frames; the camera may be offline." : e instanceof Error ? e.message : String(e));
      } finally {
        if (active) timer = window.setTimeout(tick, interval);
      }
    }
    tick();
    return () => {
      active = false;
      window.clearTimeout(timer);
      if (current.current) URL.revokeObjectURL(current.current);
      current.current = null;
    };
  }, [item.camera.id, interval]);

  const overlay = useOverlay(item.camera.id, showOverlay && connection === "live", Math.max(interval, 1000));
  const c = item.camera;
  const where = c.location ? formatLocationPath(c.location.building_name, c.location.floor_name, c.location.area_name) : "No location";
  const types = [...new Set(alerts.map((a) => a.alert_type))];
  const urgent = alerts.some((a) => a.priority === "HIGH");
  return (
    <figure
      className={`live-tile is-${connection}${urgent ? " has-alert" : ""}`}
      aria-label={`${c.display_name}: ${connection === "live" ? "live" : "no video"}${alerts.length ? `, ${alerts.length} open alert${alerts.length === 1 ? "" : "s"}` : ""}`}
    >
      <div className="live-frame">
        {url && connection === "live" ? (
          <>
            <img src={url} alt={`Live view of ${c.display_name}`} />
            {showOverlay && overlay && <DetectionLayer overlay={overlay} />}
          </>
        ) : (
          <div className="live-placeholder" role="status">
            <VideoOff className="icon" aria-hidden="true" />
            <span>{connection === "connecting" ? "Connecting…" : (problem ?? "No video")}</span>
          </div>
        )}
        <div className="live-overlay">
          <StatusBadge status={item.status.state} />
          {connection === "live" && <span className="badge tone-new">Live</span>}
          {types.map((t) => (
            <StatusBadge key={t} status={t} />
          ))}
          {showOverlay && overlay && <OverlayBadges overlay={overlay} />}
        </div>
        {onFullscreen && (
          <Button variant="ghost" size="sm" icon={Maximize2} className="live-expand" aria-label={`Fullscreen ${c.display_name}`} onClick={() => onFullscreen(item)}>
            Fullscreen
          </Button>
        )}
      </div>
      <figcaption>
        <strong>{c.display_name}</strong>
        <span className="muted small">{where}</span>
        <span className="mono muted small">{c.id}</span>
        {item.status.state !== "ONLINE" && <span className="muted small">{statusHint(item.status)}</span>}
      </figcaption>
    </figure>
  );
}

/** Polls the camera's newest AI overlay while enabled; null when there is none or it cannot be read. */
function useOverlay(cameraId: string, enabled: boolean, interval: number): Overlay | null {
  const [overlay, setOverlay] = useState<Overlay | null>(null);
  useEffect(() => {
    if (!enabled) {
      setOverlay(null);
      return;
    }
    let active = true;
    let timer = 0;
    async function tick() {
      try {
        const next = await aiConfig.overlay(cameraId);
        if (active) setOverlay(next);
      } catch {
        if (active) setOverlay(null);
      } finally {
        if (active) timer = window.setTimeout(tick, interval);
      }
    }
    tick();
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [cameraId, enabled, interval]);
  return overlay;
}

const THREAT_TEXT: Record<string, string> = { weapon_detection: "Weapon in view", fight_detection: "Fight in view" };

function moduleClass(module: string | null): string {
  if (module === "weapon_detection") return "is-weapon";
  if (module === "fight_detection") return "is-fight";
  return "is-person";
}

/** Boxes drawn in the frame's own coordinates; `meet` scaling matches the picture's object-fit: contain. */
export function DetectionLayer({ overlay }: { overlay: Overlay }) {
  if (overlay.stale || overlay.detections.length === 0) return null;
  const w = overlay.frame_width ?? 1600;
  const h = overlay.frame_height ?? 900;
  const font = Math.max(10, h * 0.03);
  return (
    <svg className="detection-layer" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="xMidYMid meet" role="img" aria-label={`${overlay.detections.length} AI detection${overlay.detections.length === 1 ? "" : "s"}`}>
      {overlay.detections.map((d, n) => {
        const [x1, y1, x2, y2] = d.box;
        const text = `${d.label}${d.track_id !== null ? ` #${d.track_id}` : ""}${d.confidence !== null ? ` ${Math.round(d.confidence * 100)}%` : ""}`;
        const labelY = y1 * h > font * 1.4 ? y1 * h - font * 0.35 : y1 * h + font * 1.1;
        return (
          <g key={`${d.track_id ?? "x"}-${n}`} className={`detection ${moduleClass(d.module)}`}>
            <rect x={x1 * w} y={y1 * h} width={Math.max(0, (x2 - x1) * w)} height={Math.max(0, (y2 - y1) * h)} rx={4} vectorEffect="non-scaling-stroke" />
            <text x={x1 * w + 4} y={labelY} fontSize={font}>
              {text}
            </text>
          </g>
        );
      })}
      {overlay.captured_at && (
        <text className="detection-time" x={8} y={h - font * 0.5} fontSize={font * 0.85}>
          {formatDateTime(overlay.captured_at)}
        </text>
      )}
    </svg>
  );
}

/** What the worker reports running, and whether a weapon or a fight is in the newest overlay. */
function OverlayBadges({ overlay }: { overlay: Overlay }) {
  if (overlay.stale) return <span className="badge tone-inactive">AI overlay: no recent data</span>;
  const threats = [...new Set(overlay.detections.map((d) => d.module).filter((m): m is string => m !== null && m in THREAT_TEXT))];
  return (
    <>
      {threats.map((m) => (
        <span key={m} className="badge tone-critical">
          {THREAT_TEXT[m]}
        </span>
      ))}
      {overlay.modules.length > 0 && (
        <span className="badge tone-normal" title="AI modules the camera's vision worker reports running">
          AI: {overlay.modules.map((m) => SHORT_LABELS[m as ModuleKey] ?? m).join(" · ")}
        </span>
      )}
    </>
  );
}
