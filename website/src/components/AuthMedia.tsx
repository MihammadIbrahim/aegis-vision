import { useEffect, useState } from "react";

import { apiBlob } from "../api/client";

/** An image or video from the Core API: fetched with the session's token (an <img src> cannot send it)
 * and shown through an object URL that is released when the element goes away. */
export function AuthMedia({
  path,
  alt,
  kind = "image",
  className,
  fallback = "Not available",
}: {
  path: string;
  alt: string;
  kind?: "image" | "video";
  className?: string;
  fallback?: string;
}) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    let created: string | null = null;
    setUrl(null);
    setFailed(null);
    apiBlob(path)
      .then((blob) => {
        if (!alive) return;
        created = URL.createObjectURL(blob);
        setUrl(created);
      })
      .catch((e: unknown) => alive && setFailed(e instanceof Error ? e.message : fallback));
    return () => {
      alive = false;
      if (created) URL.revokeObjectURL(created);
    };
  }, [path, fallback]);
  if (failed) return <p className="muted small" role="note">{failed}</p>;
  if (!url) return <p className="muted small">Loading…</p>;
  return kind === "video" ? (
    // eslint-disable-next-line jsx-a11y/media-has-caption -- CCTV footage has no sound or captions
    <video className={className} src={url} controls aria-label={alt} />
  ) : (
    <img className={className} src={url} alt={alt} />
  );
}
