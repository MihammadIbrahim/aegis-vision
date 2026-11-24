import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { useAuth } from "../../../auth/AuthContext";
import { canManagePersonnel } from "../../../auth/permissions";
import { ErrorState, PageHeader, StatusBadge } from "../../../components/ui";
import { personnel, PersonnelSelector, type PersonnelBasic } from "../../personnel";
import { access } from "../api";
import { AccessCheckForm } from "../components/AccessCheckForm";
import { GrantForm } from "../components/GrantForm";
import { GrantList } from "../components/GrantList";
import { RestrictedAreasPanel } from "../components/RestrictedAreasPanel";
import { areaLabel } from "../components/shared";
import type { Grant, RestrictedArea } from "../types";

export function RestrictedAccessPage() {
  const { session } = useAuth();
  const admin = canManagePersonnel(session?.role);
  const [params, setParams] = useSearchParams();
  const [areas, setAreas] = useState<RestrictedArea[]>([]);
  const [allAreas, setAllAreas] = useState<RestrictedArea[]>([]);
  const [person, setPerson] = useState<PersonnelBasic | null>(null);
  const [personGrants, setPersonGrants] = useState<Grant[]>([]);
  const [showRevoked, setShowRevoked] = useState(false);
  const [areaId, setAreaId] = useState("");
  const [areaGrants, setAreaGrants] = useState<Grant[]>([]);
  const [error, setError] = useState<unknown>(null);
  const personId = params.get("personnel");

  // Supervisors never ask for unrestricted areas or revoked history (both Admin-only on the server).
  const loadAreas = useCallback(() => {
    access.areas().then(setAreas).catch(setError);
    if (admin) access.areas(true).then(setAllAreas).catch(setError);
  }, [admin]);
  useEffect(loadAreas, [loadAreas]);

  const loadPerson = useCallback(() => {
    if (!personId) {
      setPerson(null);
      return;
    }
    personnel
      .details(personId)
      .then((p) => setPerson(p as PersonnelBasic))
      .catch(setError);
    access.personGrants(personId, admin && showRevoked).then(setPersonGrants).catch(setError);
  }, [personId, admin, showRevoked]);
  useEffect(loadPerson, [loadPerson]);

  const loadArea = useCallback(() => {
    if (areaId) access.areaGrants(areaId).then(setAreaGrants).catch(setError);
  }, [areaId]);
  useEffect(loadArea, [loadArea]);

  const refresh = () => {
    loadAreas();
    loadPerson();
    loadArea();
  };

  return (
    <section>
      <PageHeader
        title="Restricted-area access"
        description={admin ? undefined : "Read-only: who is currently authorized. Only Organization Admins can change access."}
      />
      {error ? <ErrorState error={error} onRetry={refresh} /> : null}
      <div className="grid-2">
        <div className="stack">
          <div className="card stack">
            <h2>Find a person</h2>
            <PersonnelSelector onSelect={(p) => setParams({ personnel: p.id })} />
          </div>
          {person && (
            <div className="card stack">
              <h2>
                {person.full_name} <StatusBadge status={person.status} />
              </h2>
              <p className="small">
                {person.personnel_id} · {person.category.label}
                {person.department ? ` · ${person.department}` : ""}
                {person.designation ? ` · ${person.designation}` : ""}
              </p>
              {admin && (
                <label className="checkbox">
                  <input type="checkbox" checked={showRevoked} onChange={(e) => setShowRevoked(e.target.checked)} />
                  Show revoked permissions (history)
                </label>
              )}
              <GrantList caption={`Access permissions of ${person.full_name}`} grants={personGrants} admin={admin} onChange={refresh} />
              {admin && <GrantForm person={person} areas={areas} onGranted={refresh} />}
              <AccessCheckForm personnelId={person.id} areas={areas} />
            </div>
          )}
        </div>
        <div className="stack">
          <div className="card stack">
            <h2>Who may enter an area</h2>
            <select aria-label="Restricted area" value={areaId} onChange={(e) => setAreaId(e.target.value)}>
              <option value="">Choose a restricted area…</option>
              {areas.map((a) => (
                <option key={a.area_id} value={a.area_id}>
                  {areaLabel(a)} ({a.active_grants})
                </option>
              ))}
            </select>
            {areaId && <GrantList caption="People authorized for this area" grants={areaGrants} admin={admin} onChange={refresh} />}
          </div>
          {admin && <RestrictedAreasPanel areas={allAreas} onChange={refresh} />}
        </div>
      </div>
      <p className="muted small">
        An access check needs a person who has already been identified. An unknown or uncertain person is never treated as a
        named person entering without permission.
      </p>
    </section>
  );
}
