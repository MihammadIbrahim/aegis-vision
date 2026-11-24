import { Activity, Cpu, Pencil, Plus, RotateCw, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Button, DataTable, ErrorState, PageHeader, StatusBadge, useToast, type Column } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { useConfirm } from "../../../hooks/useConfirm";
import { formatLocationPath } from "../../../lib/format";
import { aiConfig, SHORT_LABELS } from "../../ai-config/api";
import { LocationPicker, locations, NO_LOCATION, type LocationValue } from "../../locations";
import { live, statusHint, type CameraStatus } from "../../video";
import { cameras } from "../api";
import { BulkAiDialog } from "../components/BulkAiDialog";
import { CameraDialog } from "../components/CameraDialog";
import type { Camera, CameraQuery } from "../types";

/** How long typing pauses before the search is sent. */
export const SEARCH_DELAY_MS = 300;

/** The camera's selected AI features as pills; a selected feature that does not run now is dashed. */
function AiPills({ camera }: { camera: Camera }) {
  if (camera.ai_modules == null) return <span className="muted">—</span>;
  if (camera.ai_modules.length === 0) return <span className="muted small">No AI</span>;
  const running = camera.ai_effective_modules ?? [];
  return (
    <span className="ai-pills">
      {camera.ai_modules.map((k) => (
        <span key={k} className={`ai-pill${running.includes(k) ? "" : " off"}`} title={running.includes(k) ? "Runs on this camera" : "Selected, but not permitted now"}>
          {SHORT_LABELS[k]}
        </span>
      ))}
    </span>
  );
}

function toQuery(q: string, location: LocationValue): CameraQuery {
  return {
    q: q.trim() || undefined,
    building_id: location.building_id || undefined,
    floor_id: location.floor_id || undefined,
    area_id: location.area_id || undefined,
  };
}

/** Camera Management for the signed-in Admin or Supervisor's own campus (D-012), with each camera's
 * online/offline status and a connection test (D-023). Live video is on the Live Cameras page. */
