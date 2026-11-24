import { MODULE_KEYS, type AiConfiguration, type ModuleKey, type ModuleSet } from "../../ai-config/api";

/** Why a module cannot be switched on (decision D-028), or null when the caller may select it. */
export function unavailableReason(ai: AiConfiguration, key: ModuleKey): string | null {
  if (ai.available_modules.includes(key)) return null;
  if (ai.org_blocked[key]) return "Blocked for your organization by the platform";
  if (!ai.platform[key]) return "Disabled by the platform";
  return "Switched off for this campus";
}

/** Checkboxes for the AI features the caller may see (Supervisors never see modules hidden from them). */
export function AiFeaturesFieldset({
  ai,
  value,
  onChange,
  isNew,
}: {
  ai: AiConfiguration;
  value: ModuleSet;
  onChange: (next: ModuleSet) => void;
  isNew: boolean;
}) {
  const visible = ai.modules.filter((m) => MODULE_KEYS.includes(m.key));
  return (
    <fieldset data-testid="camera-ai-features">
      <legend>AI Features</legend>
      {isNew && <p className="hint">No AI features are enabled by default. Select the features you want to run on this camera.</p>}
      <div className="ai-features">
        {visible.map((m) => {
          const reason = unavailableReason(ai, m.key);
          return (
            <label key={m.key} className="checkbox small">
              <input
                type="checkbox"
                checked={value[m.key]}
                disabled={reason !== null && !value[m.key]}
                onChange={(e) => onChange({ ...value, [m.key]: e.target.checked })}
              />
              <span>
                {m.label}
                {reason && <span className="reason"> — {reason}</span>}
              </span>
            </label>
          );
        })}
      </div>
      {visible.length === 0 && <p className="muted small">No AI features are available to you on this campus.</p>}
    </fieldset>
  );
}
