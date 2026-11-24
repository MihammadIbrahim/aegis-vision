import { useState } from "react";

import { Field, FormDialog, PasswordInput } from "../../../components/ui";
import { aiConfig, type AiConfiguration, type BulkMode, type BulkResult, type ModuleKey } from "../../ai-config/api";
import { unavailableReason } from "./AiFeaturesFieldset";

const MODES: { mode: BulkMode; label: string; hint: string }[] = [
  { mode: "ADD", label: "Add / Enable", hint: "Switch the selected features on; other features stay as they are." },
  { mode: "REMOVE", label: "Remove / Disable", hint: "Switch the selected features off; other features stay as they are." },
  { mode: "SET", label: "Replace", hint: "Each camera runs exactly the selected features; everything else is switched off." },
];

/** Assign AI features to many cameras in one step (decision D-028): all or nothing, with the caller's password. */
export function BulkAiDialog({
  ai,
  cameraIds,
  onClose,
  onDone,
}: {
  ai: AiConfiguration;
  cameraIds: string[];
  onClose: () => void;
  onDone: (result: BulkResult) => void;
}) {
  const [mode, setMode] = useState<BulkMode>("ADD");
  const [chosen, setChosen] = useState<ModuleKey[]>([]);
  const [password, setPassword] = useState("");
  const count = cameraIds.length;
  const toggle = (key: ModuleKey, on: boolean) => setChosen(on ? [...chosen, key] : chosen.filter((k) => k !== key));

  return (
    <FormDialog
      title={`Assign AI features to ${count} camera${count === 1 ? "" : "s"}`}
      submitLabel="Apply to selected cameras"
      disabled={!password || (mode !== "SET" && chosen.length === 0)}
      onClose={onClose}
      onSubmit={async () => {
        const modules = Object.fromEntries(chosen.map((k) => [k, true]));
        onDone(await aiConfig.bulk(cameraIds, mode, modules, password));
      }}
    >
      <fieldset>
        <legend>Action</legend>
        <div className="bulk-modes" role="radiogroup" aria-label="Action">
          {MODES.map((m) => (
            <label key={m.mode} className="checkbox small">
              <input type="radio" name="bulk-mode" checked={mode === m.mode} onChange={() => setMode(m.mode)} />
              {m.label}
            </label>
          ))}
        </div>
        <p className="hint">{MODES.find((m) => m.mode === mode)?.hint}</p>
      </fieldset>
      <fieldset>
        <legend>AI Features</legend>
        <div className="ai-features">
          {ai.modules.map((m) => {
            // Switching off is possible for any visible feature; switching on only for available ones.
            const reason = mode === "REMOVE" ? null : unavailableReason(ai, m.key);
            return (
              <label key={m.key} className="checkbox small">
                <input type="checkbox" checked={chosen.includes(m.key)} disabled={reason !== null} onChange={(e) => toggle(m.key, e.target.checked)} />
                <span>
                  {m.label}
                  {reason && <span className="reason"> — {reason}</span>}
                </span>
              </label>
            );
          })}
        </div>
      </fieldset>
      <Field label="Your current password" required>
        <PasswordInput value={password} autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} />
      </Field>
    </FormDialog>
  );
}