export function CamerasPage() {
  const toast = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const tree = useAsync(() => locations.tree(), []);
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [location, setLocation] = useState<LocationValue>(NO_LOCATION);
  const [editing, setEditing] = useState<Camera | "new" | null>(null);
  // AI features (D-028): the campus's AI configuration for the dialogs; selection for bulk assignment.
  const ai = useAsync(() => aiConfig.get(), []);
  const [selected, setSelected] = useState<string[]>([]);
  const [bulkOpen, setBulkOpen] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => setQ(text), SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [text]);

  const query = toQuery(q, location);
  const list = useAsync(() => cameras.list(query), [query.q, query.building_id, query.floor_id, query.area_id]);
  const statuses = useAsync(() => live.cameras(), []);
  const [testing, setTesting] = useState<string | null>(null);
  const filtered = Boolean(query.q || query.building_id);
  const statusOf = (id: string): CameraStatus | undefined => statuses.data?.find((m) => m.camera.id === id)?.status;

  async function testConnection(camera: Camera) {
    setTesting(camera.id);
    try {
      const result = await live.healthCheck(camera.id);
      toast(result.reachable ? "success" : "error", `${camera.display_name}: ${result.detail}`);
      statuses.reload();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    } finally {
      setTesting(null);
    }
  }

  async function save(body: Parameters<typeof cameras.create>[0]) {
    if (editing === "new") await cameras.create(body);
    else if (editing) await cameras.update(editing.id, body);
    toast("success", editing === "new" ? `${body.display_name} added.` : "Changes saved.");
    setEditing(null);
    list.reload();
  }

  const visible = list.data ?? [];
  const visibleIds = visible.map((c) => c.id);
  const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((id) => selected.includes(id));
  const toggleOne = (id: string, on: boolean) => setSelected(on ? [...selected, id] : selected.filter((x) => x !== id));
  const toggleVisible = (on: boolean) =>
    setSelected(on ? [...new Set([...selected, ...visibleIds])] : selected.filter((id) => !visibleIds.includes(id)));

  async function remove(camera: Camera) {
    const { confirmed } = await confirm({
      title: `Remove ${camera.display_name}?`,
      message: "The camera record is deleted. A camera that already has alerts cannot be removed, so their history stays complete.",
      confirmLabel: "Remove camera",
      tone: "danger",
    });
    if (!confirmed) return;
    try {
      await cameras.remove(camera.id);
      toast("success", `${camera.display_name} removed.`);
      list.reload();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    }
  }

  const columns: Column<Camera>[] = [
    ...(ai.data
      ? [
          {
            key: "select",
            className: "select-cell",
            header: (
              <input type="checkbox" aria-label="Select all visible cameras" checked={allVisibleSelected} onChange={(e) => toggleVisible(e.target.checked)} />
            ),
            render: (c: Camera) => (
              <input type="checkbox" aria-label={`Select ${c.display_name}`} checked={selected.includes(c.id)} onChange={(e) => toggleOne(c.id, e.target.checked)} />
            ),
          },
        ]
      : []),
    { key: "name", header: "Camera", render: (c) => <strong>{c.display_name}</strong> },
    { key: "id", header: "Camera ID", render: (c) => <span className="mono small">{c.id}</span> },
    { key: "type", header: "Connection", render: (c) => c.connection_type },
    {
      key: "url",
      header: "Stream URL",
      className: "break",
      render: (c) => (c.stream_url ? <span className="mono small">{c.stream_url}</span> : <span className="muted">Not set</span>),
    },
    {
      key: "status",
      header: "Status",
      render: (c) => {
        const s = statusOf(c.id);
        return s ? (
          <span className="stack" style={{ gap: 0 }}>
            <StatusBadge status={s.state} />
            {s.state !== "ONLINE" && <span className="muted small">{statusHint(s)}</span>}
          </span>
        ) : (
          <span className="muted">{statuses.loading ? "…" : "—"}</span>
        );
      },
    },
    { key: "ai", header: "AI features", render: (c) => <AiPills camera={c} /> },
    { key: "env", header: "Environment", render: (c) => (c.environment ? <StatusBadge status={c.environment} /> : <span className="muted">—</span>) },
    {
      key: "location",
      header: "Location",
      render: (c) =>
        c.location ? formatLocationPath(c.location.building_name, c.location.floor_name, c.location.area_name) : <span className="muted">No location</span>,
    },
    {
      key: "actions",
      header: "Actions",
      render: (c) => (
        <div className="actions nowrap">
          <Button variant="ghost" size="sm" icon={Activity} aria-label={`Test connection of ${c.display_name}`} loading={testing === c.id} disabled={!c.stream_url} onClick={() => testConnection(c)}>
            Test
          </Button>
          <Button variant="ghost" size="sm" icon={Pencil} aria-label={`Edit ${c.display_name}`} onClick={() => setEditing(c)}>
            Edit
          </Button>
          <Button variant="ghost" size="sm" icon={Trash2} aria-label={`Remove ${c.display_name}`} onClick={() => remove(c)}>
            Remove
          </Button>
        </div>
      ),
    },
  ];

  return (
    <section>
      <PageHeader
        title="Cameras"
        description="CCTV cameras of this campus, where they are and whether they are online. Live video is on the Live Cameras page."
        actions={
          <>
            <Button variant="secondary" icon={RotateCw} onClick={() => { list.reload(); statuses.reload(); }} loading={list.loading && Boolean(list.data)}>
              Refresh
            </Button>
            <Button icon={Plus} onClick={() => setEditing("new")}>
              Add camera
            </Button>
          </>
        }
      />
      <div className="filters" role="search" aria-label="Find cameras">
        <input type="search" aria-label="Search" placeholder="Name, Camera ID or stream address" value={text} maxLength={200} onChange={(e) => setText(e.target.value)} />
        <LocationPicker tree={tree.data ?? []} value={location} onChange={setLocation} />
        {(text || location.building_id) && (
          <Button
            variant="ghost"
            onClick={() => {
              setText("");
              setQ("");
              setLocation(NO_LOCATION);
            }}
          >
            Clear
          </Button>
        )}
      </div>
      {ai.data && (
        <div className="bulk-bar" role="region" aria-label="Bulk AI assignment">
          <label className="checkbox small">
            <input type="checkbox" checked={allVisibleSelected} disabled={visibleIds.length === 0} onChange={(e) => toggleVisible(e.target.checked)} />
            Select all visible
          </label>
          <span className="count" aria-live="polite">
            {selected.length} camera{selected.length === 1 ? "" : "s"} selected
          </span>
          <Button size="sm" icon={Cpu} disabled={selected.length === 0} onClick={() => setBulkOpen(true)}>
            Assign AI Features (Bulk)
          </Button>
          {selected.length > 0 && (
            <Button size="sm" variant="ghost" onClick={() => setSelected([])}>
              Clear selection
            </Button>
          )}
        </div>
      )}
      {list.error ? (
        <ErrorState error={list.error} onRetry={list.reload} />
      ) : (
        <>
          <DataTable
            caption="Cameras"
            columns={columns}
            rows={list.data ?? []}
            rowKey={(c) => c.id}
            loading={list.loading && !list.data}
            empty={filtered ? "No cameras match." : "No cameras yet."}
          />
          {list.data && (
            <p className="muted small" aria-live="polite">
              {list.data.length} camera{list.data.length === 1 ? "" : "s"}
              {filtered ? (list.data.length === 1 ? " matches" : " match") : ""}.
            </p>
          )}
        </>
      )}
      {editing && (
        <CameraDialog camera={editing === "new" ? undefined : editing} tree={tree.data ?? []} ai={ai.data} onClose={() => setEditing(null)} onSave={save} />
      )}
      {bulkOpen && ai.data && (
        <BulkAiDialog
          ai={ai.data}
          cameraIds={selected}
          onClose={() => setBulkOpen(false)}
          onDone={(result) => {
            toast("success", `AI features updated on ${result.updated_count} camera${result.updated_count === 1 ? "" : "s"}.`);
            setBulkOpen(false);
            setSelected([]);
            list.reload();
            ai.reload();
          }}
        />
      )}
      {confirmDialog}
    </section>
  );
}
