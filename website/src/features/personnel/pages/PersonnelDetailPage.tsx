import { ImageUp, Pencil, RotateCcw, Trash2, UserX } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { useAuth } from "../../../auth/AuthContext";
import { canManagePersonnel } from "../../../auth/permissions";
import { Button, DataTable, ErrorState, LoadingState, PageHeader, StatusBadge, useToast } from "../../../components/ui";
import { useConfirm } from "../../../hooks/useConfirm";
import { formatDateTime, formatLocationPath } from "../../../lib/format";
import { access, type Grant } from "../../restricted-access";
import { personnel } from "../api";
import type { AuditEntry, PersonnelBasic, PersonnelDetail } from "../types";

function isDetail(p: PersonnelDetail | PersonnelBasic): p is PersonnelDetail {
  return "created_via" in p;
}

function Row({ label, value }: { label: string; value: unknown }) {
  const text = value === null || value === undefined || value === "" ? "—" : String(value);
  return (
    <>
      <dt>{label}</dt>
      <dd>{text}</dd>
    </>
  );
}

const ACTIONS: Record<string, string> = {
  PERSONNEL_CREATED: "Registered",
  PERSONNEL_UPDATED: "Details changed",
  PERSONNEL_DEACTIVATED: "Deactivated",
  PERSONNEL_REACTIVATED: "Reactivated",
  PERSONNEL_PHOTO_UPDATED: "Photo updated",
  PERSONNEL_PHOTO_REMOVED: "Photo removed",
  PERSONNEL_IMPORT_CONFIRMED: "Created by an Excel/CSV import",
  PERSONNEL_IMPORT_ROLLED_BACK: "Import rolled back",
};

function historyDetails(h: AuditEntry): string {
  if (Array.isArray(h.details.changed_fields)) return `Changed: ${(h.details.changed_fields as string[]).join(", ")}`;
  if (typeof h.details.imported_count === "number") return `${h.details.imported_count} records`;
  return "";
}

