import { useState } from "react";

import { Field, FormDialog, PasswordInput } from "../../../components/ui";
import { evidence } from "../api";
import type { EvidenceItem } from "../types";

/** Admins only: permanent deletion of one evidence item, confirmed with the Admin's password
 * (decision D-023). The deletion is recorded in the audit log with the item's title. */
export function DeleteEvidenceDialog({ item, onClose, onDeleted }: { item: EvidenceItem; onClose: () => void; onDeleted: () => void }) {
  const [password, setPassword] = useState("");
  const [typed, setTyped] = useState("");

  async function submit() {
    if (typed.trim() !== "DELETE") throw new Error('Type DELETE to confirm.');
    if (!password) throw new Error("Your password is required to delete evidence.");
    await evidence.remove(item.id, password);
    onDeleted();
  }

  return (
    <FormDialog title={`Delete evidence "${item.title}"?`} submitLabel="Delete permanently" onClose={onClose} onSubmit={submit}>
      <p className="notice warning">
        This removes the {item.kind === "CLIP" ? "clip" : "snapshot"} and its record permanently. It cannot be recovered. The
        deletion is logged with your name.
      </p>
      <Field label="Type DELETE to confirm" required>
        <input value={typed} autoComplete="off" onChange={(e) => setTyped(e.target.value)} />
      </Field>
      <Field label="Your password" required>
        <PasswordInput value={password} autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} />
      </Field>
    </FormDialog>
  );
}
