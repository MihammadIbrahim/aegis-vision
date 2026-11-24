import { useState } from "react";

import { Field, FormDialog } from "../../../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { LocationPicker, locations, NO_LOCATION, type LocationValue } from "../../locations";
import { staff } from "../api";
import { ROLE_LABEL } from "../labels";
import type { StaffMember } from "../types";
import { fullNameProblem, PASSWORD_HINT, passwordProblem, phoneProblem, usernameProblem } from "../validation";

function OwnPassword({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <Field label="Your current password" required hint="Confirms that you are making this change.">
      <input type="password" autoComplete="current-password" value={value} onChange={(e) => onChange(e.target.value)} />
    </Field>
  );
}

/** PUT /staff/guards/{id}: full name, username and phone number. */
export function EditGuardDialog({ guard, onClose, onSaved }: { guard: StaffMember; onClose: () => void; onSaved: (m: StaffMember) => void }) {
  const [fullName, setFullName] = useState(guard.full_name);
  const [username, setUsername] = useState(guard.username ?? "");
  const [phone, setPhone] = useState(guard.phone_number ?? "");
  const [password, setPassword] = useState("");
  const problems = { full_name: fullNameProblem(fullName), username: usernameProblem(username), phone: phoneProblem(phone) };
  const invalid = Object.values(problems).some(Boolean) || !password;
  return (
    <FormDialog
      title={`Edit ${guard.full_name}`}
      submitLabel="Save changes"
      onClose={onClose}
      disabled={invalid}
      onSubmit={async () =>
        onSaved(
          await staff.updateGuard(guard.id, {
            full_name: fullName.trim(),
            username: username.trim(),
            phone_number: phone.trim(),
            current_password: password,
          }),
        )
      }
    >
      <Field label="Full name" required error={problems.full_name}>
        <input maxLength={120} value={fullName} onChange={(e) => setFullName(e.target.value)} />
      </Field>
      <Field label="Username" required error={problems.username}>
        <input maxLength={64} value={username} onChange={(e) => setUsername(e.target.value)} />
      </Field>
      <Field label="Phone number" required error={problems.phone}>
        <input type="tel" maxLength={40} value={phone} onChange={(e) => setPhone(e.target.value)} />
      </Field>
      <p className="hint">The email address is changed by the Guard, with a code sent to it.</p>
      <OwnPassword value={password} onChange={setPassword} />
    </FormDialog>
  );
}

/** PUT /staff/guards/{id}/password. The Guard's earlier sessions stop working. */
export function SetPasswordDialog({ guard, onClose, onSaved }: { guard: StaffMember; onClose: () => void; onSaved: () => void }) {
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [password, setPassword] = useState("");
  const problem = next ? passwordProblem(next, guard.username ?? "", guard.email) : undefined;
  const mismatch = again && again !== next ? "The two passwords do not match." : undefined;
  return (
    <FormDialog
      title={`Set a new password for ${guard.full_name}`}
      submitLabel="Set password"
      onClose={onClose}
      disabled={!next || Boolean(problem) || again !== next || !password}
      onSubmit={async () => {
        await staff.setGuardPassword(guard.id, { new_password: next, current_password: password });
        onSaved();
      }}
    >
      <p className="dialog-message">The Guard is signed out everywhere and must use the new password. Tell it to them in person.</p>
      <Field label="New password" required hint={PASSWORD_HINT} error={problem}>
        <input type="password" autoComplete="new-password" maxLength={128} value={next} onChange={(e) => setNext(e.target.value)} />
      </Field>
      <Field label="Repeat the new password" required error={mismatch}>
        <input type="password" autoComplete="new-password" maxLength={128} value={again} onChange={(e) => setAgain(e.target.value)} />
      </Field>
      <OwnPassword value={password} onChange={setPassword} />
    </FormDialog>
  );
}

/** POST /staff/{id}/deactivate or /reactivate (Admins only). */
export function StatusDialog({ member, onClose, onSaved }: { member: StaffMember; onClose: () => void; onSaved: (m: StaffMember) => void }) {
  const [password, setPassword] = useState("");
  const deactivate = member.status === "ACTIVE";
  const label = ROLE_LABEL[member.role];
  return (
    <FormDialog
      title={`${deactivate ? "Deactivate" : "Reactivate"} ${member.full_name}?`}
      submitLabel={deactivate ? `Deactivate ${label}` : `Reactivate ${label}`}
      onClose={onClose}
      disabled={!password}
      onSubmit={async () =>
        onSaved(await (deactivate ? staff.deactivate : staff.reactivate)(member.id, { current_password: password }))
      }
    >
      <p className="dialog-message">
        {deactivate
          ? `The account's status becomes "Left": the ${label} cannot sign in and is signed out at once. Nothing is deleted, so history stays linked.${
              member.role === "GUARD" ? " The Guard's location assignment is removed." : ""
            }`
          : `The ${label} can sign in again with their existing password.${member.role === "GUARD" ? " They return as a Standby Guard." : ""}`}
      </p>
      <OwnPassword value={password} onChange={setPassword} />
    </FormDialog>
  );
}

/** PUT /locations/guard-assignments/{guard_id}: the Guard becomes a Stationed Guard (and is signed out). */
export function AssignLocationDialog({ guard, onClose, onSaved }: { guard: StaffMember; onClose: () => void; onSaved: () => void }) {
  const tree = useAsync(() => locations.tree(), []);
  const current = guard.location;
  const [value, setValue] = useState<LocationValue>(
    current ? { building_id: current.building_id, floor_id: current.floor_id ?? "", area_id: current.area_id ?? "" } : NO_LOCATION,
  );
  return (
    <FormDialog
      title={`${current ? "Change" : "Assign"} the post of ${guard.full_name}`}
      submitLabel={current ? "Save location" : "Assign location"}
      onClose={onClose}
      disabled={!value.building_id}
      onSubmit={async () => {
        await staff.assignGuard(guard.display_id, {
          building_id: value.building_id,
          floor_id: value.floor_id || null,
          area_id: value.area_id || null,
        });
        onSaved();
      }}
    >
      <p className="dialog-message">
        A Stationed Guard receives the alerts raised by cameras at or inside this location. Choose a building, and optionally a
        floor and area. The Guard is signed out of the app at once and signs in again in Stationed mode.
      </p>
      {tree.error ? (
        <p className="notice error" role="alert">
          {tree.error instanceof Error ? tree.error.message : String(tree.error)}
        </p>
      ) : tree.data && tree.data.length === 0 ? (
        <p className="notice warning">This campus has no buildings yet. An Admin adds them under Buildings &amp; Locations.</p>
      ) : (
        <div className="row picker-row" role="group" aria-label="Guard location">
          <LocationPicker tree={tree.data ?? []} value={value} onChange={setValue} anyBuildingLabel="Choose a building" />
        </div>
      )}
    </FormDialog>
  );
}
