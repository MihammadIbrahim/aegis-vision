import { Camera, Film, Scissors } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button, ErrorState, LoadingState } from "../../../components/ui";
import { formatDateTime } from "../../../lib/format";
import { recordings } from "../api";
import { formatDuration } from "../labels";
import type { EventMarker, RecordingSegment } from "../types";
import { SaveEvidenceDialog, type EvidenceOrigin } from "./SaveEvidenceDialog";

/** Plays one recording segment (fetched with the session token, played from an object URL) and lets
 * staff mark in/out points to save a clip, or save the current frame as a snapshot. Markers of
 * this segment are listed with "jump" buttons so AI events can be reached exactly. */
export function SegmentPlayer({
  segment,
  markers,
  onSaved,
}: {
  segment: RecordingSegment;
  markers: EventMarker[];
  onSaved: (title: string) => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [time, setTime] = useState(0);
  const [inPoint, setInPoint] = useState<number | null>(null);
  const [outPoint, setOutPoint] = useState<number | null>(null);
  const [saving, setSaving] = useState<EvidenceOrigin | null>(null);

  useEffect(() => {
    let active = true;
    let objectUrl: string | null = null;
    setUrl(null);
    setError(null);
    setInPoint(null);
    setOutPoint(null);
    setTime(0);
    recordings
      .media(segment.id)
      .then((blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch((e: unknown) => active && setError(e));
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [segment.id]);

  const started = new Date(segment.started_at).getTime();
  const own = markers.filter((m) => m.camera_id === segment.camera_id && new Date(m.last_detected_at).getTime() >= started && new Date(m.detected_at).getTime() <= started + segment.duration_seconds * 1000);

  function jump(iso: string) {
    const offset = Math.max(0, Math.min(segment.duration_seconds, (new Date(iso).getTime() - started) / 1000));
    if (video.current) video.current.currentTime = offset;
    setTime(offset);
  }

  const clipReady = inPoint !== null && outPoint !== null && outPoint > inPoint;
  return (
    <div className="player card">
      <div className="card-head">
        <h3>
          {segment.camera_name} · {formatDateTime(segment.started_at)}
        </h3>
        <span className="muted small">{formatDuration(segment.duration_seconds)}</span>
      </div>
      {error ? (
        <ErrorState error={error} />
      ) : !url ? (
        <LoadingState label="Loading recording…" />
      ) : (
        <video ref={video} src={url} controls playsInline className="player-video" onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)} aria-label={`Recording of ${segment.camera_name}`} />
      )}
      <div className="actions player-actions">
        <span className="muted small">
          Position {formatDuration(time)}
          {inPoint !== null && ` · in ${formatDuration(inPoint)}`}
          {outPoint !== null && ` · out ${formatDuration(outPoint)}`}
        </span>
        <Button variant="outline" size="sm" icon={Scissors} onClick={() => setInPoint(time)} disabled={!url}>
          Mark in
        </Button>
        <Button variant="outline" size="sm" icon={Scissors} onClick={() => setOutPoint(time)} disabled={!url}>
          Mark out
        </Button>
        <Button size="sm" icon={Film} disabled={!clipReady} onClick={() => setSaving({ segmentId: segment.id, startOffset: inPoint as number, endOffset: outPoint })}>
          Save clip
        </Button>
        <Button size="sm" variant="secondary" icon={Camera} disabled={!url} onClick={() => setSaving({ segmentId: segment.id, startOffset: time, endOffset: null })}>
          Save snapshot
        </Button>
      </div>
      {own.length > 0 && (
        <ul className="player-markers" aria-label="AI events in this recording">
          {own.map((m) => (
            <li key={m.alert_id}>
              <Button variant="ghost" size="sm" onClick={() => jump(m.detected_at)}>
                {m.alert_type.replace(/_/g, " ").toLowerCase()} at {formatDuration((new Date(m.detected_at).getTime() - started) / 1000)}
              </Button>
            </li>
          ))}
        </ul>
      )}
      {saving && (
        <SaveEvidenceDialog
          origin={saving}
          defaultTitle={`${segment.camera_name} ${formatDateTime(segment.started_at)}`}
          onClose={() => setSaving(null)}
          onSaved={(item) => {
            setSaving(null);
            onSaved(item.title);
          }}
        />
      )}
    </div>
  );
}