export function PersonnelDetailPage() {
  const { id = "" } = useParams();
  const { session } = useAuth();
  const admin = canManagePersonnel(session?.role);
  const toast = useToast();
  const { confirm, dialog } = useConfirm();
  const [person, setPerson] = useState<PersonnelDetail | PersonnelBasic | null>(null);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [history, setHistory] = useState<AuditEntry[]>([]);
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const found = await personnel.details(id);
      setPerson(found);
      setGrants(await access.personGrants(id));
      // History and photo are Admin-only on the server; Supervisors never request them.
      if (isDetail(found)) {
        setHistory(await personnel.history(id));
        if (found.has_photo) setPhotoUrl(URL.createObjectURL(await personnel.photo(id)));
        else setPhotoUrl(null);
      }
    } catch (e) {
      setError(e);
    }
  }, [id]);
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => () => (photoUrl ? URL.revokeObjectURL(photoUrl) : undefined), [photoUrl]);

  async function run(action: () => Promise<unknown>, done: string) {
    setBusy(true);
    try {
      await action();
      toast("success", done);
      await load();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function deactivate(name: string) {
    const answer = await confirm({
      title: `Deactivate ${name}?`,
      message: "The record is kept for history, and all of this person's restricted-area access is revoked.",
      confirmLabel: "Deactivate",
      tone: "danger",
      reason: { label: "Reason", maxLength: 500 },
    });
    if (answer.confirmed) await run(() => personnel.deactivate(id, answer.reason), "Person deactivated.");
  }

  async function reactivate(name: string) {
    const answer = await confirm({
      title: `Reactivate ${name}?`,
      message: "Earlier restricted-area access is not restored; grant it again if needed.",
      confirmLabel: "Reactivate",
    });
    if (answer.confirmed) await run(() => personnel.reactivate(id), "Person reactivated.");
  }

  async function removePhoto() {
    const answer = await confirm({ title: "Remove the photo?", confirmLabel: "Remove photo", tone: "danger" });
    if (answer.confirmed) await run(() => personnel.removePhoto(id), "Photo removed.");
  }

  if (!person) return error ? <ErrorState error={error} onRetry={() => void load()} /> : <LoadingState />;
  const detail = isDetail(person) ? person : null;
  const basic = isDetail(person) ? null : person;
  return (
    <section>
      <PageHeader
        back={{ to: "/personnel", label: "Personnel Directory" }}
        title={
          <>
            {person.full_name} <StatusBadge status={person.status} />
          </>
        }
        actions={
          admin &&
          detail && (
            <>
              <Link className="button" to={`/personnel/${id}/edit`}>
                <Pencil className="icon" aria-hidden="true" />
                Edit
              </Link>
              {detail.status === "ACTIVE" ? (
                <Button variant="danger" icon={UserX} loading={busy} onClick={() => void deactivate(person.full_name)}>
                  Deactivate
                </Button>
              ) : (
                <Button icon={RotateCcw} loading={busy} onClick={() => void reactivate(person.full_name)}>
                  Reactivate
                </Button>
              )}
            </>
          )
        }
      />
      {error ? <ErrorState error={error} onRetry={() => void load()} /> : null}
      <div className="grid-2">
        <div className="card">
          <h2>Details</h2>
          <dl>
            <Row label="Personnel ID" value={person.personnel_id} />
            <Row label="Institutional ID" value={person.external_id} />
            <Row label="Category" value={person.category.label} />
            <Row label="Department" value={person.department} />
            <Row label="Designation" value={person.designation} />
            <Row label="Start date" value={person.start_date} />
            <Row label="End date" value={person.end_date} />
            {detail ? (
              <>
                <Row label="Email" value={detail.email} />
                <Row label="Phone" value={detail.phone} />
                <Row label="Joining / registration date" value={detail.joining_date} />
                <Row label="Address" value={detail.address} />
                <Row label="Emergency contact" value={detail.emergency_contact_name} />
                <Row label="Emergency phone" value={detail.emergency_contact_phone} />
                <Row label="Relation" value={detail.emergency_contact_relation} />
                <Row
                  label="Assigned location"
                  value={detail.location && formatLocationPath(detail.location.building_name, detail.location.floor_name, detail.location.area_name)}
                />
                <Row label="Additional information" value={detail.notes} />
                {Object.entries(detail.custom_attributes).map(([key, value]) => (
                  <Row key={key} label={key.replace(/_/g, " ")} value={value} />
                ))}
                <Row label="Created via" value={detail.created_via === "IMPORT" ? "Excel/CSV import" : "Manual registration"} />
                <Row label="Created" value={`${formatDateTime(detail.created_at)} by ${detail.created_by ?? "—"}`} />
                <Row label="Last updated" value={`${formatDateTime(detail.updated_at)} by ${detail.updated_by ?? "—"}`} />
                {detail.deactivated_at && (
                  <Row label="Deactivated" value={`${formatDateTime(detail.deactivated_at)}: ${detail.deactivation_reason ?? ""}`} />
                )}
              </>
            ) : (
              <Row
                label="Assigned location"
                value={
                  basic?.assigned_location &&
                  formatLocationPath(basic.assigned_location.building, basic.assigned_location.floor, basic.assigned_location.area)
                }
              />
            )}
          </dl>
        </div>
        <div className="stack">
          <div className="card stack">
            <h2>Photo</h2>
            {detail ? (
              <>
                {photoUrl ? <img className="photo" src={photoUrl} alt={`Profile of ${person.full_name}`} /> : <p className="muted">No photo.</p>}
                <p className="muted small">A profile photo only. It is not used for face recognition and is not proof of identity.</p>
                <div className="actions">
                  <label className="button secondary">
                    <ImageUp className="icon" aria-hidden="true" />
                    Upload JPEG/PNG
                    <input
                      type="file"
                      accept="image/jpeg,image/png"
                      hidden
                      onChange={(e) => {
                        const file = e.target.files?.[0];
                        e.target.value = "";
                        if (file) void run(() => personnel.uploadPhoto(id, file), "Photo uploaded.");
                      }}
                    />
                  </label>
                  {detail.has_photo && (
                    <Button variant="ghost" icon={Trash2} onClick={() => void removePhoto()}>
                      Remove photo
                    </Button>
                  )}
                </div>
              </>
            ) : (
              <p className="muted">Photos are shown to Supervisors only within an authorized identity review.</p>
            )}
          </div>
          <div className="card">
            <h2>Restricted-area access</h2>
            {grants.length === 0 ? (
              <p className="muted">No active access permissions.</p>
            ) : (
              <ul className="plain">
                {grants.map((g) => (
                  <li key={g.id}>
                    <strong>{g.area.name}</strong> ({formatLocationPath(g.area.building, g.area.floor)}) — until{" "}
                    {g.valid_until ? formatDateTime(g.valid_until) : "revoked"}
                    {g.schedule && ` · ${g.schedule.start}–${g.schedule.end} (${g.schedule.timezone})`}
                  </li>
                ))}
              </ul>
            )}
            <Link to={`/restricted-access?personnel=${id}`}>{admin ? "Manage access" : "View access"}</Link>
          </div>
        </div>
      </div>
      {detail && (
        <div className="card">
          <h2>History</h2>
          <DataTable
            caption="History"
            rows={history}
            rowKey={(h) => `${h.occurred_at}-${h.action}`}
            empty="No history yet."
            columns={[
              { key: "when", header: "When", render: (h) => formatDateTime(h.occurred_at) },
              { key: "what", header: "What", render: (h) => ACTIONS[h.action] ?? h.action },
              { key: "by", header: "By", render: (h) => h.actor_name ?? "—" },
              { key: "details", header: "Details", className: "small", render: historyDetails },
            ]}
          />
        </div>
      )}
      {dialog}
    </section>
  );
}
