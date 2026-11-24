import { Save } from "lucide-react";
import { useEffect, useState } from "react";

import { platform } from "../../api/platform";
import { AI_MODULE_KEYS, type AiModuleKey, type AiSettings, type AiValues } from "../../api/types";
import { Button, ErrorState, Field, FormDialog, LoadingState, PageHeader, StatusBadge, useToast } from "../../components/ui";
import { useAsync } from "../../hooks/useAsync";
import { formatDateTime } from "../../lib/format";
import { AI_MODULE_LABELS } from "../labels";

type NumberKey = "frame_sample_fps" | "detection_cooldown_seconds" | "max_task_retries" | "task_timeout_seconds";

/** The numeric defaults with the API's limits (platform_management_dto.py). */
export const PARAMETERS: Array<{ key: NumberKey; label: string; min: number; max: number; hint: string }> = [
  { key: "frame_sample_fps", label: "Frame sampling (frames per second)", min: 1, max: 30, hint: "1–30" },
  { key: "detection_cooldown_seconds", label: "Detection cooldown (seconds)", min: 0, max: 3600, hint: "0–3600; repeats of the same event are merged" },
  { key: "max_task_retries", label: "Task retries", min: 0, max: 10, hint: "0–10" },
  { key: "task_timeout_seconds", label: "Task timeout (seconds)", min: 5, max: 600, hint: "5–600" },
];

function valuesOf(s: AiSettings): AiValues {
  const { modules: _m, updated_at: _u, updated_by_username: _b, ...values } = s;
  return values;
}

/** Problems with the edited values, per field (empty when they can be sent). */
export function aiProblems(v: AiValues): Partial<Record<keyof AiValues, string>> {
  const found: Partial<Record<keyof AiValues, string>> = {};
  const threshold = Number(v.default_confidence_threshold);
  if (!/^\d(\.\d{1,2})?$/.test(String(v.default_confidence_threshold)) || threshold < 0.05 || threshold > 0.99) {
    found.default_confidence_threshold = "Enter a number from 0.05 to 0.99 with at most two decimals.";
  }
  for (const p of PARAMETERS) {
    const n = v[p.key];
    if (!Number.isInteger(n) || n < p.min || n > p.max) found[p.key] = `Enter a whole number from ${p.min} to ${p.max}.`;
  }
  return found;
}

/** Platform AI Availability & Settings (page 5). Saved with the current password. */
export function AiSettingsPage() {
  const toast = useToast();
  const settings = useAsync(() => platform.aiSettings(), []);
  const [values, setValues] = useState<AiValues | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [password, setPassword] = useState("");

  useEffect(() => {
    if (settings.data) setValues(valuesOf(settings.data));
  }, [settings.data]);

  if (settings.error) return <ErrorState error={settings.error} onRetry={settings.reload} />;
  if (!settings.data || !values) return <LoadingState />;
  const implemented = Object.fromEntries(settings.data.modules.map((m) => [m.key, m.implemented])) as Record<AiModuleKey, boolean>;
  const problems = aiProblems(values);
  const original = valuesOf(settings.data);
  const changed = (Object.keys(values) as Array<keyof AiValues>).filter((k) => String(values[k]) !== String(original[k]));

  const moduleList = (group: "core" | "optional") =>
    AI_MODULE_KEYS.filter((k) => AI_MODULE_LABELS[k].group === group).map((key) => (
      <li key={key} className="switch-row">
        <label className="checkbox">
          <input type="checkbox" role="switch" checked={values[key]} onChange={(e) => setValues({ ...values, [key]: e.target.checked })} />
          <span>{AI_MODULE_LABELS[key].name}</span>
        </label>
        {!implemented[key] && <StatusBadge status="PLANNED" />}
      </li>
    ));

  return (
    <section>
      <PageHeader
        title="Platform AI settings"
        description="Which AI modules are available platform-wide, and the default processing parameters."
        actions={
          <Button icon={Save} disabled={changed.length === 0 || Object.keys(problems).length > 0} onClick={() => setConfirming(true)}>
            Save settings
          </Button>
        }
      />
      <p className="notice info">
        These settings are stored for the whole platform. No AI worker reads them yet, and modules marked "Not built yet" have no
        effect when enabled.
      </p>
      <div className="grid-2">
        <section className="card" aria-labelledby="core-title">
          <h2 id="core-title">Core computer-vision modules</h2>
          <ul className="plain stack">{moduleList("core")}</ul>
        </section>
        <section className="card" aria-labelledby="optional-title">
          <h2 id="optional-title">Optional and extended AI services</h2>
          <ul className="plain stack">{moduleList("optional")}</ul>
        </section>
      </div>
      <section className="card" aria-labelledby="params-title">
        <h2 id="params-title">Default parameters</h2>
        <div className="form-grid">
          <Field label="Default confidence threshold" required hint="0.05–0.99" error={problems.default_confidence_threshold}>
            <input
              inputMode="decimal"
              value={values.default_confidence_threshold}
              onChange={(e) => setValues({ ...values, default_confidence_threshold: e.target.value })}
            />
          </Field>
          {PARAMETERS.map((p) => (
            <Field key={p.key} label={p.label} required hint={p.hint} error={problems[p.key]}>
              <input
                type="number"
                min={p.min}
                max={p.max}
                step={1}
                value={Number.isNaN(values[p.key]) ? "" : values[p.key]}
                onChange={(e) => setValues({ ...values, [p.key]: e.target.value === "" ? Number.NaN : Number(e.target.value) })}
              />
            </Field>
          ))}
        </div>
        <p className="muted small">
          Last changed {formatDateTime(settings.data.updated_at)}
          {settings.data.updated_by_username ? ` by ${settings.data.updated_by_username}` : ""}.
        </p>
      </section>
      {confirming && (
        <FormDialog
          title="Save platform AI settings"
          submitLabel="Save settings"
          disabled={!password}
          onClose={() => {
            setConfirming(false);
            setPassword("");
          }}
          onSubmit={async () => {
            await platform.updateAiSettings({ ...values, current_password: password });
            setConfirming(false);
            setPassword("");
            toast("success", "Platform AI settings saved.");
            settings.reload();
          }}
        >
          <p className="dialog-message">
            {changed.length} setting{changed.length === 1 ? "" : "s"} will change for every organization on the platform.
          </p>
          <Field label="Your current password" required hint="Re-enter your Super Admin password to confirm.">
            <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
        </FormDialog>
      )}
    </section>
  );
}
