import { EyeOff, RotateCw, Settings2 } from "lucide-react";
import { useState } from "react";

import { useAuth } from "../../../auth/AuthContext";
import { Button, DataTable, ErrorState, Field, FormDialog, LoadingState, PageHeader, PasswordInput, StatusBadge, useToast, type Column } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { formatDateTime } from "../../../lib/format";
import { aiConfig, MODULE_KEYS, onlyKeys, SHORT_LABELS, type AiConfiguration, type CameraAi, type ModuleKey, type ModuleSet } from "../api";

function ModulesDialog({
  title,
  intro,
  initial,
  allowed,
  labels,
  keys = MODULE_KEYS,
  reasonOf,
  onClose,
  onSave,
}: {
  title: string;
  intro: string;
  initial: ModuleSet;
  allowed: ModuleSet;
  labels: Record<string, string>;
  keys?: ModuleKey[];
  reasonOf?: (key: ModuleKey) => string;
  onClose: () => void;
  onSave: (modules: ModuleSet, password: string) => Promise<void>;
}) {
  const [modules, setModules] = useState<ModuleSet>(initial);
  const [password, setPassword] = useState("");
  return (
    <FormDialog title={title} submitLabel="Save" disabled={!password} onClose={onClose} onSubmit={() => onSave(modules, password)}>
      <p className="dialog-message">{intro}</p>
      <fieldset className="stack">
        <legend className="sr-only">Modules</legend>
        {keys.map((key) => (
          <label key={key} className="checkbox small">
            <input
              type="checkbox"
              checked={modules[key]}
              disabled={!allowed[key] && !modules[key]}
              onChange={(e) => setModules({ ...modules, [key]: e.target.checked })}
            />
            {labels[key] ?? key}
            {!allowed[key] && <span className="muted"> — {reasonOf ? reasonOf(key) : "not permitted above"}</span>}
          </label>
        ))}
      </fieldset>
      <Field label="Your current password" required>
        <PasswordInput value={password} autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} />
      </Field>
    </FormDialog>
  );
}

/** Admins: which modules the campus's Supervisors see and manage (decision D-028). Hiding a module never switches
 * it off on a camera; Supervisors' changes keep it as it is. */
function SupervisorAccessPanel({ c, labels, onSaved }: { c: AiConfiguration; labels: Record<string, string>; onSaved: () => void }) {
  const toast = useToast();
  const initial = c.supervisor_visible_modules as ModuleSet;
  const [visible, setVisible] = useState<ModuleSet>(initial);
  const [confirming, setConfirming] = useState(false);
  const changed = MODULE_KEYS.some((k) => visible[k] !== initial[k]);
  const notice = (k: ModuleKey): string | null =>
    c.org_blocked[k] ? "Blocked for your organization by the platform: nobody on this campus can use it."
      : !c.platform[k] ? "Disabled by the platform: nobody can use it until the Super Admin enables it."
        : null;
  return (
    <section className="card" aria-labelledby="ai-supervisor-access" data-testid="supervisor-access">
      <div className="card-head">
        <h2 id="ai-supervisor-access">Supervisor AI Feature Access</h2>
        <Button size="sm" variant="secondary" icon={EyeOff} disabled={!changed} onClick={() => setConfirming(true)}>
          Save Supervisor access
        </Button>
      </div>
      <p className="muted small">
        Choose which AI features this campus's Supervisors see and manage. Hiding a feature does not switch it off on any camera;
        a Supervisor's changes keep hidden features exactly as they are.
      </p>
      <ul className="plain stack">
        {MODULE_KEYS.map((k) => (
          <li key={k}>
            <label className="checkbox small">
              <input type="checkbox" checked={visible[k]} onChange={(e) => setVisible({ ...visible, [k]: e.target.checked })} />
              Visible to Supervisors: {labels[k] ?? k}
            </label>
            {notice(k) && <p className="muted small m-0">{notice(k)}</p>}
          </li>
        ))}
      </ul>
      {confirming && (
        <PasswordDialog
          title="Save Supervisor AI feature access"
          onClose={() => setConfirming(false)}
          onSave={async (password) => {
            // The campus modules are re-sent unchanged (without what the platform or organization block excludes).
            await aiConfig.saveCampus(onlyKeys(c.campus, MODULE_KEYS.filter((k) => c.platform[k] && !c.org_blocked[k])), password, visible);
            toast("success", "Supervisor AI feature access saved.");
            setConfirming(false);
            onSaved();
          }}
        />
      )}
    </section>
  );
}

