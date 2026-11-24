import { statusLook } from "../../../lib/status";
import type { EventMarker, RecordingSegment } from "../types";

function position(iso: string, since: number, span: number): number {
  return Math.min(100, Math.max(0, ((new Date(iso).getTime() - since) / span) * 100));
}

/** The span the bar shows: the requested period, narrowed to what was recorded (plus a little padding)
 * when recordings and events cover only part of it, so a few minutes of footage in a 24-hour window
 * stay visible instead of collapsing into one pixel. */
export function visibleRange(
  since: string,
  until: string,
  segments: RecordingSegment[],
  markers: EventMarker[],
): { start: number; end: number; zoomed: boolean } {
  const start = new Date(since).getTime();
  const end = new Date(until).getTime();
  const times: number[] = [];
  for (const s of segments) times.push(new Date(s.started_at).getTime(), new Date(s.ended_at).getTime());
  for (const m of markers) times.push(new Date(m.detected_at).getTime(), new Date(m.last_detected_at).getTime());
  if (times.length === 0) return { start, end, zoomed: false };
  const lo = Math.min(...times);
  const hi = Math.max(...times);
  const pad = Math.max(30_000, (hi - lo) * 0.03);
  const zoomedStart = Math.max(start, lo - pad);
  const zoomedEnd = Math.min(end, hi + pad);
  const zoomed = zoomedEnd - zoomedStart < (end - start) * 0.9;
  return zoomed ? { start: zoomedStart, end: zoomedEnd, zoomed } : { start, end, zoomed: false };
}

/** The period as a bar: recorded stretches (clickable segments) and AI event markers above them.
 * Everything is also available as text (the segment table and marker list), so the bar is a
 * shortcut, not the only way in. */
export function TimelineBar({
  since,
  until,
  segments,
  markers,
  selected,
  onSelect,
  onMarker,
}: {
  since: string;
  until: string;
  segments: RecordingSegment[];
  markers: EventMarker[];
  selected: string | null;
  onSelect: (segment: RecordingSegment) => void;
  onMarker: (marker: EventMarker) => void;
}) {
  const range = visibleRange(since, until, segments, markers);
  const start = range.start;
  const span = Math.max(1, range.end - start);
  return (
    <div className="timeline" aria-label="Recording timeline">
      <div className="timeline-markers">
        {markers.map((m) => {
          const look = statusLook(m.alert_type);
          const Icon = look.icon;
          return (
            <button
              key={m.alert_id}
              type="button"
              className={`timeline-marker tone-${look.tone}`}
              style={{ left: `${position(m.detected_at, start, span)}%` }}
              title={`${look.label} · ${new Date(m.detected_at).toLocaleString()}`}
              aria-label={`${look.label} at ${new Date(m.detected_at).toLocaleTimeString()}`}
              onClick={() => onMarker(m)}
            >
              <Icon className="icon" aria-hidden="true" />
            </button>
          );
        })}
      </div>
      <div className="timeline-track">
        {segments.map((s) => {
          const left = position(s.started_at, start, span);
          const width = Math.max(0.4, position(s.ended_at, start, span) - left);
          return (
            <button
              key={s.id}
              type="button"
              className={`timeline-segment${selected === s.id ? " is-selected" : ""}`}
              style={{ left: `${left}%`, width: `${width}%` }}
              title={`${s.camera_name} · ${new Date(s.started_at).toLocaleTimeString()}`}
              aria-label={`Recording of ${s.camera_name} at ${new Date(s.started_at).toLocaleTimeString()}`}
              aria-pressed={selected === s.id}
              onClick={() => onSelect(s)}
            />
          );
        })}
      </div>
      <div className="timeline-scale muted small">
        <span>{new Date(range.start).toLocaleString()}</span>
        {range.zoomed && <span>Zoomed to the recorded part of the period</span>}
        <span>{new Date(range.end).toLocaleString()}</span>
      </div>
    </div>
  );
}
