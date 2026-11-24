import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { imports } from "../api";
import type { ImportDetail } from "../types";

import { ErrorState, PageHeader } from "../../../components/ui";

// Bulk import (Organization Admins only). All or nothing: while any blocking error remains, no
// record is imported; the Admin downloads the marked-up copy of the file, fixes it and uploads
// again. Column suggestions are only suggestions: every column needs an explicit decision.

import { MappingStep } from "../components/MappingStep";
import { ResultStep } from "../components/ResultStep";
import { UploadStep } from "../components/UploadStep";

export { MappingStep, ResultStep, UploadStep };





export function ImportWizardPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<ImportDetail | null>(null);
  const [remap, setRemap] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    if (id) imports.get(id).then(setDetail).catch(setError);
    else setDetail(null);
  }, [id]);
  useEffect(load, [load]);

  const show = (d: ImportDetail) => {
    setRemap(false);
    setDetail(d);
    if (d.batch.id !== id) navigate(`/personnel/imports/${d.batch.id}`, { replace: true });
  };

  let step;
  if (!detail) step = <UploadStep onUploaded={show} />;
  else if (detail.batch.status === "UPLOADED" || remap) step = <MappingStep detail={detail} onValidated={show} />;
  else if (detail.batch.status === "VALIDATED" || detail.batch.status === "INVALID")
    step = (
      <ResultStep
        detail={detail}
        onConfirmed={show}
        onRemap={() => setRemap(true)}
        onRestart={() => {
          setDetail(null);
          navigate("/personnel/imports/new");
        }}
      />
    );
  else
    step = (
      <div className="card stack">
        <h2>Import {detail.batch.status.toLowerCase().replace("_", " ")}</h2>
        {detail.batch.status === "IMPORTED" && <p>{detail.batch.imported_count} personnel records were imported.</p>}
        <div className="actions">
          <Link className="button" to="/personnel">
            Open the directory
          </Link>
          <Link className="button secondary" to="/personnel/imports">
            Import history
          </Link>
        </div>
      </div>
    );

  return (
    <section>
      <PageHeader title="Bulk personnel import" back={{ to: "/personnel/imports", label: "Import history" }} />
      {error ? <ErrorState error={error} /> : null}
      {step}
    </section>
  );
}