function PasswordDialog({ title, onClose, onSave }: { title: string; onClose: () => void; onSave: (password: string) => Promise<void> }) {
  const [password, setPassword] = useState("");
  return (
    <FormDialog title={title} submitLabel="Save" disabled={!password} onClose={onClose} onSubmit={() => onSave(password)}>
      <Field label="Your current password" required>
        <PasswordInput value={password} autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} />
      </Field>
    </FormDialog>
  );
}

function ModuleList({ set, empty = "None" }: { set: ModuleSet; empty?: string }) {
  const on = MODULE_KEYS.filter((k) => set[k]);
  return <>{on.length ? on.map((k) => SHORT_LABELS[k]).join(", ") : <span className="muted">{empty}</span>}</>;
}

/** AI Configuration: what the platform permits, the campus's modules (Admins), each camera's modules
 * (Admins and Supervisors) and what the vision workers actually run. */
export function AiConfigurationPage() {
  const { session } = useAuth();
  const toast = useToast();
  const loaded = useAsync(() => aiConfig.get(), []);
  const [editingCampus, setEditingCampus] = useState(false);
  const [camera, setCamera] = useState<CameraAi | null>(null);
  const isAdmin = session?.role === "ADMIN";
  if (loaded.error) return <ErrorState error={loaded.error} onRetry={loaded.reload} />;
  if (!loaded.data) return <LoadingState />;
  const c: AiConfiguration = loaded.data;
  const labels = Object.fromEntries(c.modules.map((m) => [m.key, m.label]));
  const visibleKeys = c.modules.map((m) => m.key);
  const notBlocked = MODULE_KEYS.filter((k) => c.platform[k] && !c.org_blocked[k]);
  const platformAllowed = Object.fromEntries(MODULE_KEYS.map((k) => [k, c.platform[k] && !c.org_blocked[k]])) as ModuleSet;
  const reasonOf = (k: ModuleKey) =>
    c.org_blocked[k] ? "blocked for your organization by the platform" : !c.platform[k] ? "disabled by the platform" : "switched off for this campus";

  const columns: Column<CameraAi>[] = [
    { key: "camera", header: "Camera", render: (x) => <div className="stack gap-0"><strong>{x.camera_name}</strong><span className="muted small">{x.location ?? "No location"}</span></div> },
    { key: "stream", header: "Stream", render: (x) => <StatusBadge status={x.stream_state} /> },
    { key: "effective", header: "May run", render: (x) => <ModuleList set={x.effective} /> },
    {
      key: "running",
      header: "Running now",
      render: (x) =>
        x.running_now ? (
          <div className="stack gap-0">
            <span>{x.running.length ? x.running.map((k) => SHORT_LABELS[k as keyof typeof SHORT_LABELS] ?? k).join(", ") : "Nothing"}</span>
            <span className="muted small">{x.frames_per_second ?? "—"} frames/s · {formatDateTime(x.reported_at)}</span>
          </div>
        ) : (
          <span className="muted">{x.reported_at ? `Last report ${formatDateTime(x.reported_at)}` : "No vision worker"}</span>
        ),
    },
    { key: "error", header: "Problem", render: (x) => (x.last_error ? <span className="text-warning">{x.last_error}</span> : "—") },
    {
      key: "edit",
      header: <span className="sr-only">Change</span>,
      render: (x) => (
        <Button size="sm" variant="secondary" icon={Settings2} onClick={() => setCamera(x)}>
          Modules
        </Button>
      ),
    },
  ];

  return (
    <section>
      <PageHeader
        title="AI Configuration"
        description="A camera runs a module only when the platform, your organization, this campus and the camera all allow it. Changes need your password and are audited."
        actions={
          <Button variant="secondary" icon={RotateCw} onClick={loaded.reload}>
            Refresh
          </Button>
        }
      />
      <div className="grid-2">
        <section className="card" aria-labelledby="ai-levels">
          <div className="card-head">
            <h2 id="ai-levels">Modules</h2>
            {isAdmin && (
              <Button size="sm" variant="secondary" onClick={() => setEditingCampus(true)}>
                Change campus modules
              </Button>
            )}
          </div>
          <div className="table-scroll">
            <table>
              <caption className="sr-only">Module permissions</caption>
              <thead>
                <tr>
                  <th scope="col">Module</th>
                  <th scope="col">Platform</th>
                  <th scope="col">Your organization</th>
                  <th scope="col">This campus</th>
                </tr>
              </thead>
              <tbody>
                {c.modules.map((m) => (
                  <tr key={m.key}>
                    <th scope="row">{m.label}</th>
                    <td>{c.platform[m.key] ? "Permitted" : "Not permitted"}</td>
                    <td>{c.org_blocked[m.key] ? <span className="text-warning">Blocked by the platform</span> : "Allowed"}</td>
                    <td>{c.campus_permitted[m.key] ? "On" : c.campus[m.key] ? "On (blocked by the platform)" : "Off"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted small">
            {c.campus_customized ? `Campus modules last changed ${formatDateTime(c.campus_updated_at)} by ${c.campus_updated_by_name ?? "—"}.` : "This campus uses the default modules (face recognition off)."}
            {!isAdmin && " Only an Admin changes the campus modules."}
          </p>
          {c.blocked_by_platform.length > 0 && (
            <p className="notice warning" role="note">
              Blocked for your organization by the platform: {c.blocked_by_platform.map((k) => SHORT_LABELS[k]).join(", ")}. No camera runs
              {c.blocked_by_platform.length === 1 ? " it" : " them"} and {c.blocked_by_platform.length === 1 ? "it" : "they"} cannot be switched on until
              the Super Admin unblocks {c.blocked_by_platform.length === 1 ? "it" : "them"}.
            </p>
          )}
          <p className="muted small">New cameras run no AI feature until one is selected for them (on the camera or in bulk on the Cameras page).</p>
        </section>
        <section className="card" aria-labelledby="ai-rules">
          <h2 id="ai-rules">High-priority alert rules</h2>
          <ul className="plain stack">
            {c.alert_rules.map((r) => (
              <li key={r.detection} className="row">
                <StatusBadge status={r.detection} /> → <StatusBadge status={r.priority} />
              </li>
            ))}
          </ul>
          <p className="muted small">Fixed by the requirements: weapon and fight/violence detections always raise high-priority alerts.</p>
        </section>
      </div>
      {isAdmin && c.supervisor_visible_modules && <SupervisorAccessPanel key={JSON.stringify(c.supervisor_visible_modules)} c={c} labels={labels} onSaved={loaded.reload} />}
      <section className="card" aria-labelledby="ai-cameras">
        <h2 id="ai-cameras">Cameras</h2>
        <DataTable caption="Camera AI modules" columns={columns} rows={c.cameras} rowKey={(x) => x.camera_id} empty="No cameras yet." />
      </section>
      {editingCampus && (
        <ModulesDialog
          title="Campus AI modules"
          intro="Cameras of this campus can run only the modules switched on here."
          initial={onlyKeys(c.campus, notBlocked)}
          allowed={platformAllowed}
          labels={labels}
          reasonOf={reasonOf}
          onClose={() => setEditingCampus(false)}
          onSave={async (modules, password) => {
            await aiConfig.saveCampus(modules, password);
            toast("success", "Campus AI modules saved.");
            setEditingCampus(false);
            loaded.reload();
          }}
        />
      )}
      {camera && (
        <ModulesDialog
          title={`AI modules of ${camera.camera_name}`}
          intro="Switch modules on or off for this camera. Modules the campus does not permit cannot be switched on."
          initial={onlyKeys(camera.configured, [...c.available_modules, ...visibleKeys.filter((k) => camera.configured[k] && c.campus_permitted[k])])}
          allowed={c.campus_permitted}
          labels={labels}
          keys={visibleKeys}
          reasonOf={reasonOf}
          onClose={() => setCamera(null)}
          onSave={async (modules, password) => {
            await aiConfig.saveCamera(camera.camera_id, modules, password);
            toast("success", "Camera AI modules saved.");
            setCamera(null);
            loaded.reload();
          }}
        />
      )}
    </section>
  );
}
